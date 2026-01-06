package server

import (
	"fmt"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"seetong-dvr/internal/seetong"
)

// DVRServer DVR 服务器核心类
// 与 Python dvr_server.py 完全对应
type DVRServer struct {
	dvrPath  string
	storage  *seetong.TPSStorage
	loaded   bool
	timezone string
	mu       sync.RWMutex

	// 音频采样率
	audioSampleRate int
}

// NewDVRServer 创建 DVR 服务器
func NewDVRServer(dvrPath string) *DVRServer {
	return &DVRServer{
		dvrPath:         dvrPath,
		timezone:        "Asia/Shanghai",
		audioSampleRate: 8000,
	}
}

// Load 加载 DVR 数据
func (s *DVRServer) Load() error {
	s.storage = seetong.NewTPSStorage(s.dvrPath)
	if err := s.storage.Load(); err != nil {
		return err
	}

	s.loaded = true
	fmt.Printf("✓ 发现 %d 个段落索引\n", len(s.storage.GetSegments()))
	return nil
}

// IsLoaded 是否已加载
func (s *DVRServer) IsLoaded() bool {
	return s.loaded
}

// GetStorage 获取存储管理器
func (s *DVRServer) GetStorage() *seetong.TPSStorage {
	return s.storage
}

// GetDVRPath 获取 DVR 路径
func (s *DVRServer) GetDVRPath() string {
	return s.dvrPath
}

// BuildVPSCache 构建帧索引和 VPS 缓存
func (s *DVRServer) BuildVPSCache() {
	if !s.loaded || s.storage == nil {
		return
	}

	segments := s.storage.GetSegments()
	fileIndices := make([]int, len(segments))
	for i, seg := range segments {
		fileIndices[i] = seg.FileIndex
	}

	fmt.Printf("[Cache] 开始构建缓存，共 %d 个文件...\n", len(fileIndices))
	startTime := time.Now()

	cachedCount := s.storage.BuildCache(fileIndices, func(current, total, fileIndex int) {
		if current%10 == 0 || current == total {
			elapsed := time.Since(startTime)
			fmt.Printf("[Cache] 进度: %d/%d (%.1fs)\n", current, total, elapsed.Seconds())
		}
	})

	elapsed := time.Since(startTime)
	fmt.Printf("[Cache] ✓ 缓存完成: %d 个文件，耗时 %.1fs\n", cachedCount, elapsed.Seconds())
}

// GetCacheStatus 获取缓存构建状态
func (s *DVRServer) GetCacheStatus() CacheStatus {
	if !s.loaded || s.storage == nil {
		return CacheStatus{
			Status:   "not_loaded",
			Progress: 0,
			Total:    0,
			Current:  0,
			Cached:   0,
		}
	}

	status := s.storage.GetCacheStatus()
	building, _ := status["building"].(bool)
	progress, _ := status["progress"].(int)
	totalSegments, _ := status["total_segments"].(int)
	cachedSegments, _ := status["cached_segments"].(int)

	if building {
		return CacheStatus{
			Status:   "building",
			Progress: progress,
			Total:    totalSegments,
			Current:  cachedSegments,
			Cached:   cachedSegments,
		}
	}

	return CacheStatus{
		Status:   "ready",
		Progress: 100,
		Total:    totalSegments,
		Current:  cachedSegments,
		Cached:   cachedSegments,
	}
}

// GetRecordingDates 获取有录像的日期列表（只返回已缓存的段落）
// 与 Python dvr_server.get_recording_dates 对应
func (s *DVRServer) GetRecordingDates(channel *int) map[string]bool {
	if !s.loaded || s.storage == nil {
		return make(map[string]bool)
	}

	segments := s.storage.GetCachedSegments()
	if channel != nil {
		var filtered []*seetong.SegmentRecord
		for _, seg := range segments {
			if seg.Channel == *channel {
				filtered = append(filtered, seg)
			}
		}
		segments = filtered
	}

	loc, _ := time.LoadLocation(s.timezone)
	dates := make(map[string]bool)

	for _, seg := range segments {
		dt := time.Unix(seg.StartTime, 0).In(loc)
		dates[dt.Format("2006-01-02")] = true

		dtEnd := time.Unix(seg.EndTime, 0).In(loc)
		dates[dtEnd.Format("2006-01-02")] = true
	}

	return dates
}

// GetRecordings 获取指定日期的录像列表（只返回已缓存的段落）
// 与 Python dvr_server.get_recordings 对应
func (s *DVRServer) GetRecordings(date string, channel *int) []RecordingInfo {
	if !s.loaded || s.storage == nil {
		return nil
	}

	loc, _ := time.LoadLocation(s.timezone)

	targetDate, err := time.ParseInLocation("2006-01-02", date, loc)
	if err != nil {
		return nil
	}

	dayStart := targetDate
	dayEnd := targetDate.Add(24 * time.Hour)
	startTs := dayStart.Unix()
	endTs := dayEnd.Unix()

	var recordings []RecordingInfo

	for _, seg := range s.storage.GetCachedSegments() {
		if channel != nil && seg.Channel != *channel {
			continue
		}

		if seg.StartTime < endTs && seg.EndTime > startTs {
			actualStart := max(seg.StartTime, startTs)
			actualEnd := min(seg.EndTime, endTs)

			startDt := time.Unix(actualStart, 0).In(loc)
			endDt := time.Unix(actualEnd, 0).In(loc)

			recordings = append(recordings, RecordingInfo{
				ID:             seg.FileIndex,
				Channel:        seg.Channel,
				Start:          startDt.Format("15:04:05"),
				End:            endDt.Format("15:04:05"),
				StartTimestamp: actualStart,
				EndTimestamp:   actualEnd,
				Duration:       actualEnd - actualStart,
				FrameCount:     seg.FrameCount,
			})
		}
	}

	sort.Slice(recordings, func(i, j int) bool {
		return recordings[i].StartTimestamp < recordings[j].StartTimestamp
	})

	return recordings
}

// GetChannels 获取所有通道
func (s *DVRServer) GetChannels() []int {
	if !s.loaded || s.storage == nil {
		return []int{}
	}

	channelMap := make(map[int]bool)
	for _, seg := range s.storage.GetCachedSegments() {
		channelMap[seg.Channel] = true
	}

	channels := make([]int, 0, len(channelMap))
	for ch := range channelMap {
		channels = append(channels, ch)
	}
	sort.Ints(channels)
	return channels
}

// GetConfig 获取配置
func (s *DVRServer) GetConfig() Config {
	s.mu.RLock()
	defer s.mu.RUnlock()

	cfg := Config{
		StoragePath: s.dvrPath,
		Loaded:      s.loaded,
		Timezone:    s.timezone,
	}

	if s.loaded && s.storage != nil {
		cfg.EntryCount = len(s.storage.GetSegments())
		// 统计 TRec 文件数量
		matches, _ := filepath.Glob(filepath.Join(s.dvrPath, "TRec*.tps"))
		cfg.FileCount = len(matches)
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

// GetTimezone 获取时区
func (s *DVRServer) GetTimezone() string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.timezone
}

// Close 关闭服务器
func (s *DVRServer) Close() {
	// 目前无需清理
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

func max(a, b int64) int64 {
	if a > b {
		return a
	}
	return b
}

func min(a, b int64) int64 {
	if a < b {
		return a
	}
	return b
}
