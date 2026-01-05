package server

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"seetong-dvr/internal/index"
	"seetong-dvr/internal/models"
	"seetong-dvr/internal/tindex"
	"seetong-dvr/internal/trec"
)

// DVRServer DVR 服务器核心
type DVRServer struct {
	basePath string
	loaded   bool
	timezone string

	mu           sync.RWMutex
	indexParser  *tindex.TPSIndexParser
	vpsCache     []models.VPSCacheEntry
	frameIndexes map[int]*index.IndexCache           // mmap 缓存
	frameRecords map[int][]models.FrameIndexRecord   // 内存列表

	// 缓存构建状态
	cacheBuilding bool
	cacheProgress int
	cacheTotal    int
	cacheCurrent  int
}

// NewDVRServer 创建 DVR 服务器
func NewDVRServer(basePath string) *DVRServer {
	return &DVRServer{
		basePath:     basePath,
		timezone:     "Asia/Shanghai",
		frameIndexes: make(map[int]*index.IndexCache),
		frameRecords: make(map[int][]models.FrameIndexRecord),
	}
}

// Load 加载 DVR 数据
func (s *DVRServer) Load() error {
	indexFile := filepath.Join(s.basePath, "TIndex00.tps")
	if _, err := os.Stat(indexFile); os.IsNotExist(err) {
		return fmt.Errorf("索引文件不存在: %s", indexFile)
	}

	// 确保缓存目录存在
	index.SetCacheDir(index.GetCacheDir())
	fmt.Printf("[DVR] 缓存目录: %s\n", index.GetCacheDir())

	// 解析索引文件
	s.indexParser = tindex.NewTPSIndexParser(indexFile)
	if err := s.indexParser.Parse(); err != nil {
		return fmt.Errorf("索引解析失败: %v", err)
	}

	s.loaded = true
	fmt.Printf("[DVR] ✓ 已加载 %d 个索引条目\n", len(s.indexParser.Entries))

	return nil
}

// BuildVPSCache 构建 VPS 缓存
func (s *DVRServer) BuildVPSCache() error {
	if !s.loaded {
		if err := s.Load(); err != nil {
			return err
		}
	}

	total := len(s.indexParser.Entries)
	s.mu.Lock()
	s.cacheBuilding = true
	s.cacheTotal = total
	s.cacheCurrent = 0
	s.cacheProgress = 0
	s.mu.Unlock()

	fmt.Printf("[VPS Cache] 开始构建，共 %d 个文件...\n", total)
	startTime := time.Now()

	var entries []models.VPSCacheEntry
	cachedCount := 0

	for i, entry := range s.indexParser.Entries {
		recFile := s.indexParser.GetRecFile(entry.EntryIndex)

		// 检查文件是否存在
		if _, err := os.Stat(recFile); os.IsNotExist(err) {
			s.updateProgress(i + 1)
			continue
		}

		// 加载帧索引
		records, err := s.loadFrameIndex(entry.EntryIndex, recFile)
		if err != nil || len(records) == 0 {
			s.updateProgress(i + 1)
			continue
		}

		// 获取视频帧的时间范围
		var startUs, endUs int64
		var unixTs int64
		count := 0

		for _, r := range records {
			if r.Channel == models.ChannelVideo1 || r.Channel == models.ChannelVideo2 {
				ts := int64(r.TimestampUs)
				if startUs == 0 || ts < startUs {
					startUs = ts
				}
				if ts > endUs {
					endUs = ts
				}
				if unixTs == 0 {
					unixTs = int64(r.UnixTs)
				}
				count++
			}
		}

		if count > 0 {
			entries = append(entries, models.VPSCacheEntry{
				FileIndex:   entry.EntryIndex,
				RecFile:     recFile,
				StartTimeUs: startUs,
				EndTimeUs:   endUs,
				UnixTs:      unixTs,
				FrameCount:  count,
				Channel:     entry.Channel,
				StartTime:   entry.StartTime,
				EndTime:     entry.EndTime,
			})
			cachedCount++
		}

		s.updateProgress(i + 1)

		// 进度显示
		if (i+1)%10 == 0 || i+1 == total {
			elapsed := time.Since(startTime)
			fmt.Printf("[VPS Cache] 进度: %d/%d (%.1fs)\n", i+1, total, elapsed.Seconds())
		}
	}

	s.mu.Lock()
	s.vpsCache = entries
	s.cacheBuilding = false
	s.cacheProgress = 100
	s.mu.Unlock()

	elapsed := time.Since(startTime)
	fmt.Printf("[VPS Cache] ✓ 缓存完成: %d 个文件，耗时 %.1fs\n", cachedCount, elapsed.Seconds())

	return nil
}

