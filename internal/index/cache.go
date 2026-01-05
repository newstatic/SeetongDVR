package index

import (
	"crypto/md5"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"unsafe"

	"seetong-dvr/internal/models"

	"golang.org/x/sys/unix"
)

const RecordSize = 32 // 每条记录 32 字节

// IndexCache mmap 映射的索引缓存
type IndexCache struct {
	data    []byte                      // mmap 映射的原始数据
	Records []models.FrameIndexRecord   // 零拷贝切片视图
	Count   int
}

var cacheDir string

func init() {
	// 默认缓存目录：工作目录下的 .index_cache
	cwd, err := os.Getwd()
	if err != nil {
		cacheDir = ".index_cache"
	} else {
		cacheDir = filepath.Join(cwd, ".index_cache")
	}
}

// SetCacheDir 设置缓存目录
func SetCacheDir(dir string) {
	cacheDir = dir
	os.MkdirAll(cacheDir, 0755)
}

// GetCacheDir 获取当前缓存目录
func GetCacheDir() string {
	return cacheDir
}

// GetFileHash 计算文件的唯一标识
func GetFileHash(filePath string) (string, error) {
	info, err := os.Stat(filePath)
	if err != nil {
		return "", err
	}
	
	identifier := fmt.Sprintf("%s:%d", filepath.Base(filePath), info.Size())
	hash := md5.Sum([]byte(identifier))
	return hex.EncodeToString(hash[:]), nil
}

func getCachePath(hash string) string {
	return filepath.Join(cacheDir, hash+".bin")
}

// LoadCache 使用 mmap 加载索引缓存 (零拷贝)
func LoadCache(filePath string) (*IndexCache, error) {
	hash, err := GetFileHash(filePath)
	if err != nil {
		return nil, err
	}
	
	cachePath := getCachePath(hash)
	
	f, err := os.Open(cachePath)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	
	info, err := f.Stat()
	if err != nil {
		return nil, err
	}
	
	size := int(info.Size())
	if size%RecordSize != 0 {
		return nil, fmt.Errorf("invalid cache file size: %d", size)
	}
	
	// mmap 映射文件
	data, err := unix.Mmap(int(f.Fd()), 0, size, unix.PROT_READ, unix.MAP_SHARED)
	if err != nil {
		return nil, err
	}
	
	count := size / RecordSize
	
	// 零拷贝: 直接将 mmap 内存解释为结构体切片
	records := unsafe.Slice((*models.FrameIndexRecord)(unsafe.Pointer(&data[0])), count)
	
	return &IndexCache{
		data:    data,
		Records: records,
		Count:   count,
	}, nil
}

// Close 释放 mmap 映射
func (c *IndexCache) Close() error {
	if c.data != nil {
		return unix.Munmap(c.data)
	}
	return nil
}

// SaveCache 保存索引到缓存文件
func SaveCache(filePath string, records []models.FrameIndexRecord) error {
	hash, err := GetFileHash(filePath)
	if err != nil {
		return err
	}
	
	cachePath := getCachePath(hash)
	
	f, err := os.Create(cachePath)
	if err != nil {
		return err
	}
	defer f.Close()
	
	// 直接写入结构体内存
	data := unsafe.Slice((*byte)(unsafe.Pointer(&records[0])), len(records)*RecordSize)
	_, err = f.Write(data)
	
	fmt.Printf("[IndexCache] 保存: %s -> %s.bin (%d 条)\n", 
		filepath.Base(filePath), hash, len(records))
	
	return err
}

// CacheExists 检查缓存是否存在
func CacheExists(filePath string) bool {
	hash, err := GetFileHash(filePath)
	if err != nil {
		return false
	}
	_, err = os.Stat(getCachePath(hash))
	return err == nil
}
