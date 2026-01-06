"""
数据模型 (仅 WebSocket 会话相关)

TPS 文件相关的数据结构在 seetong_lib.py 中
"""

from dataclasses import dataclass
from aiohttp import web


@dataclass
class StreamSession:
    """视频流会话"""
    channel: int
    timestamp: int
    is_playing: bool
    speed: float
    ws: web.WebSocketResponse
