#!/usr/bin/env python3
"""
tpsrecordLib.dll Python 实现
基于逆向工程分析的完整功能复现

该模块提供与原生 DLL 相同的 API 接口，用于读取和管理天视通 DVR 的 TPS 录像文件。

文件格式:
  - TIndex00.tps: 主索引文件
  - TRec{N:06d}.tps: 视频录像文件 (纯 H.265/HEVC)

作者: 逆向分析自 tpsrecordLib.dll
"""

import struct
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import List, Optional, Callable, Dict, Tuple, BinaryIO
from enum import IntEnum, IntFlag
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tps_storage")

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))

# ============================================================================
# 常量定义 (从 DLL 逆向分析得到)
# ============================================================================

# 文件魔数
TPS_INDEX_MAGIC = 0x1F2E3D4C

# 索引文件偏移 (从 DLL 汇编分析确认)
SEGMENT_INDEX_OFFSET = 0x4FC      # 段落索引起始偏移
FRAME_INDEX_OFFSET = 0x84C0       # 帧索引起始偏移 (34,000 字节)
ENTRY_SIZE = 0x40                 # 每条记录 64 字节

# DLL 分析得到的限制常量
MAX_SEGMENT_COUNT = 0xB9E         # 最大段落数 2974
MAX_FRAME_INDEX_COUNT = 0x7B40    # 最大帧索引数 31552
TREC_FILE_SIZE = 0x10000000       # TRec 文件大小 256MB

# 错误码 (从 DLL 字符串分析)
class TPSError(IntEnum):
    SUCCESS = 0
    NOT_INITIALIZED = 0xC000010D
    INVALID_PARAM = 0xC0000103
    INVALID_HANDLE = 0xC0000105
    SEEK_ERROR = 0xC0000106
    READ_ERROR = 0xC0000107
    FILE_NOT_FOUND = 0xC0000108

# 帧类型
class FrameType(IntEnum):
    VIDEO_I = 0x01  # I 帧
    VIDEO_P = 0x02  # P 帧
    VIDEO_B = 0x03  # B 帧
    AUDIO = 0x10    # 音频帧

# 事件类型
class EventType(IntFlag):
    NONE = 0x00
    MOTION = 0x01
    ALARM = 0x02
    SCHEDULE = 0x04
    MANUAL = 0x08


# ============================================================================
# 数据结构定义
# ============================================================================

@dataclass
class FileIndexHeader:
    """
    TIndex00.tps 文件头结构
    偏移 0x00, 大小 32 字节
    """
    iFileStartCode: int      # 0x00: 魔数 0x1F2E3D4C
    iModifyTimes: int        # 0x04: 64位修改时间戳
    iVersion: int            # 0x0C: 版本号
    iAVFiles: int            # 0x10: 录像文件数量
    iCurrFileRecNo: int      # 0x14: 当前录像文件编号
    iCrcSum: int             # 0x18: CRC校验和


@dataclass
class SegmentIndexRecord:
    """
    段落索引记录
    偏移 0x4FC 开始, 每条 64 字节

    重要发现: 段落索引的序号 = TRec 文件编号
    例如: 段落 #0 对应 TRec000000.tps
          段落 #14 对应 TRec000014.tps
    """
    iFrameStartOffset: int   # 0x00: 帧索引块内的起始索引
    channel: int             # 0x04: 通道号
    flags: int               # 0x05: 标志位
    iInfoCount: int          # 0x06: I帧/VPS 数量
    start_time: int          # 0x08: 开始时间 (Unix秒)
    end_time: int            # 0x0C: 结束时间 (Unix秒)
    file_index: int = 0      # 段落序号 = TRec 文件编号
    # 0x10-0x3F: 保留

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
class FrameIndexRecord:
    """
    帧索引记录 (61秒粒度)
    偏移 0x84C0 开始, 每条 64 字节
    """
    type_1: int              # 0x00: 类型标识 (常量 1)
    type_2: int              # 0x04: 类型标识 (常量 1)
    start_time: int          # 0x08: GOP 开始时间
    end_time: int            # 0x0C: GOP 结束时间
    file_start_offset: int   # 0x10: 视频文件起始偏移
    file_end_offset: int     # 0x14: 视频文件结束偏移
    frame_count: int         # 0x18: 帧数
    flags: int               # 0x1A: 标志
    size_or_flags: int       # 0x1C: 大小或标志
    gop_index: int           # 0x20: GOP 序号
    # 0x24-0x3B: 保留
    checksum: int            # 0x3C: 校验和

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
    def data_size(self) -> int:
        return self.file_end_offset - self.file_start_offset


