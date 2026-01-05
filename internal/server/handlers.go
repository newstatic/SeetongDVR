package server

import (
	"encoding/binary"
	"sort"
	"strconv"

	"seetong-dvr/internal/models"
	"seetong-dvr/internal/trec"

	"github.com/kataras/iris/v12"
)

// Handlers API 处理器
type Handlers struct {
	dvr *DVRServer
}

// NewHandlers 创建处理器
func NewHandlers(dvr *DVRServer) *Handlers {
	return &Handlers{dvr: dvr}
}

// ==================== Python 风格 API (v1) ====================

// GetConfig 获取配置
func (h *Handlers) GetConfig(ctx iris.Context) {
	cfg := h.dvr.GetConfig()
	result := iris.Map{
		"storagePath": cfg.StoragePath,
		"loaded":      cfg.Loaded,
		"timezone":    cfg.Timezone,
	}
	if cfg.Loaded {
		result["entryCount"] = cfg.EntryCount
		result["fileCount"] = cfg.FileCount
		result["cacheStatus"] = h.dvr.GetCacheStatus()
	}
	ctx.JSON(result)
}

// SetConfig 设置配置
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
		"timezone": h.dvr.timezone,
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

	// 更新存储路径（需要重新加载）
	if req.StoragePath != "" {
		// 创建新的 DVR 服务器实例
		newDvr := NewDVRServer(req.StoragePath)
		if err := newDvr.Load(); err != nil {
			ctx.StatusCode(400)
			ctx.JSON(iris.Map{
				"storagePath": req.StoragePath,
				"loaded":      false,
				"error":       "无法加载指定路径的 DVR 数据",
			})
			return
		}

		// 替换当前实例
		h.dvr.Close()
		*h.dvr = *newDvr

		// 在后台构建缓存
		go h.dvr.BuildVPSCache()

		result["storagePath"] = req.StoragePath
		result["loaded"] = true
		result["entryCount"] = len(h.dvr.indexParser.Entries)
		result["cacheStatus"] = h.dvr.GetCacheStatus()
	} else {
		result["storagePath"] = h.dvr.basePath
		result["loaded"] = h.dvr.loaded
		if h.dvr.loaded && h.dvr.indexParser != nil {
			result["entryCount"] = len(h.dvr.indexParser.Entries)
			result["fileCount"] = len(h.dvr.vpsCache)
		}
	}

	ctx.JSON(result)
}

// GetCacheStatus 获取缓存构建状态
func (h *Handlers) GetCacheStatus(ctx iris.Context) {
	ctx.JSON(h.dvr.GetCacheStatus())
}

// GetDates 获取有录像的日期列表
func (h *Handlers) GetDates(ctx iris.Context) {
	channelStr := ctx.URLParam("channel")
	var channel *int
	if channelStr != "" {
		ch, _ := strconv.Atoi(channelStr)
		channel = &ch
	}

	datesMap := h.dvr.GetRecordingDates(channel)

	// 转换为排序的列表
	var dates []string
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
	ctx.JSON(iris.Map{"recordings": recordings})
}

// ==================== Go 风格 API ====================

// GetVPSCache 获取 VPS 缓存列表
func (h *Handlers) GetVPSCache(ctx iris.Context) {
	entries := h.dvr.GetVPSCache()

	type Response struct {
		FileIndex   int    `json:"file_index"`
		RecFile     string `json:"rec_file"`
		StartTimeUs int64  `json:"start_time_us"`
		EndTimeUs   int64  `json:"end_time_us"`
		UnixTs      int64  `json:"unix_ts"`
		FrameCount  int    `json:"frame_count"`
	}

	var result []Response
	for _, e := range entries {
		result = append(result, Response{
			FileIndex:   e.FileIndex,
			RecFile:     e.RecFile,
			StartTimeUs: e.StartTimeUs,
			EndTimeUs:   e.EndTimeUs,
			UnixTs:      e.UnixTs,
			FrameCount:  e.FrameCount,
		})
	}

	ctx.JSON(result)
}

// GetFrameIndex 获取帧索引
func (h *Handlers) GetFrameIndex(ctx iris.Context) {
	fileIndex, err := ctx.Params().GetInt("file_index")
	if err != nil {
		ctx.StatusCode(400)
		ctx.JSON(iris.Map{"error": "invalid file_index"})
		return
	}

	records := h.dvr.GetFrameIndex(fileIndex)
	if records == nil {
		ctx.StatusCode(404)
		ctx.JSON(iris.Map{"error": "frame index not found"})
		return
	}

	type FrameInfo struct {
		FrameType   uint32 `json:"frame_type"`
		Channel     uint32 `json:"channel"`
		FrameSeq    uint32 `json:"frame_seq"`
		FileOffset  uint32 `json:"file_offset"`
		FrameSize   uint32 `json:"frame_size"`
		TimestampUs uint64 `json:"timestamp_us"`
		UnixTs      uint32 `json:"unix_ts"`
	}

	result := make([]FrameInfo, len(records))
	for i, r := range records {
		result[i] = FrameInfo{
			FrameType:   r.FrameType,
			Channel:     r.Channel,
			FrameSeq:    r.FrameSeq,
			FileOffset:  r.FileOffset,
			FrameSize:   r.FrameSize,
			TimestampUs: r.TimestampUs,
			UnixTs:      r.UnixTs,
		}
	}

	ctx.JSON(result)
}

