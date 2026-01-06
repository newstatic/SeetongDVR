"""
DVR 服务器核心类

处理视频/音频流的读取和发送
所有 TPS 文件解析算法都在 tps_storage_lib 中
"""

import asyncio
import struct
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List
from zoneinfo import ZoneInfo

from aiohttp import web

from .tps_storage_lib import (
    TPSStorage,
    NalType,
    FrameIndexRecord,
    parse_nal_units,
    strip_start_code,
    find_vps_sps_pps_idr,
    CHANNEL_VIDEO_CH1,
    CHANNEL_AUDIO,
    FRAME_TYPE_I,
)

from .config import (
    AUDIO_SAMPLE_RATE,
    TEST_MODE,
)


class DVRServer:
    """DVR Web 服务器核心类"""

    def __init__(self, dvr_path: str):
        self.dvr_path = Path(dvr_path)
        self.storage: Optional[TPSStorage] = None
        self.loaded = False

        # 缓存构建状态
        self._cache_building = False
        self._cache_progress = 0
        self._cache_total = 0
        self._cache_current = 0

    def load(self) -> bool:
        """加载 DVR 数据"""
        self.storage = TPSStorage(str(self.dvr_path))
        if not self.storage.load():
            return False

        print(f"✓ 发现 {len(list(self.dvr_path.glob('TRec*.tps')))} 个录像文件")
        self.loaded = True
        return True

    def build_vps_cache(self):
        """构建帧索引缓存（同步，启动时调用）"""
        if not self.loaded:
            return

        segments = self.storage.segments
        if TEST_MODE:
            segments = segments[:1]
            print(f"[VPS Cache] TEST_MODE 启用，只处理第一个文件")

        total = len(segments)
        self._cache_building = True
        self._cache_total = total
        self._cache_current = 0
        self._cache_progress = 0

        print(f"[VPS Cache] 开始构建帧索引缓存，共 {total} 个文件...")

        start_time = time.time()
        cached_count = 0

        for i, seg in enumerate(segments):
            file_index = seg.file_index
            # 预加载帧索引（会自动缓存）
            records = self.storage.get_frame_index(file_index)
            if records:
                cached_count += 1

            self._cache_current = i + 1
            self._cache_progress = int((i + 1) / total * 100)

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
                "test_mode": TEST_MODE,
            }

        if self._cache_building:
            return {
                "status": "building",
                "progress": self._cache_progress,
                "total": self._cache_total,
                "current": self._cache_current,
                "cached": len(self.storage._frame_index_cache),
                "test_mode": TEST_MODE,
            }

        total_entries = len(self.storage.segments)
        processed = 1 if TEST_MODE else total_entries

        return {
            "status": "ready",
            "progress": 100,
            "total": total_entries,
            "current": processed,
            "cached": len(self.storage._frame_index_cache),
            "test_mode": TEST_MODE,
        }

    def get_recording_dates(self, channel: Optional[int] = None, tz_name: str = "Asia/Shanghai") -> dict:
        """获取有录像的日期列表"""
        if not self.loaded:
            return {"dates": [], "channels": []}

        segments = self.storage.segments
        if channel is not None:
            segments = [s for s in segments if s.channel == channel]

        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("Asia/Shanghai")

        dates_set = set()
        for seg in segments:
            dt = datetime.fromtimestamp(seg.start_time, tz=tz)
            dates_set.add(dt.strftime("%Y-%m-%d"))
            dt_end = datetime.fromtimestamp(seg.end_time, tz=tz)
            dates_set.add(dt_end.strftime("%Y-%m-%d"))

        channels = sorted(set(s.channel for s in self.storage.segments))

        return {
            "dates": sorted(dates_set),
            "channels": channels
        }

    def get_recordings(self, date: str, channel: Optional[int] = None, tz_name: str = "Asia/Shanghai") -> dict:
        """获取指定日期的录像列表"""
        if not self.loaded:
            return {"recordings": []}

        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("Asia/Shanghai")

        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=tz)
        except ValueError:
            return {"recordings": [], "error": "无效日期格式"}

        day_start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        start_ts = int(day_start.timestamp())
        end_ts = int(day_end.timestamp())

        recordings = []
        for seg in self.storage.segments:
            if channel is not None and seg.channel != channel:
                continue

            if seg.start_time < end_ts and seg.end_time > start_ts:
                actual_start = max(seg.start_time, start_ts)
                actual_end = min(seg.end_time, end_ts)

                start_dt = datetime.fromtimestamp(actual_start, tz=tz)
                end_dt = datetime.fromtimestamp(actual_end, tz=tz)

                recordings.append({
                    "id": seg.file_index,
                    "channel": seg.channel,
                    "start": start_dt.strftime("%H:%M:%S"),
                    "end": end_dt.strftime("%H:%M:%S"),
                    "startTimestamp": actual_start,
                    "endTimestamp": actual_end,
                    "duration": actual_end - actual_start,
                    "frameCount": seg.frame_count
                })

        recordings.sort(key=lambda x: x["startTimestamp"])
        return {"recordings": recordings}

    def find_entry_for_time(self, timestamp: int, channel: int) -> Optional[dict]:
        """查找包含指定时间的录像条目"""
        seg = self.storage.find_segment_by_time(timestamp, channel)
        if seg:
            return {
                "entry": seg,
                "file_index": seg.file_index
            }
        return None

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
        elif NalType.is_keyframe(nal_type):
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

    async def stream_video_with_audio(self, ws: web.WebSocketResponse, channel: int,
                                       start_timestamp: int, speed: float = 1.0):
        """流式传输音视频数据"""
        if not self.loaded:
            await ws.send_json({"error": "DVR 未加载"})
            return

        result = self.find_entry_for_time(start_timestamp, channel)
        if not result:
            await ws.send_json({"error": "未找到指定时间的录像"})
            return

        seg = result["entry"]
        file_index = seg.file_index

        rec_file = self.storage.get_rec_file(file_index)
        if not rec_file:
            await ws.send_json({"error": f"录像文件不存在: TRec{file_index:06d}.tps"})
            return

        print(f"[StreamAV] file_index={file_index} -> {rec_file.name}")
        print(f"[StreamAV] 时间范围: {seg.start_time} - {seg.end_time}")
        print(f"[StreamAV] 请求时间戳: {start_timestamp}")

        try:
            frame_index = self.storage.get_frame_index(file_index)
            if not frame_index:
                await ws.send_json({"error": "帧索引为空"})
                return

            # 分离视频帧和音频帧
            video_frames = [f for f in frame_index if f.channel == CHANNEL_VIDEO_CH1]
            audio_frames = [f for f in frame_index if f.channel == CHANNEL_AUDIO]

            print(f"[StreamAV] 视频帧: {len(video_frames)}, 音频帧: {len(audio_frames)}")

            if not video_frames:
                await ws.send_json({"error": "未找到视频帧"})
                return

            # 查找最接近请求时间的 I 帧
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

            first_iframe = video_frames[start_video_idx]
            actual_start_time = first_iframe.unix_ts
            print(f"[StreamAV] 从视频帧 #{start_video_idx} 开始，时间: {actual_start_time}")

            await ws.send_json({
                "type": "stream_start",
                "channel": channel,
                "startTime": seg.start_time,
                "endTime": seg.end_time,
                "actualStartTime": actual_start_time,
                "hasAudio": len(audio_frames) > 0,
                "audioFormat": "g711-ulaw",
                "audioSampleRate": AUDIO_SAMPLE_RATE,
            })

            with open(rec_file, 'rb') as f:
                # 读取 512KB 数据，查找 VPS/SPS/PPS/IDR
                f.seek(first_iframe.file_offset)
                header_data = f.read(512 * 1024)

                result = find_vps_sps_pps_idr(header_data)
                if not result:
                    print(f"[StreamAV] 警告: 未找到 VPS/SPS/PPS/IDR!")
                    await ws.send_json({"type": "error", "message": "未找到视频头"})
                    return

                vps, sps, pps, idr, idr_end_offset = result

                # 发送视频头
                await self._send_frame(ws, vps, NalType.VPS, actual_start_time * 1000)
                await self._send_frame(ws, sps, NalType.SPS, actual_start_time * 1000)
                await self._send_frame(ws, pps, NalType.PPS, actual_start_time * 1000)
                await self._send_frame(ws, idr, NalType.IDR_W_RADL, actual_start_time * 1000)

                print(f"[StreamAV] 已发送视频头: VPS({len(vps)}), SPS({len(sps)}), PPS({len(pps)}), IDR({len(idr)})")

                # 计算后续帧的起始位置
                stream_pos = first_iframe.file_offset + idr_end_offset
                print(f"[StreamAV] 从字节流位置 {stream_pos} 开始读取后续帧")

                # 设置音频帧起始索引
                audio_idx = 0
                for i, af in enumerate(audio_frames):
                    if af.unix_ts >= actual_start_time:
                        audio_idx = i
                        break

                frame_interval = 1.0 / 25.0 / speed
                current_time_ms = actual_start_time * 1000
                frame_count = 0
                last_log_time = time.time()

                # 使用字节流方式读取
                buffer = bytearray()
                CHUNK_SIZE = 64 * 1024

                while not ws.closed:
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

                    # 读取更多数据到缓冲区
                    if len(buffer) < 256 * 1024:
                        f.seek(stream_pos)
                        chunk = f.read(CHUNK_SIZE)
                        if not chunk:
                            print(f"[StreamAV] 文件结束")
                            break
                        buffer.extend(chunk)
                        stream_pos += len(chunk)

                    # 解析缓冲区中的 NAL 单元
                    nal_units = parse_nal_units(bytes(buffer))
                    if len(nal_units) <= 1:
                        continue

                    # 发送除最后一个之外的所有 NAL
                    for nal_offset, nal_size, nal_type in nal_units[:-1]:
                        nal_data = strip_start_code(bytes(buffer[nal_offset:nal_offset + nal_size]))

                        if NalType.is_keyframe(nal_type):
                            print(f"[StreamAV] 遇到新的 IDR")

                        await self._send_frame(ws, nal_data, nal_type, current_time_ms)

                        if NalType.is_video_frame(nal_type):
                            frame_count += 1
                            current_time_ms += int(frame_interval * 1000)
                            await asyncio.sleep(frame_interval)

                    # 移除已处理的数据
                    last_nal_end = nal_units[-2][0] + nal_units[-2][1]
                    buffer = buffer[last_nal_end:]

                    now = time.time()
                    if now - last_log_time >= 1.0:
                        fps = frame_count / (now - last_log_time)
                        print(f"[StreamAV] FPS: {fps:.1f}, 音频帧: {audio_idx}/{len(audio_frames)}")
                        frame_count = 0
                        last_log_time = now

            await ws.send_json({"type": "stream_end"})

        except Exception as e:
            import traceback
            traceback.print_exc()
            await ws.send_json({"type": "error", "message": str(e)})
