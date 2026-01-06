package seetong

import (
	"crypto/md5"
	"encoding/binary"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"syscall"
	"unsafe"
)

// ============================================================================
// Mmap 索引缓存 - 零拷贝实现
// ============================================================================

// 缓存文件格式:
// Header (32 bytes):
//   Magic (4): "SIDX"
//   Version (4): 1
//   RecordCount (4): N
//   FileHash (16): MD5
//   Reserved (4)
// Records (N * RecordSize bytes each) - 与 FrameIndexRecord 内存布局一致

const (
	CacheMagic      = "SIDX"
	CacheVersion    = 2 // 版本 2: 使用与 FrameIndexRecord 相同的布局
	CacheHeaderSize = 32
)

// FrameIndexRecord 的内存大小 (需要与 lib.go 中的定义保持一致)
// FrameType(4) + Channel(4) + FrameSeq(4) + FileOffset(4) + FrameSize(4) + TimestampUs(8) + UnixTs(4) + padding(4) = 36
// 但由于 Go 对齐规则，实际为 40 字节
var frameIndexRecordSize = int(unsafe.Sizeof(FrameIndexRecord{}))

// MmapCache mmap 索引缓存 - 零拷贝
type MmapCache struct {
	data    []byte             // mmap 原始数据
	count   int                // 记录数量
	Records []FrameIndexRecord // 直接指向 mmap 的切片，零拷贝！
}

var (
	cacheDir   string
	cacheDirMu sync.Mutex
)

// SetCacheDir 设置缓存目录
func SetCacheDir(dir string) {
	cacheDirMu.Lock()
	defer cacheDirMu.Unlock()
	cacheDir = dir
	os.MkdirAll(dir, 0755)
}

// GetCacheDir 获取缓存目录
func GetCacheDir() string {
	cacheDirMu.Lock()
	defer cacheDirMu.Unlock()
	if cacheDir == "" {
		// 默认使用工作目录下的 .index_cache
		wd, err := os.Getwd()
		if err != nil {
			wd = "."
		}
		cacheDir = filepath.Join(wd, ".index_cache")
		os.MkdirAll(cacheDir, 0755)
	}
	return cacheDir
}

// getFileHash 计算文件哈希（快速版本）
// DVR 文件大小固定 256MB，不能用来区分
// 使用文件名 + 最后修改时间 + 头部 4KB 内容生成 hash
func getFileHash(filePath string) [16]byte {
	var hash [16]byte

	info, err := os.Stat(filePath)
	if err != nil {
		return hash
	}

	f, err := os.Open(filePath)
	if err != nil {
		return hash
	}
	defer f.Close()

	// 只读取头部 4KB（包含文件头和部分索引，足够区分不同文件）
	buf := make([]byte, 4096)
	n, _ := f.Read(buf)

	h := md5.New()
	h.Write([]byte(filepath.Base(filePath)))                    // 文件名
	binary.Write(h, binary.LittleEndian, info.ModTime().Unix()) // 最后修改时间
	h.Write(buf[:n])                                            // 头部内容
	copy(hash[:], h.Sum(nil))
	return hash
}

// getCachePath 获取缓存文件路径
func getCachePath(recFilePath string) string {
	hash := getFileHash(recFilePath)
	return filepath.Join(GetCacheDir(), fmt.Sprintf("%x.sidx", hash))
}

// CacheExists 检查缓存是否存在且有效
func CacheExists(recFilePath string) bool {
	cachePath := getCachePath(recFilePath)
	info, err := os.Stat(cachePath)
	if err != nil {
		return false
	}
	// 至少要有 header
	return info.Size() >= CacheHeaderSize
}