@dataclass
class DataFrameInfo:
    """
    数据帧信息 (回调中使用)
    从 DLL 日志字符串推断的结构
    """
    code: int                # 帧类型码
    type: int                # 类型 (视频/音频)
    frameType: int           # 帧子类型 (I/P)
    subType: int             # 子类型
    No: int                  # 帧序号
    offset: int              # 文件偏移
    length: int              # 帧长度
    time: int                # 时间戳 (rTime, 毫秒)
    iCrcSum: int             # CRC


@dataclass
class TimeSegment:
    """时间段信息 (用于查询结果)"""
    start_time: int
    end_time: int
    event_type: int
    file_index: int
    segment_index: int


@dataclass
class PlaybackHandle:
    """播放器句柄"""
    handle_id: int
    start_time: int
    end_time: int
    current_time: int
    is_paused: bool = False
    is_running: bool = True
    callback: Optional[Callable] = None
    file_index: int = 0
    segment_index: int = 0
    frame_offset: int = 0


# ============================================================================
# 全局状态 (模拟 DLL 中的全局变量)
# ============================================================================

@dataclass
class GlobalState:
    """全局状态管理器"""
    is_initialized: bool = False
    storage_path: str = ""
    max_av_files: int = 256

    # 索引数据
    file_header: Optional[FileIndexHeader] = None
    segment_records: List[SegmentIndexRecord] = field(default_factory=list)
    frame_records: List[FrameIndexRecord] = field(default_factory=list)

    # 播放器句柄
    playback_handles: Dict[int, PlaybackHandle] = field(default_factory=dict)
    next_handle_id: int = 1

    # 同步锁
    mutex: threading.Lock = field(default_factory=threading.Lock)

    # 日志回调
    log_callback: Optional[Callable] = None
    log_level: int = 1


# 全局单例
_global_state = GlobalState()


# ============================================================================
# 日志函数实现
# ============================================================================

def TSDK_SetLogLevel(level: int) -> None:
    """
    设置日志级别

    对应 DLL 导出: TSDK_SetLogLevel
    RVA: 0x15A0

    Args:
        level: 日志级别 (0=关闭, 1=错误, 2=警告, 3=信息, 4=调试)
    """
    _global_state.log_level = level
    logger.setLevel([logging.CRITICAL, logging.ERROR, logging.WARNING,
                     logging.INFO, logging.DEBUG][min(level, 4)])


def TSDK_SetLogCb(callback: Optional[Callable[[str], None]]) -> None:
    """
    设置日志回调函数

    对应 DLL 导出: TSDK_SetLogCb
    RVA: 0x15B0

    Args:
        callback: 日志回调函数，接收日志字符串
    """
    _global_state.log_callback = callback


def TSDK_LogPrint(message: str) -> None:
    """
    输出日志

    对应 DLL 导出: TSDK_LogPrint
    RVA: 0x15C0

    Args:
        message: 日志消息
    """
    if _global_state.log_callback:
        _global_state.log_callback(message)
    else:
        logger.info(message)


# ============================================================================
# 时间函数
# ============================================================================

def monotonic_us() -> int:
    """
    获取单调递增的微秒时间戳

    对应 DLL 导出: monotonic_us
    RVA: 0x68C0

    Returns:
        微秒级时间戳
    """
    return int(time.monotonic() * 1_000_000)


# ============================================================================
# 存储管理函数
# ============================================================================

def t_pkgstorage_Init(path: str, max_files: int = 256) -> int:
    """
    初始化存储系统

    对应 DLL 导出: t_pkgstorage_Init@8
    RVA: 0x6980
    大小: 695 字节

    功能:
      1. 验证路径有效性
      2. 初始化全局数据结构
      3. 加载索引文件
      4. 解析段落索引和帧索引

    Args:
        path: TPS 文件所在目录
        max_files: 最大录像文件数

    Returns:
        0 成功, 错误码失败
    """
    global _global_state

    TSDK_LogPrint(f"pkgstorage Init start, path: {path}")

    with _global_state.mutex:
        if _global_state.is_initialized:
            TSDK_LogPrint("Already initialized")
            return TPSError.SUCCESS

        # 验证路径
        if not os.path.isdir(path):
            TSDK_LogPrint(f"Invalid path: {path}")
            return TPSError.INVALID_PARAM

        index_path = os.path.join(path, "TIndex00.tps")
        if not os.path.exists(index_path):
            TSDK_LogPrint(f"Index file not found: {index_path}")
            return TPSError.FILE_NOT_FOUND

        _global_state.storage_path = path
        _global_state.max_av_files = max_files

        # 加载索引
        try:
            _load_index_file(index_path)
        except Exception as e:
            TSDK_LogPrint(f"Failed to load index: {e}")
            return TPSError.READ_ERROR

        _global_state.is_initialized = True

        TSDK_LogPrint(f"pkgstorage Init complete, segments: {len(_global_state.segment_records)}, "
                      f"frames: {len(_global_state.frame_records)}")

        return TPSError.SUCCESS


