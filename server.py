#!/usr/bin/env python3
"""
天视通 DVR Web 服务器
提供 REST API 和 WebSocket 视频流服务
"""

import asyncio
import json
import struct
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Set
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from aiohttp import web, WSMsgType
import aiohttp_cors

from dvr import TPSIndexParser, TPSVideoParser, NalType, BEIJING_TZ
# 精确时间算法说明 (详见 PRD 附录 B):
# VPS_time = start_time + (vps_byte_offset / total_bytes) * duration
# 基于字节位置线性插值计算每个关键帧的精确时间

# 配置
DEFAULT_DVR_PATH = "/Volumes/NO NAME"
DEFAULT_TIMEZONE = "Asia/Shanghai"
HOST = "0.0.0.0"
PORT = 8100

# 全局时区设置
current_timezone = DEFAULT_TIMEZONE

# 用户可调的时间偏移（秒）
# 由于 DVR 系统时间与视频水印时间可能存在偏差，用户可以手动调整
# 正值 = 显示时间向前（更晚），负值 = 显示时间向后（更早）
time_offset_seconds = 0


@dataclass
class StreamSession:
    """视频流会话"""
    channel: int
    timestamp: int
    is_playing: bool
    speed: float
    ws: web.WebSocketResponse


class DVRServer:
    """DVR Web 服务器"""

    def __init__(self, dvr_path: str):
        self.dvr_path = Path(dvr_path)
        self.index_parser: Optional[TPSIndexParser] = None
        self.video_parser: Optional[TPSVideoParser] = None
        self.sessions: Dict[str, StreamSession] = {}
        self.loaded = False

    def load(self) -> bool:
        """加载 DVR 数据"""
        index_file = self.dvr_path / "TIndex00.tps"
        if not index_file.exists():
            print(f"索引文件不存在: {index_file}")
            return False

        self.index_parser = TPSIndexParser(str(index_file))
        if not self.index_parser.parse():
            print("索引解析失败")
            return False

        self.video_parser = TPSVideoParser(str(self.dvr_path))

        print(f"✓ 已加载 {len(self.index_parser.entries)} 个索引条目")
        print(f"✓ 发现 {len(self.video_parser.rec_files)} 个录像文件")
        self.loaded = True
        return True

    def get_recording_dates(self, channel: Optional[int] = None, tz_name: str = "Asia/Shanghai") -> dict:
        """获取有录像的日期列表"""
        if not self.loaded:
            return {"dates": [], "channels": []}

        entries = self.index_parser.entries
        if channel is not None:
            entries = [e for e in entries if e.channel == channel]

        # 使用指定时区
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("Asia/Shanghai")

        # 提取日期（使用指定时区）
        dates_set: Set[str] = set()
        for entry in entries:
            # 使用指定时区转换时间
            dt = datetime.fromtimestamp(entry.start_time, tz=tz)
            date_str = dt.strftime("%Y-%m-%d")
            dates_set.add(date_str)
            # 也添加结束时间的日期（处理跨午夜的情况）
            dt_end = datetime.fromtimestamp(entry.end_time, tz=tz)
            dates_set.add(dt_end.strftime("%Y-%m-%d"))

        # 获取所有通道
        channels = sorted(set(e.channel for e in self.index_parser.entries))

        return {
            "dates": sorted(dates_set),
            "channels": channels
        }

    def get_recordings(self, date: str, channel: Optional[int] = None, tz_name: str = "Asia/Shanghai") -> dict:
        """获取指定日期的录像列表"""
        if not self.loaded:
            return {"recordings": []}

        # 使用指定时区
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("Asia/Shanghai")

        # 解析日期
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=tz)
        except ValueError:
            return {"recordings": [], "error": "无效日期格式"}

        # 计算日期范围（使用指定时区的0点到24点）
        day_start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        start_ts = int(day_start.timestamp())
        end_ts = int(day_end.timestamp())

        # 筛选录像
        recordings = []
        for entry in self.index_parser.entries:
            if channel is not None and entry.channel != channel:
                continue

            # 检查时间范围是否有交集
            if entry.start_time < end_ts and entry.end_time > start_ts:
                # 计算在当天范围内的实际开始和结束时间
                actual_start = max(entry.start_time, start_ts)
                actual_end = min(entry.end_time, end_ts)

                # 转换为当天的时间显示（使用指定时区）
                start_dt = datetime.fromtimestamp(actual_start, tz=tz)
                end_dt = datetime.fromtimestamp(actual_end, tz=tz)

                recordings.append({
                    "id": entry.entry_index,
                    "channel": entry.channel,
                    "start": start_dt.strftime("%H:%M:%S"),
                    "end": end_dt.strftime("%H:%M:%S"),
                    "startTimestamp": actual_start,
                    "endTimestamp": actual_end,
                    "duration": actual_end - actual_start,
                    "frameCount": entry.frame_count
                })

        # 按开始时间排序
        recordings.sort(key=lambda x: x["startTimestamp"])

        return {"recordings": recordings}

    def find_entry_for_time(self, timestamp: int, channel: int) -> Optional[dict]:
        """查找包含指定时间的录像条目"""
        for entry in self.index_parser.entries:
            if entry.channel == channel:
                if entry.start_time <= timestamp <= entry.end_time:
                    return {
                        "entry": entry,
                        "file_index": self._get_file_index_for_entry(entry)
                    }
        return None

    def _get_file_index_for_entry(self, entry) -> int:
        """根据索引条目获取对应的录像文件索引"""
        # 简化逻辑：根据 file_offset 确定文件
        # TRec 文件每个 256MB
        file_size = 256 * 1024 * 1024
        return entry.file_offset // file_size

    async def stream_video(self, ws: web.WebSocketResponse, channel: int,
                           start_timestamp: int, speed: float = 1.0):
        """流式传输视频数据"""
        if not self.loaded:
            await ws.send_json({"error": "DVR 未加载"})
            return

        # 查找起始位置
        result = self.find_entry_for_time(start_timestamp, channel)
        if not result:
            await ws.send_json({"error": "未找到指定时间的录像"})
            return

        entry = result["entry"]

        # 正确的映射: entry_index 对应 TRec 文件编号
        # 每个索引条目对应一个 TRec 文件
        try:
            file_index = entry.entry_index
            rec_file = self.video_parser.get_rec_file(file_index)
            if not rec_file:
                await ws.send_json({"error": f"录像文件不存在: TRec{file_index:06d}.tps"})
                return

            print(f"[Stream] entry={entry.entry_index} -> TRec{file_index:06d}.tps")
            print(f"[Stream] entry时间范围: {entry.start_time} - {entry.end_time}")
            print(f"[Stream] 请求时间戳: {start_timestamp}")

            # 扫描文件中所有 VPS 位置
            vps_pattern = b'\x00\x00\x00\x01\x40'
            vps_positions = []

            with open(rec_file, 'rb') as f:
                # 分块扫描整个文件，找到所有 VPS 位置
                chunk_size = 64 * 1024 * 1024  # 64MB
                offset = 0
                overlap = len(vps_pattern) - 1
                prev_tail = b''

                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break

                    # 拼接上一块的尾部以处理跨块的模式
                    search_data = prev_tail + chunk
                    search_offset = offset - len(prev_tail)

                    # 查找所有 VPS
                    pos = 0
                    while True:
                        pos = search_data.find(vps_pattern, pos)
                        if pos == -1:
                            break
                        actual_pos = search_offset + pos
                        vps_positions.append(actual_pos)
                        pos += len(vps_pattern)

                    # 保存尾部用于下一轮
                    prev_tail = chunk[-overlap:] if len(chunk) >= overlap else chunk
                    offset += len(chunk)

            if not vps_positions:
                await ws.send_json({"error": "未找到视频数据"})
                return

            print(f"[Stream] 文件中共有 {len(vps_positions)} 个 VPS")

            # 使用精确时间算法
            # 核心公式: VPS_time = start_time + (vps_byte_offset / total_bytes) * duration
            # 基于字节位置线性插值
            entry_duration = entry.end_time - entry.start_time

            # 获取文件大小作为总字节数
            with open(rec_file, 'rb') as size_check:
                size_check.seek(0, 2)  # Seek to end
                file_size = size_check.tell()

            # 计算每个 VPS 的精确时间戳
            # 使用字节位置线性插值
            vps_times = []
            for vps_pos in vps_positions:
                # 使用字节位置占总文件大小的比例来插值时间
                progress = vps_pos / file_size if file_size > 0 else 0
                vps_time = int(entry.start_time + progress * entry_duration)
                vps_times.append(vps_time)

            # 应用用户时间偏移
            vps_times = [t + time_offset_seconds for t in vps_times]

            actual_video_start_time = vps_times[0] if vps_times else entry.start_time
            actual_video_duration = entry_duration
            time_ratio = 1.0

            print(f"[Stream] VPS总数: {len(vps_positions)}, 索引时长: {entry_duration}秒")
            print(f"[Stream] 用户偏移: {time_offset_seconds}秒")
            print(f"[Stream] 实际视频开始: {actual_video_start_time} ({datetime.fromtimestamp(actual_video_start_time, tz=ZoneInfo('Asia/Shanghai')).strftime('%H:%M:%S')})")

            # 查找最接近请求时间的 VPS
            target_vps_index = 0
            min_diff = abs(vps_times[0] - start_timestamp) if vps_times else float('inf')

            for i, vps_time in enumerate(vps_times):
                diff = abs(vps_time - start_timestamp)
                if diff < min_diff:
                    min_diff = diff
                    target_vps_index = i

            vps_pos = vps_positions[target_vps_index]
            print(f"[Stream] 目标 VPS 索引: {target_vps_index}, 字节偏移: {vps_pos}, 时间差: {min_diff}秒")

            # 使用精确计算的时间戳
            actual_start_time = vps_times[target_vps_index] if target_vps_index < len(vps_times) else entry.start_time

            print(f"[Stream] 请求时间戳: {start_timestamp} ({datetime.fromtimestamp(start_timestamp, tz=ZoneInfo('Asia/Shanghai')).strftime('%H:%M:%S')})")
            print(f"[Stream] 校正后时间戳: {actual_start_time} ({datetime.fromtimestamp(actual_start_time, tz=ZoneInfo('Asia/Shanghai')).strftime('%H:%M:%S')})")

            # 计算实际的结束时间
            actual_end_time = entry.end_time  # end_time 本身是准确的

            await ws.send_json({
                "type": "stream_start",
                "channel": channel,
                "startTime": entry.start_time,  # 索引开始时间（用于 timeline 定位）
                "endTime": entry.end_time,  # 索引结束时间
                "actualVideoStartTime": actual_video_start_time,  # 实际视频开始时间（校正后）
                "actualEndTime": actual_end_time,  # 实际视频结束时间
                "seekVpsIndex": target_vps_index,
                "totalVps": len(vps_positions),
                "actualStartTime": actual_start_time,  # 当前播放位置的校正时间戳
                "actualVideoDuration": int(actual_video_duration),
                "timeRatio": time_ratio,  # 时间映射比率
            })

            with open(rec_file, 'rb') as f:
                # 定位到目标 VPS 位置读取视频头
                f.seek(vps_pos)
                data = f.read(10 * 1024 * 1024)  # 读取 10MB 用于提取头部

                # 从 VPS 位置提取视频头 (VPS + SPS + PPS) 和第一个 IDR 帧
                # IDR 帧可能很大（100KB+），需要足够的空间
                header_data = data[:512 * 1024]  # 512KB 应该足够包含完整的 IDR 帧
                header_nals = self._parse_nal_units(header_data)

                header_bytes = bytearray()
                idr_sent = False
                for nal_offset, nal_size, nal_type in header_nals:
                    if nal_type in (NalType.VPS, NalType.SPS, NalType.PPS):
                        header_bytes.extend(header_data[nal_offset:nal_offset + nal_size])
                    elif nal_type in (NalType.IDR_W_RADL, NalType.IDR_N_LP):
                        # 找到 IDR 帧，先发送参数集，再发送 IDR
                        if header_bytes:
                            await self._send_nal_units(ws, bytes(header_bytes), actual_start_time * 1000)
                            print(f"[Stream] 已发送视频头，大小={len(header_bytes)} 字节")
                        # 发送 IDR 帧
                        idr_data = header_data[nal_offset:nal_offset + nal_size]
                        idr_data = self._strip_start_code(idr_data)
                        await self._send_frame(ws, idr_data, nal_type, actual_start_time * 1000)
                        print(f"[Stream] 已发送 IDR 帧，大小={len(idr_data)} 字节")
                        idr_sent = True
                        break

                if not idr_sent and header_bytes:
                    await self._send_nal_units(ws, bytes(header_bytes), actual_start_time * 1000)
                    print(f"[Stream] 已发送视频头（无 IDR），大小={len(header_bytes)} 字节")

                # 已经在 vps_pos 位置读取了 data，继续从 data 流式传输
                # 跳过已发送的头部 NAL 单元，从 IDR 之后开始
                # 找到第一个 IDR 帧之后的位置
                skip_offset = 0
                for nal_offset, nal_size, nal_type in header_nals:
                    if nal_type in (NalType.IDR_W_RADL, NalType.IDR_N_LP):
                        skip_offset = nal_offset + nal_size
                        break

                buffer = bytearray(data[skip_offset:])  # 从 IDR 之后开始
                frame_interval = 1.0 / 25.0 / speed  # 25fps
                # 使用实际 VPS 位置对应的时间戳，而不是用户请求的时间戳
                # IDR 帧已发送，所以时间戳需要加上一帧的时间
                current_time_ms = actual_start_time * 1000 + int(frame_interval * 1000)

                # 记录当前文件位置
                current_file_pos = vps_pos + len(data)
                print(f"[Stream] 跳过头部 {skip_offset} 字节，buffer 大小: {len(buffer)}")

                # 帧计数器（调试用）
                frame_count = 0
                last_log_time = time.time()

                while True:
                    # 检查连接状态
                    if ws.closed:
                        break

                    # 解析并发送 NAL 单元
                    nal_units = self._parse_nal_units(bytes(buffer))
                    if len(nal_units) > 1:
                        # 保留最后一个可能不完整的 NAL
                        for nal_offset, nal_size, nal_type in nal_units[:-1]:
                            nal_data = bytes(buffer[nal_offset:nal_offset + nal_size])
                            # 去掉起始码
                            nal_data = self._strip_start_code(nal_data)

                            # 发送帧
                            await self._send_frame(ws, nal_data, nal_type, current_time_ms)

                            # 只对实际视频帧（IDR/TRAIL）更新时间戳并等待
                            # VPS/SPS/PPS 是参数集，不是视频帧
                            # SEI 等其他类型也不是视频帧
                            is_video_frame = nal_type in (
                                NalType.IDR_W_RADL, NalType.IDR_N_LP,  # IDR 帧
                                NalType.TRAIL_R, NalType.TRAIL_N,  # P/B 帧
                            )
                            if is_video_frame:
                                frame_count += 1
                                current_time_ms += int(frame_interval * 1000)
                                await asyncio.sleep(frame_interval)

                                # 每秒打印一次统计信息
                                now = time.time()
                                if now - last_log_time >= 1.0:
                                    fps = frame_count / (now - last_log_time)
                                    print(f"[Stream] FPS: {fps:.1f}, 当前时间戳: {current_time_ms // 1000}")
                                    frame_count = 0
                                    last_log_time = now

                        # 移除已处理的数据
                        last_offset = nal_units[-2][0] + nal_units[-2][1]
                        buffer = buffer[last_offset:]

                    # 读取更多数据
                    f.seek(current_file_pos)
                    chunk = f.read(64 * 1024)  # 64KB
                    if not chunk:
                        # 文件读完，尝试下一个文件
                        file_index += 1
                        next_rec_file = self.video_parser.get_rec_file(file_index)
                        if not next_rec_file:
                            await ws.send_json({"type": "stream_end"})
                            break
                        rec_file = next_rec_file
                        current_file_pos = 0
                        f.close()
                        f = open(rec_file, 'rb')
                        continue

                    buffer.extend(chunk)
                    current_file_pos += len(chunk)

        except Exception as e:
            await ws.send_json({"type": "error", "message": str(e)})

    def _parse_nal_units(self, data: bytes) -> list:
        """解析 NAL 单元"""
        results = []
        pos = 0
        start_code_4 = b'\x00\x00\x00\x01'
        start_code_3 = b'\x00\x00\x01'

        while pos < len(data) - 4:
            # 查找起始码
            if data[pos:pos + 4] == start_code_4:
                start = pos
                start_len = 4
            elif data[pos:pos + 3] == start_code_3:
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
            next_pos = pos + start_len
            while next_pos < len(data) - 4:
                if data[next_pos:next_pos + 4] == start_code_4:
                    break
                if data[next_pos:next_pos + 3] == start_code_3:
                    break
                next_pos += 1
            else:
                next_pos = len(data)

            size = next_pos - start
            results.append((start, size, nal_type))
            pos = next_pos

        return results

    async def _send_frame(self, ws: web.WebSocketResponse, nal_data: bytes,
                          nal_type: int, timestamp_ms: int):
        """发送视频帧"""
        # 帧格式:
        # Magic (4 bytes): 'H265'
        # Timestamp (8 bytes): Unix 时间戳毫秒
        # FrameType (1 byte): 0=P帧, 1=I帧, 2=VPS, 3=SPS, 4=PPS
        # DataLen (4 bytes): NAL 数据长度
        # Data (N bytes): NAL 数据

        # 确定帧类型
        if nal_type == NalType.VPS:
            frame_type = 2
        elif nal_type == NalType.SPS:
            frame_type = 3
        elif nal_type == NalType.PPS:
            frame_type = 4
        elif nal_type in (NalType.IDR_W_RADL, NalType.IDR_N_LP):
            frame_type = 1
        else:
            frame_type = 0

        # 构建帧头
        header = struct.pack(
            '>4sQBI',
            b'H265',
            timestamp_ms,
            frame_type,
            len(nal_data)
        )

        await ws.send_bytes(header + nal_data)

    async def _send_nal_units(self, ws: web.WebSocketResponse, data: bytes,
                              timestamp_ms: int):
        """发送多个 NAL 单元"""
        nal_units = self._parse_nal_units(data)
        for offset, size, nal_type in nal_units:
            nal_data = data[offset:offset + size]
            # 去掉起始码，只发送纯 NAL 数据
            nal_data = self._strip_start_code(nal_data)
            await self._send_frame(ws, nal_data, nal_type, timestamp_ms)

    def _strip_start_code(self, nal_data: bytes) -> bytes:
        """去掉 NAL 起始码"""
        if len(nal_data) >= 4 and nal_data[:4] == b'\x00\x00\x00\x01':
            return nal_data[4:]
        elif len(nal_data) >= 3 and nal_data[:3] == b'\x00\x00\x01':
            return nal_data[3:]
        return nal_data

    def _build_vps_index(self) -> list:
        """构建 TRec000000.tps 的 VPS 位置索引表

        扫描第一个录像文件，记录所有 VPS NAL 单元的字节偏移位置。
        VPS (Video Parameter Set, NAL type 32) 标记每个 GOP 的开始。
        """
        rec_file = self.video_parser.get_rec_file(0)
        if not rec_file:
            print("[VPS Index] 错误: 找不到 TRec000000.tps")
            return []

        print(f"[VPS Index] 正在扫描 {rec_file}...")
        vps_positions = []

        # VPS 的起始码 + NAL type: 00 00 00 01 40 (type 32 = 0x40 >> 1)
        vps_pattern = b'\x00\x00\x00\x01\x40'

        with open(rec_file, 'rb') as f:
            # 分块读取以节省内存
            chunk_size = 64 * 1024 * 1024  # 64MB
            offset = 0
            overlap = len(vps_pattern) - 1
            prev_tail = b''

            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break

                # 拼接上一块的尾部以处理跨块的模式
                search_data = prev_tail + chunk
                search_offset = offset - len(prev_tail)

                # 查找所有 VPS
                pos = 0
                while True:
                    pos = search_data.find(vps_pattern, pos)
                    if pos == -1:
                        break
                    actual_pos = search_offset + pos
                    vps_positions.append(actual_pos)
                    pos += len(vps_pattern)

                # 保存尾部用于下一轮
                prev_tail = chunk[-overlap:] if len(chunk) >= overlap else chunk
                offset += len(chunk)

        print(f"[VPS Index] 构建完成，共 {len(vps_positions)} 个 VPS")
        if len(vps_positions) > 0:
            print(f"[VPS Index] 第一个 VPS 位于 {vps_positions[0]}, 最后一个位于 {vps_positions[-1]}")

        return vps_positions


