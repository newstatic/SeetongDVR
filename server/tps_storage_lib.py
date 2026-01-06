#!/usr/bin/env python3
"""
天视通 (Seetong) DVR TPS 文件存储库

统一管理所有 TPS 文件格式解析和视频数据处理算法：
1. TIndex00.tps 主索引解析
2. TRec*.tps 录像文件的帧索引解析（文件末尾）
3. NAL 单元解析和视频流读取
4. VPS 扫描和时间计算

文件结构:
- TIndex00.tps: 主索引文件，包含段落索引和帧索引
- TRec{N:06d}.tps: 录像文件，每个 256MB
  - 数据区域: 0x00000000 - 0x0F900000 (视频/音频数据)
  - 索引区域: 0x0F900000 - 0x10000000 (帧索引，倒序存储)
"""

import struct
import hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import List, Optional, Tuple, NamedTuple, Iterator, BinaryIO
from enum import IntEnum

import numpy as np

# ============================================================================
# 常量定义
# ============================================================================

# 时区
BEIJING_TZ = timezone(timedelta(hours=8))

# TIndex00.tps 常量
TPS_INDEX_MAGIC = 0x1F2E3D4C
SEGMENT_INDEX_OFFSET = 0x4FC
FRAME_INDEX_OFFSET = 0x84C0
ENTRY_SIZE = 0x40

# TRec 文件常量
TREC_FILE_SIZE = 0x10000000  # 256MB
TREC_INDEX_REGION_START = 0x0F900000  # 索引区域起始
TREC_FRAME_INDEX_MAGIC = 0x4C3D2E1F
TREC_FRAME_INDEX_SIZE = 44

# 通道定义
CHANNEL_VIDEO_CH1 = 2
CHANNEL_AUDIO = 3
CHANNEL_VIDEO_CH2 = 258
VALID_CHANNELS = (CHANNEL_VIDEO_CH1, CHANNEL_AUDIO, CHANNEL_VIDEO_CH2)

# 帧类型
FRAME_TYPE_I = 1
FRAME_TYPE_P = 3

# 有效时间戳下限：2020-01-01
MIN_VALID_TIMESTAMP = 1577836800


# ============================================================================
# NAL 类型定义
# ============================================================================

class NalType(IntEnum):
    """H.265/HEVC NAL 单元类型"""
    TRAIL_N = 0      # P帧 (非参考)
    TRAIL_R = 1      # P帧 (参考)
    IDR_W_RADL = 19  # IDR 帧
    IDR_N_LP = 20    # IDR 帧
    VPS = 32         # 视频参数集
    SPS = 33         # 序列参数集
    PPS = 34         # 图像参数集

    @classmethod
    def name(cls, value: int) -> str:
        """获取 NAL 类型名称"""
        names = {
            0: "TRAIL_N", 1: "TRAIL_R",
            19: "IDR_W_RADL", 20: "IDR_N_LP",
            32: "VPS", 33: "SPS", 34: "PPS",
        }
        return names.get(value, f"NAL_{value}")

    @classmethod
    def is_video_frame(cls, nal_type: int) -> bool:
        """判断是否为视频帧 NAL"""
        return nal_type in (cls.TRAIL_N, cls.TRAIL_R, cls.IDR_W_RADL, cls.IDR_N_LP)

    @classmethod
    def is_keyframe(cls, nal_type: int) -> bool:
        """判断是否为关键帧"""
        return nal_type in (cls.IDR_W_RADL, cls.IDR_N_LP)

    @classmethod
    def is_header(cls, nal_type: int) -> bool:
        """判断是否为头部 NAL (VPS/SPS/PPS)"""
        return nal_type in (cls.VPS, cls.SPS, cls.PPS)


# NAL 起始码
NAL_START_CODE_4 = b'\x00\x00\x00\x01'
NAL_START_CODE_3 = b'\x00\x00\x01'

# VPS 搜索模式 (00 00 00 01 40)
VPS_PATTERN = b'\x00\x00\x00\x01\x40'


# ============================================================================
# 数据结构定义
# ============================================================================

