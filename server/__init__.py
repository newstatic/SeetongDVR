"""
天视通 DVR Web 服务器模块
"""

from .config import DEFAULT_DVR_PATH, DEFAULT_TIMEZONE, HOST, PORT
from .models import StreamSession
from .seetong_lib import (
    TPSStorage,
    CachedSegmentInfo,
    FrameIndexRecord,
    SegmentRecord,
    NalType,
    parse_nal_units,
    strip_start_code,
    parse_trec_frame_index,
    create_storage,
)
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
    'StreamSession',
    'TPSStorage',
    'CachedSegmentInfo',
    'FrameIndexRecord',
    'SegmentRecord',
    'NalType',
    'parse_nal_units',
    'strip_start_code',
    'parse_trec_frame_index',
    'create_storage',
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
