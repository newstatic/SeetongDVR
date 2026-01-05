#!/usr/bin/env python3
"""
从真实 OSD 图像中提取训练数据

步骤:
1. 从已验证的图像中裁剪 OSD 区域
2. 预处理为二值图像
3. 生成 ground truth 文本文件
"""

import os
import csv
from PIL import Image

# 路径配置
SOURCE_DIR = "/Users/ttttt/PycharmProjects/SeetongDVR/osd_precise_test"
OUTPUT_DIR = "/Users/ttttt/PycharmProjects/SeetongDVR/tesseract_train/ground-truth"
INDEX_CSV = "/Users/ttttt/PycharmProjects/SeetongDVR/osd_precise_test/index_times.csv"


def load_index_times():
    """加载索引时间"""
    times = {}
    with open(INDEX_CSV, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # precise_001.jpg -> 2025-12-18 13:07:29
            times[row['filename']] = row['index_time']
    return times


def extract_osd_region(image_path: str) -> Image.Image:
    """提取并预处理 OSD 区域"""
    img = Image.open(image_path)

    # 裁剪 OSD 区域（只取数字部分，跳过星期）
    # 正确的坐标 (基于实际图像分析):
    # 日期 "2025-12-18": x=28 到 x=343, y=35 到 y=85 (左移4px)
    # 时间 "13:07:29": x=583 到 x=863, y=35 到 y=85
    date_region = img.crop((28, 35, 343, 85))
    time_region = img.crop((583, 35, 863, 85))

    # 合并为单行
    combined = Image.new('RGB', (date_region.width + 30 + time_region.width, 50), (128, 128, 128))
    combined.paste(date_region, (0, 0))
    combined.paste(time_region, (date_region.width + 30, 0))

    # 转灰度
    gray = combined.convert('L')

    # 放大3x (提高细节)
    scale = 3
    gray = gray.resize((gray.width * scale, gray.height * scale), Image.LANCZOS)

    # 对比度增强
    from PIL import ImageEnhance
    enhancer = ImageEnhance.Contrast(gray)
    gray = enhancer.enhance(1.5)

    # 二值化 - 白底黑字 (Tesseract 偏好)
    # 使用自适应阈值范围
    threshold = 160
    binary = gray.point(lambda x: 0 if x > threshold else 255, 'L')

    return binary


def format_ground_truth(datetime_str: str) -> str:
    """
    格式化 ground truth

    输入: 2025-12-18 13:07:29
    输出: 2025-12-18 13:07:29 (只保留数字和分隔符)
    """
    return datetime_str


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 加载索引时间作为 ground truth
    index_times = load_index_times()
    print(f"加载 {len(index_times)} 条索引时间")

    count = 0
    for filename, datetime_str in index_times.items():
        src_path = os.path.join(SOURCE_DIR, filename)
        if not os.path.exists(src_path):
            continue

        # 生成训练文件名 (不带扩展名)
        base_name = f"osd.dvr.exp0.{count:04d}"

        # 提取并保存图像
        try:
            img = extract_osd_region(src_path)
            img_path = os.path.join(OUTPUT_DIR, f"{base_name}.tif")
            img.save(img_path, 'TIFF')

            # 保存 ground truth
            gt_path = os.path.join(OUTPUT_DIR, f"{base_name}.gt.txt")
            gt_text = format_ground_truth(datetime_str)
            with open(gt_path, 'w') as f:
                f.write(gt_text)

            count += 1
            if count % 20 == 0:
                print(f"已处理 {count} 个文件")

        except Exception as e:
            print(f"处理 {filename} 失败: {e}")

    print(f"\n完成! 生成 {count} 个训练样本")
    print(f"输出目录: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
