#!/usr/bin/env python3
"""
导出指定时间范围的视频和音频，合成为带时间戳字幕的MP4文件

输出文件:
- video_xxx.h265: 原始 H.265 视频流
- audio_xxx.wav: WAV 音频文件
- timestamp_xxx.srt: 时间戳字幕
- output_xxx.mp4: 合成后的 MP4 文件（视频+音频+字幕）
"""

import struct
import subprocess
import wave
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from server.seetong_lib import (
    TPSStorage,
    CHANNEL_VIDEO_CH1,
    CHANNEL_AUDIO,
    NalType,
)

# 目标时间: 2025-12-18 14:39:15 北京时间
TARGET_TIME_STR = "2025-12-18 14:39:15"
RANGE_SECONDS = 5 * 60  # 前后5分钟

# DVR 路径
DVR_PATH = "/Volumes/NO NAME"

# 输出目录
OUTPUT_DIR = Path("/Users/ttttt/PycharmProjects/SeetongDVR-python/audio_export")

# 时区
TZ = ZoneInfo("Asia/Shanghai")


def decode_ulaw(ulaw_byte: int) -> int:
    """解码单个 μ-law 字节"""
    ulaw_byte = ~ulaw_byte & 0xFF
    sign = 1 if (ulaw_byte & 0x80) else -1
    exponent = (ulaw_byte >> 4) & 0x07
    mantissa = ulaw_byte & 0x0F
    magnitude = ((mantissa << 3) + 0x84) << exponent
    return sign * (magnitude - 0x84)