func (s *DVRServer) updateProgress(current int) {
	s.mu.Lock()
	s.cacheCurrent = current
	if s.cacheTotal > 0 {
		s.cacheProgress = current * 100 / s.cacheTotal
	}
	s.mu.Unlock()
}

// loadFrameIndex 加载帧索引 (优先使用 mmap 缓存)
func (s *DVRServer) loadFrameIndex(fileIndex int, recFile string) ([]models.FrameIndexRecord, error) {
	// 先尝试从 mmap 缓存加载
	if index.CacheExists(recFile) {
		cache, err := index.LoadCache(recFile)
		if err == nil {
			s.mu.Lock()
			s.frameIndexes[fileIndex] = cache
			s.mu.Unlock()
			fmt.Printf("[IndexCache] 加载: %s (%d 条)\n", filepath.Base(recFile), cache.Count)
			return cache.Records, nil
		}
	}

	// 解析 TRec 文件
	records, err := trec.ParseFrameIndex(recFile)
	if err != nil {
		return nil, err
	}

	// 保存到缓存
	if len(records) > 0 {
		if err := index.SaveCache(recFile, records); err != nil {
			fmt.Printf("[IndexCache] 保存失败: %v\n", err)
		}

		s.mu.Lock()
		s.frameRecords[fileIndex] = records
		s.mu.Unlock()
	}

	return records, nil
}

// ==================== 查询方法 ====================

// GetVPSCache 获取 VPS 缓存
func (s *DVRServer) GetVPSCache() []models.VPSCacheEntry {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.vpsCache
}

// GetFrameIndex 获取文件的帧索引
func (s *DVRServer) GetFrameIndex(fileIndex int) []models.FrameIndexRecord {
	s.mu.RLock()
	defer s.mu.RUnlock()

	// 先查 mmap 缓存
	if cache, ok := s.frameIndexes[fileIndex]; ok {
		return cache.Records
	}

	// 再查内存列表
	if records, ok := s.frameRecords[fileIndex]; ok {
		return records
	}

	return nil
}

// FindFrameByTimestamp 根据时间戳查找帧
func (s *DVRServer) FindFrameByTimestamp(timestampUs int64) (fileIndex int, frameIdx int, found bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	for _, entry := range s.vpsCache {
		if timestampUs >= entry.StartTimeUs && timestampUs <= entry.EndTimeUs {
			records := s.GetFrameIndex(entry.FileIndex)
			if records == nil {
				continue
			}

			// 二分查找最接近的帧
			idx := sort.Search(len(records), func(i int) bool {
				return int64(records[i].TimestampUs) >= timestampUs
			})

			if idx > 0 && idx < len(records) {
				// 找到最接近的 I 帧
				for i := idx; i >= 0; i-- {
					if records[i].FrameType == models.FrameTypeI &&
						(records[i].Channel == models.ChannelVideo1 ||
							records[i].Channel == models.ChannelVideo2) {
						return entry.FileIndex, i, true
					}
				}
			}
		}
	}

	return 0, 0, false
}

// GetRecordingDates 获取有录像的日期列表
func (s *DVRServer) GetRecordingDates(channel *int) map[string]bool {
	s.mu.RLock()
	defer s.mu.RUnlock()

	dates := make(map[string]bool)
	loc, _ := time.LoadLocation(s.timezone)

	for _, entry := range s.vpsCache {
		if channel != nil && entry.Channel != *channel {
			continue
		}

		// 开始日期
		startDt := time.Unix(entry.StartTime, 0).In(loc)
		dates[startDt.Format("2006-01-02")] = true

		// 结束日期（处理跨午夜）
		endDt := time.Unix(entry.EndTime, 0).In(loc)
		dates[endDt.Format("2006-01-02")] = true
	}

	return dates
}

