#!/usr/bin/env python3
"""
Data augmentation for OCR training.
Generates augmented samples with rotation, brightness, noise, and scale variations.
"""

import os
import shutil
import random
from PIL import Image, ImageEnhance, ImageFilter
import numpy as np

GROUND_TRUTH_DIR = "ground-truth"
AUGMENTED_DIR = "ground-truth-augmented"

# Augmentation parameters
ROTATION_RANGE = (-2, 2)  # degrees
BRIGHTNESS_RANGE = (0.8, 1.2)
CONTRAST_RANGE = (0.8, 1.2)
SCALE_RANGE = (0.95, 1.05)
NOISE_LEVEL = 5  # max pixel noise


def augment_image(img, suffix):
    """Apply augmentation based on suffix type."""
    img = img.convert('L')  # Ensure grayscale

    if suffix == 'rot1':
        # Rotate slightly clockwise
        angle = random.uniform(0.5, 2)
        img = img.rotate(angle, fillcolor=255, resample=Image.BICUBIC)

    elif suffix == 'rot2':
        # Rotate slightly counter-clockwise
        angle = random.uniform(-2, -0.5)
        img = img.rotate(angle, fillcolor=255, resample=Image.BICUBIC)

    elif suffix == 'bright1':
        # Increase brightness
        enhancer = ImageEnhance.Brightness(img)
        img = enhancer.enhance(random.uniform(1.05, 1.2))

    elif suffix == 'bright2':
        # Decrease brightness
        enhancer = ImageEnhance.Brightness(img)
        img = enhancer.enhance(random.uniform(0.8, 0.95))

    elif suffix == 'contrast1':
        # Increase contrast
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(random.uniform(1.1, 1.3))

    elif suffix == 'contrast2':
        # Decrease contrast
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(random.uniform(0.7, 0.9))

    elif suffix == 'noise':
        # Add slight noise
        arr = np.array(img)
        noise = np.random.randint(-NOISE_LEVEL, NOISE_LEVEL + 1, arr.shape)
        arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)

    elif suffix == 'scale1':
        # Scale up slightly
        w, h = img.size
        scale = random.uniform(1.02, 1.05)
        new_w, new_h = int(w * scale), int(h * scale)
        img = img.resize((new_w, new_h), Image.BICUBIC)
        # Crop back to original size from center
        left = (new_w - w) // 2
        top = (new_h - h) // 2
        img = img.crop((left, top, left + w, top + h))

    elif suffix == 'scale2':
        # Scale down slightly
        w, h = img.size
        scale = random.uniform(0.95, 0.98)
        new_w, new_h = int(w * scale), int(h * scale)
        img_scaled = img.resize((new_w, new_h), Image.BICUBIC)
        # Paste onto white background
        img = Image.new('L', (w, h), 255)
        left = (w - new_w) // 2
        top = (h - new_h) // 2
        img.paste(img_scaled, (left, top))

    return img


def create_augmented_dataset():
    """Create augmented dataset from original ground-truth."""
    print("Creating augmented dataset...")

    # Clean and recreate augmented directory
    if os.path.exists(AUGMENTED_DIR):
        shutil.rmtree(AUGMENTED_DIR)
    os.makedirs(AUGMENTED_DIR)

    # Get all original samples
    gt_files = sorted([f for f in os.listdir(GROUND_TRUTH_DIR) if f.endswith('.gt.txt')])

    # Augmentation types
    aug_types = ['rot1', 'rot2', 'bright1', 'bright2', 'contrast1', 'contrast2', 'noise', 'scale1', 'scale2']

    total_original = 0
    total_augmented = 0

    for i, gt_file in enumerate(gt_files):
        base_name = gt_file.replace('.gt.txt', '')
        tif_path = os.path.join(GROUND_TRUTH_DIR, base_name + '.tif')
        gt_path = os.path.join(GROUND_TRUTH_DIR, gt_file)
        box_path = os.path.join(GROUND_TRUTH_DIR, base_name + '.box')

        if not os.path.exists(tif_path):
            continue

        # Read ground truth
        with open(gt_path, 'r') as f:
            gt_text = f.read()

        # Copy original files
        for ext in ['.tif', '.gt.txt', '.box']:
            src = os.path.join(GROUND_TRUTH_DIR, base_name + ext)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(AUGMENTED_DIR, base_name + ext))
        total_original += 1

        # Load image
        try:
            img = Image.open(tif_path)
        except Exception as e:
            print(f"  Error loading {tif_path}: {e}")
            continue

        # Create augmented versions
        for aug_type in aug_types:
            aug_name = f"{base_name}_{aug_type}"
            aug_tif = os.path.join(AUGMENTED_DIR, aug_name + '.tif')
            aug_gt = os.path.join(AUGMENTED_DIR, aug_name + '.gt.txt')
            aug_box = os.path.join(AUGMENTED_DIR, aug_name + '.box')

            # Augment image
            aug_img = augment_image(img.copy(), aug_type)
            aug_img.save(aug_tif)

            # Copy ground truth (same text)
            with open(aug_gt, 'w') as f:
                f.write(gt_text)

            # Copy box file if exists
            if os.path.exists(box_path):
                shutil.copy2(box_path, aug_box)

            total_augmented += 1

        if (i + 1) % 100 == 0:
            print(f"  Processed {i + 1}/{len(gt_files)}")

    print(f"\nOriginal samples: {total_original}")
    print(f"Augmented samples: {total_augmented}")
    print(f"Total samples: {total_original + total_augmented}")

    return total_original + total_augmented


if __name__ == '__main__':
    create_augmented_dataset()