// SaveMmapCache 保存帧索引到缓存文件
// 直接按 FrameIndexRecord 的内存布局写入，便于后续 mmap 零拷贝读取
func SaveMmapCache(recFilePath string, records []FrameIndexRecord) error {
	if len(records) == 0 {
		return nil
	}

	cachePath := getCachePath(recFilePath)
	fileHash := getFileHash(recFilePath)

	totalSize := CacheHeaderSize + len(records)*frameIndexRecordSize

	f, err := os.Create(cachePath)
	if err != nil {
		return err
	}
	defer f.Close()

	if err := f.Truncate(int64(totalSize)); err != nil {
		return err
	}

	// mmap 写入
	data, err := syscall.Mmap(int(f.Fd()), 0, totalSize,
		syscall.PROT_READ|syscall.PROT_WRITE, syscall.MAP_SHARED)
	if err != nil {
		return err
	}
	defer syscall.Munmap(data)

	// 写入 header
	copy(data[0:4], CacheMagic)
	binary.LittleEndian.PutUint32(data[4:8], CacheVersion)
	binary.LittleEndian.PutUint32(data[8:12], uint32(len(records)))
	copy(data[12:28], fileHash[:])

	// 直接拷贝整个 records 切片的内存到 mmap
	// 这是写入时唯一的拷贝，读取时零拷贝
	recordsBytes := unsafe.Slice((*byte)(unsafe.Pointer(&records[0])), len(records)*frameIndexRecordSize)
	copy(data[CacheHeaderSize:], recordsBytes)

	return nil
}

// LoadMmapCache 从缓存加载帧索引 - 零拷贝！
// 返回的 MmapCache.Records 直接指向 mmap 内存，无需反序列化
// 注意：不再验证原始文件 hash，信任本地缓存
func LoadMmapCache(recFilePath string) (*MmapCache, error) {
	cachePath := getCachePath(recFilePath)

	f, err := os.Open(cachePath)
	if err != nil {
		return nil, err
	}
	// 注意：不能 defer f.Close()，因为 mmap 需要 fd 保持打开
	// 但实际上 mmap 后可以关闭 fd，数据仍然有效

	info, err := f.Stat()
	if err != nil {
		f.Close()
		return nil, err
	}

	if info.Size() < CacheHeaderSize {
		f.Close()
		return nil, fmt.Errorf("cache file too small")
	}

	// mmap 只读
	data, err := syscall.Mmap(int(f.Fd()), 0, int(info.Size()),
		syscall.PROT_READ, syscall.MAP_SHARED)
	if err != nil {
		f.Close()
		return nil, err
	}

	// mmap 完成后可以关闭 fd
	f.Close()

	// 验证 magic
	if string(data[0:4]) != CacheMagic {
		syscall.Munmap(data)
		return nil, fmt.Errorf("invalid cache magic")
	}

	// 验证版本
	version := binary.LittleEndian.Uint32(data[4:8])
	if version != CacheVersion {
		syscall.Munmap(data)
		return nil, fmt.Errorf("cache version mismatch: got %d, want %d", version, CacheVersion)
	}

	count := int(binary.LittleEndian.Uint32(data[8:12]))

	// 不再验证原始文件 hash - 信任本地缓存，避免读取 U 盘
	// 缓存路径已经包含了文件 hash，如果文件变化会生成新的缓存路径

	// 验证大小
	expectedSize := CacheHeaderSize + count*frameIndexRecordSize
	if int(info.Size()) < expectedSize {
		syscall.Munmap(data)
		return nil, fmt.Errorf("cache file truncated")
	}

	cache := &MmapCache{
		data:  data,
		count: count,
	}

	// 零拷贝：直接将 mmap 内存解释为 []FrameIndexRecord
	if count > 0 {
		ptr := unsafe.Pointer(&data[CacheHeaderSize])
		cache.Records = unsafe.Slice((*FrameIndexRecord)(ptr), count)
	}

	return cache, nil
}

// Close 释放 mmap
func (c *MmapCache) Close() {
	if c.data != nil {
		syscall.Munmap(c.data)
		c.data = nil
		c.Records = nil
		c.count = 0
	}
}

// Count 返回记录数量
func (c *MmapCache) Count() int {
	return c.count
}

// ============================================================================
// 带缓存的帧索引解析
// ============================================================================

