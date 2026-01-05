#!/usr/bin/env python3
"""
天视通 (Seetong) DVR TPS 文件解析器
包含索引解析和视频帧提取功能

功能:
1. 解析 TIndex00.tps 索引文件
2. 解析 TRec*.tps 录像文件中的视频帧
3. 支持按时间定位和提取视频片段
4. 导出为标准 H.265 文件供播放器播放
"""

import struct
import os
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import List, Optional, BinaryIO, Tuple, Iterator
import json
import sys
from pathlib import Path

# 北京时间 UTC+8
BEIJING_TZ = timezone(timedelta(hours=8))

# H.265 NAL 起始码
NAL_START_CODE = b'\x00\x00\x00\x01'
NAL_START_CODE_3 = b'\x00\x00\x01'


# H.265 NAL 类型
class NalType:
    VPS = 32  # 0x40 >> 1
    SPS = 33  # 0x42 >> 1
    PPS = 34  # 0x44 >> 1
    IDR_W_RADL = 19  # 0x26 >> 1
    IDR_N_LP = 20
    TRAIL_R = 1
    TRAIL_N = 0


@dataclass
class TPSHeader:
    """TPS索引文件头"""
    magic: bytes
    file_count: int
    entry_count: int


@dataclass
class IndexEntry:
    """索引条目"""
    offset: int
    channel: int
    frame_count: int
    start_time: int
    end_time: int
    file_offset: int
    entry_index: int

    @property
    def start_datetime(self) -> datetime:
        return datetime.fromtimestamp(self.start_time, tz=BEIJING_TZ)

    @property
    def end_datetime(self) -> datetime:
        return datetime.fromtimestamp(self.end_time, tz=BEIJING_TZ)

    @property
    def duration_seconds(self) -> int:
        return self.end_time - self.start_time

    @property
    def duration_str(self) -> str:
        secs = self.duration_seconds
        hours = secs // 3600
        mins = (secs % 3600) // 60
        secs = secs % 60
        return f"{hours:02d}:{mins:02d}:{secs:02d}"

    def to_dict(self) -> dict:
        return {
            "entry_index": self.entry_index,
            "offset": hex(self.offset),
            "channel": self.channel,
            "frame_count": self.frame_count,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "start_datetime": self.start_datetime.isoformat(),
            "end_datetime": self.end_datetime.isoformat(),
            "duration": self.duration_str,
            "file_offset": hex(self.file_offset),
        }


@dataclass
class VideoFrame:
    """视频帧信息"""
    offset: int  # 在文件中的偏移
    size: int  # 帧大小
    nal_type: int  # NAL类型
    is_keyframe: bool  # 是否关键帧
    timestamp: int = 0  # 时间戳(如果可用)

    @property
    def nal_type_name(self) -> str:
        names = {
            0: "TRAIL_N", 1: "TRAIL_R",
            19: "IDR_W_RADL", 20: "IDR_N_LP",
            32: "VPS", 33: "SPS", 34: "PPS",
        }
        return names.get(self.nal_type, f"NAL_{self.nal_type}")


