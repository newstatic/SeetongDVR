#!/usr/bin/env python3
"""
导出指定时间范围的音频原始数据

支持:
- 标准 WAV 文件
- BWF (Broadcast Wave Format) 元数据，包含录制时间戳
- SRT 字幕文件，包含每秒的实际时间戳
"""

import struct
import wave
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from server.seetong_lib import (
    TPSStorage,
    CHANNEL_AUDIO,
    TREC_INDEX_REGION_START,
)

# 目标时间: 2025-12-18 14:42:55 北京时间
TARGET_TIME_STR = "2025-12-18 14:42:55"
RANGE_SECONDS = 5 * 60  # 前后5分钟

# DVR 路径
DVR_PATH = "/Volumes/NO NAME"

# 输出目录
OUTPUT_DIR = Path("/Users/ttttt/PycharmProjects/SeetongDVR-python/audio_export")


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    # 解析目标时间
    tz = ZoneInfo("Asia/Shanghai")
    target_dt = datetime.strptime(TARGET_TIME_STR, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
    target_ts = int(target_dt.timestamp())

    start_ts = target_ts - RANGE_SECONDS
    end_ts = target_ts + RANGE_SECONDS

    print(f"目标时间: {TARGET_TIME_STR} ({target_ts})")
    print(f"导出范围: {datetime.fromtimestamp(start_ts, tz)} - {datetime.fromtimestamp(end_ts, tz)}")
    print(f"时间戳范围: {start_ts} - {end_ts}")
    print()

    # 加载存储
    storage = TPSStorage(DVR_PATH)
    if not storage.load():
        print("加载失败!")
        return

    print(f"已加载 {len(storage.segments)} 个段落")

    # 查找包含目标时间的段落
    target_seg = None
    for seg in storage.segments:
        if seg.start_time <= target_ts <= seg.end_time:
            target_seg = seg
            break

    if not target_seg:
        print(f"未找到包含目标时间的段落!")
        print("可用段落时间范围:")
        for seg in storage.segments[:5]:
            print(f"  {seg.file_index}: {datetime.fromtimestamp(seg.start_time, tz)} - {datetime.fromtimestamp(seg.end_time, tz)}")
        return

    print(f"找到段落: file_index={target_seg.file_index}")
    print(f"段落时间: {datetime.fromtimestamp(target_seg.start_time, tz)} - {datetime.fromtimestamp(target_seg.end_time, tz)}")
    print()

    # 构建缓存
    print("构建缓存...")
    storage.build_cache([target_seg.file_index])

    # 获取音频帧
    audio_frames = storage.get_audio_frames(target_seg.file_index)
    print(f"总音频帧数: {len(audio_frames)}")

    if not audio_frames:
        print("没有音频帧!")
        return

    # 过滤时间范围内的音频帧
    # 注意：音频帧的 unix_ts 精度只到秒，需要用 file_offset 做更精确的过滤
    filtered_frames = []
    for af in audio_frames:
        if start_ts <= af.unix_ts <= end_ts:
            filtered_frames.append(af)

    print(f"时间范围内的音频帧: {len(filtered_frames)}")

    if not filtered_frames:
        print("时间范围内没有音频帧!")
        # 打印一些音频帧信息
        print("前10个音频帧:")
        for af in audio_frames[:10]:
            print(f"  unix_ts={af.unix_ts} ({datetime.fromtimestamp(af.unix_ts, tz)}), offset={af.file_offset}, size={af.frame_size}")
        return

    # 读取音频数据
    rec_file = storage.get_rec_file(target_seg.file_index)
    print(f"录像文件: {rec_file}")

    raw_audio_data = bytearray()
    frame_sizes = []

    with open(rec_file, 'rb') as f:
        for af in filtered_frames:
            f.seek(af.file_offset)
            data = f.read(af.frame_size)
            raw_audio_data.extend(data)
            frame_sizes.append(af.frame_size)

    print(f"读取了 {len(filtered_frames)} 帧, 总大小: {len(raw_audio_data)} 字节")
    print(f"帧大小统计: min={min(frame_sizes)}, max={max(frame_sizes)}, avg={sum(frame_sizes)/len(frame_sizes):.1f}")

    # 保存原始 G.711 数据
    raw_file = OUTPUT_DIR / f"audio_{TARGET_TIME_STR.replace(':', '-').replace(' ', '_')}_raw.g711"
    with open(raw_file, 'wb') as f:
        f.write(raw_audio_data)
    print(f"已保存原始 G.711 数据: {raw_file}")

    # 解码为 PCM 并保存为 WAV
    def decode_ulaw(ulaw_byte):
        """解码单个 μ-law 字节"""
        # μ-law 编码是取反的
        ulaw_byte = ~ulaw_byte & 0xFF
        sign = 1 if (ulaw_byte & 0x80) else -1
        exponent = (ulaw_byte >> 4) & 0x07
        mantissa = ulaw_byte & 0x0F
        magnitude = ((mantissa << 3) + 0x84) << exponent
        return sign * (magnitude - 0x84)

    pcm_data = []
    for b in raw_audio_data:
        pcm_data.append(decode_ulaw(b))

    # 保存为 BWF (Broadcast Wave Format) WAV 文件
    # BWF 是 WAV 的扩展，添加 bext chunk 包含时间戳元数据
    wav_file = OUTPUT_DIR / f"audio_{TARGET_TIME_STR.replace(':', '-').replace(' ', '_')}.wav"

    # 计算音频实际开始时间
    first_frame_ts = filtered_frames[0].unix_ts
    audio_start_dt = datetime.fromtimestamp(first_frame_ts, tz)

    # 创建 BWF bext chunk
    def create_bext_chunk(origin_time: datetime, sample_rate: int = 8000) -> bytes:
        """
        创建 BWF bext chunk

        bext chunk 格式 (EBU Tech 3285):
        - Description: 256 bytes (ASCII)
        - Originator: 32 bytes (ASCII)
        - OriginatorReference: 32 bytes (ASCII)
        - OriginationDate: 10 bytes (YYYY-MM-DD)
        - OriginationTime: 8 bytes (HH:MM:SS)
        - TimeReferenceLow: 4 bytes (sample count low 32 bits)
        - TimeReferenceHigh: 4 bytes (sample count high 32 bits)
        - Version: 2 bytes
        - UMID: 64 bytes
        - LoudnessValue: 2 bytes
        - LoudnessRange: 2 bytes
        - MaxTruePeakLevel: 2 bytes
        - MaxMomentaryLoudness: 2 bytes
        - MaxShortTermLoudness: 2 bytes
        - Reserved: 180 bytes
        """
        description = f"DVR Audio Export - {origin_time.strftime('%Y-%m-%d %H:%M:%S')}".encode('ascii')
        description = description[:256].ljust(256, b'\x00')

        originator = b"SeetongDVR-python".ljust(32, b'\x00')
        originator_ref = b"".ljust(32, b'\x00')

        origin_date = origin_time.strftime('%Y-%m-%d').encode('ascii')  # 10 bytes
        origin_time_str = origin_time.strftime('%H:%M:%S').encode('ascii')  # 8 bytes

        # 时间参考：从午夜开始的采样数
        midnight = origin_time.replace(hour=0, minute=0, second=0, microsecond=0)
        seconds_since_midnight = (origin_time - midnight).total_seconds()
        samples_since_midnight = int(seconds_since_midnight * sample_rate)

        time_ref_low = samples_since_midnight & 0xFFFFFFFF
        time_ref_high = (samples_since_midnight >> 32) & 0xFFFFFFFF

        bext_data = (
            description +  # 256 bytes
            originator +  # 32 bytes
            originator_ref +  # 32 bytes
            origin_date +  # 10 bytes
            origin_time_str +  # 8 bytes
            struct.pack('<II', time_ref_low, time_ref_high) +  # 8 bytes
            struct.pack('<H', 2) +  # Version: 2 bytes
            b'\x00' * 64 +  # UMID: 64 bytes
            b'\x00' * 10 +  # Loudness fields: 10 bytes
            b'\x00' * 180  # Reserved: 180 bytes
        )

        # bext chunk: 'bext' + size + data
        chunk = b'bext' + struct.pack('<I', len(bext_data)) + bext_data
        return chunk

    # 先用 wave 模块创建基本 WAV
    with wave.open(str(wav_file), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(8000)
        wf.writeframes(struct.pack(f'<{len(pcm_data)}h', *pcm_data))

    # 然后在 WAV 文件中插入 bext chunk（在 fmt 后，data 前）
    with open(wav_file, 'rb') as f:
        wav_data = f.read()

    # 找到 'data' chunk 的位置
    data_pos = wav_data.find(b'data')
    if data_pos > 0:
        bext_chunk = create_bext_chunk(audio_start_dt)
        # 重建 WAV：RIFF header + fmt chunk + bext chunk + data chunk
        new_wav = (
            wav_data[:4] +  # 'RIFF'
            struct.pack('<I', len(wav_data) - 8 + len(bext_chunk)) +  # 更新文件大小
            wav_data[8:data_pos] +  # WAVE + fmt chunk
            bext_chunk +  # 插入 bext chunk
            wav_data[data_pos:]  # data chunk
        )
        with open(wav_file, 'wb') as f:
            f.write(new_wav)
        print(f"已保存 BWF WAV 文件: {wav_file}")
        print(f"  录制时间: {audio_start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        print(f"已保存 WAV 文件: {wav_file}")

    print(f"WAV 时长: {len(pcm_data) / 8000:.2f} 秒")

    # 生成 SRT 字幕文件，每秒显示实际时间戳
    srt_file = OUTPUT_DIR / f"audio_{TARGET_TIME_STR.replace(':', '-').replace(' ', '_')}.srt"
    duration_seconds = len(pcm_data) / 8000

    with open(srt_file, 'w', encoding='utf-8') as f:
        for i in range(int(duration_seconds) + 1):
            actual_ts = first_frame_ts + i
            actual_dt = datetime.fromtimestamp(actual_ts, tz)

            # SRT 时间格式: HH:MM:SS,mmm
            start_time = f"00:{i // 60:02d}:{i % 60:02d},000"
            end_i = i + 1
            end_time = f"00:{end_i // 60:02d}:{end_i % 60:02d},000"

            f.write(f"{i + 1}\n")
            f.write(f"{start_time} --> {end_time}\n")
            f.write(f"{actual_dt.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Unix: {actual_ts}\n")
            f.write("\n")

    print(f"已保存 SRT 字幕文件: {srt_file}")

    # 保存帧信息
    info_file = OUTPUT_DIR / f"audio_{TARGET_TIME_STR.replace(':', '-').replace(' ', '_')}_info.txt"
    with open(info_file, 'w') as f:
        f.write(f"目标时间: {TARGET_TIME_STR}\n")
        f.write(f"时间戳: {target_ts}\n")
        f.write(f"导出范围: {start_ts} - {end_ts}\n")
        f.write(f"段落: file_index={target_seg.file_index}\n")
        f.write(f"音频帧数: {len(filtered_frames)}\n")
        f.write(f"原始数据大小: {len(raw_audio_data)} 字节\n")
        f.write(f"WAV 时长: {len(pcm_data) / 8000:.2f} 秒\n")
        f.write(f"\n帧大小分布:\n")
        size_counts = {}
        for s in frame_sizes:
            size_counts[s] = size_counts.get(s, 0) + 1
        for size, count in sorted(size_counts.items()):
            f.write(f"  {size} 字节: {count} 帧\n")
        f.write(f"\n前50帧详情:\n")
        for i, af in enumerate(filtered_frames[:50]):
            f.write(f"  [{i}] unix_ts={af.unix_ts}, offset={af.file_offset}, size={af.frame_size}\n")

    print(f"已保存帧信息: {info_file}")


if __name__ == '__main__':
    main()
