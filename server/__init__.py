"""
天视通 DVR Web 服务器模块
"""

from .config import DEFAULT_DVR_PATH, DEFAULT_TIMEZONE, HOST, PORT
from .models import FrameIndexRecord, StreamSession
from .frame_index import parse_frame_index
from .index_cache import clear_cache, get_cache_info
from .dvr_server import DVRServer
from .handlers import (
    handle_get_dates,
    handle_get_recordings,
    handle_get_config,
    handle_set_config,
    handle_get_cache_status,
    handle_websocket,
)
from .app import create_app, main

__all__ = [
    'DEFAULT_DVR_PATH',
    'DEFAULT_TIMEZONE',
    'HOST',
    'PORT',
    'FrameIndexRecord',
    'StreamSession',
    'parse_frame_index',
    'clear_cache',
    'get_cache_info',
    'DVRServer',
    'handle_get_dates',
    'handle_get_recordings',
    'handle_get_config',
    'handle_set_config',
    'handle_get_cache_status',
    'handle_websocket',
    'create_app',
    'main',
]