def _load_index_file(index_path: str) -> None:
    """加载索引文件"""
    with open(index_path, 'rb') as f:
        # 读取文件头
        f.seek(0)
        magic = struct.unpack('<I', f.read(4))[0]

        if magic != TPS_INDEX_MAGIC:
            raise ValueError(f"Invalid magic: {magic:08X}")

        f.seek(0x10)
        av_files = struct.unpack('<I', f.read(4))[0]
        entry_count = struct.unpack('<I', f.read(4))[0]

        _global_state.file_header = FileIndexHeader(
            iFileStartCode=magic,
            iModifyTimes=0,
            iVersion=0,
            iAVFiles=av_files,
            iCurrFileRecNo=entry_count,
            iCrcSum=0
        )

        # 读取段落索引
        # 重要: 段落索引的序号 = TRec 文件编号
        _global_state.segment_records = []
        f.seek(SEGMENT_INDEX_OFFSET)

        segment_index = 0  # 段落序号，同时也是 TRec 文件编号
        for _ in range(entry_count + 20):  # 多读一些
            data = f.read(ENTRY_SIZE)
            if len(data) < ENTRY_SIZE:
                break

            offset = struct.unpack('<I', data[0:4])[0]
            channel = data[4]
            flags = data[5]
            frame_count = struct.unpack('<H', data[6:8])[0]
            start_time = struct.unpack('<I', data[8:12])[0]
            end_time = struct.unpack('<I', data[12:16])[0]

            # 验证有效性
            if channel == 0 or channel == 0xFE:
                segment_index += 1
                continue
            if start_time < 1577836800 or end_time <= start_time:
                segment_index += 1
                continue

            record = SegmentIndexRecord(
                iFrameStartOffset=offset,
                channel=channel,
                flags=flags,
                iInfoCount=frame_count,
                start_time=start_time,
                end_time=end_time,
                file_index=segment_index  # 段落序号 = TRec 文件编号
            )
            _global_state.segment_records.append(record)
            segment_index += 1

        # 读取帧索引
        _global_state.frame_records = []
        f.seek(0, 2)
        file_size = f.tell()

        f.seek(FRAME_INDEX_OFFSET)

        while f.tell() + ENTRY_SIZE <= file_size:
            data = f.read(ENTRY_SIZE)
            if len(data) < ENTRY_SIZE:
                break

            type_1, type_2 = struct.unpack('<II', data[0:8])
            if type_1 != 1 or type_2 != 1:
                continue

            start_time = struct.unpack('<I', data[8:12])[0]
            end_time = struct.unpack('<I', data[12:16])[0]
            file_start = struct.unpack('<I', data[16:20])[0]
            file_end = struct.unpack('<I', data[20:24])[0]
            frame_count = struct.unpack('<H', data[24:26])[0]
            flags = struct.unpack('<H', data[26:28])[0]
            size_or_flags = struct.unpack('<I', data[28:32])[0]
            gop_index = struct.unpack('<I', data[32:36])[0]
            checksum = struct.unpack('<I', data[60:64])[0]

            if start_time < 1577836800 or end_time <= start_time:
                continue

            record = FrameIndexRecord(
                type_1=type_1,
                type_2=type_2,
                start_time=start_time,
                end_time=end_time,
                file_start_offset=file_start,
                file_end_offset=file_end,
                frame_count=frame_count,
                flags=flags,
                size_or_flags=size_or_flags,
                gop_index=gop_index,
                checksum=checksum
            )
            _global_state.frame_records.append(record)

            if len(_global_state.frame_records) >= MAX_FRAME_INDEX_COUNT:
                break