// GetFrame 获取帧数据
func (h *Handlers) GetFrame(ctx iris.Context) {
	fileIndex, err := ctx.Params().GetInt("file_index")
	if err != nil {
		ctx.StatusCode(400)
		ctx.JSON(iris.Map{"error": "invalid file_index"})
		return
	}

	frameIdx, err := ctx.Params().GetInt("frame_idx")
	if err != nil {
		ctx.StatusCode(400)
		ctx.JSON(iris.Map{"error": "invalid frame_idx"})
		return
	}

	// 获取文件路径
	entries := h.dvr.GetVPSCache()
	var recFile string
	for _, e := range entries {
		if e.FileIndex == fileIndex {
			recFile = e.RecFile
			break
		}
	}

	if recFile == "" {
		ctx.StatusCode(404)
		ctx.JSON(iris.Map{"error": "file not found"})
		return
	}

	// 获取帧索引
	records := h.dvr.GetFrameIndex(fileIndex)
	if records == nil || frameIdx >= len(records) {
		ctx.StatusCode(404)
		ctx.JSON(iris.Map{"error": "frame not found"})
		return
	}

	record := records[frameIdx]

	// 读取帧数据
	data, err := trec.ReadFrame(recFile, record.FileOffset, record.FrameSize)
	if err != nil {
		ctx.StatusCode(500)
		ctx.JSON(iris.Map{"error": err.Error()})
		return
	}

	ctx.ContentType("application/octet-stream")
	ctx.Write(data)
}

// GetFramesBatch 批量获取帧数据
func (h *Handlers) GetFramesBatch(ctx iris.Context) {
	fileIndex, err := ctx.Params().GetInt("file_index")
	if err != nil {
		ctx.StatusCode(400)
		ctx.JSON(iris.Map{"error": "invalid file_index"})
		return
	}

	startIdx, _ := strconv.Atoi(ctx.URLParam("start"))
	count, _ := strconv.Atoi(ctx.URLParam("count"))
	if count <= 0 {
		count = 30
	}
	if count > 300 {
		count = 300
	}

	// 获取文件路径
	entries := h.dvr.GetVPSCache()
	var recFile string
	for _, e := range entries {
		if e.FileIndex == fileIndex {
			recFile = e.RecFile
			break
		}
	}

	if recFile == "" {
		ctx.StatusCode(404)
		ctx.JSON(iris.Map{"error": "file not found"})
		return
	}

	// 获取帧索引
	records := h.dvr.GetFrameIndex(fileIndex)
	if records == nil {
		ctx.StatusCode(404)
		ctx.JSON(iris.Map{"error": "frame index not found"})
		return
	}

	// 筛选视频帧
	var videoFrames []int
	for i, r := range records {
		if r.Channel == models.ChannelVideo1 || r.Channel == models.ChannelVideo2 {
			videoFrames = append(videoFrames, i)
		}
	}

	if startIdx >= len(videoFrames) {
		ctx.StatusCode(404)
		ctx.JSON(iris.Map{"error": "start index out of range"})
		return
	}

	endIdx := startIdx + count
	if endIdx > len(videoFrames) {
		endIdx = len(videoFrames)
	}

	// 读取帧数据，构建批量响应
	// 格式: [4字节帧数] + [每帧: 4字节大小 + 8字节时间戳 + 数据]
	var result []byte
	frameCount := uint32(endIdx - startIdx)
	result = binary.LittleEndian.AppendUint32(result, frameCount)

	for i := startIdx; i < endIdx; i++ {
		frameIdx := videoFrames[i]
		record := records[frameIdx]

		data, err := trec.ReadFrame(recFile, record.FileOffset, record.FrameSize)
		if err != nil {
			continue
		}

		result = binary.LittleEndian.AppendUint32(result, uint32(len(data)))
		result = binary.LittleEndian.AppendUint64(result, record.TimestampUs)
		result = append(result, data...)
	}

	ctx.ContentType("application/octet-stream")
	ctx.Write(result)
}

// SeekToTimestamp 跳转到指定时间戳
func (h *Handlers) SeekToTimestamp(ctx iris.Context) {
	timestampUs, err := strconv.ParseInt(ctx.URLParam("ts"), 10, 64)
	if err != nil {
		ctx.StatusCode(400)
		ctx.JSON(iris.Map{"error": "invalid timestamp"})
		return
	}

	fileIndex, frameIdx, found := h.dvr.FindFrameByTimestamp(timestampUs)
	if !found {
		ctx.StatusCode(404)
		ctx.JSON(iris.Map{"error": "timestamp not found"})
		return
	}

	ctx.JSON(iris.Map{
		"file_index": fileIndex,
		"frame_idx":  frameIdx,
	})
}

