#!/usr/bin/env python3
"""
Line-based training v2 with:
- Augmented data (5350 samples)
- Train/validation split (90/10)
- 10000 iterations
- Adjusted learning rate (0.0001)
"""

import os
import subprocess
import shutil
import random

# Configuration
AUGMENTED_DIR = "ground-truth-augmented"
OUTPUT_DIR = "output"
MODEL_NAME = "dvr_line_v2"
LOCAL_TRAINEDDATA = "eng.traineddata"
LOCAL_LSTM = "backup_20260104/output/eng.lstm"

# Training parameters
MAX_ITERATIONS = 10000
LEARNING_RATE = 0.0001
VALIDATION_SPLIT = 0.1  # 10% for validation


def generate_lstmf_files():
    """Generate LSTMF files for all training samples."""
    print("Generating LSTMF files...")

    tessdata_dir = os.path.dirname(os.path.abspath(LOCAL_TRAINEDDATA))
    configs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'configs')
    lstm_train_config = os.path.join(configs_dir, 'lstm.train')

    gt_files = sorted([f for f in os.listdir(AUGMENTED_DIR) if f.endswith('.gt.txt')])
    generated = 0

    for i, gt_file in enumerate(gt_files):
        base_name = gt_file.replace('.gt.txt', '')
        tif_path = os.path.join(AUGMENTED_DIR, base_name + '.tif')
        lstmf_path = os.path.join(AUGMENTED_DIR, base_name + '.lstmf')

        if not os.path.exists(tif_path):
            continue

        # Generate LSTMF if not exists
        if not os.path.exists(lstmf_path):
            cmd = [
                'tesseract', tif_path,
                os.path.join(AUGMENTED_DIR, base_name),
                '--psm', '7',
                '--tessdata-dir', tessdata_dir,
                '-l', 'eng',
                lstm_train_config
            ]
            subprocess.run(cmd, capture_output=True, text=True)

        if os.path.exists(lstmf_path):
            generated += 1

        if (i + 1) % 500 == 0:
            print(f"  Processed {i + 1}/{len(gt_files)}")

    print(f"Total LSTMF files: {generated}")
    return generated


def create_train_val_split():
    """Create train/validation split."""
    print("\nCreating train/validation split...")

    lstmf_files = sorted([f for f in os.listdir(AUGMENTED_DIR) if f.endswith('.lstmf')])
    lstmf_paths = [os.path.abspath(os.path.join(AUGMENTED_DIR, f)) for f in lstmf_files]

    # Shuffle
    random.seed(42)
    random.shuffle(lstmf_paths)

    # Split
    val_size = int(len(lstmf_paths) * VALIDATION_SPLIT)
    val_paths = lstmf_paths[:val_size]
    train_paths = lstmf_paths[val_size:]

    # Write list files
    train_file = os.path.join(OUTPUT_DIR, 'train_list.txt')
    val_file = os.path.join(OUTPUT_DIR, 'val_list.txt')

    with open(train_file, 'w') as f:
        f.write('\n'.join(train_paths))

    with open(val_file, 'w') as f:
        f.write('\n'.join(val_paths))

    print(f"  Training samples: {len(train_paths)}")
    print(f"  Validation samples: {len(val_paths)}")

    return train_file, val_file