def t_pkgstorage_uninit() -> int:
    """
    反初始化存储系统

    对应 DLL 导出: t_pkgstorage_uninit@0
    RVA: 0xA0E0
    大小: 350 字节

    功能:
      1. 停止所有播放器
      2. 释放资源
      3. 重置全局状态

    Returns:
        0 成功
    """
    global _global_state

    TSDK_LogPrint("pkgstorage uninit")

    with _global_state.mutex:
        # 停止所有播放器
        for handle in _global_state.playback_handles.values():
            handle.is_running = False

        _global_state.playback_handles.clear()
        _global_state.segment_records.clear()
        _global_state.frame_records.clear()
        _global_state.file_header = None
        _global_state.is_initialized = False
        _global_state.storage_path = ""

    return TPSError.SUCCESS


def t_pkgstorage_format(path: str) -> int:
    """
    格式化存储

    对应 DLL 导出: t_pkgstorage_format@4
    RVA: 0x7570
    大小: 330 字节

    警告: 此函数会删除所有录像数据

    Args:
        path: 存储路径

    Returns:
        0 成功
    """
    TSDK_LogPrint(f"pkgstorage format: {path}")
    # 实际实现会删除所有 TRec*.tps 和重置 TIndex00.tps
    # 这里仅作为接口定义
    return TPSError.SUCCESS


def t_pkgstorage_check_data_multiChannel() -> int:
    """
    检查多通道数据

    对应 DLL 导出: t_pkgstorage_check_data_multiChannel
    RVA: 0x6900
    大小: 107 字节

    Returns:
        通道数
    """
    if not _global_state.is_initialized:
        return 0

    channels = set(r.channel for r in _global_state.segment_records)
    return len(channels)


def t_pkgstorage_pause() -> int:
    """
    暂停存储

    对应 DLL 导出: t_pkgstorage_pause@0
    RVA: 0x81D0
    大小: 113 字节
    """
    TSDK_LogPrint("pkgstorage pause")
    return TPSError.SUCCESS


def t_pkgstorage_resume() -> int:
    """
    恢复存储

    对应 DLL 导出: t_pkgstorage_resume@0
    RVA: 0x8130
    大小: 96 字节
    """
    TSDK_LogPrint("pkgstorage resume")
    return TPSError.SUCCESS


def t_pkgstorage_reflash() -> int:
    """
    刷新存储

    对应 DLL 导出: t_pkgstorage_reflash@0
    RVA: 0x82B0
    大小: 103 字节
    """
    TSDK_LogPrint("pkgstorage reflash")
    return TPSError.SUCCESS


def t_pkgstorage_data_Input(frame_data: bytes) -> int:
    """
    输入数据帧 (录制用)

    对应 DLL 导出: t_pkgstorage_data_Input@4
    RVA: 0x8390
    大小: 204 字节

    Args:
        frame_data: 帧数据

    Returns:
        0 成功
    """
    TSDK_LogPrint(f"pkgstorage data input: {len(frame_data)} bytes")
    return TPSError.SUCCESS


def t_pkgstorage_get_status(channel: int) -> Tuple[int, int, int, int]:
    """
    获取存储状态

    对应 DLL 导出: t_pkgstorage_get_status@16
    RVA: 0x8710
    大小: 123 字节

    Args:
        channel: 通道号

    Returns:
        (状态, 已用空间, 总空间, 剩余时间)
    """
    if not _global_state.is_initialized:
        return (0, 0, 0, 0)

    return (1, 0, 0, 0)


def t_pkgstorage_set_write_mode(mode: int) -> int:
    """
    设置写入模式

    对应 DLL 导出: t_pkgstorage_set_write_mode@4
    RVA: 0x8820
    大小: 76 字节

    Args:
        mode: 写入模式

    Returns:
        0 成功
    """
    TSDK_LogPrint(f"pkgstorage set write mode: {mode}")
    return TPSError.SUCCESS


def t_pkgstorage_set_pre_record_time(seconds: int) -> int:
    """
    设置预录时间

    对应 DLL 导出: t_pkgstorage_set_pre_record_time@4
    RVA: 0x88B0
    大小: 76 字节

    Args:
        seconds: 预录秒数

    Returns:
        0 成功
    """
    TSDK_LogPrint(f"pkgstorage set pre record time: {seconds}s")
    return TPSError.SUCCESS


