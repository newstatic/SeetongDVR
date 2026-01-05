#!/usr/bin/env python3
"""Test the trained Tesseract model on 100 OSD samples."""

import os
import csv
import subprocess
from pathlib import Path
from PIL import Image
from datetime import datetime
import tempfile

# Paths
PROJECT_DIR = Path(__file__).parent
SAMPLES_DIR = PROJECT_DIR / "osd_precise_test"
TRAINEDDATA_PATH = PROJECT_DIR / "tesstrain" / "data" / "dvr.traineddata"
TESSDATA_DIR = PROJECT_DIR / "tesstrain" / "data"

def preprocess_osd_region(image_path: str, invert=False) -> str:
    """Extract and preprocess OSD region, return path to temp file."""
    img = Image.open(image_path)

    # Extract date and time regions - match training data coordinates
    # 日期 "2025-12-18": x=28 到 x=343, y=35 到 y=85
    # 时间 "13:07:29": x=583 到 x=863, y=35 到 y=85
    date_region = img.crop((28, 35, 343, 85))
    time_region = img.crop((583, 35, 863, 85))

    # Combine horizontally with gap (gray background like training)
    combined_width = date_region.width + 30 + time_region.width
    combined = Image.new('RGB', (combined_width, 50), color=(128, 128, 128))
    combined.paste(date_region, (0, 0))
    combined.paste(time_region, (date_region.width + 30, 0))

    # Convert to grayscale
    gray = combined.convert('L')

    # Scale up 3x (match training)
    scale = 3
    scaled = gray.resize((gray.width * scale, gray.height * scale), Image.Resampling.LANCZOS)

    # Contrast enhancement (match training)
    from PIL import ImageEnhance
    enhancer = ImageEnhance.Contrast(scaled)
    scaled = enhancer.enhance(1.5)

    # Binarize - match training: threshold 160
    threshold = 160
    if invert:
        # Training used: 0 if x > threshold else 255 (white background, black text)
        binary = scaled.point(lambda x: 0 if x > threshold else 255, 'L')
    else:
        binary = scaled.point(lambda x: 255 if x > threshold else 0)

    # Save to temp file
    temp_path = tempfile.mktemp(suffix='.png')
    binary.save(temp_path)
    return temp_path

def ocr_with_trained_model(image_path: str) -> str:
    """Run OCR using the trained dvr model."""
    # Use inverted preprocessing to match training data
    temp_img = preprocess_osd_region(image_path, invert=True)

    try:
        # Run tesseract with custom tessdata path
        result = subprocess.run([
            'tesseract', temp_img, 'stdout',
            '--tessdata-dir', str(TESSDATA_DIR),
            '-l', 'dvr',
            '--psm', '7',  # Treat as single line
            '-c', 'tessedit_char_whitelist=0123456789-: '
        ], capture_output=True, text=True, timeout=10)

        return result.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"
    finally:
        if os.path.exists(temp_img):
            os.remove(temp_img)

def ocr_with_default_model(image_path: str) -> str:
    """Run OCR using default eng model for comparison."""
    temp_img = preprocess_osd_region(image_path)

    try:
        result = subprocess.run([
            'tesseract', temp_img, 'stdout',
            '-l', 'eng',
            '--psm', '7',
            '-c', 'tessedit_char_whitelist=0123456789-: '
        ], capture_output=True, text=True, timeout=10)

        return result.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"
    finally:
        if os.path.exists(temp_img):
            os.remove(temp_img)

def parse_datetime(text: str):
    """Parse OCR text to datetime."""
    text = text.strip()
    # Clean up common OCR artifacts
    text = text.replace(':', ':').replace(' :', ':').replace(': ', ':')

    # Try various formats
    formats = [
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d%H:%M:%S',
    ]

    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except:
            continue

    return None

def main():
    print("=" * 80)
    print("TESSERACT TRAINED MODEL TEST")
    print("=" * 80)
    print(f"Trained model: {TRAINEDDATA_PATH}")
    print(f"Model exists: {TRAINEDDATA_PATH.exists()}")
    print()

    # Load ground truth from CSV
    csv_path = SAMPLES_DIR / "index_times.csv"
    ground_truth = {}
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            ground_truth[row['filename']] = row['index_time']

    print(f"Loaded {len(ground_truth)} ground truth entries")
    print()

    # Test each sample
    trained_results = []
    default_results = []

    for filename in sorted(ground_truth.keys()):
        image_path = SAMPLES_DIR / filename
        if not image_path.exists():
            continue

        expected = ground_truth[filename]

        # OCR with trained model
        trained_text = ocr_with_trained_model(str(image_path))
        trained_parsed = parse_datetime(trained_text)
        trained_match = trained_text == expected or (trained_parsed and trained_parsed.strftime('%Y-%m-%d %H:%M:%S') == expected)

        # OCR with default model
        default_text = ocr_with_default_model(str(image_path))
        default_parsed = parse_datetime(default_text)
        default_match = default_text == expected or (default_parsed and default_parsed.strftime('%Y-%m-%d %H:%M:%S') == expected)

        trained_results.append({
            'filename': filename,
            'expected': expected,
            'ocr_text': trained_text,
            'match': trained_match
        })

        default_results.append({
            'filename': filename,
            'expected': expected,
            'ocr_text': default_text,
            'match': default_match
        })

        # Print progress
        status_t = "✓" if trained_match else "✗"
        status_d = "✓" if default_match else "✗"
        print(f"{filename}: Expected: {expected}")
        print(f"  Trained [{status_t}]: {trained_text}")
        print(f"  Default [{status_d}]: {default_text}")
        print()

    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)

    trained_correct = sum(1 for r in trained_results if r['match'])
    default_correct = sum(1 for r in default_results if r['match'])
    total = len(trained_results)

    print(f"Total samples: {total}")
    print(f"Trained model accuracy: {trained_correct}/{total} ({100*trained_correct/total:.1f}%)")
    print(f"Default model accuracy: {default_correct}/{total} ({100*default_correct/total:.1f}%)")
    print()

    # Show errors for trained model
    print("TRAINED MODEL ERRORS:")
    for r in trained_results:
        if not r['match']:
            print(f"  {r['filename']}: Expected '{r['expected']}', Got '{r['ocr_text']}'")

    print()
    print("DEFAULT MODEL ERRORS:")
    for r in default_results:
        if not r['match']:
            print(f"  {r['filename']}: Expected '{r['expected']}', Got '{r['ocr_text']}'")

if __name__ == "__main__":
    main()
