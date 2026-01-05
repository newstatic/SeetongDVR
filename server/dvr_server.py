"""
DVR 服务器核心类

处理视频/音频流的读取和发送
"""

import asyncio
import struct
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Set, List
from zoneinfo import ZoneInfo

from aiohttp import web

from .dvr import TPSIndexParser, TPSVideoParser, NalType
from .tps_storage_lib import (
    t_pkgstorage_Init, _global_state,
    scan_vps_with_times, find_nearest_vps
)

from .config import (
    CHANNEL_VIDEO_CH1, CHANNEL_AUDIO,
    FRAME_TYPE_I, AUDIO_SAMPLE_RATE,
)
from .models import FrameIndexRecord
from .frame_index import parse_frame_index


class DVRServer:
    """DVR Web 服务器核心类"""

    def __init__(self, dvr_path: str):
        self.dvr_path = Path(dvr_path)
        self.index_parser: Optional[TPSIndexParser] = None
        self.video_parser: Optional[TPSVideoParser] = None
        self.loaded = False

        # VPS 索引缓存: {file_index: (vps_positions, vps_times)}
        self._vps_cache: Dict[int, tuple] = {}
        self._vps_cache_lock = asyncio.Lock() if asyncio else None

        # 帧索引缓存: {file_index: List[FrameIndexRecord]}
        self._frame_index_cache: Dict[int, List[FrameIndexRecord]] = {}

        # 缓存构建状态
        self._cache_building = False
        self._cache_progress = 0
        self._cache_total = 0
        self._cache_current = 0

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

        # 清空旧缓存
        self._vps_cache.clear()

        return True

    def build_vps_cache(self):
        """构建所有文件的 VPS 索引缓存（同步，启动时调用）"""
        if not self.loaded:
            return

        # 初始化 tps_storage_lib
        if not _global_state.is_initialized:
            init_result = t_pkgstorage_Init(str(self.dvr_path))
            if init_result != 0:
                print(f"[VPS Cache] 警告: 初始化 tps_storage_lib 失败: {init_result}")
                return

        total = len(self.index_parser.entries)
        self._cache_building = True
        self._cache_total = total
        self._cache_current = 0
        self._cache_progress = 0

        print(f"[VPS Cache] 开始构建 VPS 索引缓存，共 {total} 个文件...")

        start_time = time.time()
        cached_count = 0

        for i, entry in enumerate(self.index_parser.entries):
            file_index = entry.entry_index
            if file_index in self._vps_cache:
                self._cache_current = i + 1
                self._cache_progress = int((i + 1) / total * 100)
                continue

            rec_file = self.video_parser.get_rec_file(file_index)
            if not rec_file:
                self._cache_current = i + 1
                self._cache_progress = int((i + 1) / total * 100)
                continue

            # 扫描 VPS
            vps_positions, vps_times = scan_vps_with_times(
                rec_file, entry.start_time, entry.end_time
            )

            # 缓存结果
            self._vps_cache[file_index] = (vps_positions, vps_times)

            # 同时构建帧索引缓存
            records = parse_frame_index(rec_file)
            if records:
                self._frame_index_cache[file_index] = records

            cached_count += 1

            # 更新进度
            self._cache_current = i + 1
            self._cache_progress = int((i + 1) / total * 100)

            # 进度显示
            if (i + 1) % 10 == 0 or i + 1 == total:
                elapsed = time.time() - start_time
                print(f"[VPS Cache] 进度: {i + 1}/{total} ({elapsed:.1f}s)")

        self._cache_building = False
        self._cache_progress = 100

        elapsed = time.time() - start_time
        print(f"[VPS Cache] ✓ 缓存完成: {cached_count} 个文件，耗时 {elapsed:.1f}s")

    def get_cache_status(self) -> dict:
        """获取缓存构建状态"""
        if not self.loaded:
            return {
                "status": "not_loaded",
                "progress": 0,
                "total": 0,
                "current": 0,
                "cached": 0,
            }

        if self._cache_building:
            return {
                "status": "building",
                "progress": self._cache_progress,
                "total": self._cache_total,
                "current": self._cache_current,
                "cached": len(self._vps_cache),
            }

        return {
            "status": "ready",
            "progress": 100,
            "total": len(self.index_parser.entries) if self.index_parser else 0,
            "current": len(self.index_parser.entries) if self.index_parser else 0,
            "cached": len(self._vps_cache),
        }

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

        # 计算日期范围
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
                actual_start = max(entry.start_time, start_ts)
                actual_end = min(entry.end_time, end_ts)

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

        recordings.sort(key=lambda x: x["startTimestamp"])
        return {"recordings": recordings}

    def find_entry_for_time(self, timestamp: int, channel: int) -> Optional[dict]:
        """查找包含指定时间的录像条目"""
        for entry in self.index_parser.entries:
            if entry.channel == channel:
                if entry.start_time <= timestamp <= entry.end_time:
                    return {
                        "entry": entry,
                        "file_index": entry.entry_index
                    }
        return None

    def get_frame_index(self, file_index: int) -> List[FrameIndexRecord]:
        """获取指定文件的帧索引（带缓存）"""
        if file_index in self._frame_index_cache:
            return self._frame_index_cache[file_index]

        rec_file = self.video_parser.get_rec_file(file_index)
        if not rec_file:
            return []

        records = parse_frame_index(rec_file)
        self._frame_index_cache[file_index] = records
        print(f"[FrameIndex] 解析 TRec{file_index:06d}.tps: {len(records)} 条记录")
        return records

    # ==================== NAL 解析工具方法 ====================

    def _parse_nal_units(self, data: bytes) -> list:
        """解析 NAL 单元"""
        results = []
        pos = 0
        start_code_4 = b'\x00\x00\x00\x01'
        start_code_3 = b'\x00\x00\x01'

        while pos < len(data) - 4:
            if data[pos:pos + 4] == start_code_4:
                start = pos
                start_len = 4
            elif data[pos:pos + 3] == start_code_3:
                start = pos
                start_len = 3
            else:
                pos += 1
                continue

            nal_byte_pos = start + start_len
            if nal_byte_pos >= len(data):
                break
            nal_type = (data[nal_byte_pos] >> 1) & 0x3F

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

    def _strip_start_code(self, nal_data: bytes) -> bytes:
        """去掉 NAL 起始码"""
        if len(nal_data) >= 4 and nal_data[:4] == b'\x00\x00\x00\x01':
            return nal_data[4:]
        elif len(nal_data) >= 3 and nal_data[:3] == b'\x00\x00\x01':
            return nal_data[3:]
        return nal_data

    # ==================== 帧发送方法 ====================

    async def _send_frame(self, ws: web.WebSocketResponse, nal_data: bytes,
                          nal_type: int, timestamp_ms: int):
        """发送视频帧

        帧格式:
        Magic (4 bytes): 'H265'
        Timestamp (8 bytes): Unix 时间戳毫秒
        FrameType (1 byte): 0=P帧, 1=I帧, 2=VPS, 3=SPS, 4=PPS
        DataLen (4 bytes): NAL 数据长度
        Data (N bytes): NAL 数据
        """
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
            nal_data = self._strip_start_code(nal_data)
            await self._send_frame(ws, nal_data, nal_type, timestamp_ms)

    async def _send_audio_frame(self, ws: web.WebSocketResponse, audio_data: bytes,
                                 timestamp_ms: int):
        """发送音频帧

        音频帧格式:
        Magic (4 bytes): 'G711'
        Timestamp (8 bytes): Unix 时间戳毫秒
        SampleRate (2 bytes): 采样率 (8000)
        DataLen (4 bytes): 音频数据长度
        Data (N bytes): G.711 μ-law 编码的音频数据
        """
        header = struct.pack(
            '>4sQHI',
            b'G711',
            timestamp_ms,
            AUDIO_SAMPLE_RATE,
            len(audio_data)
        )
        await ws.send_bytes(header + audio_data)

    # ==================== 视频流方法 ====================

    async def stream_video(self, ws: web.WebSocketResponse, channel: int,
                           start_timestamp: int, speed: float = 1.0):
        """流式传输视频数据（仅视频，无音频）"""
        if not self.loaded:
            await ws.send_json({"error": "DVR 未加载"})
            return

        result = self.find_entry_for_time(start_timestamp, channel)
        if not result:
            await ws.send_json({"error": "未找到指定时间的录像"})
            return

        entry = result["entry"]
        file_index = entry.entry_index

        try:
            rec_file = self.video_parser.get_rec_file(file_index)
            if not rec_file:
                await ws.send_json({"error": f"录像文件不存在: TRec{file_index:06d}.tps"})
                return

            print(f"[Stream] entry={entry.entry_index} -> TRec{file_index:06d}.tps")
            print(f"[Stream] entry时间范围: {entry.start_time} - {entry.end_time}")
            print(f"[Stream] 请求时间戳: {start_timestamp}")

            # 使用缓存的 VPS 索引
            if file_index in self._vps_cache:
                vps_positions, vps_times = self._vps_cache[file_index]
                print(f"[Stream] 使用缓存的 VPS 索引")
            else:
                print(f"[Stream] 缓存未命中，正在扫描...")
                if not _global_state.is_initialized:
                    init_result = t_pkgstorage_Init(str(self.dvr_path))
                    if init_result != 0:
                        print(f"[Stream] 警告: 初始化 tps_storage_lib 失败: {init_result}")

                vps_positions, vps_times = scan_vps_with_times(
                    rec_file, entry.start_time, entry.end_time
                )
                self._vps_cache[file_index] = (vps_positions, vps_times)

            if not vps_positions:
                await ws.send_json({"error": "未找到视频数据"})
                return

            entry_duration = entry.end_time - entry.start_time
            actual_video_start_time = vps_times[0] if vps_times else entry.start_time
            actual_video_duration = entry_duration
            time_ratio = 1.0

            print(f"[Stream] VPS总数: {len(vps_positions)}, 索引时长: {entry_duration}秒")
            print(f"[Stream] 实际视频开始: {actual_video_start_time}")

            target_vps_index, min_diff = find_nearest_vps(vps_times, start_timestamp)
            vps_pos = vps_positions[target_vps_index]
            print(f"[Stream] 目标 VPS 索引: {target_vps_index}, 字节偏移: {vps_pos}, 时间差: {min_diff}秒")

            actual_start_time = vps_times[target_vps_index] if target_vps_index < len(vps_times) else entry.start_time

            await ws.send_json({
                "type": "stream_start",
                "channel": channel,
                "startTime": entry.start_time,
                "endTime": entry.end_time,
                "actualVideoStartTime": actual_video_start_time,
                "actualEndTime": entry.end_time,
                "seekVpsIndex": target_vps_index,
                "totalVps": len(vps_positions),
                "actualStartTime": actual_start_time,
                "actualVideoDuration": int(actual_video_duration),
                "timeRatio": time_ratio,
            })

            with open(rec_file, 'rb') as f:
                f.seek(vps_pos)
                data = f.read(10 * 1024 * 1024)

                header_data = data[:512 * 1024]
                header_nals = self._parse_nal_units(header_data)

                header_bytes = bytearray()
                idr_sent = False
                for nal_offset, nal_size, nal_type in header_nals:
                    if nal_type in (NalType.VPS, NalType.SPS, NalType.PPS):
                        header_bytes.extend(header_data[nal_offset:nal_offset + nal_size])
                    elif nal_type in (NalType.IDR_W_RADL, NalType.IDR_N_LP):
                        if header_bytes:
                            await self._send_nal_units(ws, bytes(header_bytes), actual_start_time * 1000)
                            print(f"[Stream] 已发送视频头，大小={len(header_bytes)} 字节")
                        idr_data = header_data[nal_offset:nal_offset + nal_size]
                        idr_data = self._strip_start_code(idr_data)
                        await self._send_frame(ws, idr_data, nal_type, actual_start_time * 1000)
                        print(f"[Stream] 已发送 IDR 帧，大小={len(idr_data)} 字节")
                        idr_sent = True
                        break

                if not idr_sent and header_bytes:
                    await self._send_nal_units(ws, bytes(header_bytes), actual_start_time * 1000)
                    print(f"[Stream] 已发送视频头（无 IDR），大小={len(header_bytes)} 字节")

                skip_offset = 0
                for nal_offset, nal_size, nal_type in header_nals:
                    if nal_type in (NalType.IDR_W_RADL, NalType.IDR_N_LP):
                        skip_offset = nal_offset + nal_size
                        break

                buffer = bytearray(data[skip_offset:])
                frame_interval = 1.0 / 25.0 / speed
                current_time_ms = actual_start_time * 1000 + int(frame_interval * 1000)

                current_file_pos = vps_pos + len(data)
                print(f"[Stream] 跳过头部 {skip_offset} 字节，buffer 大小: {len(buffer)}")

                frame_count = 0
                last_log_time = time.time()

                while True:
                    if ws.closed:
                        break

                    nal_units = self._parse_nal_units(bytes(buffer))
                    if len(nal_units) > 1:
                        for nal_offset, nal_size, nal_type in nal_units[:-1]:
                            nal_data = bytes(buffer[nal_offset:nal_offset + nal_size])
                            nal_data = self._strip_start_code(nal_data)

                            await self._send_frame(ws, nal_data, nal_type, current_time_ms)

                            is_video_frame = nal_type in (
                                NalType.IDR_W_RADL, NalType.IDR_N_LP,
                                NalType.TRAIL_R, NalType.TRAIL_N,
                            )
                            if is_video_frame:
                                frame_count += 1
                                current_time_ms += int(frame_interval * 1000)
                                await asyncio.sleep(frame_interval)

                                now = time.time()
                                if now - last_log_time >= 1.0:
                                    fps = frame_count / (now - last_log_time)
                                    print(f"[Stream] FPS: {fps:.1f}, 当前时间戳: {current_time_ms // 1000}")
                                    frame_count = 0
                                    last_log_time = now

                        last_offset = nal_units[-2][0] + nal_units[-2][1]
                        buffer = buffer[last_offset:]

                    f.seek(current_file_pos)
                    chunk = f.read(64 * 1024)
                    if not chunk:
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

    async def stream_video_with_audio(self, ws: web.WebSocketResponse, channel: int,
                                       start_timestamp: int, speed: float = 1.0):
        """流式传输音视频数据（使用帧索引）"""
        if not self.loaded:
            await ws.send_json({"error": "DVR 未加载"})
            return

        result = self.find_entry_for_time(start_timestamp, channel)
        if not result:
            await ws.send_json({"error": "未找到指定时间的录像"})
            return

        entry = result["entry"]
        file_index = entry.entry_index

        rec_file = self.video_parser.get_rec_file(file_index)
        if not rec_file:
            await ws.send_json({"error": f"录像文件不存在: TRec{file_index:06d}.tps"})
            return

        print(f"[StreamAV] entry={entry.entry_index} -> TRec{file_index:06d}.tps")
        print(f"[StreamAV] entry时间范围: {entry.start_time} - {entry.end_time}")
        print(f"[StreamAV] 请求时间戳: {start_timestamp}")

        try:
            frame_index = self.get_frame_index(file_index)
            if not frame_index:
                # 回退到旧方法
                await self.stream_video(ws, channel, start_timestamp, speed)
                return

            # 分离视频帧和音频帧
            video_frames = [f for f in frame_index if f.channel == CHANNEL_VIDEO_CH1]
            audio_frames = [f for f in frame_index if f.channel == CHANNEL_AUDIO]

            print(f"[StreamAV] 视频帧: {len(video_frames)}, 音频帧: {len(audio_frames)}")

            if not video_frames:
                await ws.send_json({"error": "未找到视频帧"})
                return

            # 查找最接近请求时间的视频帧（找 I 帧）
            start_video_idx = 0
            for i, vf in enumerate(video_frames):
                if vf.unix_ts >= start_timestamp:
                    for j in range(i, -1, -1):
                        if video_frames[j].frame_type == FRAME_TYPE_I:
                            start_video_idx = j
                            break
                    else:
                        start_video_idx = max(0, i - 1)
                    break

            actual_start_time = video_frames[start_video_idx].unix_ts
            print(f"[StreamAV] 从视频帧 #{start_video_idx} 开始，时间: {actual_start_time}")

            await ws.send_json({
                "type": "stream_start",
                "channel": channel,
                "startTime": entry.start_time,
                "endTime": entry.end_time,
                "actualStartTime": actual_start_time,
                "hasAudio": len(audio_frames) > 0,
                "audioFormat": "g711-ulaw",
                "audioSampleRate": AUDIO_SAMPLE_RATE,
            })

            with open(rec_file, 'rb') as f:
                first_iframe = video_frames[start_video_idx]
                f.seek(first_iframe.file_offset)
                header_data = f.read(512 * 1024)

                header_nals = self._parse_nal_units(header_data)
                header_bytes = bytearray()

                for nal_offset, nal_size, nal_type in header_nals:
                    if nal_type in (NalType.VPS, NalType.SPS, NalType.PPS):
                        header_bytes.extend(header_data[nal_offset:nal_offset + nal_size])
                    elif nal_type in (NalType.IDR_W_RADL, NalType.IDR_N_LP):
                        if header_bytes:
                            await self._send_nal_units(ws, bytes(header_bytes), actual_start_time * 1000)
                            print(f"[StreamAV] 已发送视频头，大小={len(header_bytes)} 字节")

                        idr_data = header_data[nal_offset:nal_offset + nal_size]
                        idr_data = self._strip_start_code(idr_data)
                        await self._send_frame(ws, idr_data, nal_type, actual_start_time * 1000)
                        print(f"[StreamAV] 已发送 IDR 帧，大小={len(idr_data)} 字节")
                        break

                # 设置音频帧起始索引
                audio_idx = 0
                for i, af in enumerate(audio_frames):
                    if af.unix_ts >= actual_start_time:
                        audio_idx = i
                        break

                frame_interval = 1.0 / 166.0 / speed
                current_time_ms = actual_start_time * 1000
                frame_count = 0
                last_log_time = time.time()

                video_idx = start_video_idx + 1

                while video_idx < len(video_frames) and not ws.closed:
                    vf = video_frames[video_idx]

                    # 发送音频帧
                    while audio_idx < len(audio_frames):
                        af = audio_frames[audio_idx]
                        if af.unix_ts * 1000 <= current_time_ms:
                            f.seek(af.file_offset)
                            audio_data = f.read(af.frame_size)
                            await self._send_audio_frame(ws, audio_data, af.unix_ts * 1000)
                            audio_idx += 1
                        else:
                            break

                    # 发送视频帧
                    f.seek(vf.file_offset)
                    video_data = f.read(vf.frame_size)

                    nal_units = self._parse_nal_units(video_data)
                    for nal_offset, nal_size, nal_type in nal_units:
                        nal_data = self._strip_start_code(video_data[nal_offset:nal_offset + nal_size])
                        await self._send_frame(ws, nal_data, nal_type, vf.unix_ts * 1000)

                    current_time_ms = vf.unix_ts * 1000
                    frame_count += 1
                    video_idx += 1

                    await asyncio.sleep(frame_interval)

                    now = time.time()
                    if now - last_log_time >= 1.0:
                        fps = frame_count / (now - last_log_time)
                        print(f"[StreamAV] FPS: {fps:.1f}, 视频帧: {video_idx}/{len(video_frames)}, 音频帧: {audio_idx}/{len(audio_frames)}")
                        frame_count = 0
                        last_log_time = now

            await ws.send_json({"type": "stream_end"})

        except Exception as e:
            import traceback
            traceback.print_exc()
            await ws.send_json({"type": "error", "message": str(e)})