class FrameIndexRecord(NamedTuple):
    """TRec 文件中的帧索引记录（文件末尾索引区域）

    每条记录 44 字节，按时间倒序存储
    用于精确定位音视频帧
    """
    frame_type: int      # 1=I帧, 3=P帧/音频
    channel: int         # 2=Video CH1, 3=Audio, 258=Video CH2
    frame_seq: int       # 帧序号
    file_offset: int     # 数据区域内的偏移
    frame_size: int      # 帧数据大小
    timestamp_us: int    # 设备单调时钟（微秒级）
    unix_ts: int         # Unix时间戳（秒）


@dataclass
class SegmentRecord:
    """段落索引记录（TIndex00.tps 中）

    段落索引的序号 = TRec 文件编号
    例如: 段落 #0 对应 TRec000000.tps
    """
    file_index: int      # TRec 文件编号
    channel: int         # 通道号
    start_time: int      # 开始时间 (Unix秒)
    end_time: int        # 结束时间 (Unix秒)
    frame_count: int     # I帧/VPS 数量

    @property
    def start_datetime(self) -> datetime:
        return datetime.fromtimestamp(self.start_time, tz=BEIJING_TZ)

    @property
    def end_datetime(self) -> datetime:
        return datetime.fromtimestamp(self.end_time, tz=BEIJING_TZ)

    @property
    def duration_seconds(self) -> int:
        return self.end_time - self.start_time


@dataclass
class NalUnit:
    """NAL 单元信息"""
    offset: int      # 在数据中的偏移（包含起始码）
    size: int        # 总大小（包含起始码）
    nal_type: int    # NAL 类型
    data: bytes = None  # NAL 数据（不含起始码），可选


@dataclass
class VideoStreamState:
    """视频流读取状态"""
    file_handle: BinaryIO
    stream_pos: int          # 当前读取位置
    buffer: bytearray        # 读取缓冲区
    current_time_ms: int     # 当前时间戳（毫秒）
    frame_count: int = 0     # 已发送帧数
    vps: bytes = None        # 缓存的 VPS
    sps: bytes = None        # 缓存的 SPS
    pps: bytes = None        # 缓存的 PPS


# ============================================================================
# 索引缓存
# ============================================================================

# NumPy 结构化数组的 dtype
RECORD_DTYPE = np.dtype([
    ('frame_type', 'u4'),
    ('channel', 'u4'),
    ('frame_seq', 'u4'),
    ('file_offset', 'u4'),
    ('frame_size', 'u4'),
    ('timestamp_us', 'u8'),
    ('unix_ts', 'u4'),
])


class IndexCache:
    """帧索引缓存管理器

    使用 NumPy 存储，加载速度比纯 Python 快 10 倍以上
    """

    def __init__(self, cache_dir: Path = None):
        if cache_dir is None:
            cache_dir = Path(__file__).parent.parent / '.index_cache'
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(exist_ok=True)

    def _get_file_hash(self, file_path: str) -> str:
        """计算文件哈希（文件名 + 大小）"""
        path = Path(file_path)
        stat = path.stat()
        identifier = f"{path.name}:{stat.st_size}"
        return hashlib.md5(identifier.encode()).hexdigest()

    def _get_cache_path(self, file_hash: str) -> Path:
        return self.cache_dir / f"{file_hash}.npy"

    def save(self, file_path: str, records: List[FrameIndexRecord]) -> str:
        """保存帧索引到缓存"""
        file_hash = self._get_file_hash(file_path)
        cache_path = self._get_cache_path(file_hash)

        arr = np.array(
            [(r.frame_type, r.channel, r.frame_seq, r.file_offset,
              r.frame_size, r.timestamp_us, r.unix_ts) for r in records],
            dtype=RECORD_DTYPE
        )
        np.save(cache_path, arr)
        return file_hash

    def load(self, file_path: str) -> Optional[List[FrameIndexRecord]]:
        """从缓存加载帧索引"""
        file_hash = self._get_file_hash(file_path)
        cache_path = self._get_cache_path(file_hash)

        if not cache_path.exists():
            return None

        try:
            arr = np.load(cache_path)
            return [FrameIndexRecord._make(row) for row in arr.tolist()]
        except (ValueError, OSError):
            cache_path.unlink(missing_ok=True)
            return None

    def clear(self):
        """清除所有缓存"""
        for f in self.cache_dir.glob('*.npy'):
            f.unlink()


