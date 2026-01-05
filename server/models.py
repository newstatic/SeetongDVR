"""
数据模型
"""

from dataclasses import dataclass
from typing import NamedTuple
from aiohttp import web


class FrameIndexRecord(NamedTuple):
    """TRec 文件中的帧索引记录

    帧索引位于 TRec 文件末尾的索引区域（偏移 0x0F900000 开始）
    每条记录 44 字节，按时间倒序存储

    使用 NamedTuple 以获得最快的创建速度（比 dataclass 快 3-5 倍）
    """
    frame_type: int      # 1=I帧, 3=P帧/音频
    channel: int         # 2=Video CH1, 3=Audio, 258=Video CH2
    frame_seq: int       # 帧序号
    file_offset: int     # 数据区域内的偏移
    frame_size: int      # 帧数据大小
    timestamp_us: int    # 设备单调时钟（微秒级）
    unix_ts: int         # Unix时间戳（秒）


@dataclass
class StreamSession:
    """视频流会话"""
    channel: int
    timestamp: int
    is_playing: bool
    speed: float
    ws: web.WebSocketResponse