class TPSIndexParser:
    """TPS索引文件解析器"""

    INDEX_START_OFFSET = 0x4FC
    ENTRY_SIZE = 0x40

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.header: Optional[TPSHeader] = None
        self.entries: List[IndexEntry] = []

    def parse(self) -> bool:
        try:
            with open(self.filepath, 'rb') as f:
                self._parse_header(f)
                self._parse_entries(f)
            return True
        except Exception as e:
            print(f"解析错误: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _parse_header(self, f: BinaryIO):
        f.seek(0)
        magic = f.read(8)

        f.seek(0x10)
        file_count = struct.unpack('<I', f.read(4))[0]
        entry_count = struct.unpack('<I', f.read(4))[0]

        self.header = TPSHeader(magic=magic, file_count=file_count, entry_count=entry_count)

    def _parse_entries(self, f: BinaryIO):
        self.entries = []

        f.seek(0, 2)
        file_size = f.tell()

        offset = self.INDEX_START_OFFSET
        entry_index = 0

        while offset + self.ENTRY_SIZE <= file_size:
            f.seek(offset)
            data = f.read(self.ENTRY_SIZE)

            if len(data) < self.ENTRY_SIZE:
                break

            file_offset = struct.unpack('<I', data[0:4])[0]
            channel = data[4]
            flags = data[5]
            frame_count = struct.unpack('<H', data[6:8])[0]
            start_time = struct.unpack('<I', data[8:12])[0]
            end_time = struct.unpack('<I', data[12:16])[0]

            if channel == 0 or channel == 0xFE:
                offset += self.ENTRY_SIZE
                entry_index += 1
                continue

            if start_time < 1577836800 or end_time < 1577836800:
                offset += self.ENTRY_SIZE
                entry_index += 1
                continue

            if end_time <= start_time:
                offset += self.ENTRY_SIZE
                entry_index += 1
                continue

            entry = IndexEntry(
                offset=offset,
                channel=channel,
                frame_count=frame_count,
                start_time=start_time,
                end_time=end_time,
                file_offset=file_offset,
                entry_index=entry_index
            )

            self.entries.append(entry)
            offset += self.ENTRY_SIZE
            entry_index += 1

    def get_time_range(self) -> Tuple[Optional[datetime], Optional[datetime]]:
        if not self.entries:
            return None, None

        min_time = min(e.start_time for e in self.entries)
        max_time = max(e.end_time for e in self.entries)

        return (
            datetime.fromtimestamp(min_time, tz=BEIJING_TZ),
            datetime.fromtimestamp(max_time, tz=BEIJING_TZ)
        )

    def get_channels(self) -> List[int]:
        return sorted(set(e.channel for e in self.entries))

    def get_entries_by_channel(self, channel: int) -> List[IndexEntry]:
        return [e for e in self.entries if e.channel == channel]

    def get_entries_by_time(self, start: datetime, end: datetime, channel: int = None) -> List[IndexEntry]:
        """获取指定时间范围内的索引条目"""
        start_ts = int(start.timestamp())
        end_ts = int(end.timestamp())

        results = []
        for e in self.entries:
            if channel is not None and e.channel != channel:
                continue
            # 检查时间范围是否有交集
            if e.start_time <= end_ts and e.end_time >= start_ts:
                results.append(e)
        return results

    def find_entry_at_time(self, target_time: datetime, channel: int = None) -> Optional[IndexEntry]:
        """查找包含指定时间点的索引条目"""
        target_ts = int(target_time.timestamp())

        for entry in self.entries:
            if channel is not None and entry.channel != channel:
                continue
            if entry.start_time <= target_ts <= entry.end_time:
                return entry
        return None

    def export_to_json(self, output_path: str):
        start_time, end_time = self.get_time_range()
        data = {
            "header": {
                "magic": self.header.magic.hex(),
                "file_count": self.header.file_count,
                "entry_count": self.header.entry_count,
            },
            "time_range": {
                "start": start_time.isoformat() if start_time else None,
                "end": end_time.isoformat() if end_time else None,
            },
            "channels": self.get_channels(),
            "total_entries": len(self.entries),
            "entries": [e.to_dict() for e in self.entries]
        }

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"\n✓ 索引已导出到 {output_path}")

    def print_summary(self):
        print("=" * 90)
        print("天视通 (Seetong) TPS 索引文件解析结果")
        print("=" * 90)

        if self.header:
            print(f"\n【文件头信息】")
            print(f"  魔数: {self.header.magic.hex()}")
            print(f"  录像文件数: {self.header.file_count}")
            print(f"  索引条目数(头部): {self.header.entry_count}")

        if self.entries:
            start_time, end_time = self.get_time_range()
            channels = self.get_channels()

            print(f"\n【录像时间范围】")
            print(f"  开始: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  结束: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  总跨度: {end_time - start_time}")

            print(f"\n【通道信息】")
            print(f"  通道数: {len(channels)}")
            print(f"  通道列表: {channels}")

            print(f"\n【解析统计】")
            print(f"  有效索引条目: {len(self.entries)}")

            print("\n【各通道录像统计】")
            for ch in channels:
                ch_entries = self.get_entries_by_channel(ch)
                total_duration = sum(e.duration_seconds for e in ch_entries)
                hours = total_duration // 3600
                mins = (total_duration % 3600) // 60
                total_frames = sum(e.frame_count for e in ch_entries)
                print(f"  通道 {ch:2d}: {len(ch_entries):4d} 个片段, "
                      f"总时长 {hours:3d}小时{mins:02d}分钟, "
                      f"I帧总数 {total_frames}")

    def print_entries(self, limit: int = None, channel: int = None):
        entries = self.entries
        if channel is not None:
            entries = self.get_entries_by_channel(channel)

        total = len(entries)
        if limit:
            entries = entries[:limit]

        print(f"\n【索引条目详情】(共 {total} 条，显示前 {len(entries)} 条)")
        print("-" * 110)
        print(
            f"{'序号':>4} | {'通道':>4} | {'开始时间':^19} | {'结束时间':^8} | {'时长':^8} | {'I帧数':>5} | {'文件偏移':>12}")
        print("-" * 110)
        for i, entry in enumerate(entries):
            print(f"{i + 1:4d} | Ch{entry.channel:02d} | "
                  f"{entry.start_datetime.strftime('%Y-%m-%d %H:%M:%S')} | "
                  f"{entry.end_datetime.strftime('%H:%M:%S')} | "
                  f"{entry.duration_str} | "
                  f"{entry.frame_count:5d} | "
                  f"0x{entry.file_offset:08X}")