// ParseTRecFrameIndexWithCache 解析 TRec 文件帧索引（带 mmap 缓存）
// 首次解析后缓存到磁盘，后续直接 mmap 零拷贝读取
func ParseTRecFrameIndexWithCache(recFilePath string) ([]FrameIndexRecord, error) {
	// 尝试从 mmap 缓存加载
	if CacheExists(recFilePath) {
		cache, err := LoadMmapCache(recFilePath)
		if err == nil {
			// 注意：这里返回的是 mmap 内存的切片视图
			// 调用者使用完后应该考虑 cache 的生命周期
			// 但由于我们在 buildSegmentCache 中会复制到 CachedSegmentInfo
			// 所以这里可以安全地复制一份
			records := make([]FrameIndexRecord, cache.Count())
			copy(records, cache.Records)
			cache.Close()
			LogDebug("MmapCache 加载", "file", filepath.Base(recFilePath), "count", len(records))
			return records, nil
		}
		// 缓存无效，继续解析原始文件
	}

	// 解析原始文件
	records, err := ParseTRecFrameIndex(recFilePath)
	if err != nil {
		return nil, err
	}

	// 保存到缓存
	if len(records) > 0 {
		if err := SaveMmapCache(recFilePath, records); err != nil {
			LogWarn("MmapCache 保存失败", "error", err)
		} else {
			LogDebug("MmapCache 保存", "file", filepath.Base(recFilePath), "count", len(records))
		}
	}

	return records, nil
}

// ============================================================================
// 全局缓存管理器 - 保持 mmap 打开以实现真正零拷贝
// ============================================================================

// GlobalMmapManager 全局 mmap 缓存管理器
// 保持所有已加载的 mmap 缓存打开，实现真正的零拷贝访问
type GlobalMmapManager struct {
	caches map[string]*MmapCache // recFilePath -> cache
	mu     sync.RWMutex
}

var globalMmapManager = &GlobalMmapManager{
	caches: make(map[string]*MmapCache),
}

// GetGlobalMmapManager 获取全局管理器
func GetGlobalMmapManager() *GlobalMmapManager {
	return globalMmapManager
}

// GetOrLoad 获取或加载缓存（零拷贝）
func (m *GlobalMmapManager) GetOrLoad(recFilePath string) ([]FrameIndexRecord, error) {
	m.mu.RLock()
	if cache, ok := m.caches[recFilePath]; ok {
		records := cache.Records
		m.mu.RUnlock()
		return records, nil
	}
	m.mu.RUnlock()

	// 需要加载
	m.mu.Lock()
	defer m.mu.Unlock()

	// 双重检查
	if cache, ok := m.caches[recFilePath]; ok {
		return cache.Records, nil
	}

	// 尝试从 mmap 缓存加载
	if CacheExists(recFilePath) {
		cache, err := LoadMmapCache(recFilePath)
		if err == nil {
			m.caches[recFilePath] = cache
			fmt.Printf("[MmapManager] 零拷贝加载: %s (%d 条)\n", filepath.Base(recFilePath), cache.Count())
			return cache.Records, nil
		}
	}

	// 解析原始文件并缓存
	records, err := ParseTRecFrameIndex(recFilePath)
	if err != nil {
		return nil, err
	}

	// 保存到磁盘缓存
	if len(records) > 0 {
		if err := SaveMmapCache(recFilePath, records); err == nil {
			// 重新加载为 mmap
			cache, err := LoadMmapCache(recFilePath)
			if err == nil {
				m.caches[recFilePath] = cache
				fmt.Printf("[MmapManager] 新建缓存: %s (%d 条)\n", filepath.Base(recFilePath), cache.Count())
				return cache.Records, nil
			}
		}
	}

	// 回退：返回内存中的 records
	return records, nil
}

// Close 关闭所有缓存
func (m *GlobalMmapManager) Close() {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, cache := range m.caches {
		cache.Close()
	}
	m.caches = make(map[string]*MmapCache)
}

// Stats 返回统计信息
func (m *GlobalMmapManager) Stats() (int, int) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	totalRecords := 0
	for _, cache := range m.caches {
		totalRecords += cache.Count()
	}
	return len(m.caches), totalRecords
}

// ============================================================================
// VPS 位置缓存
// ============================================================================

const (
	VPSCacheMagic   = "VPOS"
	VPSCacheVersion = 1
)

// getVPSCachePath 获取 VPS 缓存文件路径
func getVPSCachePath(recFilePath string) string {
	hash := getFileHash(recFilePath)
	return filepath.Join(GetCacheDir(), fmt.Sprintf("%x.vpos", hash))
}