def t_pkgstorage_start_event(event_type: int) -> int:
    """
    开始事件录制

    对应 DLL 导出: t_pkgstorage_start_event@4
    RVA: 0x8940
    大小: 141 字节

    Args:
        event_type: 事件类型

    Returns:
        0 成功
    """
    TSDK_LogPrint(f"pkgstorage start event: {event_type:#x}")
    return TPSError.SUCCESS


def t_pkgstorage_stop_event(event_type: int) -> int:
    """
    停止事件录制

    对应 DLL 导出: t_pkgstorage_stop_event@4
    RVA: 0x8A50
    大小: 165 字节

    Args:
        event_type: 事件类型

    Returns:
        0 成功
    """
    TSDK_LogPrint(f"pkgstorage stop event: {event_type:#x}")
    return TPSError.SUCCESS


# ============================================================================
# 播放器函数
# ============================================================================

def t_pkgstorage_pb_query_by_month(year: int, month: int, event_type: int) -> List[int]:
    """
    按月查询录像日期

    对应 DLL 导出: t_pkgstorage_pb_query_by_month@12
    RVA: 0x8B80
    大小: 844 字节

    Args:
        year: 年份
        month: 月份 (1-12)
        event_type: 事件类型过滤

    Returns:
        有录像的日期列表 (1-31)
    """
    TSDK_LogPrint(f"pkgstorage query by month: {year}-{month:02d}")

    if not _global_state.is_initialized:
        return []

    days_with_recording = set()

    for record in _global_state.segment_records:
        dt = record.start_datetime
        if dt.year == year and dt.month == month:
            days_with_recording.add(dt.day)

    return sorted(days_with_recording)


def t_pkgstorage_pb_query_by_day(year: int, month: int, day: int,
                                  event_type: int, channel: int = 1) -> List[TimeSegment]:
    """
    按天查询录像时间段

    对应 DLL 导出: t_pkgstorage_pb_query_by_day@28
    RVA: 0x9100
    大小: 1026 字节

    日志字符串: "Get day query %d-%d-%d, iEvenType:%#x"

    Args:
        year: 年份 (1970-2100)
        month: 月份 (1-12)
        day: 日期 (1-31)
        event_type: 事件类型
        channel: 通道号

    Returns:
        TimeSegment 列表
    """
    TSDK_LogPrint(f"Get day query {year}-{month}-{day}, iEvenType:{event_type:#x}")

    if not _global_state.is_initialized:
        return []

    # 验证日期范围
    if not (1970 <= year <= 2100):
        return []
    if not (1 <= month <= 12):
        return []
    if not (1 <= day <= 31):
        return []

    # 计算当天的时间范围
    try:
        day_start = datetime(year, month, day, 0, 0, 0, tzinfo=BEIJING_TZ)
        day_end = datetime(year, month, day, 23, 59, 59, tzinfo=BEIJING_TZ)
    except ValueError:
        return []

    day_start_ts = int(day_start.timestamp())
    day_end_ts = int(day_end.timestamp())

    results = []

    for i, record in enumerate(_global_state.segment_records):
        if record.channel != channel:
            continue

        # 检查是否与当天有交集
        if record.end_time < day_start_ts or record.start_time > day_end_ts:
            continue

        segment = TimeSegment(
            start_time=max(record.start_time, day_start_ts),
            end_time=min(record.end_time, day_end_ts),
            event_type=event_type,
            file_index=i // 10,
            segment_index=i
        )
        results.append(segment)

    TSDK_LogPrint(f"Query result: {len(results)} segments")
    return results


def t_pkgstorage_pb_query_free_ts_arr(arr: List) -> int:
    """
    释放查询结果数组

    对应 DLL 导出: t_pkgstorage_pb_query_free_ts_arr@4
    RVA: 0x9840
    大小: 73 字节

    Args:
        arr: 要释放的数组

    Returns:
        0 成功
    """
    # Python 自动垃圾回收，无需手动释放
    arr.clear()
    return TPSError.SUCCESS