class TPSVideoParser:
    """TPS录像文件视频帧解析器"""

    def __init__(self, rec_dir: str):
        """
        Args:
            rec_dir: 包含TRec*.tps文件的目录路径
        """
        self.rec_dir = Path(rec_dir)
        self.rec_files = sorted(self.rec_dir.glob("TRec*.tps"))

    def get_rec_file(self, index: int) -> Optional[Path]:
        """获取指定索引的录像文件"""
        filename = f"TRec{index:06d}.tps"
        filepath = self.rec_dir / filename
        return filepath if filepath.exists() else None

    def find_nal_units(self, data: bytes, max_count: int = None) -> List[Tuple[int, int, int]]:
        """
        查找数据中的所有NAL单元

        Returns:
            List of (offset, size, nal_type)
        """
        results = []
        pos = 0

        while pos < len(data) - 4:
            # 查找起始码
            if data[pos:pos + 4] == NAL_START_CODE:
                start = pos + 4
                # NAL类型在第一个字节的高6位 >> 1
                if start < len(data):
                    nal_type = (data[start] >> 1) & 0x3F

                    # 查找下一个起始码来确定大小
                    next_pos = data.find(NAL_START_CODE, start)
                    if next_pos == -1:
                        size = len(data) - pos
                    else:
                        size = next_pos - pos

                    results.append((pos, size, nal_type))

                    if max_count and len(results) >= max_count:
                        break

                    pos = pos + size
                else:
                    pos += 1
            elif data[pos:pos + 3] == NAL_START_CODE_3:
                start = pos + 3
                if start < len(data):
                    nal_type = (data[start] >> 1) & 0x3F

                    next_pos = data.find(NAL_START_CODE_3, start)
                    next_pos4 = data.find(NAL_START_CODE, start)

                    if next_pos == -1 and next_pos4 == -1:
                        size = len(data) - pos
                    elif next_pos == -1:
                        size = next_pos4 - pos
                    elif next_pos4 == -1:
                        size = next_pos - pos
                    else:
                        size = min(next_pos, next_pos4) - pos

                    results.append((pos, size, nal_type))

                    if max_count and len(results) >= max_count:
                        break

                    pos = pos + size
                else:
                    pos += 1
            else:
                pos += 1

        return results

    def extract_video_header(self, file_index: int = 0) -> Optional[bytes]:
        """
        提取视频头 (VPS + SPS + PPS)
        通常在第一个录像文件的开头
        """
        rec_file = self.get_rec_file(file_index)
        if not rec_file:
            return None

        with open(rec_file, 'rb') as f:
            # 读取前 1KB 来查找头信息
            data = f.read(1024)

        nals = self.find_nal_units(data, max_count=10)

        header_data = bytearray()
        for offset, size, nal_type in nals:
            if nal_type in (NalType.VPS, NalType.SPS, NalType.PPS):
                header_data.extend(data[offset:offset + size])
            elif nal_type in (NalType.IDR_W_RADL, NalType.IDR_N_LP):
                # 遇到IDR帧就停止
                break

        return bytes(header_data) if header_data else None

    def scan_keyframes(self, file_index: int, max_frames: int = 100) -> List[VideoFrame]:
        """扫描录像文件中的关键帧"""
        rec_file = self.get_rec_file(file_index)
        if not rec_file:
            return []

        frames = []

        with open(rec_file, 'rb') as f:
            # 分块读取并查找关键帧
            chunk_size = 1024 * 1024  # 1MB
            file_offset = 0

            while len(frames) < max_frames:
                f.seek(file_offset)
                data = f.read(chunk_size)
                if not data:
                    break

                nals = self.find_nal_units(data)

                for offset, size, nal_type in nals:
                    is_keyframe = nal_type in (NalType.IDR_W_RADL, NalType.IDR_N_LP)

                    if is_keyframe or nal_type in (NalType.VPS, NalType.SPS, NalType.PPS):
                        frame = VideoFrame(
                            offset=file_offset + offset,
                            size=size,
                            nal_type=nal_type,
                            is_keyframe=is_keyframe
                        )
                        frames.append(frame)

                        if len(frames) >= max_frames:
                            break

                file_offset += chunk_size - 100  # 留一些重叠避免边界问题

        return frames

    def extract_clip(self, start_offset: int, end_offset: int,
                     file_index: int, output_path: str,
                     include_header: bool = True) -> bool:
        """
        从录像文件中提取视频片段

        Args:
            start_offset: 起始偏移
            end_offset: 结束偏移 (-1 表示到文件末尾)
            file_index: 录像文件索引
            output_path: 输出文件路径
            include_header: 是否包含视频头
        """
        rec_file = self.get_rec_file(file_index)
        if not rec_file:
            print(f"错误: 找不到录像文件 TRec{file_index:06d}.tps")
            return False

        try:
            with open(output_path, 'wb') as out_f:
                # 写入视频头
                if include_header:
                    header = self.extract_video_header(file_index)
                    if header:
                        out_f.write(header)

                # 写入视频数据
                with open(rec_file, 'rb') as in_f:
                    in_f.seek(start_offset)

                    if end_offset == -1:
                        # 读到文件末尾
                        while True:
                            chunk = in_f.read(1024 * 1024)
                            if not chunk:
                                break
                            out_f.write(chunk)
                    else:
                        remaining = end_offset - start_offset
                        while remaining > 0:
                            chunk_size = min(remaining, 1024 * 1024)
                            chunk = in_f.read(chunk_size)
                            if not chunk:
                                break
                            out_f.write(chunk)
                            remaining -= len(chunk)

            print(f"✓ 已导出到 {output_path}")
            return True

        except Exception as e:
            print(f"导出错误: {e}")
            return False

    def export_full_file(self, file_index: int, output_path: str) -> bool:
        """导出完整的录像文件为H.265格式"""
        return self.extract_clip(0, -1, file_index, output_path, include_header=False)


