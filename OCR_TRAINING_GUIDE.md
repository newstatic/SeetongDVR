# OCR Model Training Guide

This guide explains how to train custom Tesseract OCR models for recognizing OSD (On-Screen Display) timestamps from Seetong DVR video frames.

## Overview

The DVR embeds timestamps in the video as white text on a semi-transparent background. Standard OCR models have difficulty with this format, so we train custom models optimized for:

- Monospace digit font used by DVR
- Specific character set: `0-9`, `-`, `:`, space
- Fixed format: `YYYY-MM-DD HH:MM:SS`

## Prerequisites

```bash
# Install Tesseract with training tools
brew install tesseract tesseract-lang  # macOS
# or
sudo apt install tesseract-ocr libtesseract-dev  # Ubuntu

# Install 7z for extracting training data
brew install p7zip  # macOS
# or
sudo apt install p7zip-full  # Ubuntu

# Verify installation
tesseract --version
7z --help
```

## Training Data Structure

```
tesseract_train/
├── ground-truth.7z            # Compressed training samples (530 samples)
├── ground-truth/              # Extracted training samples (ignored by git)
│   ├── osd.dvr.exp0.0000.tif  # Image file
│   ├── osd.dvr.exp0.0000.gt.txt  # Ground truth text
│   ├── osd.dvr.exp0.0000.box  # Character bounding boxes
│   └── ...
├── scripts/                   # Training and testing scripts
│   ├── train_line_v2.py       # Main training script with augmentation
│   ├── augment_data.py        # Data augmentation utility
│   ├── extract_training_data.py  # Extract OSD from frames
│   ├── test_trained_model.py  # Test model accuracy
│   └── verify_vps_ocr.py      # Verify OCR on VPS-extracted frames
└── dvr_line_v2.traineddata    # Trained OCR model (line-optimized)
```

### Extract Training Data

Before training, extract the compressed ground truth data:

```bash
cd tesseract_train
7z x ground-truth.7z -oground-truth
```

This will create a `ground-truth/` directory with 530 training samples:
- `.tif` files: Preprocessed OSD images
- `.gt.txt` files: Ground truth text labels
- `.box` files: Character bounding boxes
- `.lstmf` files: LSTM training features

## Quick Start: Use Existing Samples

The included training data is ready to use. Skip to Step 4 if you want to train with existing samples.

## Quick Start: Use Training Scripts

The `scripts/` directory contains ready-to-use Python scripts for the complete training workflow:

### 1. Extract Training Data from Frames

```bash
cd tesseract_train/scripts
python extract_training_data.py
```

This extracts OSD regions from video frames and creates ground-truth files.

### 2. Augment Training Data

```bash
python augment_data.py
```

Creates 10x more training samples with variations (rotation, brightness, contrast, noise, scale).

### 3. Train the Model

```bash
python train_line_v2.py
```

This script:
- Generates LSTMF files from ground truth
- Creates train/validation split (90/10)
- Fine-tunes from English model with 10,000 iterations
- Creates final `.traineddata` file

### 4. Test Model Accuracy

```bash
python test_trained_model.py
```

Compares trained model vs default English model on test samples.

### 5. Verify on Real Frames

```bash
python verify_vps_ocr.py
```

Tests OCR accuracy on VPS-extracted video frames.

## Step 1: Extract NEW Training Samples

### Using the Frame Extractor Scripts

The project includes two extraction scripts:

1. **`precise_frame_extractor.py`** - Basic extractor using byte-position interpolation
2. **`precise_frame_extractor_final.py`** - Advanced extractor with OCR verification

```bash
# Extract frames using the basic extractor
python precise_frame_extractor.py

# Extract frames with OCR verification (requires trained model)
python precise_frame_extractor_final.py
```

### Manual OSD Extraction

Extract OSD regions from video frames:

```python
import cv2
from PIL import Image

def extract_osd_region(frame_path, output_path):
    """Extract OSD timestamp region from video frame."""
    img = cv2.imread(frame_path)

    # OSD is typically in top-left corner
    # Adjust coordinates based on your DVR model
    osd_region = img[10:50, 10:280]  # y1:y2, x1:x2

    # Convert to grayscale and enhance
    gray = cv2.cvtColor(osd_region, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)

    # Save as TIFF (required by Tesseract)
    cv2.imwrite(output_path, binary)
```