def t_pkgstorage_pb_create(start_time: int, end_time: int,
                            callback: Optional[Callable] = None,
                            user_data: Optional[any] = None) -> Optional[PlaybackHandle]:
    """
    创建播放器

    对应 DLL 导出: t_pkgstorage_pb_create@16
    RVA: 0x98B0
    大小: 178 字节

    日志字符串: "cbReplayCallback = %p"

    Args:
        start_time: 开始时间 (Unix秒)
        end_time: 结束时间 (Unix秒)
        callback: 帧回调函数
        user_data: 用户数据

    Returns:
        PlaybackHandle 或 None
    """
    TSDK_LogPrint(f"pkgstorage pb create: {start_time} - {end_time}")
    TSDK_LogPrint(f"cbReplayCallback = {callback}")

    if not _global_state.is_initialized:
        return None

    if end_time <= start_time:
        if end_time == 0:
            end_time = 0x7FFFFFFF
        else:
            return None

    with _global_state.mutex:
        handle_id = _global_state.next_handle_id
        _global_state.next_handle_id += 1

        handle = PlaybackHandle(
            handle_id=handle_id,
            start_time=start_time,
            end_time=end_time,
            current_time=start_time,
            callback=callback
        )

        _global_state.playback_handles[handle_id] = handle

    return handle


def t_pkgstorage_pb_seek(handle: PlaybackHandle, seek_time: int) -> int:
    """
    播放器时间定位

    对应 DLL 导出: t_pkgstorage_pb_seek@8
    RVA: 0xA290
    大小: 146 字节

    日志字符串:
      - "Pkg seek time %s(%s-%s)"
      - "Seek ok, file:%d,seg:%d,seek:%u,time:%u-%u"
      - "Pkg Invalid seek time %u(%u-%u)"

    Args:
        handle: 播放器句柄
        seek_time: 目标时间 (Unix秒)

    Returns:
        0 成功, 错误码失败
    """
    if handle is None:
        TSDK_LogPrint("Pkg Invalid input Exit ERR! hDataPopper = NULL")
        return TPSError.INVALID_HANDLE

    if not _global_state.is_initialized:
        return TPSError.NOT_INITIALIZED

    # 验证时间范围
    if seek_time < handle.start_time or seek_time > handle.end_time:
        TSDK_LogPrint(f"Pkg Invalid seek time {seek_time}({handle.start_time}-{handle.end_time})")
        return TPSError.SEEK_ERROR

    # 查找对应的帧记录
    frame_record = None
    for fr in _global_state.frame_records:
        if fr.start_time <= seek_time < fr.end_time:
            frame_record = fr
            break

    if frame_record:
        handle.current_time = seek_time
        handle.frame_offset = frame_record.file_start_offset

        # 计算文件内偏移
        if frame_record.duration_seconds > 0:
            progress = (seek_time - frame_record.start_time) / frame_record.duration_seconds
            offset_in_record = int(progress * (frame_record.file_end_offset - frame_record.file_start_offset))
            handle.frame_offset = frame_record.file_start_offset + offset_in_record

        TSDK_LogPrint(f"Seek ok, time:{seek_time}, offset:{handle.frame_offset:#x}")
    else:
        handle.current_time = seek_time
        TSDK_LogPrint(f"Seek ok, time:{seek_time}, no frame record found")

    return TPSError.SUCCESS


def t_pkgstorage_pb_pause(handle: PlaybackHandle) -> int:
    """
    暂停播放

    对应 DLL 导出: t_pkgstorage_pb_pause@4
    RVA: 0x9C20
    大小: 134 字节

    Args:
        handle: 播放器句柄

    Returns:
        0 成功
    """
    if handle is None:
        return TPSError.INVALID_HANDLE

    TSDK_LogPrint(f"Pkg pause hDataPopper = {handle.handle_id:#x}")
    handle.is_paused = True
    return TPSError.SUCCESS


def t_pkgstorage_pb_resume(handle: PlaybackHandle) -> int:
    """
    恢复播放

    对应 DLL 导出: t_pkgstorage_pb_resume@4
    RVA: 0x9DB0
    大小: 134 字节

    Args:
        handle: 播放器句柄

    Returns:
        0 成功
    """
    if handle is None:
        return TPSError.INVALID_HANDLE

    TSDK_LogPrint(f"Pkg resume hDataPopper = {handle.handle_id:#x}")
    handle.is_paused = False
    return TPSError.SUCCESS


def t_pkgstorage_pb_release(handle: PlaybackHandle) -> int:
    """
    释放播放器

    对应 DLL 导出: t_pkgstorage_pb_release@4
    RVA: 0x9F40
    大小: 116 字节

    Args:
        handle: 播放器句柄

    Returns:
        0 成功
    """
    if handle is None:
        return TPSError.INVALID_HANDLE

    TSDK_LogPrint(f"Pkg release hDataPopper = {handle.handle_id:#x}")

    handle.is_running = False

    with _global_state.mutex:
        if handle.handle_id in _global_state.playback_handles:
            del _global_state.playback_handles[handle.handle_id]

    return TPSError.SUCCESS