class SeetongDVR:
    """天视通DVR完整解析器"""

    def __init__(self, dvr_path: str):
        """
        Args:
            dvr_path: DVR存储路径 (包含TIndex00.tps和TRec*.tps的目录)
        """
        self.dvr_path = Path(dvr_path)
        self.index_parser = TPSIndexParser(str(self.dvr_path / "TIndex00.tps"))
        self.video_parser = TPSVideoParser(str(self.dvr_path))

    def load(self) -> bool:
        """加载DVR数据"""
        if not self.index_parser.parse():
            return False

        print(f"✓ 已加载 {len(self.index_parser.entries)} 个索引条目")
        print(f"✓ 发现 {len(self.video_parser.rec_files)} 个录像文件")
        return True

    def get_info(self):
        """打印DVR信息"""
        self.index_parser.print_summary()

    def list_recordings(self, channel: int = None, limit: int = 30):
        """列出录像"""
        self.index_parser.print_entries(limit=limit, channel=channel)

    def export_recording(self, entry_index: int, output_path: str) -> bool:
        """导出指定索引条目对应的录像"""
        if entry_index >= len(self.index_parser.entries):
            print(f"错误: 索引 {entry_index} 超出范围")
            return False

        entry = self.index_parser.entries[entry_index]
        print(f"正在导出: 通道{entry.channel} "
              f"{entry.start_datetime.strftime('%Y-%m-%d %H:%M:%S')} - "
              f"{entry.end_datetime.strftime('%H:%M:%S')}")

        # 这里需要更多信息来确定准确的文件和偏移
        # 暂时使用简化方法
        file_index = entry_index // 2  # 粗略估算

        return self.video_parser.export_full_file(file_index, output_path)

    def quick_test(self, output_path: str = "test_output.h265") -> bool:
        """快速测试 - 导出第一个录像文件"""
        print("\n正在导出第一个录像文件进行测试...")
        return self.video_parser.export_full_file(0, output_path)