def train():
    """Train the model with validation."""
    print("\n" + "=" * 60)
    print("Training with augmented data")
    print(f"  Iterations: {MAX_ITERATIONS}")
    print(f"  Learning rate: {LEARNING_RATE}")
    print("=" * 60)

    # Copy eng.lstm if needed
    eng_lstm = os.path.join(OUTPUT_DIR, 'eng.lstm')
    if not os.path.exists(eng_lstm):
        shutil.copy2(LOCAL_LSTM, eng_lstm)

    # Remove old checkpoints
    for f in os.listdir(OUTPUT_DIR):
        if MODEL_NAME in f and ('.checkpoint' in f or f == f'{MODEL_NAME}_checkpoint'):
            os.remove(os.path.join(OUTPUT_DIR, f))

    train_file = os.path.join(OUTPUT_DIR, 'train_list.txt')
    val_file = os.path.join(OUTPUT_DIR, 'val_list.txt')
    model_output = os.path.join(OUTPUT_DIR, MODEL_NAME)

    cmd = [
        'lstmtraining',
        '--model_output', model_output,
        '--continue_from', eng_lstm,
        '--traineddata', os.path.abspath(LOCAL_TRAINEDDATA),
        '--train_listfile', train_file,
        '--eval_listfile', val_file,  # Validation set
        '--max_iterations', str(MAX_ITERATIONS),
        '--target_error_rate', '0.001',
        '--learning_rate', str(LEARNING_RATE),
    ]

    print(f"\nCommand: {' '.join(cmd)}\n")
    subprocess.run(cmd)

    # Find best checkpoint
    checkpoints = [f for f in os.listdir(OUTPUT_DIR) if MODEL_NAME in f and f.endswith('.checkpoint')]
    if checkpoints:
        checkpoints.sort(key=lambda x: float(x.split('_')[3]) if len(x.split('_')) > 3 else 999)
        return os.path.join(OUTPUT_DIR, checkpoints[0])
    return None


def create_final(checkpoint):
    """Create final traineddata."""
    if not checkpoint:
        print("No checkpoint found")
        return None

    print(f"\nCreating final model from {os.path.basename(checkpoint)}...")

    output_file = os.path.join(OUTPUT_DIR, f'{MODEL_NAME}.traineddata')

    cmd = [
        'lstmtraining',
        '--stop_training',
        '--continue_from', checkpoint,
        '--traineddata', os.path.abspath(LOCAL_TRAINEDDATA),
        '--model_output', output_file
    ]

    subprocess.run(cmd, capture_output=True, text=True)

    if os.path.exists(output_file):
        print(f"Created: {output_file}")
        shutil.copy2(output_file, f'{MODEL_NAME}.traineddata')
        return output_file
    return None


def test_model(traineddata):
    """Test on original (non-augmented) samples."""
    if not traineddata:
        return

    print("\n" + "=" * 60)
    print("Testing Model on original samples")
    print("=" * 60)

    tessdata_dir = os.path.dirname(os.path.abspath(traineddata))
    model_name = os.path.basename(traineddata).replace('.traineddata', '')

    # Test on original ground-truth (not augmented)
    original_dir = "ground-truth"
    gt_files = sorted([f for f in os.listdir(original_dir) if f.endswith('.gt.txt')])

    correct = 0
    total = 0
    errors = []

    for gt_file in gt_files:
        base_name = gt_file.replace('.gt.txt', '')
        tif_path = os.path.join(original_dir, base_name + '.tif')
        gt_path = os.path.join(original_dir, gt_file)

        if not os.path.exists(tif_path):
            continue

        with open(gt_path, 'r') as f:
            expected = f.read().strip()

        result = subprocess.run([
            'tesseract', tif_path, 'stdout',
            '--psm', '7',
            '--tessdata-dir', tessdata_dir,
            '-l', model_name,
        ], capture_output=True, text=True)

        predicted = result.stdout.strip()

        if predicted == expected:
            correct += 1
        else:
            errors.append((base_name, predicted, expected))
        total += 1

    acc = correct / total * 100 if total else 0
    print(f"\nAccuracy: {correct}/{total} = {acc:.1f}%")

    if errors:
        print(f"\nErrors ({len(errors)}):")
        for name, pred, exp in errors[:20]:  # Show first 20
            print(f"  {name}: '{pred}' != '{exp}'")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    generate_lstmf_files()
    create_train_val_split()
    checkpoint = train()
    traineddata = create_final(checkpoint)
    test_model(traineddata)


if __name__ == '__main__':
    main()
