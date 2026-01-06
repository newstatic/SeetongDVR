#!/usr/bin/env python3
"""
天视通 (Seetong) DVR 算法库

统一管理所有 TPS 文件格式解析和视频数据处理算法。

================================================================================
文件结构
================================================================================

TIndex00.tps - 主索引文件
  - 偏移 0x4FC: 段落索引 (SegmentRecord)，每条 64 字节
  - 段落索引序号 = TRec 文件编号 (segment_index -> TRec{index:06d}.tps)
  - 记录每个 TRec 文件的时间范围和通道信息

TRec{N:06d}.tps - 录像文件 (每个 256MB)
  - 数据区域: 0x00000000 - 0x0F900000 (视频/音频原始数据，H.265 NAL 流)
  - 索引区域: 0x0F900000 - 0x10000000 (帧索引，按时间倒序存储)

================================================================================
帧索引结构 (FrameIndexRecord, 44 字节)
================================================================================

  偏移   大小   字段
  0x00   4      Magic (0x4C3D2E1F)
  0x04   4      frame_type: 1=I帧, 3=P帧/音频
  0x08   4      channel: 2=Video CH1, 3=Audio, 258=Video CH2
  0x0C   4      frame_seq: 帧序号
  0x10   4      file_offset: 数据区域内偏移
  0x14   4      frame_size: 帧数据大小
  0x18   8      timestamp_us: 设备单调时钟（微秒）
  0x20   4      unix_ts: Unix 时间戳（秒）
  0x24   8      reserved

重要：frame_type=1 (I帧) 的 file_offset 指向的是帧索引记录对应的数据位置，
      但这个位置 **不一定是 VPS/SPS/PPS/IDR 的起始位置**！
      实际的 VPS 可能在这个偏移之后的几十KB处。

================================================================================
获取特定时间视频的算法
================================================================================

1. 查找段落 (find_segment_by_time)
   - 输入: timestamp, channel
   - 遍历 TIndex00.tps 的段落索引
   - 找到 start_time <= timestamp <= end_time 的段落
   - 返回: file_index (TRec 文件编号)

2. 加载帧索引 (get_frame_index)
   - 输入: file_index
   - 解析 TRec 文件末尾索引区域 (0x0F900000 开始)
   - 返回: 所有帧的 (frame_type, channel, file_offset, frame_size, unix_ts) 列表

3. 查找 I 帧 (stream_video_with_audio 中的逻辑)
   - 过滤出 channel=2 的视频帧
   - 从目标时间向前查找最近的 frame_type=1 的 I 帧
   - 获取该 I 帧的 file_offset

4. 读取视频头 (find_vps_sps_pps_idr)
   - 从 I 帧的 file_offset 开始读取 512KB 数据
   - 在这 512KB 中搜索 VPS (00 00 00 01 40) 起始位置
   - 从 VPS 开始按顺序提取: VPS -> SPS -> PPS -> IDR
   - 返回: (vps, sps, pps, idr, idr_end_offset)

5. 流式读取后续帧
   - 起始位置: file_offset + idr_end_offset (IDR 结束后)
   - 连续读取字节流，解析 NAL 单元 (00 00 00 01 或 00 00 01)
   - 按帧率 (25fps) 发送给客户端

================================================================================
关键理解
================================================================================

帧索引的 file_offset 是"元数据标记"，不是精确的 NAL 起始位置：
- I 帧的 offset 可能指向该时间点附近的数据
- 实际的 VPS/SPS/PPS/IDR 需要通过搜索字节流来找到
- 后续 P 帧必须从 IDR 结束位置连续读取，而不是使用帧索引的 offset
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


# ============================================================================
# 精确时间计算
# ============================================================================

def calculate_precise_time(seg: 'SegmentRecord', byte_offset: int,
                           data_region_size: int = TREC_INDEX_REGION_START) -> int:
    """根据字节偏移计算精确时间戳（简化版）

    使用字节位置线性插值公式：
    precise_time = start_time + (byte_offset / data_region_size) × duration

    Args:
        seg: 段落记录（包含 start_time, end_time）
        byte_offset: 数据在文件中的字节偏移
        data_region_size: 数据区域总大小（默认 0x0F900000）

    Returns:
        精确的 Unix 时间戳（秒）
    """
    if data_region_size <= 0:
        return seg.start_time

    duration = seg.end_time - seg.start_time
    time_offset = (byte_offset / data_region_size) * duration
    return int(seg.start_time + time_offset)


def calculate_precise_time_from_iframes(
    i_frames: list,
    target_offset: int,
    seg: 'SegmentRecord'
) -> int:
    """根据 I 帧列表计算目标偏移的精确时间

    PRD 附录 B 精确算法：使用相邻 I 帧的字节范围和时间范围进行插值

    Args:
        i_frames: I 帧列表，每个元素为 (offset, unix_ts)，按 offset 排序
        target_offset: 目标字节偏移
        seg: 段落记录

    Returns:
        精确的 Unix 时间戳（秒）
    """
    if not i_frames:
        return seg.start_time

    # 如果只有一个 I 帧，使用简化算法
    if len(i_frames) == 1:
        return calculate_precise_time(seg, target_offset)

    # 找到 target_offset 所在的 I 帧区间
    prev_iframe = None
    next_iframe = None

    for i, (offset, ts) in enumerate(i_frames):
        if offset <= target_offset:
            prev_iframe = (offset, ts)
            if i + 1 < len(i_frames):
                next_iframe = i_frames[i + 1]
        else:
            if prev_iframe is None:
                # target 在第一个 I 帧之前
                prev_iframe = (0, seg.start_time)
                next_iframe = (offset, ts)
            break

    if prev_iframe is None:
        return seg.start_time

    if next_iframe is None:
        # target 在最后一个 I 帧之后，使用段落结束时间
        next_iframe = (TREC_INDEX_REGION_START, seg.end_time)

    # 计算插值
    prev_offset, prev_time = prev_iframe
    next_offset, next_time = next_iframe

    byte_range = next_offset - prev_offset
    if byte_range <= 0:
        return prev_time

    time_range = next_time - prev_time
    byte_offset_in_range = target_offset - prev_offset

    time_offset = (byte_offset_in_range / byte_range) * time_range
    return int(prev_time + time_offset)


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
    支持两种时间计算模式：
    1. 帧率累加模式（默认）：每帧时间 = 上一帧时间 + 帧间隔
    2. 字节偏移模式：每帧时间 = 根据字节偏移插值计算
    """

    CHUNK_SIZE = 64 * 1024  # 64KB
    MIN_BUFFER_SIZE = 256 * 1024  # 256KB

    def __init__(self, file_handle: BinaryIO, start_pos: int, start_time_ms: int,
                 seg: 'SegmentRecord' = None, frame_offsets: List[Tuple[int, int]] = None):
        self.f = file_handle
        self.stream_pos = start_pos
        self.buffer = bytearray()
        self.buffer_start_pos = start_pos  # 缓冲区对应的文件起始位置
        self.current_time_ms = start_time_ms
        self.frame_interval_ms = 40  # 25fps
        self.frame_count = 0

        # 字节偏移时间计算所需的信息
        self.seg = seg
        self.frame_offsets = frame_offsets  # [(offset, unix_ts), ...] 用于精确时间计算
        self.use_precise_time = seg is not None and frame_offsets is not None

    def set_fps(self, fps: float):
        """设置帧率"""
        self.frame_interval_ms = int(1000 / fps)

    def _get_precise_time_ms(self, nal_file_offset: int) -> int:
        """根据 NAL 的文件偏移计算精确时间（毫秒）"""
        if not self.use_precise_time:
            return self.current_time_ms

        precise_time = calculate_precise_time_from_iframes(
            self.frame_offsets, nal_file_offset, self.seg
        )
        return precise_time * 1000

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
            print(f"[VideoStreamReader] _fill_buffer: 文件结束, stream_pos={self.stream_pos}")
            return False

        # 如果缓冲区为空，更新缓冲区起始位置
        if len(self.buffer) == 0:
            self.buffer_start_pos = self.stream_pos

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
        # 尝试多次填充缓冲区，跳过非 NAL 数据
        max_attempts = 10
        for attempt in range(max_attempts):
            if not self._fill_buffer():
                print(f"[VideoStreamReader] read_next_nals: _fill_buffer 返回 False, attempt={attempt}")
                return

            nal_units = parse_nal_units(bytes(self.buffer))
            if self.frame_count == 0 or attempt > 0:
                print(f"[VideoStreamReader] read_next_nals: buffer_len={len(self.buffer)}, nal_units_count={len(nal_units)}, attempt={attempt}")

            if len(nal_units) >= 2:
                break  # 找到足够的 NAL 单元

            # NAL 单元不足，可能遇到非视频数据区域
            if len(nal_units) == 0:
                # 完全没有 NAL 起始码，跳过整个缓冲区
                print(f"[VideoStreamReader] read_next_nals: 无 NAL 起始码, 跳过 {len(self.buffer)} 字节, 前32字节: {self.buffer[:32].hex()}")
                self.buffer.clear()
            elif len(nal_units) == 1:
                # 只有一个不完整的 NAL，需要更多数据
                # 强制读取更多数据
                self.f.seek(self.stream_pos)
                chunk = self.f.read(self.CHUNK_SIZE * 4)  # 读取更大的块
                if not chunk:
                    print(f"[VideoStreamReader] read_next_nals: 无法读取更多数据")
                    return
                self.buffer.extend(chunk)
                self.stream_pos += len(chunk)
        else:
            # 多次尝试后仍然失败
            print(f"[VideoStreamReader] read_next_nals: {max_attempts} 次尝试后仍无法找到 NAL 单元")
            return

        # 发送除最后一个之外的所有 NAL（最后一个可能不完整）
        for offset, size, nal_type in nal_units[:-1]:
            nal_data = strip_start_code(bytes(self.buffer[offset:offset + size]))

            # 计算此 NAL 的文件偏移
            nal_file_offset = self.buffer_start_pos + offset

            # 使用精确时间或帧率累加时间
            if self.use_precise_time and NalType.is_video_frame(nal_type):
                timestamp_ms = self._get_precise_time_ms(nal_file_offset)
            else:
                timestamp_ms = self.current_time_ms

            yield (nal_data, nal_type, timestamp_ms)

            # 视频帧更新时间戳（用于非精确模式的回退）
            if NalType.is_video_frame(nal_type):
                self.frame_count += 1
                self.current_time_ms += self.frame_interval_ms

        # 移除已处理的数据，更新缓冲区起始位置
        last_nal_end = nal_units[-2][0] + nal_units[-2][1]
        self.buffer_start_pos += last_nal_end
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
        self._iframe_offsets_cache: dict = {}  # 缓存 I 帧偏移列表: {(file_index, channel): [(offset, unix_ts), ...]}
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

    def get_segment_by_file_index(self, file_index: int) -> Optional[SegmentRecord]:
        """根据文件索引查找段落"""
        for seg in self.segments:
            if seg.file_index == file_index:
                return seg
        return None

    def get_iframe_offsets(self, file_index: int, channel: int) -> List[Tuple[int, int]]:
        """获取 I 帧偏移列表（带缓存）

        Args:
            file_index: TRec 文件编号
            channel: 通道号

        Returns:
            [(offset, unix_ts), ...] 按 offset 排序
            如果没有 frame_type=1 的帧，返回采样的帧偏移列表
        """
        cache_key = (file_index, channel)
        if cache_key in self._iframe_offsets_cache:
            return self._iframe_offsets_cache[cache_key]

        frame_index = self.get_frame_index(file_index)
        video_frames = [f for f in frame_index if f.channel == channel]

        # 先尝试获取 frame_type=1 的 I 帧
        i_frame_offsets = [(vf.file_offset, vf.unix_ts) for vf in video_frames if vf.frame_type == FRAME_TYPE_I]

        # 如果没有 frame_type=1 的帧，使用采样的帧偏移
        if not i_frame_offsets and video_frames:
            sample_interval = max(1, len(video_frames) // 100)
            i_frame_offsets = [(video_frames[i].file_offset, video_frames[i].unix_ts)
                               for i in range(0, len(video_frames), sample_interval)]

        i_frame_offsets.sort(key=lambda x: x[0])

        self._iframe_offsets_cache[cache_key] = i_frame_offsets
        return i_frame_offsets

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

        使用精确时间算法：根据字节偏移插值计算每帧的精确时间

        Args:
            file_index: TRec 文件编号
            target_time: 目标时间戳（秒）
            channel: 通道号

        Returns:
            (frame_record, frame_index) 或 (None, -1)
        """
        print(f"[seetong_lib] find_iframe_for_time: file_index={file_index}, target_time={target_time}, channel={channel}")

        # 获取段落信息（用于精确时间计算）
        seg = self.get_segment_by_file_index(file_index)

        frame_index = self.get_frame_index(file_index)
        if not frame_index:
            print(f"[seetong_lib] find_iframe_for_time: 帧索引为空!")
            return None, -1

        # 过滤视频帧
        video_frames = [f for f in frame_index if f.channel == channel]
        print(f"[seetong_lib] find_iframe_for_time: 总帧数={len(frame_index)}, 视频帧={len(video_frames)}")
        if not video_frames:
            print(f"[seetong_lib] find_iframe_for_time: 无视频帧!")
            return None, -1

        # 调试：统计 frame_type 分布
        frame_types = {}
        for vf in video_frames[:1000]:  # 只检查前1000帧
            ft = vf.frame_type
            frame_types[ft] = frame_types.get(ft, 0) + 1
        print(f"[seetong_lib] find_iframe_for_time: frame_type 分布（前1000帧）: {frame_types}")

        # 检查是否有 frame_type=1 的 I 帧
        has_iframe_type = FRAME_TYPE_I in frame_types

        # 使用精确时间算法查找 I 帧
        if seg:
            print(f"[seetong_lib] find_iframe_for_time: 使用精确时间算法, seg.start={seg.start_time}, seg.end={seg.end_time}")

            if has_iframe_type:
                # 使用缓存的 I 帧偏移列表
                i_frame_offsets = self.get_iframe_offsets(file_index, channel)
                print(f"[seetong_lib] find_iframe_for_time: I 帧偏移列表长度={len(i_frame_offsets)}")

                # 收集所有 I 帧及其精确时间（使用相邻 I 帧插值）
                i_frames = []
                for idx, vf in enumerate(video_frames):
                    if vf.frame_type == FRAME_TYPE_I:
                        # 使用相邻 I 帧进行精确插值
                        precise_time = calculate_precise_time_from_iframes(i_frame_offsets, vf.file_offset, seg)
                        i_frames.append((idx, vf, precise_time))
            else:
                # 没有 frame_type=1 的帧，使用简化的字节偏移时间算法
                # 每个视频帧都可以作为起点（通过 VPS 搜索找到关键帧）
                print(f"[seetong_lib] find_iframe_for_time: 无 frame_type=1，使用字节偏移算法")
                i_frames = []
                # 每隔一定数量的帧采样一个作为潜在的 I 帧位置
                sample_interval = max(1, len(video_frames) // 100)  # 约 100 个采样点
                for idx in range(0, len(video_frames), sample_interval):
                    vf = video_frames[idx]
                    precise_time = calculate_precise_time(seg, vf.file_offset)
                    i_frames.append((idx, vf, precise_time))
                print(f"[seetong_lib] find_iframe_for_time: 采样了 {len(i_frames)} 个帧作为潜在 I 帧")

            print(f"[seetong_lib] find_iframe_for_time: 找到 {len(i_frames)} 个 I 帧")
            if i_frames:
                # 找到目标时间之前最近的 I 帧
                best_iframe = None
                best_idx = -1
                best_time = 0

                for idx, vf, precise_time in i_frames:
                    if precise_time <= target_time:
                        if best_iframe is None or precise_time > best_time:
                            best_iframe = vf
                            best_idx = idx
                            best_time = precise_time

                # 如果没有找到之前的 I 帧，使用第一个
                if best_iframe is None:
                    best_idx, best_iframe, best_time = i_frames[0]

                print(f"[seetong_lib] find_iframe_for_time: 精确算法找到 I 帧 idx={best_idx}, "
                      f"offset={best_iframe.file_offset}, precise_time={best_time}, "
                      f"diff={target_time - best_time}s")
                return best_iframe, best_idx

        # 回退：使用 unix_ts 字段
        print(f"[seetong_lib] find_iframe_for_time: 使用 unix_ts 回退算法")
        if video_frames:
            print(f"[seetong_lib] find_iframe_for_time: 视频帧时间范围 {video_frames[0].unix_ts} - {video_frames[-1].unix_ts}")

        for i, vf in enumerate(video_frames):
            if vf.unix_ts >= target_time:
                if has_iframe_type:
                    # 从当前位置向前找最近的 I 帧
                    for j in range(i, -1, -1):
                        if video_frames[j].frame_type == FRAME_TYPE_I:
                            print(f"[seetong_lib] find_iframe_for_time: 找到 I 帧 j={j}, offset={video_frames[j].file_offset}")
                            return video_frames[j], j
                    # 没找到之前的 I 帧，从当前位置向后找
                    for j in range(i, len(video_frames)):
                        if video_frames[j].frame_type == FRAME_TYPE_I:
                            print(f"[seetong_lib] find_iframe_for_time: 向后找到 I 帧 j={j}, offset={video_frames[j].file_offset}")
                            return video_frames[j], j
                    # 完全没有 I 帧，返回失败
                    print(f"[seetong_lib] find_iframe_for_time: 回退算法未找到任何 I 帧!")
                    return None, -1
                else:
                    # 没有 frame_type=1，直接使用当前帧（通过 VPS 搜索找关键帧）
                    print(f"[seetong_lib] find_iframe_for_time: 无 frame_type=1，使用帧 i={i}, offset={vf.file_offset}")
                    return vf, i

        print(f"[seetong_lib] find_iframe_for_time: 目标时间超出范围!")
        return None, -1

    def get_precise_time_for_offset(self, file_index: int, byte_offset: int, channel: int = CHANNEL_VIDEO_CH1) -> int:
        """根据字节偏移获取精确时间戳

        使用 PRD 附录 B 精确算法：基于相邻 I 帧的字节范围进行插值

        Args:
            file_index: TRec 文件编号
            byte_offset: 数据在文件中的字节偏移
            channel: 通道号

        Returns:
            精确的 Unix 时间戳（秒）
        """
        seg = self.get_segment_by_file_index(file_index)
        if seg is None:
            return 0

        # 使用缓存的 I 帧偏移列表
        i_frame_offsets = self.get_iframe_offsets(file_index, channel)

        if i_frame_offsets:
            return calculate_precise_time_from_iframes(i_frame_offsets, byte_offset, seg)
        else:
            return calculate_precise_time(seg, byte_offset)

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
        print(f"[seetong_lib] read_video_header: file_index={file_index}, iframe_offset={iframe_offset}")
        rec_file = self.get_rec_file(file_index)
        if not rec_file:
            print(f"[seetong_lib] read_video_header: rec_file 不存在!")
            return None

        print(f"[seetong_lib] read_video_header: 打开文件 {rec_file}")
        with open(rec_file, 'rb') as f:
            f.seek(iframe_offset)
            data = f.read(512 * 1024)  # 读取 512KB
            print(f"[seetong_lib] read_video_header: 读取了 {len(data)} 字节")

            result = find_vps_sps_pps_idr(data)
            if result:
                vps, sps, pps, idr, idr_end_offset = result
                stream_start_pos = iframe_offset + idr_end_offset
                print(f"[seetong_lib] read_video_header: 找到视频头, idr_end_offset={idr_end_offset}, stream_start_pos={stream_start_pos}")
                return (vps, sps, pps, idr, stream_start_pos)

        print(f"[seetong_lib] read_video_header: 未找到 VPS/SPS/PPS/IDR!")
        return None

    def create_stream_reader(self, file_index: int, stream_pos: int, start_time_ms: int,
                             channel: int = CHANNEL_VIDEO_CH1
                             ) -> Optional[VideoStreamReader]:
        """创建视频流读取器

        Args:
            file_index: TRec 文件编号
            stream_pos: 开始读取的文件位置
            start_time_ms: 开始时间戳（毫秒）
            channel: 通道号（用于获取帧偏移列表）

        Returns:
            VideoStreamReader 或 None
        """
        rec_file = self.get_rec_file(file_index)
        if not rec_file:
            return None

        # 获取段落信息和帧偏移列表（用于精确时间计算）
        seg = self.get_segment_by_file_index(file_index)
        frame_offsets = self.get_iframe_offsets(file_index, channel)

        f = open(rec_file, 'rb')
        return VideoStreamReader(f, stream_pos, start_time_ms, seg, frame_offsets)


# ============================================================================
# 便捷函数
# ============================================================================

def create_storage(dvr_path: str) -> Optional[TPSStorage]:
    """创建并加载 TPS 存储管理器"""
    storage = TPSStorage(dvr_path)
    if storage.load():
        return storage
    return None