# 全局缓存实例
_index_cache = IndexCache()


# ============================================================================
# NAL 解析算法
# ============================================================================

def parse_nal_units(data: bytes) -> List[Tuple[int, int, int]]:
    """解析数据中的所有 NAL 单元

    Args:
        data: 原始字节数据

    Returns:
        List of (offset, size, nal_type)
        - offset: NAL 起始位置（包含起始码）
        - size: NAL 总大小（包含起始码）
        - nal_type: NAL 类型
    """
    results = []
    pos = 0

    while pos < len(data) - 4:
        # 检查 4 字节起始码
        if data[pos:pos + 4] == NAL_START_CODE_4:
            start = pos
            start_len = 4
        # 检查 3 字节起始码
        elif data[pos:pos + 3] == NAL_START_CODE_3:
            start = pos
            start_len = 3
        else:
            pos += 1
            continue

        # 获取 NAL 类型
        nal_byte_pos = start + start_len
        if nal_byte_pos >= len(data):
            break
        nal_type = (data[nal_byte_pos] >> 1) & 0x3F

        # 查找下一个起始码
        next_pos = start + start_len
        while next_pos < len(data) - 4:
            if data[next_pos:next_pos + 4] == NAL_START_CODE_4:
                break
            if data[next_pos:next_pos + 3] == NAL_START_CODE_3:
                break
            next_pos += 1
        else:
            next_pos = len(data)

        size = next_pos - start
        results.append((start, size, nal_type))
        pos = next_pos

    return results


def strip_start_code(nal_data: bytes) -> bytes:
    """去掉 NAL 起始码"""
    if len(nal_data) >= 4 and nal_data[:4] == NAL_START_CODE_4:
        return nal_data[4:]
    elif len(nal_data) >= 3 and nal_data[:3] == NAL_START_CODE_3:
        return nal_data[3:]
    return nal_data


def find_vps_sps_pps_idr(data: bytes) -> Optional[Tuple[bytes, bytes, bytes, bytes, int]]:
    """在数据中查找 VPS/SPS/PPS/IDR 序列

    从 VPS 开始，按顺序查找完整的视频头和第一个 IDR

    Args:
        data: 原始字节数据

    Returns:
        (vps, sps, pps, idr, idr_end_offset) 或 None
        - idr_end_offset: IDR 结束位置（相对于 data 开头）
    """
    nals = parse_nal_units(data)

    # 找到 VPS 的位置
    vps_idx = -1
    for i, (_, _, nal_type) in enumerate(nals):
        if nal_type == NalType.VPS:
            vps_idx = i
            break

    if vps_idx < 0:
        return None

    vps = sps = pps = idr = None
    idr_end_offset = 0

    for i in range(vps_idx, len(nals)):
        offset, size, nal_type = nals[i]
        nal_data = strip_start_code(data[offset:offset + size])

        if nal_type == NalType.VPS and vps is None:
            vps = nal_data
        elif nal_type == NalType.SPS and sps is None:
            sps = nal_data
        elif nal_type == NalType.PPS and pps is None:
            pps = nal_data
        elif NalType.is_keyframe(nal_type):
            idr = nal_data
            idr_end_offset = offset + size
            break

    if vps and sps and pps and idr:
        return (vps, sps, pps, idr, idr_end_offset)
    return None


# ============================================================================
# TRec 帧索引解析
# ============================================================================

