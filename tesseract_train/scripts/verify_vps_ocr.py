#!/usr/bin/env python3
"""验证 VPS 精确提取的 OSD 时间"""

import os
import subprocess
import tempfile
from pathlib import Path
from PIL import Image, ImageEnhance
from datetime import datetime
import re

PROJECT_DIR = Path(__file__).parent
SAMPLES_DIR = PROJECT_DIR / "osd_vps_test"
TESSDATA_DIR = PROJECT_DIR / "tesseract_train" / "output"
MODEL_NAME = "dvr_line_v2"


def preprocess_osd_region(image_path: str) -> str:
    img = Image.open(image_path)
    date_region = img.crop((28, 35, 343, 85))
    time_region = img.crop((583, 35, 863, 85))
    combined_width = date_region.width + 30 + time_region.width
    combined = Image.new('RGB', (combined_width, 50), color=(128, 128, 128))
    combined.paste(date_region, (0, 0))
    combined.paste(time_region, (date_region.width + 30, 0))
    gray = combined.convert('L')
    scaled = gray.resize((gray.width * 3, gray.height * 3), Image.Resampling.LANCZOS)
    enhancer = ImageEnhance.Contrast(scaled)
    scaled = enhancer.enhance(1.5)
    binary = scaled.point(lambda x: 0 if x > 160 else 255, 'L')
    temp_path = tempfile.mktemp(suffix='.png')
    binary.save(temp_path)
    return temp_path


def ocr_image(image_path: str) -> str:
    temp_img = preprocess_osd_region(image_path)
    try:
        result = subprocess.run([
            'tesseract', temp_img, 'stdout',
            '--tessdata-dir', str(TESSDATA_DIR),
            '-l', MODEL_NAME,
            '--psm', '7',
            '-c', 'tessedit_char_whitelist=0123456789-: '
        ], capture_output=True, text=True, timeout=10)
        return result.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"
    finally:
        if os.path.exists(temp_img):
            os.remove(temp_img)


def verify_samples():
    print("=" * 80)
    print("VPS 精确帧提取 OSD 验证")
    print("=" * 80)

    results_file = SAMPLES_DIR / "vps_results.txt"
    if not results_file.exists():
        print("错误: 找不到 vps_results.txt")
        return

    # 解析 index_time
    index_times = {}
    with open(results_file, 'r') as f:
        for line in f:
            match = re.search(r'样本(\d+).*index_time=(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*状态=OK', line)
            if match:
                sample_id = int(match.group(1))
                index_time = match.group(2)
                index_times[sample_id] = index_time

    print(f"找到 {len(index_times)} 个成功样本\n")
    print("注意: index_time 是帧索引的 start_time")
    print("      OSD_time 是向前搜索 VPS 后提取帧的实际时间")
    print("      预期: OSD_time <= index_time (因为向前搜索)\n")

    results = []

    for sample_id in sorted(index_times.keys()):
        index_time = index_times[sample_id]
        image_path = SAMPLES_DIR / f"vps_{sample_id:03d}.jpg"

        if not image_path.exists():
            continue

        ocr_text = ocr_image(str(image_path))

        # 解析 OCR 时间
        ocr_match = re.match(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})', ocr_text)
        if ocr_match:
            osd_time = f"{ocr_match.group(1)} {ocr_match.group(2)}"
        else:
            osd_time = ocr_text

        # 计算时间差
        time_diff = None
        try:
            osd_dt = datetime.strptime(osd_time, "%Y-%m-%d %H:%M:%S")
            idx_dt = datetime.strptime(index_time, "%Y-%m-%d %H:%M:%S")
            time_diff = int((osd_dt - idx_dt).total_seconds())
        except:
            pass

        results.append({
            'sample_id': sample_id,
            'index_time': index_time,
            'osd_time': osd_time,
            'time_diff': time_diff
        })

        diff_str = f"差{time_diff:+d}s" if time_diff is not None else "?"
        print(f"样本 {sample_id:03d}: index={index_time} | OSD={osd_time} | {diff_str}")

    # 统计
    print("\n" + "=" * 80)
    print("统计结果")
    print("=" * 80)

    time_diffs = [r['time_diff'] for r in results if r['time_diff'] is not None]
    if time_diffs:
        print(f"总样本: {len(results)}")
        print(f"平均差异: {sum(time_diffs)/len(time_diffs):.1f} 秒")
        print(f"最小差异: {min(time_diffs)} 秒")
        print(f"最大差异: {max(time_diffs)} 秒")

        # OSD 时间应该 <= index_time (因为向前搜索)
        correct = sum(1 for d in time_diffs if d <= 0)
        print(f"OSD <= index: {correct}/{len(time_diffs)}")


if __name__ == "__main__":
    verify_samples()