// SaveVPSCache 保存 VPS 位置到缓存
func SaveVPSCache(recFilePath string, positions []int) error {
	if len(positions) == 0 {
		return nil
	}

	cachePath := getVPSCachePath(recFilePath)
	fileHash := getFileHash(recFilePath)

	// Header (32) + positions (N * 4 bytes)
	totalSize := CacheHeaderSize + len(positions)*4

	f, err := os.Create(cachePath)
	if err != nil {
		return err
	}
	defer f.Close()

	if err := f.Truncate(int64(totalSize)); err != nil {
		return err
	}

	data, err := syscall.Mmap(int(f.Fd()), 0, totalSize,
		syscall.PROT_READ|syscall.PROT_WRITE, syscall.MAP_SHARED)
	if err != nil {
		return err
	}
	defer syscall.Munmap(data)

	// Header
	copy(data[0:4], VPSCacheMagic)
	binary.LittleEndian.PutUint32(data[4:8], VPSCacheVersion)
	binary.LittleEndian.PutUint32(data[8:12], uint32(len(positions)))
	copy(data[12:28], fileHash[:])

	// Positions
	for i, pos := range positions {
		binary.LittleEndian.PutUint32(data[CacheHeaderSize+i*4:], uint32(pos))
	}

	return nil
}

// LoadVPSCache 从缓存加载 VPS 位置
func LoadVPSCache(recFilePath string) ([]int, error) {
	cachePath := getVPSCachePath(recFilePath)

	info, err := os.Stat(cachePath)
	if err != nil {
		return nil, err
	}

	if info.Size() < CacheHeaderSize {
		return nil, fmt.Errorf("vps cache too small")
	}

	f, err := os.Open(cachePath)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	data, err := syscall.Mmap(int(f.Fd()), 0, int(info.Size()),
		syscall.PROT_READ, syscall.MAP_SHARED)
	if err != nil {
		return nil, err
	}
	defer syscall.Munmap(data)

	// 验证 magic
	if string(data[0:4]) != VPSCacheMagic {
		return nil, fmt.Errorf("invalid vps cache magic")
	}

	// 验证版本
	version := binary.LittleEndian.Uint32(data[4:8])
	if version != VPSCacheVersion {
		return nil, fmt.Errorf("vps cache version mismatch")
	}

	count := int(binary.LittleEndian.Uint32(data[8:12]))

	// 验证文件哈希
	var storedHash [16]byte
	copy(storedHash[:], data[12:28])
	currentHash := getFileHash(recFilePath)
	if storedHash != currentHash {
		return nil, fmt.Errorf("vps cache hash mismatch")
	}

	// 验证大小
	expectedSize := CacheHeaderSize + count*4
	if int(info.Size()) < expectedSize {
		return nil, fmt.Errorf("vps cache truncated")
	}

	// 读取 positions
	positions := make([]int, count)
	for i := 0; i < count; i++ {
		positions[i] = int(binary.LittleEndian.Uint32(data[CacheHeaderSize+i*4:]))
	}

	return positions, nil
}

// VPSCacheExists 检查 VPS 缓存是否存在
func VPSCacheExists(recFilePath string) bool {
	cachePath := getVPSCachePath(recFilePath)
	info, err := os.Stat(cachePath)
	if err != nil {
		return false
	}
	return info.Size() >= CacheHeaderSize
}

// ScanVPSPositionsWithCache 扫描 VPS 位置（带缓存）
func ScanVPSPositionsWithCache(recFilePath string) ([]int, error) {
	// 尝试从缓存加载
	if VPSCacheExists(recFilePath) {
		positions, err := LoadVPSCache(recFilePath)
		if err == nil {
			LogDebug("VPS缓存 加载", "file", filepath.Base(recFilePath), "count", len(positions))
			return positions, nil
		}
	}

	// 扫描原始文件
	positions, err := ScanVPSPositions(recFilePath)
	if err != nil {
		return nil, err
	}

	// 保存到缓存
	if len(positions) > 0 {
		if err := SaveVPSCache(recFilePath, positions); err != nil {
			LogWarn("VPS缓存 保存失败", "error", err)
		} else {
			LogDebug("VPS缓存 保存", "file", filepath.Base(recFilePath), "count", len(positions))
		}
	}

	return positions, nil
}