def parse_trec_frame_index(rec_file_path: str, use_cache: bool = True) -> List[FrameIndexRecord]:
    """解析 TRec 文件中的帧索引

    帧索引位于文件末尾的索引区域（0x0F900000 开始），按时间倒序存储

    Args:
        rec_file_path: TRec 文件路径
        use_cache: 是否使用缓存

    Returns:
        按时间正序排列的帧索引记录列表
    """
    # 尝试从缓存加载
    if use_cache:
        cached = _index_cache.load(rec_file_path)
        if cached is not None:
            print(f"[IndexCache] 加载: {Path(rec_file_path).name} ({len(cached)} 条)")
            return cached

    records = []

    with open(rec_file_path, 'rb') as f:
        # 搜索帧索引起始位置
        magic_bytes = struct.pack('<I', TREC_FRAME_INDEX_MAGIC)
        f.seek(TREC_INDEX_REGION_START)
        data = f.read(0x700000)

        idx = data.find(magic_bytes)
        if idx == -1:
            return records

        index_start = TREC_INDEX_REGION_START + idx
        f.seek(index_start)

        while True:
            data = f.read(TREC_FRAME_INDEX_SIZE)
            if len(data) < TREC_FRAME_INDEX_SIZE:
                break

            magic = struct.unpack('<I', data[0:4])[0]
            if magic != TREC_FRAME_INDEX_MAGIC:
                break

            frame_type = struct.unpack('<I', data[4:8])[0]
            channel = struct.unpack('<I', data[8:12])[0]
            frame_seq = struct.unpack('<I', data[12:16])[0]
            file_offset = struct.unpack('<I', data[16:20])[0]
            frame_size = struct.unpack('<I', data[20:24])[0]
            timestamp_us = struct.unpack('<Q', data[24:32])[0]
            unix_ts = struct.unpack('<I', data[32:36])[0]

            if unix_ts > MIN_VALID_TIMESTAMP and channel in VALID_CHANNELS:
                records.append(FrameIndexRecord(
                    frame_type=frame_type,
                    channel=channel,
                    frame_seq=frame_seq,
                    file_offset=file_offset,
                    frame_size=frame_size,
                    timestamp_us=timestamp_us,
                    unix_ts=unix_ts,
                ))

    # 按时间正序排列
    records.sort(key=lambda x: x.timestamp_us)

    # 保存到缓存
    if use_cache and records:
        _index_cache.save(rec_file_path, records)
        print(f"[IndexCache] 保存: {Path(rec_file_path).name} ({len(records)} 条)")

    return records


# ============================================================================
# VPS 扫描
# ============================================================================

def scan_vps_positions(file_path: str) -> List[int]:
    """扫描文件中所有 VPS 位置

    Args:
        file_path: TRec 文件路径

    Returns:
        VPS 字节偏移列表
    """
    vps_positions = []

    with open(file_path, 'rb') as f:
        chunk_size = 64 * 1024 * 1024  # 64MB
        offset = 0
        overlap = len(VPS_PATTERN) - 1
        prev_tail = b''

        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break

            search_data = prev_tail + chunk
            search_offset = offset - len(prev_tail)

            pos = 0
            while True:
                pos = search_data.find(VPS_PATTERN, pos)
                if pos == -1:
                    break
                actual_pos = search_offset + pos
                vps_positions.append(actual_pos)
                pos += len(VPS_PATTERN)

            prev_tail = chunk[-overlap:] if len(chunk) >= overlap else chunk
            offset += len(chunk)

    return vps_positions


def find_nearest_vps(vps_times: List[int], target_time: int) -> Tuple[int, int]:
    """查找最接近目标时间的 VPS 索引

    Args:
        vps_times: VPS 时间戳列表
        target_time: 目标时间戳

    Returns:
        (index, time_diff): VPS 索引和时间差（秒）
    """
    if not vps_times:
        return 0, 0

    min_diff = abs(vps_times[0] - target_time)
    target_index = 0

    for i, vps_time in enumerate(vps_times):
        diff = abs(vps_time - target_time)
        if diff < min_diff:
            min_diff = diff
            target_index = i

    return target_index, min_diff


# ============================================================================
# TIndex00.tps 解析
# ============================================================================

