#!/usr/bin/env python3
"""
精确帧提取器 - 使用字节位置插值算法

基于逆向分析的核心算法：
    VPS_time = start_time + (vps_byte_offset / total_bytes) * duration

验证精度：
    - 65% 完全匹配（0秒误差）
    - 90% 在 ±1秒内
    - 平均误差 0.3 秒
"""

import os
import sys
import subprocess
import tempfile
from datetime import datetime
from typing import Optional, Tuple, List
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tps_storage_lib import (
    t_pkgstorage_Init, t_pkgstorage_uninit, TPSError,
    _global_state, BEIJING_TZ, FrameIndexRecord,
    get_segment_by_time, get_frame_record_by_time,
    calculate_vps_precise_time, find_vps_in_range, extract_gop_data
)


@dataclass
class ExtractedFrame:
    """提取的帧信息"""
    timestamp: int          # 精确时间戳（Unix秒）
    datetime_str: str       # 格式化时间字符串
    trec_file: str          # TRec 文件名
    vps_offset: int         # VPS 偏移
    frame_index: int        # 帧索引序号
    output_path: str        # 输出路径
    success: bool           # 是否成功
    error: str = ""         # 错误信息


class PreciseFrameExtractor:
    """精确帧提取器"""

    def __init__(self, storage_path: str):
        """
        初始化提取器

        Args:
            storage_path: TPS 存储路径（包含 TIndex00.tps 和 TRec*.tps）
        """
        self.storage_path = storage_path
        self.file_handles = {}  # TRec 文件句柄缓存
        self._initialized = False

    def __enter__(self):
        self.init()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def init(self) -> bool:
        """初始化存储系统"""
        result = t_pkgstorage_Init(self.storage_path)
        if result == TPSError.SUCCESS:
            self._initialized = True
            return True
        return False

    def cleanup(self):
        """清理资源"""
        # 关闭所有文件句柄
        for f in self.file_handles.values():
            f.close()
        self.file_handles.clear()

        if self._initialized:
            t_pkgstorage_uninit()
            self._initialized = False

    def _get_file_handle(self, trec_file: str):
        """获取或打开 TRec 文件句柄"""
        if trec_file not in self.file_handles:
            trec_path = os.path.join(self.storage_path, trec_file)
            if os.path.exists(trec_path):
                self.file_handles[trec_file] = open(trec_path, 'rb')
            else:
                return None
        return self.file_handles[trec_file]

    def _decode_to_jpeg(self, hevc_data: bytes, output_path: str) -> bool:
        """使用 ffmpeg 解码 HEVC 到 JPEG"""
        with tempfile.NamedTemporaryFile(suffix='.h265', delete=False) as tmp:
            tmp.write(hevc_data)
            tmp_path = tmp.name

        try:
            cmd = [
                'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                '-i', tmp_path,
                '-frames:v', '1',
                '-q:v', '2',
                output_path
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            return result.returncode == 0 and os.path.exists(output_path)
        except Exception:
            return False
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def extract_frame_by_index(self, frame_idx: int, output_path: str) -> ExtractedFrame:
        """
        根据帧索引提取帧

        Args:
            frame_idx: 帧索引序号
            output_path: 输出 JPEG 路径

        Returns:
            ExtractedFrame 对象
        """
        if not self._initialized:
            return ExtractedFrame(
                timestamp=0, datetime_str="", trec_file="",
                vps_offset=0, frame_index=frame_idx, output_path=output_path,
                success=False, error="Not initialized"
            )

        if frame_idx < 0 or frame_idx >= len(_global_state.frame_records):
            return ExtractedFrame(
                timestamp=0, datetime_str="", trec_file="",
                vps_offset=0, frame_index=frame_idx, output_path=output_path,
                success=False, error=f"Invalid frame index: {frame_idx}"
            )

        fr = _global_state.frame_records[frame_idx]

        # 找对应的段落和 TRec 文件
        seg = get_segment_by_time(fr.start_time)
        if seg is None:
            return ExtractedFrame(
                timestamp=0, datetime_str="", trec_file="",
                vps_offset=0, frame_index=frame_idx, output_path=output_path,
                success=False, error="No segment found"
            )

        trec_file = f"TRec{seg.file_index:06d}.tps"
        f = self._get_file_handle(trec_file)
        if f is None:
            return ExtractedFrame(
                timestamp=0, datetime_str="", trec_file=trec_file,
                vps_offset=0, frame_index=frame_idx, output_path=output_path,
                success=False, error=f"TRec file not found: {trec_file}"
            )

        # 查找第一个 VPS
        vps_list = find_vps_in_range(f, fr.file_start_offset, fr.file_end_offset)
        if not vps_list:
            return ExtractedFrame(
                timestamp=0, datetime_str="", trec_file=trec_file,
                vps_offset=0, frame_index=frame_idx, output_path=output_path,
                success=False, error="No VPS found"
            )

        vps_offset = vps_list[0]

        # 计算精确时间
        precise_time = calculate_vps_precise_time(fr, vps_offset)
        dt = datetime.fromtimestamp(precise_time, tz=BEIJING_TZ)
        datetime_str = dt.strftime('%Y-%m-%d %H:%M:%S')

        # 提取 GOP 数据
        gop_data = extract_gop_data(f, vps_offset)
        if gop_data is None:
            return ExtractedFrame(
                timestamp=precise_time, datetime_str=datetime_str,
                trec_file=trec_file, vps_offset=vps_offset,
                frame_index=frame_idx, output_path=output_path,
                success=False, error="Failed to extract GOP"
            )

        # 解码
        if self._decode_to_jpeg(gop_data, output_path):
            return ExtractedFrame(
                timestamp=precise_time, datetime_str=datetime_str,
                trec_file=trec_file, vps_offset=vps_offset,
                frame_index=frame_idx, output_path=output_path,
                success=True
            )
        else:
            return ExtractedFrame(
                timestamp=precise_time, datetime_str=datetime_str,
                trec_file=trec_file, vps_offset=vps_offset,
                frame_index=frame_idx, output_path=output_path,
                success=False, error="Failed to decode HEVC"
            )

    def extract_frame_by_time(self, target_time: int, output_path: str) -> ExtractedFrame:
        """
        根据时间戳提取帧

        Args:
            target_time: 目标 Unix 时间戳
            output_path: 输出 JPEG 路径

        Returns:
            ExtractedFrame 对象
        """
        if not self._initialized:
            return ExtractedFrame(
                timestamp=target_time, datetime_str="", trec_file="",
                vps_offset=0, frame_index=-1, output_path=output_path,
                success=False, error="Not initialized"
            )

        # 找对应的帧索引
        fr = get_frame_record_by_time(target_time)
        if fr is None:
            return ExtractedFrame(
                timestamp=target_time, datetime_str="", trec_file="",
                vps_offset=0, frame_index=-1, output_path=output_path,
                success=False, error=f"No frame record for time {target_time}"
            )

        # 找帧索引序号
        frame_idx = -1
        for i, record in enumerate(_global_state.frame_records):
            if record.start_time == fr.start_time:
                frame_idx = i
                break

        return self.extract_frame_by_index(frame_idx, output_path)

    def get_frame_count(self) -> int:
        """获取帧索引总数"""
        if not self._initialized:
            return 0
        return len(_global_state.frame_records)

    def get_time_range(self) -> Tuple[int, int]:
        """获取录像时间范围"""
        if not self._initialized or not _global_state.frame_records:
            return (0, 0)

        start = min(fr.start_time for fr in _global_state.frame_records)
        end = max(fr.end_time for fr in _global_state.frame_records)
        return (start, end)


def extract_batch(storage_path: str, frame_indices: List[int], output_dir: str) -> List[ExtractedFrame]:
    """
    批量提取帧

    Args:
        storage_path: TPS 存储路径
        frame_indices: 帧索引列表
        output_dir: 输出目录

    Returns:
        ExtractedFrame 列表
    """
    os.makedirs(output_dir, exist_ok=True)
    results = []

    with PreciseFrameExtractor(storage_path) as extractor:
        for i, frame_idx in enumerate(frame_indices):
            output_path = os.path.join(output_dir, f"frame_{i+1:04d}.jpg")
            result = extractor.extract_frame_by_index(frame_idx, output_path)
            results.append(result)

            status = "OK" if result.success else f"FAIL: {result.error}"
            print(f"[{i+1}/{len(frame_indices)}] Frame #{frame_idx} -> {result.datetime_str} {status}")

    return results


if __name__ == "__main__":
    import random

    STORAGE_PATH = "/Volumes/NO NAME"
    OUTPUT_DIR = "/Users/ttttt/PycharmProjects/SeetongDVR/precise_extract_test"

    print("=" * 80)
    print("精确帧提取器测试")
    print("=" * 80)

    with PreciseFrameExtractor(STORAGE_PATH) as extractor:
        total = extractor.get_frame_count()
        start_ts, end_ts = extractor.get_time_range()

        print(f"帧索引数: {total}")
        print(f"时间范围: {datetime.fromtimestamp(start_ts, tz=BEIJING_TZ)} ~ "
              f"{datetime.fromtimestamp(end_ts, tz=BEIJING_TZ)}")

        # 随机选择 10 个帧
        random.seed(42)
        selected = random.sample(range(total), min(10, total))
        selected.sort()

        os.makedirs(OUTPUT_DIR, exist_ok=True)

        print(f"\n提取 {len(selected)} 个样本:")
        for i, frame_idx in enumerate(selected):
            output_path = os.path.join(OUTPUT_DIR, f"frame_{i+1:03d}.jpg")
            result = extractor.extract_frame_by_index(frame_idx, output_path)

            status = "OK" if result.success else f"FAIL: {result.error}"
            print(f"  [{i+1}] #{frame_idx} {result.trec_file} -> {result.datetime_str} {status}")

    print(f"\n输出目录: {OUTPUT_DIR}")