# 全局服务器实例
dvr_server: Optional[DVRServer] = None


# REST API 处理器
async def handle_get_dates(request: web.Request) -> web.Response:
    """GET /api/v1/recordings/dates"""
    channel = request.query.get('channel')
    channel_int = int(channel) if channel else None

    result = dvr_server.get_recording_dates(channel_int, current_timezone)
    return web.json_response(result)


async def handle_get_recordings(request: web.Request) -> web.Response:
    """GET /api/v1/recordings"""
    date = request.query.get('date')
    channel = request.query.get('channel')

    if not date:
        return web.json_response({"error": "缺少 date 参数"}, status=400)

    channel_int = int(channel) if channel else None
    result = dvr_server.get_recordings(date, channel_int, current_timezone)
    return web.json_response(result)


async def handle_websocket(request: web.Request) -> web.WebSocketResponse:
    """WebSocket /api/v1/stream"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    session_id = str(id(ws))
    stream_task: Optional[asyncio.Task] = None

    print(f"[WS] 新连接: {session_id}")

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    action = data.get('action')

                    if action == 'play':
                        channel = data.get('channel', 1)
                        timestamp = data.get('timestamp', int(time.time()))
                        speed = data.get('speed', 1.0)

                        # 取消之前的流任务
                        if stream_task and not stream_task.done():
                            stream_task.cancel()

                        # 启动新的流任务
                        stream_task = asyncio.create_task(
                            dvr_server.stream_video(ws, channel, timestamp, speed)
                        )
                        print(f"[WS] 开始播放: ch={channel}, ts={timestamp}, speed={speed}")

                    elif action == 'pause':
                        if stream_task and not stream_task.done():
                            stream_task.cancel()
                            stream_task = None
                        print(f"[WS] 暂停")

                    elif action == 'seek':
                        timestamp = data.get('timestamp')
                        channel = data.get('channel', 1)
                        speed = data.get('speed', 1.0)

                        if stream_task and not stream_task.done():
                            stream_task.cancel()

                        stream_task = asyncio.create_task(
                            dvr_server.stream_video(ws, channel, timestamp, speed)
                        )
                        print(f"[WS] Seek: ts={timestamp}")

                    elif action == 'speed':
                        # 速度变更需要重新启动流
                        speed = data.get('rate', 1.0)
                        print(f"[WS] 速度变更: {speed}x")

                except json.JSONDecodeError:
                    await ws.send_json({"error": "无效的 JSON"})

            elif msg.type == WSMsgType.ERROR:
                print(f"[WS] 错误: {ws.exception()}")

    except asyncio.CancelledError:
        pass
    finally:
        if stream_task and not stream_task.done():
            stream_task.cancel()
        print(f"[WS] 断开连接: {session_id}")

    return ws


async def handle_index(request: web.Request) -> web.Response:
    """提供前端静态文件"""
    return web.FileResponse('./web/dist/index.html')


async def handle_get_config(request: web.Request) -> web.Response:
    """GET /api/v1/config - 获取当前配置"""
    global current_timezone, time_offset_seconds
    result = {
        "storagePath": str(dvr_server.dvr_path) if dvr_server else "",
        "loaded": dvr_server.loaded if dvr_server else False,
        "timezone": current_timezone,
        "timeOffset": time_offset_seconds,  # 用户可调的时间偏移（秒）
    }
    if dvr_server and dvr_server.loaded:
        result["entryCount"] = len(dvr_server.index_parser.entries)
        result["fileCount"] = len(dvr_server.video_parser.rec_files)
    return web.json_response(result)


async def handle_set_config(request: web.Request) -> web.Response:
    """POST /api/v1/config - 设置配置"""
    global dvr_server, current_timezone, time_offset_seconds

    try:
        data = await request.json()
        new_path = data.get('storagePath')
        new_timezone = data.get('timezone')
        new_time_offset = data.get('timeOffset')

        result = {
            "timezone": current_timezone,
            "timeOffset": time_offset_seconds,
        }

        # 更新时间偏移
        if new_time_offset is not None:
            try:
                time_offset_seconds = int(new_time_offset)
                result["timeOffset"] = time_offset_seconds
                print(f"[Config] 时间偏移已更改为: {time_offset_seconds}秒")
            except (ValueError, TypeError):
                return web.json_response({"error": f"无效的时间偏移: {new_time_offset}"}, status=400)

        # 更新时区
        if new_timezone:
            try:
                # 验证时区是否有效
                ZoneInfo(new_timezone)
                current_timezone = new_timezone
                result["timezone"] = current_timezone
                print(f"[Config] 时区已更改为: {current_timezone}")
            except Exception as e:
                return web.json_response({"error": f"无效的时区: {new_timezone}"}, status=400)

        # 更新存储路径
        if new_path:
            new_server = DVRServer(new_path)
            if new_server.load():
                dvr_server = new_server
                result.update({
                    "storagePath": str(dvr_server.dvr_path),
                    "loaded": True,
                    "entryCount": len(dvr_server.index_parser.entries),
                    "fileCount": len(dvr_server.video_parser.rec_files),
                })
            else:
                return web.json_response({
                    "storagePath": new_path,
                    "loaded": False,
                    "error": "无法加载指定路径的 DVR 数据"
                }, status=400)
        else:
            # 只更新时区，返回当前存储路径状态
            result.update({
                "storagePath": str(dvr_server.dvr_path) if dvr_server else "",
                "loaded": dvr_server.loaded if dvr_server else False,
            })
            if dvr_server and dvr_server.loaded:
                result["entryCount"] = len(dvr_server.index_parser.entries)
                result["fileCount"] = len(dvr_server.video_parser.rec_files)

        return web.json_response(result)

    except json.JSONDecodeError:
        return web.json_response({"error": "无效的 JSON"}, status=400)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


def create_app(dvr_path: str) -> web.Application:
    """创建应用"""
    global dvr_server
    dvr_server = DVRServer(dvr_path)

    app = web.Application()

    # 设置 CORS
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods="*"
        )
    })

    # API 路由
    api_routes = [
        web.get('/api/v1/config', handle_get_config),
        web.post('/api/v1/config', handle_set_config),
        web.get('/api/v1/recordings/dates', handle_get_dates),
        web.get('/api/v1/recordings', handle_get_recordings),
        web.get('/api/v1/stream', handle_websocket),
    ]

    for route in api_routes:
        cors.add(app.router.add_route(route.method, route.path, route.handler))

    # 静态文件
    dist_path = Path('./web/dist')
    if dist_path.exists():
        app.router.add_get('/', handle_index)
        app.router.add_static('/assets', dist_path / 'assets')

    return app


async def on_startup(app: web.Application):
    """启动时加载 DVR"""
    if dvr_server:
        dvr_server.load()


def main():
    import argparse

    parser = argparse.ArgumentParser(description='天视通 DVR Web 服务器')
    parser.add_argument('--dvr-path', default=DEFAULT_DVR_PATH,
                        help=f'DVR 存储路径 (默认: {DEFAULT_DVR_PATH})')
    parser.add_argument('--host', default=HOST, help=f'监听地址 (默认: {HOST})')
    parser.add_argument('--port', type=int, default=PORT, help=f'监听端口 (默认: {PORT})')

    args = parser.parse_args()

    print("=" * 60)
    print("天视通 DVR Web 服务器")
    print("=" * 60)
    print(f"DVR 路径: {args.dvr_path}")
    print(f"监听地址: http://{args.host}:{args.port}")
    print("=" * 60)

    app = create_app(args.dvr_path)
    app.on_startup.append(on_startup)

    web.run_app(app, host=args.host, port=args.port)


if __name__ == '__main__':
    main()