def parse_tindex(index_path: str) -> Tuple[List[SegmentRecord], int, int]:
    """解析 TIndex00.tps 主索引文件

    Args:
        index_path: TIndex00.tps 文件路径

    Returns:
        (segments, file_count, entry_count)
    """
    segments = []

    with open(index_path, 'rb') as f:
        # 读取文件头
        f.seek(0)
        magic = struct.unpack('<I', f.read(4))[0]
        if magic != TPS_INDEX_MAGIC:
            raise ValueError(f"Invalid magic: {magic:08X}")

        f.seek(0x10)
        file_count = struct.unpack('<I', f.read(4))[0]
        entry_count = struct.unpack('<I', f.read(4))[0]

        # 读取段落索引
        f.seek(SEGMENT_INDEX_OFFSET)
        segment_index = 0

        for _ in range(entry_count + 20):
            data = f.read(ENTRY_SIZE)
            if len(data) < ENTRY_SIZE:
                break

            channel = data[4]
            frame_count = struct.unpack('<H', data[6:8])[0]
            start_time = struct.unpack('<I', data[8:12])[0]
            end_time = struct.unpack('<I', data[12:16])[0]

            # 过滤无效记录
            if channel == 0 or channel == 0xFE:
                segment_index += 1
                continue
            if start_time < MIN_VALID_TIMESTAMP or end_time <= start_time:
                segment_index += 1
                continue

            segments.append(SegmentRecord(
                file_index=segment_index,
                channel=channel,
                start_time=start_time,
                end_time=end_time,
                frame_count=frame_count,
            ))
            segment_index += 1

    return segments, file_count, entry_count


# ============================================================================
# 视频流读取器
# ============================================================================

class VideoStreamReader:
    """视频流读取器

    从指定位置连续读取 NAL 单元，支持缓冲区管理
    """

    CHUNK_SIZE = 64 * 1024  # 64KB
    MIN_BUFFER_SIZE = 256 * 1024  # 256KB

    def __init__(self, file_handle: BinaryIO, start_pos: int, start_time_ms: int):
        self.f = file_handle
        self.stream_pos = start_pos
        self.buffer = bytearray()
        self.current_time_ms = start_time_ms
        self.frame_interval_ms = 40  # 25fps
        self.frame_count = 0

    def set_fps(self, fps: float):
        """设置帧率"""
        self.frame_interval_ms = int(1000 / fps)

    def _fill_buffer(self) -> bool:
        """填充缓冲区

        Returns:
            False 如果文件结束
        """
        if len(self.buffer) >= self.MIN_BUFFER_SIZE:
            return True

        self.f.seek(self.stream_pos)
        chunk = self.f.read(self.CHUNK_SIZE)
        if not chunk:
            return False

        self.buffer.extend(chunk)
        self.stream_pos += len(chunk)
        return True

    def read_next_nals(self) -> Iterator[Tuple[bytes, int, int]]:
        """读取下一批 NAL 单元

        Yields:
            (nal_data, nal_type, timestamp_ms)
            - nal_data: NAL 数据（不含起始码）
            - nal_type: NAL 类型
            - timestamp_ms: 时间戳
        """
        if not self._fill_buffer():
            return

        nal_units = parse_nal_units(bytes(self.buffer))
        if len(nal_units) <= 1:
            return

        # 发送除最后一个之外的所有 NAL（最后一个可能不完整）
        for offset, size, nal_type in nal_units[:-1]:
            nal_data = strip_start_code(bytes(self.buffer[offset:offset + size]))

            yield (nal_data, nal_type, self.current_time_ms)

            # 视频帧更新时间戳
            if NalType.is_video_frame(nal_type):
                self.frame_count += 1
                self.current_time_ms += self.frame_interval_ms

        # 移除已处理的数据
        last_nal_end = nal_units[-2][0] + nal_units[-2][1]
        self.buffer = self.buffer[last_nal_end:]


# ============================================================================
# 统一存储管理器
# ============================================================================

