package server

import (
	"sort"
	"strconv"
	"sync"

	"github.com/kataras/iris/v12"
)

// DVRCache 单个路径的 DVR 缓存数据
type DVRCache struct {
	dvr  *DVRServer
	hash string // entryCount-fileCount 用于验证缓存有效性
}

// Handlers API 处理器
// 与 Python handlers.py 完全对应
type Handlers struct {
	dvr *DVRServer
	mu  sync.RWMutex

	// 路径历史记录（最多保留 10 个）
	pathHistory []string

	// 路径 -> DVRServer 缓存 Map
	dvrCache map[string]*DVRCache
}

const maxPathHistory = 10

// NewHandlers 创建处理器
func NewHandlers(dvr *DVRServer) *Handlers {
	return &Handlers{
		dvr:         dvr,
		pathHistory: []string{},
		dvrCache:    make(map[string]*DVRCache),
	}
}

// computeDVRHash 计算 DVR 缓存的 hash 值
func computeDVRHash(dvr *DVRServer) string {
	cfg := dvr.GetConfig()
	return strconv.Itoa(cfg.EntryCount) + "-" + strconv.Itoa(cfg.FileCount)
}

// addToPathHistory 添加路径到历史记录
func (h *Handlers) addToPathHistory(path string) {
	h.mu.Lock()
	defer h.mu.Unlock()

	// 移除重复项
	var newHistory []string
	for _, p := range h.pathHistory {
		if p != path {
			newHistory = append(newHistory, p)
		}
	}

	// 添加到开头
	h.pathHistory = append([]string{path}, newHistory...)

	// 限制数量
	if len(h.pathHistory) > maxPathHistory {
		h.pathHistory = h.pathHistory[:maxPathHistory]
	}
}

// ==================== Python 风格 API (v1) ====================
// 与 Python handlers.py 完全对应

// GetConfig 获取配置
// GET /api/v1/config
func (h *Handlers) GetConfig(ctx iris.Context) {
	cfg := h.dvr.GetConfig()

	h.mu.RLock()
	pathHistory := make([]string, len(h.pathHistory))
	copy(pathHistory, h.pathHistory)
	h.mu.RUnlock()

	result := iris.Map{
		"storagePath": cfg.StoragePath,
		"loaded":      cfg.Loaded,
		"timezone":    cfg.Timezone,
		"pathHistory": pathHistory,
	}

	if cfg.Loaded {
		result["entryCount"] = cfg.EntryCount
		result["fileCount"] = cfg.FileCount
		result["cacheStatus"] = h.dvr.GetCacheStatus()
	}

	ctx.JSON(result)
}

