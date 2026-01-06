"""
DVR 服务器核心类

负责 WebSocket/HTTP 接口，所有算法调用 seetong_lib
"""

import asyncio
import struct
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from aiohttp import web

from .seetong_lib import (
    TPSStorage,
    NalType,
    CHANNEL_VIDEO_CH1,
    CHANNEL_AUDIO,
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

    def load(self) -> bool:
        """加载 DVR 数据"""
        self.storage = TPSStorage(str(self.dvr_path))
        if not self.storage.load():
            return False

        print(f"✓ 发现 {len(list(self.dvr_path.glob('TRec*.tps')))} 个录像文件")
        self.loaded = True
        return True

    def build_vps_cache(self):
        """构建帧索引和 VPS 缓存（同步，启动时调用）"""
        if not self.loaded:
            return

        segments = self.storage.segments
        if TEST_MODE:
            file_indices = [segments[0].file_index] if segments else []
            print(f"[Cache] TEST_MODE 启用，只处理第一个文件")
        else:
            file_indices = [seg.file_index for seg in segments]

        print(f"[Cache] 开始构建缓存，共 {len(file_indices)} 个文件...")

        start_time = time.time()

        def progress_callback(current, total, file_index):
            if current % 10 == 0 or current == total:
                elapsed = time.time() - start_time
                print(f"[Cache] 进度: {current}/{total} ({elapsed:.1f}s)")

        cached_count = self.storage.build_cache(file_indices, progress_callback)

        elapsed = time.time() - start_time
        print(f"[Cache] ✓ 缓存完成: {cached_count} 个文件，耗时 {elapsed:.1f}s")

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

        # 使用 storage 的缓存状态
        cache_status = self.storage.get_cache_status()

        if cache_status["building"]:
            return {
                "status": "building",
                "progress": cache_status["progress"],
                "total": cache_status["total_segments"],
                "current": cache_status["cached_segments"],
                "cached": cache_status["cached_segments"],
                "test_mode": TEST_MODE,
            }

        return {
            "status": "ready",
            "progress": 100,
            "total": cache_status["total_segments"],
            "current": cache_status["cached_segments"],
            "cached": cache_status["cached_segments"],
            "test_mode": TEST_MODE,
        }

    def get_recording_dates(self, channel: Optional[int] = None, tz_name: str = "Asia/Shanghai") -> dict:
        """获取有录像的日期列表（只返回已缓存的段落）"""
        if not self.loaded:
            return {"dates": [], "channels": []}

        # 只使用已缓存的段落
        segments = self.storage.get_cached_segments()
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

        # 只返回已缓存段落的通道
        channels = sorted(set(s.channel for s in segments))

        return {
            "dates": sorted(dates_set),
            "channels": channels
        }

    def get_recordings(self, date: str, channel: Optional[int] = None, tz_name: str = "Asia/Shanghai") -> dict:
        """获取指定日期的录像列表（只返回已缓存的段落）"""
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

        # 只使用已缓存的段落
        recordings = []
        for seg in self.storage.get_cached_segments():
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
        """流式传输音视频数据

        所有算法逻辑都在 seetong_lib 中，此方法只负责：
        1. 调用 TPSStorage 的方法获取数据
        2. 通过 WebSocket 发送帧
        """
        if not self.loaded:
            await ws.send_json({"error": "DVR 未加载"})
            return

        # 1. 查找段落 (seetong_lib 算法)
        seg = self.storage.find_segment_by_time(start_timestamp, channel)
        if not seg:
            await ws.send_json({"error": "未找到指定时间的录像"})
            return

        file_index = seg.file_index
        print(f"[StreamAV] file_index={file_index}, 时间范围: {seg.start_time} - {seg.end_time}")
        print(f"[StreamAV] 请求时间戳: {start_timestamp}")

        try:
            # 通道映射：前端 channel 1/2 -> 帧索引 channel 2/258
            frame_channel = CHANNEL_VIDEO_CH1 if channel == 1 else (258 if channel == 2 else channel)

            # 获取音频帧用于精确时间定位
            audio_frames = self.storage.get_audio_frames(file_index)

            # 2. 使用音频帧时间戳找到目标时间对应的字节偏移
            # 这与 export_video.py 的 find_offset_for_timestamp 逻辑一致
            target_offset = 0
            if audio_frames:
                for af in audio_frames:
                    if af.unix_ts >= start_timestamp:
                        target_offset = af.file_offset
                        break
                if target_offset == 0:
                    target_offset = audio_frames[-1].file_offset
            print(f"[StreamAV] 目标时间 {start_timestamp}, 音频帧定位偏移: {target_offset}")

            # 3. 从音频帧定位的偏移搜索视频头 (VPS/SPS/PPS/IDR)
            print(f"[StreamAV] DEBUG: 开始读取视频头, offset={target_offset}")
            header_result = self.storage.read_video_header(file_index, target_offset)
            print(f"[StreamAV] DEBUG: read_video_header 返回: {header_result is not None}")
            if not header_result:
                print(f"[StreamAV] ERROR: 未找到视频头!")
                await ws.send_json({"type": "error", "message": "未找到视频头"})
                return

            vps, sps, pps, idr, stream_start_pos = header_result
            print(f"[StreamAV] DEBUG: VPS={len(vps)}, SPS={len(sps)}, PPS={len(pps)}, IDR={len(idr)}, stream_pos={stream_start_pos}")

            # 使用音频帧时间戳计算精确起始时间（比字节偏移线性插值更准确）
            # 这与 export_video.py 的 find_timestamp_for_offset 逻辑一致
            actual_start_time = 0
            if audio_frames:
                for af in audio_frames:
                    if af.file_offset <= stream_start_pos:
                        actual_start_time = af.unix_ts
                    else:
                        break
            if actual_start_time == 0:
                actual_start_time = start_timestamp  # 回退到请求时间
            print(f"[StreamAV] 精确起始时间: {actual_start_time}, stream_pos: {stream_start_pos}")

            # 找到视频起始位置之后的第一个音频帧索引
            audio_idx = 0
            for i, af in enumerate(audio_frames):
                if af.file_offset >= stream_start_pos:
                    audio_idx = i
                    break

            print(f"[Audio] 总音频帧: {len(audio_frames)}, 起始索引: {audio_idx}")
            if audio_idx < len(audio_frames):
                print(f"[Audio] 起始音频帧: offset={audio_frames[audio_idx].file_offset}, "
                      f"unix_ts={audio_frames[audio_idx].unix_ts}, 视频起始位置={stream_start_pos}")

            print(f"[StreamAV] DEBUG: 准备发送 stream_start JSON")
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
            print(f"[StreamAV] DEBUG: stream_start JSON 已发送")

            # 4. 发送视频头
            print(f"[StreamAV] DEBUG: 开始发送视频头...")
            await self._send_frame(ws, vps, NalType.VPS, actual_start_time * 1000)
            print(f"[StreamAV] DEBUG: VPS 已发送")
            await self._send_frame(ws, sps, NalType.SPS, actual_start_time * 1000)
            print(f"[StreamAV] DEBUG: SPS 已发送")
            await self._send_frame(ws, pps, NalType.PPS, actual_start_time * 1000)
            print(f"[StreamAV] DEBUG: PPS 已发送")
            await self._send_frame(ws, idr, NalType.IDR_W_RADL, actual_start_time * 1000)
            print(f"[StreamAV] DEBUG: IDR 已发送")

            print(f"[StreamAV] 已发送视频头: VPS({len(vps)}), SPS({len(sps)}), PPS({len(pps)}), IDR({len(idr)})")
            print(f"[StreamAV] 从字节流位置 {stream_start_pos} 开始读取后续帧")

            # 5. 创建流读取器 (seetong_lib 算法) - 传入通道以启用精确时间计算
            print(f"[StreamAV] DEBUG: 创建流读取器...")
            stream_reader = self.storage.create_stream_reader(
                file_index, stream_start_pos, actual_start_time * 1000, frame_channel
            )
            print(f"[StreamAV] DEBUG: stream_reader={stream_reader}, use_precise_time={stream_reader.use_precise_time if stream_reader else None}")
            if not stream_reader:
                print(f"[StreamAV] ERROR: 无法创建流读取器!")
                await ws.send_json({"type": "error", "message": "无法创建流读取器"})
                return

            # 设置播放速度
            fps = 25.0 * speed
            stream_reader.set_fps(fps)
            frame_interval = 1.0 / fps
            print(f"[StreamAV] DEBUG: fps={fps}, frame_interval={frame_interval}")

            # 音频同步状态
            audio_start_time_ms = actual_start_time * 1000  # 音频时间戳的基准

            frame_count = 0
            total_frames_sent = 0
            last_log_time = time.time()
            rec_file = self.storage.get_rec_file(file_index)
            print(f"[StreamAV] DEBUG: rec_file={rec_file}")

            try:
                with open(rec_file, 'rb') as audio_f:
                    print(f"[StreamAV] DEBUG: 进入主循环, ws.closed={ws.closed}")
                    loop_count = 0
                    while not ws.closed:
                        loop_count += 1
                        if loop_count <= 3:
                            print(f"[StreamAV] DEBUG: 循环 #{loop_count}")

                        # 6. 使用流读取器读取 NAL 单元 (seetong_lib 算法)
                        nal_count = 0
                        if loop_count <= 3:
                            print(f"[StreamAV] DEBUG: 调用 read_next_nals(), buffer_len={len(stream_reader.buffer)}, stream_pos={stream_reader.stream_pos}")

                        for nal_data, nal_type, timestamp_ms, nal_file_offset in stream_reader.read_next_nals():
                            if nal_count == 0 and loop_count <= 3:
                                print(f"[StreamAV] DEBUG: 第一个 NAL: type={nal_type}, size={len(nal_data)}, offset={nal_file_offset}")

                            if NalType.is_keyframe(nal_type):
                                print(f"[StreamAV] 遇到新的 IDR @ offset={nal_file_offset}")

                            await self._send_frame(ws, nal_data, nal_type, timestamp_ms)

                            # 每个视频帧后检查并发送音频帧
                            # 使用 NAL 的精确文件偏移而非 stream_pos，避免批量发送
                            if NalType.is_video_frame(nal_type):
                                frame_count += 1
                                total_frames_sent += 1

                                # 发送音频帧（基于精确的 NAL file_offset 同步）
                                audio_sent = 0

                                while audio_idx < len(audio_frames):
                                    af = audio_frames[audio_idx]

                                    # 使用 NAL 的精确偏移位置，而非缓冲区末尾位置
                                    if af.file_offset <= nal_file_offset:
                                        audio_f.seek(af.file_offset)
                                        audio_data = audio_f.read(af.frame_size)
                                        # 使用音频帧自己的时间戳（而非视频帧时间戳）
                                        audio_ts_ms = af.unix_ts * 1000
                                        await self._send_audio_frame(ws, audio_data, audio_ts_ms)

                                        # 详细日志（减少输出）
                                        if audio_idx % 200 == 0:
                                            print(f"[Audio] idx={audio_idx}, offset={af.file_offset}, "
                                                  f"nal_offset={nal_file_offset}, audio_ts={audio_ts_ms}ms, video_ts={timestamp_ms}ms")

                                        audio_idx += 1
                                        audio_sent += 1
                                    else:
                                        break

                                # 只在批量发送时记录警告（正常应该是 0-2 个）
                                if audio_sent > 5:
                                    print(f"[Audio WARNING] 批量发送 {audio_sent} 个音频帧 @ 帧#{total_frames_sent}, "
                                          f"nal_offset={nal_file_offset}")

                                await asyncio.sleep(frame_interval)

                            nal_count += 1

                        if loop_count <= 3:
                            print(f"[StreamAV] DEBUG: 本次循环 NAL 数量: {nal_count}")

                        if nal_count == 0:
                            print(f"[StreamAV] 文件结束, 总共发送 {total_frames_sent} 帧")
                            break

                        now = time.time()
                        if now - last_log_time >= 1.0:
                            actual_fps = frame_count / (now - last_log_time)
                            # 计算当前音视频位置差
                            if audio_idx < len(audio_frames):
                                next_audio_offset = audio_frames[audio_idx].file_offset
                                offset_diff = stream_reader.stream_pos - next_audio_offset
                            else:
                                offset_diff = 0
                            print(f"[StreamAV] FPS: {actual_fps:.1f}, 音频帧: {audio_idx}/{len(audio_frames)}, "
                                  f"总帧数: {total_frames_sent}, 视频位置: {stream_reader.stream_pos}, "
                                  f"位置差: {offset_diff}")
                            frame_count = 0
                            last_log_time = now

            finally:
                # 关闭流读取器的文件句柄
                print(f"[StreamAV] DEBUG: finally 块, 关闭流读取器")
                if stream_reader and stream_reader.f:
                    stream_reader.f.close()

            print(f"[StreamAV] DEBUG: 发送 stream_end")
            await ws.send_json({"type": "stream_end"})

        except Exception as e:
            import traceback
            traceback.print_exc()
            await ws.send_json({"type": "error", "message": str(e)})