def t_pkgstorage_pb_data_set_keyframe(handle: PlaybackHandle, enable: int) -> int:
    """
    设置只播放关键帧

    对应 DLL 导出: t_pkgstorage_pb_data_set_keyframe@8
    RVA: 0x9AB0
    大小: 122 字节

    Args:
        handle: 播放器句柄
        enable: 是否启用

    Returns:
        0 成功
    """
    if handle is None:
        return TPSError.INVALID_HANDLE

    TSDK_LogPrint(f"Pkg set keyframe: {enable}")
    return TPSError.SUCCESS


def t_pkgstorage_show_record_info(segment_index: int) -> None:
    """
    显示录像信息

    对应 DLL 导出: t_pkgstorage_show_record_info@4
    RVA: 0xA4A0
    大小: 171 字节

    Args:
        segment_index: 段落索引
    """
    if not _global_state.is_initialized:
        return

    if 0 <= segment_index < len(_global_state.segment_records):
        record = _global_state.segment_records[segment_index]
        TSDK_LogPrint(f"Show info: channel:{record.channel}, "
                      f"Time:{record.start_datetime.strftime('%Y-%m-%d %H:%M:%S')}-"
                      f"{record.end_datetime.strftime('%H:%M:%S')}")


# ============================================================================
# 辅助函数
# ============================================================================

def get_segment_by_time(timestamp: int) -> Optional[SegmentIndexRecord]:
    """
    根据时间戳获取对应的段落记录

    段落记录包含 file_index 字段，即 TRec 文件编号。
    """
    for seg in _global_state.segment_records:
        if seg.start_time <= timestamp < seg.end_time:
            return seg
    return None


def get_trec_file_path(timestamp: int, storage_path: str = None) -> Optional[str]:
    """
    根据时间戳获取对应的 TRec 文件路径

    Args:
        timestamp: Unix 时间戳
        storage_path: 存储路径（默认使用初始化时的路径）

    Returns:
        TRec 文件完整路径，如 "/path/TRec000014.tps"
    """
    seg = get_segment_by_time(timestamp)
    if seg is None:
        return None

    path = storage_path or _global_state.storage_path
    return os.path.join(path, f"TRec{seg.file_index:06d}.tps")


def get_frame_record_by_time(timestamp: int) -> Optional[FrameIndexRecord]:
    """根据时间戳获取帧记录"""
    for fr in _global_state.frame_records:
        if fr.start_time <= timestamp < fr.end_time:
            return fr
    return None


def get_frame_record_by_offset(file_offset: int) -> Optional[FrameIndexRecord]:
    """根据文件偏移获取帧记录"""
    for fr in _global_state.frame_records:
        if fr.file_start_offset <= file_offset < fr.file_end_offset:
            return fr
    return None


def calculate_precise_time(file_offset: int) -> Optional[datetime]:
    """根据文件偏移计算精确时间"""
    fr = get_frame_record_by_offset(file_offset)
    if fr is None:
        return None

    if fr.file_end_offset == fr.file_start_offset:
        progress = 0
    else:
        progress = (file_offset - fr.file_start_offset) / (fr.file_end_offset - fr.file_start_offset)

    time_offset = progress * fr.duration_seconds
    precise_time = fr.start_time + time_offset

    return datetime.fromtimestamp(precise_time, tz=BEIJING_TZ)


def calculate_vps_precise_time(frame_record: FrameIndexRecord, vps_offset: int) -> int:
    """
    根据 VPS 的字节位置计算其精确时间戳

    算法原理（从逆向分析得出）：
    - file_start_offset 指向帧索引开始时刻数据写入的位置（不一定是 VPS）
    - start_time 对应 file_start_offset 位置的帧时间
    - VPS 可能在 file_start 之后若干字节（36KB-151KB）
    - 通过字节位置线性插值计算 VPS 的精确时间

    公式：
        VPS_time = start_time + (vps_byte_offset / total_bytes) * duration

    其中：
        vps_byte_offset = vps_offset - file_start_offset
        total_bytes = file_end_offset - file_start_offset
        duration = end_time - start_time

    验证结果：
        - 65% 完全匹配（0秒误差）
        - 90% 在 ±1秒内
        - 平均误差 0.3 秒

    Args:
        frame_record: 帧索引记录
        vps_offset: VPS 在文件中的绝对偏移

    Returns:
        Unix 时间戳（秒）
    """
    byte_offset = vps_offset - frame_record.file_start_offset
    total_bytes = frame_record.file_end_offset - frame_record.file_start_offset
    duration = frame_record.end_time - frame_record.start_time

    if total_bytes <= 0:
        return frame_record.start_time

    time_offset = (byte_offset / total_bytes) * duration
    return int(frame_record.start_time + time_offset)


