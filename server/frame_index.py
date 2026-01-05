"""
TRec 文件帧索引解析

帧索引位于 TRec 文件末尾的索引区域，用于快速定位音视频帧
支持缓存以加速重复访问
"""

import struct
from typing import List, Optional

from .config import (
    FRAME_INDEX_MAGIC,
    FRAME_INDEX_SIZE,
    INDEX_REGION_START,
    VALID_CHANNELS,
)
from .models import FrameIndexRecord
from .index_cache import load_index_cache, save_index_cache


# 有效时间戳下限：2020-01-01 00:00:00 UTC
MIN_VALID_TIMESTAMP = 1577836800


def _find_frame_index_start(f, search_start: int, search_size: int) -> Optional[int]:
    """
    在索引区域中搜索帧索引的起始位置

    帧索引记录按时间倒序存储，需要找到第一个有效记录的位置
    """
    magic_bytes = struct.pack('<I', FRAME_INDEX_MAGIC)

    f.seek(search_start)
    data = f.read(search_size)

    # 搜索第一个 magic
    idx = data.find(magic_bytes)
    if idx != -1:
        return search_start + idx

    return None


def parse_frame_index(rec_file_path: str, use_cache: bool = True) -> List[FrameIndexRecord]:
    """
    解析 TRec 文件中的帧索引

    帧索引位于文件末尾的索引区域，按时间倒序存储（最新帧在前）
    需要先搜索找到帧索引的实际起始位置

    帧索引记录结构（44字节）:
    - Magic (4 bytes): 0x4C3D2E1F
    - FrameType (4 bytes): 1=I帧, 3=P帧/音频
    - Channel (4 bytes): 2=Video CH1, 3=Audio, 258=Video CH2
    - FrameSeq (4 bytes): 帧序号
    - FileOffset (4 bytes): 数据区域内偏移
    - FrameSize (4 bytes): 帧数据大小
    - TimestampUs (8 bytes): 设备单调时钟（微秒）
    - UnixTs (4 bytes): Unix时间戳（秒）
    - Reserved (8 bytes)

    Args:
        rec_file_path: TRec 文件路径
        use_cache: 是否使用缓存（默认True）

    Returns:
        按时间正序排列的帧索引记录列表
    """
    # 尝试从缓存加载
    if use_cache:
        cached = load_index_cache(rec_file_path)
        if cached is not None:
            return cached

    records = []

    with open(rec_file_path, 'rb') as f:
        # 在索引区域搜索帧索引起始位置
        # 索引区域约 7MB (0x0F900000 - 0x10000000)
        index_start = _find_frame_index_start(f, INDEX_REGION_START, 0x700000)

        if index_start is None:
            return records

        # 从找到的位置开始解析
        f.seek(index_start)

        while True:
            data = f.read(FRAME_INDEX_SIZE)
            if len(data) < FRAME_INDEX_SIZE:
                break

            magic = struct.unpack('<I', data[0:4])[0]
            if magic != FRAME_INDEX_MAGIC:
                break

            frame_type = struct.unpack('<I', data[4:8])[0]
            channel = struct.unpack('<I', data[8:12])[0]
            frame_seq = struct.unpack('<I', data[12:16])[0]
            file_offset = struct.unpack('<I', data[16:20])[0]
            frame_size = struct.unpack('<I', data[20:24])[0]
            timestamp_us = struct.unpack('<Q', data[24:32])[0]
            unix_ts = struct.unpack('<I', data[32:36])[0]

            # 过滤有效记录（2020年后，通道号合理）
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

    # 按时间正序排列（原始是倒序）
    records.sort(key=lambda x: x.timestamp_us)

    # 保存到缓存
    if use_cache and records:
        save_index_cache(rec_file_path, records)

    return records
