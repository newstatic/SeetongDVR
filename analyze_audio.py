#!/usr/bin/env python3
"""
分析 TPS 文件中的音频帧结构
"""

import struct
from pathlib import Path
from collections import defaultdict

# TRec 文件常量
TREC_INDEX_REGION_START = 0x0F900000
TREC_FRAME_INDEX_MAGIC = 0x4C3D2E1F
TREC_FRAME_INDEX_SIZE = 44

# 通道定义
CHANNEL_VIDEO_CH1 = 2
CHANNEL_AUDIO = 3

def parse_frame_index(rec_file_path: str):
    """解析帧索引"""
    records = []

    with open(rec_file_path, 'rb') as f:
        magic_bytes = struct.pack('<I', TREC_FRAME_INDEX_MAGIC)
        f.seek(TREC_INDEX_REGION_START)
        data = f.read(0x700000)

        idx = data.find(magic_bytes)
        if idx == -1:
            return records

        index_start = TREC_INDEX_REGION_START + idx
        f.seek(index_start)

        while True:
            data = f.read(TREC_FRAME_INDEX_SIZE)
            if len(data) < TREC_FRAME_INDEX_SIZE:
                break

            magic = struct.unpack('<I', data[0:4])[0]
            if magic != TREC_FRAME_INDEX_MAGIC:
                break

            frame_type = struct.unpack('<I', data[4:8])[0]
            channel = struct.unpack('<I', data[8:12])[0]
            frame_seq = struct.unpack('<I', data[12:16])[0]
            file_offset = struct.unpack('<I', data[16:20])[0]
            frame_size = struct.unpack('<I', data[20:24])[0]
            timestamp_us = struct.unpack('<Q', data[24:32])[0]
            unix_ts = struct.unpack('<I', data[32:36])[0]

            records.append({
                'frame_type': frame_type,
                'channel': channel,
                'frame_seq': frame_seq,
                'file_offset': file_offset,
                'frame_size': frame_size,
                'timestamp_us': timestamp_us,
                'unix_ts': unix_ts,
            })

    return records