def main():
    if len(sys.argv) < 2:
        default_path = "/Volumes/NO NAME"
        if os.path.exists(default_path + "/TIndex00.tps"):
            dvr_path = default_path
        else:
            print("天视通 TPS DVR 解析器")
            print("=" * 40)
            print(f"\n用法:")
            print(f"  python {sys.argv[0]} <DVR路径>")
            print(f"  python {sys.argv[0]} <DVR路径> --export <输出文件>")
            print(f"  python {sys.argv[0]} <DVR路径> --json <索引JSON>")
            print(f"\n示例:")
            print(f'  python {sys.argv[0]} "/Volumes/NO NAME"')
            print(f'  python {sys.argv[0]} "/Volumes/NO NAME" --export test.h265')
            sys.exit(1)
    else:
        dvr_path = sys.argv[1]

    # 检查是否只解析索引
    if dvr_path.endswith('.tps'):
        parser = TPSIndexParser(dvr_path)
        print(f"正在解析: {dvr_path}")
        if parser.parse():
            parser.print_summary()
            parser.print_entries(limit=30)

            if '--json' in sys.argv:
                json_idx = sys.argv.index('--json')
                if json_idx + 1 < len(sys.argv):
                    parser.export_to_json(sys.argv[json_idx + 1])
        sys.exit(0)

    # 完整DVR解析
    dvr = SeetongDVR(dvr_path)

    print(f"正在加载DVR: {dvr_path}")
    if not dvr.load():
        print("加载失败")
        sys.exit(1)

    dvr.get_info()
    dvr.list_recordings()

    # 处理命令行选项
    if '--export' in sys.argv:
        idx = sys.argv.index('--export')
        if idx + 1 < len(sys.argv):
            output = sys.argv[idx + 1]
        else:
            output = "output.h265"
        dvr.quick_test(output)

    if '--json' in sys.argv:
        idx = sys.argv.index('--json')
        if idx + 1 < len(sys.argv):
            json_path = sys.argv[idx + 1]
        else:
            json_path = "tps_index.json"
        dvr.index_parser.export_to_json(json_path)


if __name__ == '__main__':
    main()