## Step 2: Create Ground Truth Files

For each `.tif` image, create corresponding `.gt.txt` file:

```bash
# ground-truth/osd.dvr.exp0.0000.gt.txt
2025-12-18 13:07:29
```

The filename format must be: `{lang}.{fontname}.exp{N}.{NNNN}.{ext}`

## Step 3: Generate Box Files

Use Tesseract to generate initial box files:

```bash
cd tesseract_train/ground-truth

for tif in *.tif; do
    base="${tif%.tif}"
    tesseract "$tif" "$base" -l eng --psm 7 makebox
done
```

Then manually verify and correct the box files if needed.

## Step 4: Generate LSTM Training Data

```bash
# Create lstmf files from box files
for tif in ground-truth/*.tif; do
    base="${tif%.tif}"
    tesseract "$tif" "$base" -l eng --psm 7 lstm.train
done
```

## Step 5: Train the Model

### Option A: Fine-tune from English model

```bash
# Extract English LSTM
combine_tessdata -e /usr/local/share/tessdata/eng.traineddata eng.lstm

# Create training file list
ls ground-truth/*.lstmf > train_list.txt

# Start training
lstmtraining \
    --continue_from eng.lstm \
    --model_output output/dvr \
    --traineddata eng.traineddata \
    --train_listfile train_list.txt \
    --max_iterations 10000
```

### Option B: Train from scratch (more data needed)

```bash
lstmtraining \
    --model_output output/dvr \
    --traineddata eng.traineddata \
    --train_listfile train_list.txt \
    --net_spec '[1,36,0,1 Ct3,3,16 Mp3,3 Lfys48 Lfx96 Lrx96 Lfx256 O1c111]' \
    --max_iterations 50000
```

## Step 6: Create Final traineddata

```bash
# Combine checkpoint with language data
lstmtraining \
    --stop_training \
    --continue_from output/dvr_checkpoint \
    --traineddata eng.traineddata \
    --model_output dvr.traineddata
```

## Step 7: Test the Model

```python
import pytesseract
from PIL import Image

# Use custom model
custom_config = r'--oem 1 --psm 7 -l dvr'
text = pytesseract.image_to_string(Image.open('test.tif'), config=custom_config)
print(text)
```

## Training Tips

### Character Whitelist

Limit recognition to valid characters:

```python
config = r'--oem 1 --psm 7 -l dvr -c tessedit_char_whitelist=0123456789-: '
```

### Image Preprocessing

Optimal preprocessing for DVR OSD:

```python
def preprocess_osd(image):
    # Convert to grayscale
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Apply adaptive threshold
    binary = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        11, 2
    )

    # Optional: scale up for better recognition
    scaled = cv2.resize(binary, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)

    return scaled
```

### Data Augmentation

Increase training data variety:

```python
import imgaug.augmenters as iaa

augmenter = iaa.Sequential([
    iaa.Sometimes(0.5, iaa.GaussianBlur(sigma=(0, 0.5))),
    iaa.AdditiveGaussianNoise(scale=(0, 0.05*255)),
    iaa.Affine(rotate=(-2, 2)),
])

augmented = augmenter(image=original)
```

## Included Model

| Model | Description | Best For |
|-------|-------------|----------|
| `dvr_line_v2.traineddata` | Line-optimized model trained with augmented data | Full timestamp lines (YYYY-MM-DD HH:MM:SS) |

## Troubleshooting

### Low Accuracy

1. Check image quality and preprocessing
2. Verify ground truth accuracy
3. Increase training iterations
4. Add more diverse training samples

### Training Not Converging

1. Reduce learning rate
2. Check for corrupted training files
3. Verify box file accuracy

### Model Too Large

1. Use `--net_spec` with smaller network
2. Prune unused characters from unicharset

## Resources

- [Tesseract Training Documentation](https://tesseract-ocr.github.io/tessdoc/Training-Tesseract.html)
- [tesstrain](https://github.com/tesseract-ocr/tesstrain) - Training workflow
- [Tesseract LSTM Training](https://tesseract-ocr.github.io/tessdoc/tess4/TrainingTesseract-4.00.html)