// GetStats 获取统计信息
func (h *Handlers) GetStats(ctx iris.Context) {
	entries := h.dvr.GetVPSCache()

	totalFrames := 0
	for _, e := range entries {
		totalFrames += e.FrameCount
	}

	ctx.JSON(iris.Map{
		"file_count":   len(entries),
		"total_frames": totalFrames,
	})
}

// DecodeAudio G.711 μ-law 解码
func (h *Handlers) DecodeAudio(ctx iris.Context) {
	fileIndex, err := ctx.Params().GetInt("file_index")
	if err != nil {
		ctx.StatusCode(400)
		ctx.JSON(iris.Map{"error": "invalid file_index"})
		return
	}

	startIdx, _ := strconv.Atoi(ctx.URLParam("start"))
	count, _ := strconv.Atoi(ctx.URLParam("count"))
	if count <= 0 {
		count = 50
	}

	// 获取文件路径
	entries := h.dvr.GetVPSCache()
	var recFile string
	for _, e := range entries {
		if e.FileIndex == fileIndex {
			recFile = e.RecFile
			break
		}
	}

	if recFile == "" {
		ctx.StatusCode(404)
		ctx.JSON(iris.Map{"error": "file not found"})
		return
	}

	// 获取帧索引
	records := h.dvr.GetFrameIndex(fileIndex)
	if records == nil {
		ctx.StatusCode(404)
		ctx.JSON(iris.Map{"error": "frame index not found"})
		return
	}

	// 筛选音频帧
	var audioFrames []int
	for i, r := range records {
		if r.Channel == models.ChannelAudio {
			audioFrames = append(audioFrames, i)
		}
	}

	if startIdx >= len(audioFrames) {
		ctx.ContentType("audio/wav")
		ctx.Write(createWavHeader(0))
		return
	}

	endIdx := startIdx + count
	if endIdx > len(audioFrames) {
		endIdx = len(audioFrames)
	}

	// 读取并解码音频数据
	var pcmData []byte
	for i := startIdx; i < endIdx; i++ {
		frameIdx := audioFrames[i]
		record := records[frameIdx]

		data, err := trec.ReadFrame(recFile, record.FileOffset, record.FrameSize)
		if err != nil {
			continue
		}

		// G.711 μ-law 解码
		for _, b := range data {
			sample := ulawDecode(b)
			pcmData = append(pcmData, byte(sample), byte(sample>>8))
		}
	}

	// 创建 WAV 文件
	wav := createWav(pcmData)
	ctx.ContentType("audio/wav")
	ctx.Write(wav)
}

// ==================== 工具函数 ====================

// G.711 μ-law 解码表
var ulawTable = func() [256]int16 {
	var table [256]int16
	for i := 0; i < 256; i++ {
		ulawByte := byte(i)
		ulawByte = ^ulawByte
		sign := (ulawByte & 0x80) != 0
		exponent := int((ulawByte >> 4) & 0x07)
		mantissa := int(ulawByte & 0x0F)

		sample := ((mantissa << 3) + 0x84) << exponent
		sample -= 0x84

		if sign {
			table[i] = int16(-sample)
		} else {
			table[i] = int16(sample)
		}
	}
	return table
}()

func ulawDecode(b byte) int16 {
	return ulawTable[b]
}

func createWavHeader(dataSize int) []byte {
	header := make([]byte, 44)
	copy(header[0:4], "RIFF")
	binary.LittleEndian.PutUint32(header[4:8], uint32(36+dataSize))
	copy(header[8:12], "WAVE")
	copy(header[12:16], "fmt ")
	binary.LittleEndian.PutUint32(header[16:20], 16)    // fmt chunk size
	binary.LittleEndian.PutUint16(header[20:22], 1)     // PCM
	binary.LittleEndian.PutUint16(header[22:24], 1)     // mono
	binary.LittleEndian.PutUint32(header[24:28], 8000)  // sample rate
	binary.LittleEndian.PutUint32(header[28:32], 16000) // byte rate
	binary.LittleEndian.PutUint16(header[32:34], 2)     // block align
	binary.LittleEndian.PutUint16(header[34:36], 16)    // bits per sample
	copy(header[36:40], "data")
	binary.LittleEndian.PutUint32(header[40:44], uint32(dataSize))
	return header
}

func createWav(pcmData []byte) []byte {
	header := createWavHeader(len(pcmData))
	return append(header, pcmData...)
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

	// Go 风格 API - 更底层的访问
	api := app.Party("/api")
	{
		api.Get("/vps_cache", h.GetVPSCache)
		api.Get("/frame_index/{file_index:int}", h.GetFrameIndex)
		api.Get("/frame/{file_index:int}/{frame_idx:int}", h.GetFrame)
		api.Get("/frames/{file_index:int}", h.GetFramesBatch)
		api.Get("/seek", h.SeekToTimestamp)
		api.Get("/stats", h.GetStats)
		api.Get("/audio/{file_index:int}", h.DecodeAudio)
	}
}