def export_audio(storage: TPSStorage, rec_file: str, filtered_frames: list, output_prefix: str) -> tuple:
    """导出音频数据，返回 (wav_path, first_frame_ts, duration_seconds)"""
    raw_audio_data = bytearray()

    with open(rec_file, 'rb') as f:
        for af in filtered_frames:
            f.seek(af.file_offset)
            data = f.read(af.frame_size)
            raw_audio_data.extend(data)

    print(f"音频: 读取了 {len(filtered_frames)} 帧, 总大小: {len(raw_audio_data)} 字节")

    # 解码为 PCM
    pcm_data = [decode_ulaw(b) for b in raw_audio_data]

    # 保存为 WAV
    wav_file = OUTPUT_DIR / f"{output_prefix}.wav"
    with wave.open(str(wav_file), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(struct.pack(f'<{len(pcm_data)}h', *pcm_data))

    first_frame_ts = filtered_frames[0].unix_ts
    duration_seconds = len(pcm_data) / 8000

    print(f"音频: 已保存 {wav_file}, 时长: {duration_seconds:.2f} 秒")
    return wav_file, first_frame_ts, duration_seconds


def find_timestamp_for_offset(audio_frames: list, target_offset: int) -> int:
    """Find the accurate timestamp for a byte offset using audio frame index"""
    if not audio_frames:
        return 0

    # Binary search for the closest audio frame
    best_frame = audio_frames[0]
    for af in audio_frames:
        if af.file_offset <= target_offset:
            best_frame = af
        else:
            break

    return best_frame.unix_ts


def find_offset_for_timestamp(audio_frames: list, target_ts: int) -> int:
    """Find the byte offset for a target timestamp using audio frame index"""
    if not audio_frames:
        return 0

    # Find the first audio frame at or after target timestamp
    for af in audio_frames:
        if af.unix_ts >= target_ts:
            return af.file_offset

    # If all frames are before target, return last frame's offset
    return audio_frames[-1].file_offset


def export_video(storage: TPSStorage, target_seg, rec_file: str, start_ts: int, end_ts: int,
                 output_prefix: str, audio_frames: list) -> tuple:
    """导出视频数据，返回 (h265_path, video_start_ts, frame_count)"""

    # Use audio frame index to find accurate byte offset for start_ts
    target_offset = find_offset_for_timestamp(audio_frames, start_ts)
    print(f"视频: 目标时间 {datetime.fromtimestamp(start_ts, TZ)}, 对应偏移 {target_offset}")

    # Search for VPS/SPS/PPS/IDR near this offset
    header_result = storage.read_video_header(target_seg.file_index, target_offset)
    if not header_result:
        print("视频: 未找到视频头!")
        return None, None, 0

    vps, sps, pps, idr, stream_start_pos = header_result

    # Use audio frame timestamps for accurate time
    video_start_ts = find_timestamp_for_offset(audio_frames, stream_start_pos)

    print(f"视频: 起始位置 {stream_start_pos}, 起始时间 {datetime.fromtimestamp(video_start_ts, TZ)}")

    # 创建流读取器
    reader = storage.create_stream_reader(
        target_seg.file_index,
        stream_start_pos,
        video_start_ts * 1000,
        CHANNEL_VIDEO_CH1
    )
    if not reader:
        print("视频: 创建流读取器失败!")
        return None, None, 0

    # 读取视频帧直到结束时间
    h265_file = OUTPUT_DIR / f"{output_prefix}.h265"
    frame_count = 0
    start_code = b'\x00\x00\x00\x01'
    last_ts = video_start_ts

    with open(h265_file, 'wb') as f:
        # 先写入视频头
        f.write(start_code + vps)
        f.write(start_code + sps)
        f.write(start_code + pps)
        f.write(start_code + idr)
        frame_count += 1

        # 读取后续帧 - read_next_nals 是迭代器
        done = False
        while not done:
            nals = list(reader.read_next_nals())
            if not nals:
                break

            for nal_data, nal_type, timestamp_ms, nal_file_offset in nals:
                # 检查时间是否超过结束时间
                current_ts = timestamp_ms // 1000
                if current_ts > end_ts:
                    done = True
                    break

                # 写入 NAL 单元
                f.write(start_code + nal_data)
                frame_count += 1
                last_ts = current_ts

                if frame_count % 500 == 0:
                    print(f"视频: 已处理 {frame_count} 帧, 当前时间 {datetime.fromtimestamp(current_ts, TZ)}")

    # 关闭文件
    reader.f.close()

    print(f"视频: 已保存 {h265_file}, 共 {frame_count} 帧")
    return h265_file, video_start_ts, frame_count


def merge_with_ffmpeg(h265_file: Path, wav_file: Path, output_prefix: str,
                      video_start_ts: int, audio_start_ts: int) -> Path:
    """使用 ffmpeg 合成视频、音频，并叠加时间戳"""
    output_file = OUTPUT_DIR / f"{output_prefix}.mp4"

    # 计算音视频时间差（音频相对于视频的延迟，单位秒）
    audio_delay = audio_start_ts - video_start_ts
    print(f"合成: 视频开始 {video_start_ts}, 音频开始 {audio_start_ts}, 延迟 {audio_delay} 秒")

    # drawtext: use gmtime with timezone offset to avoid system timezone issues
    # Beijing timezone = UTC+8 = 8*3600 = 28800 seconds
    tz_offset = 8 * 3600
    video_epoch = video_start_ts + tz_offset
    audio_epoch = audio_start_ts + tz_offset

    # Video timestamp (bottom left, yellow, larger font)
    video_text = (
        f"drawtext=text='V\\: %{{pts\\:gmtime\\:{video_epoch}\\:%Y-%m-%d %H\\\\\\:%M\\\\\\:%S}}':"
        f"fontsize=48:fontcolor=yellow:borderw=3:bordercolor=black:"
        f"x=20:y=h-th-60"
    )

    # Audio timestamp (bottom right, cyan, larger font)
    audio_text = (
        f"drawtext=text='A\\: %{{pts\\:gmtime\\:{audio_epoch}\\:%Y-%m-%d %H\\\\\\:%M\\\\\\:%S}}':"
        f"fontsize=48:fontcolor=cyan:borderw=3:bordercolor=black:"
        f"x=w-text_w-20:y=h-th-60"
    )

    # Combined filter
    vf_filter = f"{video_text},{audio_text}"

    # ffmpeg 命令
    cmd = [
        'ffmpeg', '-y',
        '-i', str(h265_file),
    ]

    # 添加音频输入（带时间偏移）
    if audio_delay != 0:
        cmd.extend(['-itsoffset', str(audio_delay)])
    cmd.extend(['-i', str(wav_file)])

    # 输出设置
    cmd.extend([
        '-c:v', 'libx265',
        '-crf', '23',
        '-preset', 'fast',
        '-c:a', 'aac',
        '-b:a', '64k',
        '-vf', vf_filter,
        '-shortest',
        str(output_file)
    ])

    print(f"合成: 执行 ffmpeg...")
    print(f"命令: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"合成失败: {result.stderr}")
        # 尝试不带字幕的版本
        print("尝试不带硬字幕的版本...")
        cmd_simple = [
            'ffmpeg', '-y',
            '-i', str(h265_file),
        ]
        if audio_delay != 0:
            cmd_simple.extend(['-itsoffset', str(audio_delay)])
        cmd_simple.extend([
            '-i', str(wav_file),
            '-c:v', 'copy',
            '-c:a', 'aac',
            '-b:a', '64k',
            '-shortest',
            str(output_file)
        ])
        result = subprocess.run(cmd_simple, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"简单合成也失败: {result.stderr}")
            return None

    print(f"合成: 已保存 {output_file}")
    return output_file


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    # 解析目标时间
    target_dt = datetime.strptime(TARGET_TIME_STR, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
    target_ts = int(target_dt.timestamp())

    start_ts = target_ts - RANGE_SECONDS
    end_ts = target_ts + RANGE_SECONDS

    print(f"目标时间: {TARGET_TIME_STR} ({target_ts})")
    print(f"导出范围: {datetime.fromtimestamp(start_ts, TZ)} - {datetime.fromtimestamp(end_ts, TZ)}")
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
        return

    print(f"找到段落: file_index={target_seg.file_index}")
    print(f"段落时间: {datetime.fromtimestamp(target_seg.start_time, TZ)} - {datetime.fromtimestamp(target_seg.end_time, TZ)}")
    print()

    # 构建缓存
    print("构建缓存...")
    storage.build_cache([target_seg.file_index])

    rec_file = storage.get_rec_file(target_seg.file_index)
    output_prefix = f"video_{TARGET_TIME_STR.replace(':', '-').replace(' ', '_')}"

    # Get all audio frames first
    audio_frames = storage.get_audio_frames(target_seg.file_index)

    # 1. 导出视频 first (to get accurate video_start_ts)
    print("\n=== 导出视频 ===")
    h265_file, video_start_ts, frame_count = export_video(
        storage, target_seg, rec_file, start_ts, end_ts, output_prefix, audio_frames
    )

    if not h265_file:
        print("视频导出失败!")
        return

    # 2. 导出音频 - filter from video_start_ts to match video
    print("\n=== 导出音频 ===")
    # Use video_start_ts as audio start to ensure sync
    filtered_audio = [af for af in audio_frames if video_start_ts <= af.unix_ts <= end_ts]
    print(f"时间范围内的音频帧: {len(filtered_audio)}")

    if not filtered_audio:
        print("没有音频帧!")
        return

    wav_file, audio_start_ts, audio_duration = export_audio(
        storage, rec_file, filtered_audio, output_prefix
    )

    # 3. 合成 MP4（时间戳直接叠加到视频中）
    # Both timestamps start from the same time (video_start_ts)
    print("\n=== 合成 MP4 ===")
    output_file = merge_with_ffmpeg(
        h265_file, wav_file, output_prefix,
        video_start_ts, audio_start_ts
    )

    if output_file:
        print(f"\n完成! 输出文件: {output_file}")
    else:
        print("\n合成失败，但原始文件已导出:")
        print(f"  视频: {h265_file}")
        print(f"  音频: {wav_file}")
        print("\n可以手动合成:")
        print(f"  ffmpeg -i {h265_file} -i {wav_file} -c:v copy -c:a aac output.mp4")


if __name__ == '__main__':
    main()