def analyze_audio_frames(rec_file_path: str):
    """分析音频帧"""
    print(f"分析文件: {rec_file_path}")
    print("=" * 80)

    records = parse_frame_index(rec_file_path)
    print(f"总帧数: {len(records)}")

    # 分离音频和视频帧
    audio_frames = [r for r in records if r['channel'] == CHANNEL_AUDIO]
    video_frames = [r for r in records if r['channel'] == CHANNEL_VIDEO_CH1]

    print(f"音频帧: {len(audio_frames)}")
    print(f"视频帧: {len(video_frames)}")
    print()

    if not audio_frames:
        print("没有音频帧!")
        return

    # 1. 分析原始顺序（按 timestamp_us 排序后的顺序，这是帧索引的原始排序）
    audio_by_timestamp = sorted(audio_frames, key=lambda x: x['timestamp_us'])

    print("=" * 80)
    print("1. 按 timestamp_us 排序的前 20 个音频帧:")
    print("-" * 80)
    print(f"{'idx':<6} {'timestamp_us':<15} {'unix_ts':<12} {'file_offset':<12} {'frame_size':<10}")
    for i, af in enumerate(audio_by_timestamp[:20]):
        print(f"{i:<6} {af['timestamp_us']:<15} {af['unix_ts']:<12} {af['file_offset']:<12} {af['frame_size']:<10}")

    # 2. 分析按 file_offset 排序
    audio_by_offset = sorted(audio_frames, key=lambda x: x['file_offset'])

    print()
    print("=" * 80)
    print("2. 按 file_offset 排序的前 20 个音频帧:")
    print("-" * 80)
    print(f"{'idx':<6} {'file_offset':<12} {'unix_ts':<12} {'timestamp_us':<15} {'frame_size':<10}")
    for i, af in enumerate(audio_by_offset[:20]):
        print(f"{i:<6} {af['file_offset']:<12} {af['unix_ts']:<12} {af['timestamp_us']:<15} {af['frame_size']:<10}")

    # 3. 分析按 unix_ts 排序
    audio_by_unix_ts = sorted(audio_frames, key=lambda x: (x['unix_ts'], x['timestamp_us']))

    print()
    print("=" * 80)
    print("3. 按 unix_ts 排序的前 20 个音频帧:")
    print("-" * 80)
    print(f"{'idx':<6} {'unix_ts':<12} {'timestamp_us':<15} {'file_offset':<12} {'frame_size':<10}")
    for i, af in enumerate(audio_by_unix_ts[:20]):
        print(f"{i:<6} {af['unix_ts']:<12} {af['timestamp_us']:<15} {af['file_offset']:<12} {af['frame_size']:<10}")

    # 4. 分析 unix_ts 分布
    print()
    print("=" * 80)
    print("4. unix_ts 分布统计:")
    print("-" * 80)

    unix_ts_counts = defaultdict(int)
    for af in audio_frames:
        unix_ts_counts[af['unix_ts']] += 1

    sorted_ts = sorted(unix_ts_counts.keys())
    print(f"unix_ts 范围: {sorted_ts[0]} - {sorted_ts[-1]}")
    print(f"唯一 unix_ts 数量: {len(sorted_ts)}")
    print(f"时间跨度: {sorted_ts[-1] - sorted_ts[0]} 秒")

    # 检查连续性
    gaps = []
    for i in range(1, len(sorted_ts)):
        gap = sorted_ts[i] - sorted_ts[i-1]
        if gap > 1:
            gaps.append((sorted_ts[i-1], sorted_ts[i], gap))

    print(f"unix_ts 间隙数量 (>1秒): {len(gaps)}")
    if gaps:
        print("前 10 个间隙:")
        for prev, curr, gap in gaps[:10]:
            print(f"  {prev} -> {curr} (间隔 {gap} 秒)")

    # 5. 每秒的音频帧数量
    print()
    print("=" * 80)
    print("5. 每秒音频帧数量 (前 20 秒):")
    print("-" * 80)
    for ts in sorted_ts[:20]:
        count = unix_ts_counts[ts]
        print(f"  unix_ts={ts}: {count} 帧")

    # 6. 分析视频和音频的交织情况
    print()
    print("=" * 80)
    print("6. 视频和音频帧的交织分析 (按 file_offset):")
    print("-" * 80)

    all_frames = sorted(records, key=lambda x: x['file_offset'])

    # 统计交织模式
    prev_channel = None
    switches = 0
    for f in all_frames[:1000]:
        if prev_channel is not None and f['channel'] != prev_channel:
            switches += 1
        prev_channel = f['channel']

    print(f"前 1000 帧中，通道切换次数: {switches}")

    # 显示前 30 帧的交织情况
    print("前 30 帧:")
    for i, f in enumerate(all_frames[:30]):
        ch_name = "AUDIO" if f['channel'] == CHANNEL_AUDIO else "VIDEO"
        print(f"  [{i}] offset={f['file_offset']:<10} ch={ch_name:<6} unix_ts={f['unix_ts']} size={f['frame_size']}")

    # 7. 分析 timestamp_us 和 unix_ts 的关系
    print()
    print("=" * 80)
    print("7. timestamp_us 和 unix_ts 的关系分析:")
    print("-" * 80)

    # 找到一个 unix_ts 秒内的所有音频帧
    sample_ts = sorted_ts[len(sorted_ts) // 2]  # 取中间的一个时间戳
    frames_in_second = [af for af in audio_frames if af['unix_ts'] == sample_ts]
    frames_in_second.sort(key=lambda x: x['timestamp_us'])

    print(f"unix_ts={sample_ts} 秒内的 {len(frames_in_second)} 个音频帧:")
    for i, af in enumerate(frames_in_second):
        if i > 0:
            delta = af['timestamp_us'] - frames_in_second[i-1]['timestamp_us']
            print(f"  [{i}] timestamp_us={af['timestamp_us']}, delta={delta}us ({delta/1000:.1f}ms), offset={af['file_offset']}")
        else:
            print(f"  [{i}] timestamp_us={af['timestamp_us']}, offset={af['file_offset']}")


if __name__ == '__main__':
    import sys

    if len(sys.argv) > 1:
        rec_file = sys.argv[1]
    else:
        rec_file = "/Volumes/NO NAME/TRec000000.tps"

    if not Path(rec_file).exists():
        print(f"文件不存在: {rec_file}")
        sys.exit(1)

    analyze_audio_frames(rec_file)
