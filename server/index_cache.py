"""
帧索引缓存模块

将解析后的帧索引存储到本地文件，避免重复解析大文件
使用 NumPy 结构化数组存储，加载速度极快（比纯 Python 快 10 倍以上）

缓存文件格式 (.npy):
- NumPy 结构化数组，包含以下字段:
  - frame_type (uint32)
  - channel (uint32)
  - frame_seq (uint32)
  - file_offset (uint32)
  - frame_size (uint32)
  - timestamp_us (uint64)
  - unix_ts (uint32)
"""

import hashlib
from pathlib import Path
from typing import List, Optional

import numpy as np

from .models import FrameIndexRecord


# 缓存目录 (固定在项目目录下，不依赖 cwd)
CACHE_DIR = Path(__file__).parent.parent / '.index_cache'

# NumPy 结构化数组的 dtype
RECORD_DTYPE = np.dtype([
    ('frame_type', 'u4'),
    ('channel', 'u4'),
    ('frame_seq', 'u4'),
    ('file_offset', 'u4'),
    ('frame_size', 'u4'),
    ('timestamp_us', 'u8'),
    ('unix_ts', 'u4'),
])


def _get_file_hash(file_path: str) -> str:
    """
    计算文件的唯一标识哈希值

    使用文件名和大小生成哈希（不用mtime，因为SD卡重新挂载会变）
    对于TRec文件，同名同大小的文件内容相同
    """
    path = Path(file_path)
    stat = path.stat()

    # 组合文件标识信息：文件名 + 大小
    identifier = f"{path.name}:{stat.st_size}"

    # 生成 MD5 哈希
    return hashlib.md5(identifier.encode()).hexdigest()


def _ensure_cache_dir():
    """确保缓存目录存在"""
    CACHE_DIR.mkdir(exist_ok=True)


def _get_cache_path(file_hash: str) -> Path:
    """获取缓存文件路径"""
    return CACHE_DIR / f"{file_hash}.npy"


def save_index_cache(file_path: str, records: List[FrameIndexRecord]) -> str:
    """
    保存帧索引到 NumPy 缓存文件

    Args:
        file_path: 原始TRec文件路径
        records: 帧索引记录列表

    Returns:
        缓存文件的哈希值
    """
    _ensure_cache_dir()

    file_hash = _get_file_hash(file_path)
    cache_path = _get_cache_path(file_hash)

    # 转换为 NumPy 结构化数组
    arr = np.array(
        [(r.frame_type, r.channel, r.frame_seq, r.file_offset,
          r.frame_size, r.timestamp_us, r.unix_ts) for r in records],
        dtype=RECORD_DTYPE
    )

    # 保存为 .npy 文件
    np.save(cache_path, arr)

    print(f"[IndexCache] 保存: {Path(file_path).name} -> {file_hash}.npy ({len(records)} 条)")
    return file_hash


def load_index_cache(file_path: str) -> Optional[List[FrameIndexRecord]]:
    """
    从 NumPy 缓存加载帧索引

    Args:
        file_path: 原始TRec文件路径

    Returns:
        帧索引记录列表，如果缓存不存在则返回None
    """
    file_hash = _get_file_hash(file_path)
    cache_path = _get_cache_path(file_hash)

    if not cache_path.exists():
        return None

    try:
        # 加载 NumPy 数组（极快）
        arr = np.load(cache_path)

        # 转换为 NamedTuple 列表 (tolist() 先转为原生 Python 类型，快 5 倍)
        records = [FrameIndexRecord._make(row) for row in arr.tolist()]

        print(f"[IndexCache] 加载: {Path(file_path).name} ({len(records)} 条)")
        return records
    except (ValueError, OSError):
        # 缓存文件损坏，删除它
        cache_path.unlink(missing_ok=True)
        return None


def clear_cache():
    """清除所有缓存"""
    if CACHE_DIR.exists():
        for cache_file in CACHE_DIR.glob('*.npy'):
            cache_file.unlink()
        # 同时清理旧的缓存格式
        for cache_file in CACHE_DIR.glob('*.bin'):
            cache_file.unlink()
        for cache_file in CACHE_DIR.glob('*.json'):
            cache_file.unlink()


def get_cache_info() -> dict:
    """获取缓存统计信息"""
    if not CACHE_DIR.exists():
        return {'count': 0, 'size': 0}

    cache_files = list(CACHE_DIR.glob('*.npy'))
    total_size = sum(f.stat().st_size for f in cache_files)

    return {
        'count': len(cache_files),
        'size': total_size,
        'files': [f.name for f in cache_files]
    }