class TPSStorage:
    """TPS 存储管理器

    统一管理索引解析、帧定位、视频流读取
    """

    def __init__(self, dvr_path: str):
        self.dvr_path = Path(dvr_path)
        self.segments: List[SegmentRecord] = []
        self.file_count = 0
        self.entry_count = 0
        self._frame_index_cache: dict = {}
        self._vps_cache: dict = {}
        self.loaded = False

    def load(self) -> bool:
        """加载主索引"""
        index_path = self.dvr_path / "TIndex00.tps"
        if not index_path.exists():
            print(f"索引文件不存在: {index_path}")
            return False

        try:
            self.segments, self.file_count, self.entry_count = parse_tindex(str(index_path))
            self.loaded = True
            print(f"✓ 已加载 {len(self.segments)} 个段落索引")
            return True
        except Exception as e:
            print(f"加载索引失败: {e}")
            return False

    def get_rec_file(self, file_index: int) -> Optional[Path]:
        """获取录像文件路径"""
        filepath = self.dvr_path / f"TRec{file_index:06d}.tps"
        return filepath if filepath.exists() else None

    def find_segment_by_time(self, timestamp: int, channel: int) -> Optional[SegmentRecord]:
        """根据时间戳查找段落"""
        for seg in self.segments:
            if seg.channel == channel and seg.start_time <= timestamp <= seg.end_time:
                return seg
        return None

    def get_frame_index(self, file_index: int) -> List[FrameIndexRecord]:
        """获取帧索引（带缓存）"""
        if file_index in self._frame_index_cache:
            return self._frame_index_cache[file_index]

        rec_file = self.get_rec_file(file_index)
        if not rec_file:
            return []

        records = parse_trec_frame_index(str(rec_file))
        self._frame_index_cache[file_index] = records
        return records

    def find_iframe_for_time(self, file_index: int, target_time: int, channel: int = CHANNEL_VIDEO_CH1
                             ) -> Tuple[Optional[FrameIndexRecord], int]:
        """查找最接近目标时间的 I 帧

        Args:
            file_index: TRec 文件编号
            target_time: 目标时间戳（秒）
            channel: 通道号

        Returns:
            (frame_record, frame_index) 或 (None, -1)
        """
        frame_index = self.get_frame_index(file_index)
        if not frame_index:
            return None, -1

        # 过滤视频帧
        video_frames = [f for f in frame_index if f.channel == channel]
        if not video_frames:
            return None, -1

        # 找到目标时间附近的 I 帧
        for i, vf in enumerate(video_frames):
            if vf.unix_ts >= target_time:
                # 向前查找最近的 I 帧
                for j in range(i, -1, -1):
                    if video_frames[j].frame_type == FRAME_TYPE_I:
                        return video_frames[j], j
                return video_frames[max(0, i - 1)], max(0, i - 1)

        return None, -1

    def read_video_header(self, file_index: int, iframe_offset: int
                          ) -> Optional[Tuple[bytes, bytes, bytes, bytes, int]]:
        """从 I 帧位置读取视频头（VPS/SPS/PPS/IDR）

        I 帧索引的 offset 可能不是 VPS 起始位置，需要在 512KB 范围内搜索

        Args:
            file_index: TRec 文件编号
            iframe_offset: I 帧的文件偏移

        Returns:
            (vps, sps, pps, idr, stream_start_pos) 或 None
            - stream_start_pos: IDR 结束后的绝对文件位置，用于继续读取 P 帧
        """
        rec_file = self.get_rec_file(file_index)
        if not rec_file:
            return None

        with open(rec_file, 'rb') as f:
            f.seek(iframe_offset)
            data = f.read(512 * 1024)  # 读取 512KB

            result = find_vps_sps_pps_idr(data)
            if result:
                vps, sps, pps, idr, idr_end_offset = result
                stream_start_pos = iframe_offset + idr_end_offset
                return (vps, sps, pps, idr, stream_start_pos)

        return None

    def create_stream_reader(self, file_index: int, stream_pos: int, start_time_ms: int
                             ) -> Optional[VideoStreamReader]:
        """创建视频流读取器

        Args:
            file_index: TRec 文件编号
            stream_pos: 开始读取的文件位置
            start_time_ms: 开始时间戳（毫秒）

        Returns:
            VideoStreamReader 或 None
        """
        rec_file = self.get_rec_file(file_index)
        if not rec_file:
            return None

        f = open(rec_file, 'rb')
        return VideoStreamReader(f, stream_pos, start_time_ms)


# ============================================================================
# 便捷函数
# ============================================================================

def create_storage(dvr_path: str) -> Optional[TPSStorage]:
    """创建并加载 TPS 存储管理器"""
    storage = TPSStorage(dvr_path)
    if storage.load():
        return storage
    return None