// GetRecordings 获取指定日期的录像列表
func (s *DVRServer) GetRecordings(date string, channel *int) []RecordingInfo {
	s.mu.RLock()
	defer s.mu.RUnlock()

	loc, _ := time.LoadLocation(s.timezone)

	// 解析日期
	targetDate, err := time.ParseInLocation("2006-01-02", date, loc)
	if err != nil {
		return nil
	}

	dayStart := targetDate
	dayEnd := targetDate.Add(24 * time.Hour)

	startTs := dayStart.Unix()
	endTs := dayEnd.Unix()

	var recordings []RecordingInfo

	for _, entry := range s.vpsCache {
		if channel != nil && entry.Channel != *channel {
			continue
		}

		// 检查时间范围是否有交集
		if entry.StartTime < endTs && entry.EndTime > startTs {
			actualStart := max(entry.StartTime, startTs)
			actualEnd := min(entry.EndTime, endTs)

			startDt := time.Unix(actualStart, 0).In(loc)
			endDt := time.Unix(actualEnd, 0).In(loc)

			recordings = append(recordings, RecordingInfo{
				ID:             entry.FileIndex,
				Channel:        entry.Channel,
				Start:          startDt.Format("15:04:05"),
				End:            endDt.Format("15:04:05"),
				StartTimestamp: actualStart,
				EndTimestamp:   actualEnd,
				Duration:       actualEnd - actualStart,
				FrameCount:     entry.FrameCount,
			})
		}
	}

	// 按开始时间排序
	sort.Slice(recordings, func(i, j int) bool {
		return recordings[i].StartTimestamp < recordings[j].StartTimestamp
	})

	return recordings
}

// GetChannels 获取所有通道
func (s *DVRServer) GetChannels() []int {
	s.mu.RLock()
	defer s.mu.RUnlock()

	channelMap := make(map[int]bool)
	for _, entry := range s.vpsCache {
		channelMap[entry.Channel] = true
	}

	var channels []int
	for ch := range channelMap {
		channels = append(channels, ch)
	}
	sort.Ints(channels)
	return channels
}

// GetCacheStatus 获取缓存构建状态
func (s *DVRServer) GetCacheStatus() CacheStatus {
	s.mu.RLock()
	defer s.mu.RUnlock()

	if !s.loaded {
		return CacheStatus{
			Status:   "not_loaded",
			Progress: 0,
			Total:    0,
			Current:  0,
			Cached:   0,
		}
	}

	if s.cacheBuilding {
		return CacheStatus{
			Status:   "building",
			Progress: s.cacheProgress,
			Total:    s.cacheTotal,
			Current:  s.cacheCurrent,
			Cached:   len(s.vpsCache),
		}
	}

	return CacheStatus{
		Status:   "ready",
		Progress: 100,
		Total:    len(s.indexParser.Entries),
		Current:  len(s.indexParser.Entries),
		Cached:   len(s.vpsCache),
	}
}

// GetConfig 获取配置
func (s *DVRServer) GetConfig() Config {
	s.mu.RLock()
	defer s.mu.RUnlock()

	cfg := Config{
		StoragePath: s.basePath,
		Loaded:      s.loaded,
		Timezone:    s.timezone,
	}

	if s.loaded && s.indexParser != nil {
		cfg.EntryCount = len(s.indexParser.Entries)
		cfg.FileCount = len(s.vpsCache)
	}

	return cfg
}

// SetTimezone 设置时区
func (s *DVRServer) SetTimezone(tz string) error {
	if _, err := time.LoadLocation(tz); err != nil {
		return err
	}
	s.mu.Lock()
	s.timezone = tz
	s.mu.Unlock()
	return nil
}

// Close 关闭服务器，释放 mmap 资源
func (s *DVRServer) Close() {
	s.mu.Lock()
	defer s.mu.Unlock()

	for _, cache := range s.frameIndexes {
		cache.Close()
	}
}

// ==================== 数据类型 ====================

// RecordingInfo 录像信息
type RecordingInfo struct {
	ID             int    `json:"id"`
	Channel        int    `json:"channel"`
	Start          string `json:"start"`
	End            string `json:"end"`
	StartTimestamp int64  `json:"startTimestamp"`
	EndTimestamp   int64  `json:"endTimestamp"`
	Duration       int64  `json:"duration"`
	FrameCount     int    `json:"frameCount"`
}

// CacheStatus 缓存状态
type CacheStatus struct {
	Status   string `json:"status"`
	Progress int    `json:"progress"`
	Total    int    `json:"total"`
	Current  int    `json:"current"`
	Cached   int    `json:"cached"`
}

// Config 配置
type Config struct {
	StoragePath string `json:"storagePath"`
	Loaded      bool   `json:"loaded"`
	Timezone    string `json:"timezone"`
	EntryCount  int    `json:"entryCount,omitempty"`
	FileCount   int    `json:"fileCount,omitempty"`
}