// SetConfig 设置配置
// POST /api/v1/config
func (h *Handlers) SetConfig(ctx iris.Context) {
	var req struct {
		StoragePath string `json:"storagePath"`
		Timezone    string `json:"timezone"`
	}

	if err := ctx.ReadJSON(&req); err != nil {
		ctx.StatusCode(400)
		ctx.JSON(iris.Map{"error": "无效的 JSON"})
		return
	}

	result := iris.Map{
		"timezone": h.dvr.GetTimezone(),
	}

	// 更新时区
	if req.Timezone != "" {
		if err := h.dvr.SetTimezone(req.Timezone); err != nil {
			ctx.StatusCode(400)
			ctx.JSON(iris.Map{"error": "无效的时区: " + req.Timezone})
			return
		}
		result["timezone"] = req.Timezone
	}

	// 更新存储路径
	if req.StoragePath != "" {
		h.mu.Lock()

		// 保存当前 DVR 到缓存（如果已加载）
		if h.dvr.IsLoaded() {
			currentPath := h.dvr.GetDVRPath()
			if currentPath != "" && currentPath != req.StoragePath {
				h.dvrCache[currentPath] = &DVRCache{
					dvr:  h.dvr,
					hash: computeDVRHash(h.dvr),
				}
			}
		}

		// 检查缓存中是否有目标路径的数据
		var newDvr *DVRServer
		var fromCache bool

		if cached, ok := h.dvrCache[req.StoragePath]; ok {
			// 创建临时 DVR 来获取当前文件系统的 hash
			tempDvr := NewDVRServer(req.StoragePath)
			if err := tempDvr.Load(); err == nil {
				currentHash := computeDVRHash(tempDvr)
				if currentHash == cached.hash {
					// Hash 一致，使用缓存
					newDvr = cached.dvr
					fromCache = true
					// 从缓存中移除（因为即将成为当前 DVR）
					delete(h.dvrCache, req.StoragePath)
				} else {
					// Hash 不一致，需要重新加载
					newDvr = tempDvr
				}
			} else {
				h.mu.Unlock()
				ctx.StatusCode(400)
				ctx.JSON(iris.Map{
					"storagePath": req.StoragePath,
					"loaded":      false,
					"error":       "无法加载指定路径的 DVR 数据",
				})
				return
			}
		} else {
			// 缓存中没有，需要新加载
			newDvr = NewDVRServer(req.StoragePath)
			if err := newDvr.Load(); err != nil {
				h.mu.Unlock()
				ctx.StatusCode(400)
				ctx.JSON(iris.Map{
					"storagePath": req.StoragePath,
					"loaded":      false,
					"error":       "无法加载指定路径的 DVR 数据",
				})
				return
			}
		}

		// 替换当前实例（不关闭旧的，因为已经缓存了）
		h.dvr = newDvr
		h.mu.Unlock()

		// 添加到路径历史
		h.addToPathHistory(req.StoragePath)

		// 如果不是从缓存恢复，需要构建缓存
		if !fromCache {
			go h.dvr.BuildVPSCache()
		}

		h.mu.RLock()
		pathHistory := make([]string, len(h.pathHistory))
		copy(pathHistory, h.pathHistory)
		h.mu.RUnlock()

		result["storagePath"] = req.StoragePath
		result["loaded"] = true
		result["entryCount"] = len(h.dvr.GetStorage().GetSegments())
		result["cacheStatus"] = h.dvr.GetCacheStatus()
		result["pathHistory"] = pathHistory
		result["fromCache"] = fromCache
	} else {
		cfg := h.dvr.GetConfig()
		result["storagePath"] = cfg.StoragePath
		result["loaded"] = cfg.Loaded
		if cfg.Loaded {
			result["entryCount"] = cfg.EntryCount
			result["fileCount"] = cfg.FileCount
		}
	}

	ctx.JSON(result)
}

// GetCacheStatus 获取缓存构建状态
// GET /api/v1/cache/status
func (h *Handlers) GetCacheStatus(ctx iris.Context) {
	ctx.JSON(h.dvr.GetCacheStatus())
}

// GetDates 获取有录像的日期列表
// GET /api/v1/recordings/dates
func (h *Handlers) GetDates(ctx iris.Context) {
	channelStr := ctx.URLParam("channel")
	var channel *int
	if channelStr != "" {
		ch, _ := strconv.Atoi(channelStr)
		channel = &ch
	}

	datesMap := h.dvr.GetRecordingDates(channel)

	// 转换为排序的列表
	dates := make([]string, 0, len(datesMap))
	for d := range datesMap {
		dates = append(dates, d)
	}
	sort.Strings(dates)

	ctx.JSON(iris.Map{
		"dates":    dates,
		"channels": h.dvr.GetChannels(),
	})
}

// GetRecordings 获取指定日期的录像列表
// GET /api/v1/recordings
func (h *Handlers) GetRecordings(ctx iris.Context) {
	date := ctx.URLParam("date")
	if date == "" {
		ctx.StatusCode(400)
		ctx.JSON(iris.Map{"error": "缺少 date 参数"})
		return
	}

	channelStr := ctx.URLParam("channel")
	var channel *int
	if channelStr != "" {
		ch, _ := strconv.Atoi(channelStr)
		channel = &ch
	}

	recordings := h.dvr.GetRecordings(date, channel)
	if recordings == nil {
		recordings = []RecordingInfo{}
	}

	ctx.JSON(iris.Map{"recordings": recordings})
}

// ==================== 路由注册 ====================

// RegisterRoutes 注册路由
func RegisterRoutes(app *iris.Application, h *Handlers) {
	// Python 风格 API (v1) - 与 Python 版本兼容
	v1 := app.Party("/api/v1")
	{
		v1.Get("/config", h.GetConfig)
		v1.Post("/config", h.SetConfig)
		v1.Get("/cache/status", h.GetCacheStatus)
		v1.Get("/recordings/dates", h.GetDates)
		v1.Get("/recordings", h.GetRecordings)
		v1.Get("/stream", h.HandleWebSocket) // WebSocket 视频流
	}
}