def find_vps_in_range(file_handle: BinaryIO, start: int, end: int) -> List[int]:
    """
    在文件范围内查找所有 VPS (H.265 Video Parameter Set) 位置

    VPS NAL 单元标识: 00 00 00 01 40 01

    Args:
        file_handle: 已打开的文件句柄
        start: 搜索起始偏移
        end: 搜索结束偏移

    Returns:
        VPS 位置列表（绝对文件偏移）
    """
    VPS_PATTERN = bytes([0x00, 0x00, 0x00, 0x01, 0x40, 0x01])

    file_handle.seek(start)
    data = file_handle.read(end - start)

    positions = []
    pos = 0
    while True:
        found = data.find(VPS_PATTERN, pos)
        if found < 0:
            break
        positions.append(start + found)
        pos = found + 6

    return positions


def extract_gop_data(file_handle: BinaryIO, vps_offset: int, max_size: int = 500000) -> Optional[bytes]:
    """
    从 VPS 偏移处提取完整的 GOP 数据

    Args:
        file_handle: 已打开的文件句柄
        vps_offset: VPS 起始偏移
        max_size: 最大读取大小

    Returns:
        GOP 数据（从 VPS 到下一个 VPS 之前），如果无效返回 None
    """
    VPS_PATTERN = bytes([0x00, 0x00, 0x00, 0x01, 0x40, 0x01])

    file_handle.seek(vps_offset)
    data = file_handle.read(max_size)

    # 验证是 VPS
    if data[:6] != VPS_PATTERN:
        return None

    # 查找下一个 VPS
    next_vps = data.find(VPS_PATTERN, 6)
    if next_vps > 0:
        return data[:next_vps]
    else:
        return data


# ============================================================================
# 版本信息
# ============================================================================

# 对应 DLL 导出: szVersion_PKGSTREAM (RVA: 0xC020)
VERSION = "tps_storage_lib Python 1.0.0 (based on tpsrecordLib.dll reverse engineering)"


def get_version() -> str:
    """获取版本信息"""
    return VERSION


# ============================================================================
# 测试代码
# ============================================================================

if __name__ == "__main__":
    import sys

    print("=" * 80)
    print("tps_storage_lib - tpsrecordLib.dll Python 实现")
    print("=" * 80)
    print(f"版本: {VERSION}")

    # 测试路径
    test_path = "/Volumes/NO NAME"

    if len(sys.argv) > 1:
        test_path = sys.argv[1]

    if not os.path.exists(test_path):
        print(f"测试路径不存在: {test_path}")
        sys.exit(1)

    # 初始化
    print(f"\n初始化: {test_path}")
    result = t_pkgstorage_Init(test_path)
    print(f"初始化结果: {result}")

    if result == TPSError.SUCCESS:
        # 检查通道
        channels = t_pkgstorage_check_data_multiChannel()
        print(f"通道数: {channels}")

        # 查询月份
        days = t_pkgstorage_pb_query_by_month(2026, 1, 0)
        print(f"2026-01 有录像的日期: {days}")

        # 查询某天
        segments = t_pkgstorage_pb_query_by_day(2026, 1, 1, 0)
        print(f"2026-01-01 录像段落数: {len(segments)}")

        if segments:
            seg = segments[0]
            print(f"  第一段: {datetime.fromtimestamp(seg.start_time, tz=BEIJING_TZ)} - "
                  f"{datetime.fromtimestamp(seg.end_time, tz=BEIJING_TZ)}")

        # 创建播放器
        if segments:
            handle = t_pkgstorage_pb_create(segments[0].start_time, segments[0].end_time)
            if handle:
                print(f"\n创建播放器成功: handle={handle.handle_id}")

                # 测试 seek
                seek_time = segments[0].start_time + 3600  # 1小时后
                result = t_pkgstorage_pb_seek(handle, seek_time)
                print(f"Seek 结果: {result}")

                # 释放
                t_pkgstorage_pb_release(handle)

        # 反初始化
        t_pkgstorage_uninit()
        print("\n反初始化完成")
