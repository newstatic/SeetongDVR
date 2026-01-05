package server

import (
	"encoding/binary"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"sync"
	"time"

	"seetong-dvr/internal/models"
	"seetong-dvr/internal/trec"

	"github.com/gorilla/websocket"
	"github.com/kataras/iris/v12"
)

var upgrader = websocket.Upgrader{
	ReadBufferSize:  1024,
	WriteBufferSize: 64 * 1024,
	CheckOrigin:     func(r *http.Request) bool { return true },
}

// WSMessage WebSocket 消息
type WSMessage struct {
	Action    string  `json:"action"`
	Channel   int     `json:"channel"`
	Timestamp int64   `json:"timestamp"`
	Speed     float64 `json:"speed"`
	Audio     bool    `json:"audio"`
}

// StreamSession 流会话
type StreamSession struct {
	ws       *websocket.Conn
	dvr      *DVRServer
	stopChan chan struct{}
	mu       sync.Mutex
	running  bool
}

// HandleWebSocket WebSocket 处理器
func (h *Handlers) HandleWebSocket(ctx iris.Context) {
	ws, err := upgrader.Upgrade(ctx.ResponseWriter(), ctx.Request(), nil)
	if err != nil {
		fmt.Printf("[WS] Upgrade error: %v\n", err)
		return
	}
	defer ws.Close()

	session := &StreamSession{
		ws:       ws,
		dvr:      h.dvr,
		stopChan: make(chan struct{}),
	}

	sessionID := fmt.Sprintf("%p", ws)
	fmt.Printf("[WS] 新连接: %s\n", sessionID)

	// 发送连接确认
	session.sendJSON(map[string]interface{}{
		"type":    "connected",
		"message": "WebSocket 连接成功",
	})

	for {
		_, message, err := ws.ReadMessage()
		if err != nil {
			if websocket.IsUnexpectedCloseError(err, websocket.CloseGoingAway, websocket.CloseAbnormalClosure) {
				fmt.Printf("[WS] Error: %v\n", err)
			}
			break
		}

		fmt.Printf("[WS] 收到消息: %s\n", string(message))

		var msg WSMessage
		if err := json.Unmarshal(message, &msg); err != nil {
			fmt.Printf("[WS] JSON 解析错误: %v\n", err)
			session.sendJSON(map[string]interface{}{"error": "无效的 JSON"})
			continue
		}

		fmt.Printf("[WS] 解析结果: action=%s, ch=%d, ts=%d, speed=%.1f, audio=%v\n",
			msg.Action, msg.Channel, msg.Timestamp, msg.Speed, msg.Audio)

		switch msg.Action {
		case "play":
			session.stop()
			if msg.Speed == 0 {
				msg.Speed = 1.0
			}
			fmt.Printf("[WS] 开始播放: ch=%d, ts=%d, speed=%.1f, audio=%v\n",
				msg.Channel, msg.Timestamp, msg.Speed, msg.Audio)
			if msg.Audio {
				go session.streamVideoWithAudio(msg.Channel, msg.Timestamp, msg.Speed)
			} else {
				go session.streamVideo(msg.Channel, msg.Timestamp, msg.Speed)
			}

		case "pause":
			session.stop()
			fmt.Printf("[WS] 暂停\n")

		case "seek":
			session.stop()
			if msg.Speed == 0 {
				msg.Speed = 1.0
			}
			if msg.Audio {
				go session.streamVideoWithAudio(msg.Channel, msg.Timestamp, msg.Speed)
			} else {
				go session.streamVideo(msg.Channel, msg.Timestamp, msg.Speed)
			}
			fmt.Printf("[WS] Seek: ts=%d, audio=%v\n", msg.Timestamp, msg.Audio)

		case "speed":
			fmt.Printf("[WS] 速度变更: %.1fx\n", msg.Speed)
		}
	}

	session.stop()
	fmt.Printf("[WS] 断开连接: %s\n", sessionID)
}

func (s *StreamSession) stop() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.running {
		close(s.stopChan)
		s.stopChan = make(chan struct{})
		s.running = false
	}
}

func (s *StreamSession) sendJSON(v interface{}) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.ws.WriteJSON(v)
}

func (s *StreamSession) sendBytes(data []byte) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.ws.WriteMessage(websocket.BinaryMessage, data)
}

// streamVideo 流式传输视频（无音频）
func (s *StreamSession) streamVideo(channel int, startTimestamp int64, speed float64) {
	s.mu.Lock()
	s.running = true
	stopChan := s.stopChan
	s.mu.Unlock()

	defer func() {
		s.mu.Lock()
		s.running = false
		s.mu.Unlock()
	}()

	fmt.Printf("[Stream] 请求: ch=%d, ts=%d, speed=%.1f\n", channel, startTimestamp, speed)

	// 查找录像
	entry := s.findEntryForTime(startTimestamp, channel)
	if entry == nil {
		fmt.Printf("[Stream] 错误: 未找到指定时间的录像 (ts=%d, ch=%d)\n", startTimestamp, channel)
		s.sendJSON(map[string]interface{}{"error": "未找到指定时间的录像"})
		return
	}
	fmt.Printf("[Stream] 找到录像: FileIndex=%d, RecFile=%s\n", entry.FileIndex, entry.RecFile)

	records := s.dvr.GetFrameIndex(entry.FileIndex)
	if records == nil {
		fmt.Printf("[Stream] 错误: 帧索引不存在 (FileIndex=%d)\n", entry.FileIndex)
		s.sendJSON(map[string]interface{}{"error": "帧索引不存在"})
		return
	}
	fmt.Printf("[Stream] 帧索引: %d 条记录\n", len(records))

	// 筛选视频帧
	var videoFrames []int
	for i, r := range records {
		if r.Channel == models.ChannelVideo1 || r.Channel == models.ChannelVideo2 {
			videoFrames = append(videoFrames, i)
		}
	}

	if len(videoFrames) == 0 {
		s.sendJSON(map[string]interface{}{"error": "未找到视频帧"})
		return
	}

	// 查找起始帧（I帧）
	startIdx := 0
	for i, idx := range videoFrames {
		if records[idx].UnixTs >= uint32(startTimestamp) {
			// 向前找 I 帧
			for j := i; j >= 0; j-- {
				if records[videoFrames[j]].FrameType == models.FrameTypeI {
					startIdx = j
					break
				}
			}
			break
		}
	}

	actualStartTime := int64(records[videoFrames[startIdx]].UnixTs)

	// 发送 stream_start
	s.sendJSON(map[string]interface{}{
		"type":            "stream_start",
		"channel":         channel,
		"startTime":       entry.StartTime,
		"endTime":         entry.EndTime,
		"actualStartTime": actualStartTime,
		"hasAudio":        false,
	})

	// 打开文件
	f, err := os.Open(entry.RecFile)
	if err != nil {
		s.sendJSON(map[string]interface{}{"error": err.Error()})
		return
	}
	defer f.Close()

	frameInterval := time.Duration(float64(time.Second) / 25.0 / speed)
	ticker := time.NewTicker(frameInterval)
	defer ticker.Stop()

	frameCount := 0
	lastLogTime := time.Now()

	for i := startIdx; i < len(videoFrames); i++ {
		select {
		case <-stopChan:
			return
		case <-ticker.C:
		}

		idx := videoFrames[i]
		record := records[idx]

		// 读取帧数据
		data := make([]byte, record.FrameSize)
		if _, err := f.ReadAt(data, int64(record.FileOffset)); err != nil {
			continue
		}

		// 解析 NAL 单元并发送
		nalUnits := parseNALUnits(data)
		for _, nal := range nalUnits {
			nalData := stripStartCode(data[nal.offset : nal.offset+nal.size])
			s.sendVideoFrame(nalData, nal.nalType, int64(record.UnixTs)*1000)
		}

		frameCount++

		// 日志
		if time.Since(lastLogTime) >= time.Second {
			fps := float64(frameCount) / time.Since(lastLogTime).Seconds()
			fmt.Printf("[Stream] FPS: %.1f, 帧: %d/%d\n", fps, i, len(videoFrames))
			frameCount = 0
			lastLogTime = time.Now()
		}
	}

	s.sendJSON(map[string]interface{}{"type": "stream_end"})
}

// streamVideoWithAudio 流式传输音视频
func (s *StreamSession) streamVideoWithAudio(channel int, startTimestamp int64, speed float64) {
	s.mu.Lock()
	s.running = true
	stopChan := s.stopChan
	s.mu.Unlock()

	defer func() {
		s.mu.Lock()
		s.running = false
		s.mu.Unlock()
	}()

	fmt.Printf("[StreamAV] 请求: ch=%d, ts=%d, speed=%.1f\n", channel, startTimestamp, speed)

	// 查找录像
	entry := s.findEntryForTime(startTimestamp, channel)
	if entry == nil {
		fmt.Printf("[StreamAV] 错误: 未找到指定时间的录像 (ts=%d, ch=%d)\n", startTimestamp, channel)
		s.sendJSON(map[string]interface{}{"error": "未找到指定时间的录像"})
		return
	}
	fmt.Printf("[StreamAV] 找到录像: FileIndex=%d, RecFile=%s\n", entry.FileIndex, entry.RecFile)

	records := s.dvr.GetFrameIndex(entry.FileIndex)
	if records == nil {
		fmt.Printf("[StreamAV] 错误: 帧索引不存在 (FileIndex=%d)\n", entry.FileIndex)
		s.sendJSON(map[string]interface{}{"error": "帧索引不存在"})
		return
	}
	fmt.Printf("[StreamAV] 帧索引: %d 条记录\n", len(records))

	// 分离视频帧和音频帧
	var videoFrames, audioFrames []int
	for i, r := range records {
		if r.Channel == models.ChannelVideo1 || r.Channel == models.ChannelVideo2 {
			videoFrames = append(videoFrames, i)
		} else if r.Channel == models.ChannelAudio {
			audioFrames = append(audioFrames, i)
		}
	}

	fmt.Printf("[StreamAV] 视频帧: %d, 音频帧: %d\n", len(videoFrames), len(audioFrames))

	if len(videoFrames) == 0 {
		s.sendJSON(map[string]interface{}{"error": "未找到视频帧"})
		return
	}

	// 查找起始帧（I帧）
	startIdx := 0
	for i, idx := range videoFrames {
		if records[idx].UnixTs >= uint32(startTimestamp) {
			for j := i; j >= 0; j-- {
				if records[videoFrames[j]].FrameType == models.FrameTypeI {
					startIdx = j
					break
				}
			}
			break
		}
	}

	actualStartTime := int64(records[videoFrames[startIdx]].UnixTs)

	// 发送 stream_start
	s.sendJSON(map[string]interface{}{
		"type":            "stream_start",
		"channel":         channel,
		"startTime":       entry.StartTime,
		"endTime":         entry.EndTime,
		"actualStartTime": actualStartTime,
		"hasAudio":        len(audioFrames) > 0,
		"audioFormat":     "g711-ulaw",
		"audioSampleRate": 8000,
	})

	// 打开文件
	f, err := os.Open(entry.RecFile)
	if err != nil {
		s.sendJSON(map[string]interface{}{"error": err.Error()})
		return
	}
	defer f.Close()

	// 发送视频头（VPS/SPS/PPS + 第一个 I 帧）
	firstIFrameIdx := videoFrames[startIdx]
	firstRecord := records[firstIFrameIdx]
	headerData := make([]byte, min(512*1024, int(firstRecord.FrameSize)+100*1024))
	f.ReadAt(headerData, int64(firstRecord.FileOffset))

	nalUnits := parseNALUnits(headerData)
	headerSent := false
	var headerBytes []byte

	fmt.Printf("[StreamAV] 首帧解析到 %d 个 NAL 单元\n", len(nalUnits))
	for i, nal := range nalUnits {
		fmt.Printf("[StreamAV]   NAL[%d]: type=%d, offset=%d, size=%d\n", i, nal.nalType, nal.offset, nal.size)
		if i >= 10 {
			fmt.Printf("[StreamAV]   ... 省略剩余 %d 个\n", len(nalUnits)-10)
			break
		}
	}

	for _, nal := range nalUnits {
		if nal.nalType == NAL_VPS || nal.nalType == NAL_SPS || nal.nalType == NAL_PPS {
			headerBytes = append(headerBytes, headerData[nal.offset:nal.offset+nal.size]...)
			fmt.Printf("[StreamAV] 收集到 NAL type=%d, 累计 headerBytes=%d\n", nal.nalType, len(headerBytes))
		} else if nal.nalType == NAL_IDR_W_RADL || nal.nalType == NAL_IDR_N_LP {
			if len(headerBytes) > 0 {
				headerNals := parseNALUnits(headerBytes)
				fmt.Printf("[StreamAV] headerBytes 解析出 %d 个 NAL\n", len(headerNals))
				for _, h := range headerNals {
					nalData := stripStartCode(headerBytes[h.offset : h.offset+h.size])
					fmt.Printf("[StreamAV] 发送 NAL type=%d, size=%d (去除start code后)\n", h.nalType, len(nalData))
					s.sendVideoFrame(nalData, h.nalType, actualStartTime*1000)
				}
				fmt.Printf("[StreamAV] 已发送视频头，大小=%d 字节\n", len(headerBytes))
			} else {
				fmt.Printf("[StreamAV] 警告: 遇到 IDR 但没有收集到 VPS/SPS/PPS!\n")
			}
			idrData := stripStartCode(headerData[nal.offset : nal.offset+nal.size])
			s.sendVideoFrame(idrData, nal.nalType, actualStartTime*1000)
			fmt.Printf("[StreamAV] 已发送 IDR 帧，大小=%d 字节\n", len(idrData))
			headerSent = true
			break
		}
	}

	if !headerSent && len(headerBytes) > 0 {
		for _, h := range parseNALUnits(headerBytes) {
			s.sendVideoFrame(stripStartCode(headerBytes[h.offset:h.offset+h.size]), h.nalType, actualStartTime*1000)
		}
	}

	// 设置音频起始索引
	audioIdx := 0
	for i, idx := range audioFrames {
		if records[idx].UnixTs >= uint32(actualStartTime) {
			audioIdx = i
			break
		}
	}

	frameInterval := time.Duration(float64(time.Second) / 166.0 / speed)
	ticker := time.NewTicker(frameInterval)
	defer ticker.Stop()

	frameCount := 0
	lastLogTime := time.Now()
	currentTimeMs := actualStartTime * 1000

	for i := startIdx + 1; i < len(videoFrames); i++ {
		select {
		case <-stopChan:
			return
		case <-ticker.C:
		}

		vfIdx := videoFrames[i]
		vf := records[vfIdx]

		// 发送音频帧
		for audioIdx < len(audioFrames) {
			afIdx := audioFrames[audioIdx]
			af := records[afIdx]
			if int64(af.UnixTs)*1000 <= currentTimeMs {
				audioData, _ := trec.ReadFrame(entry.RecFile, af.FileOffset, af.FrameSize)
				if audioData != nil {
					s.sendAudioFrame(audioData, int64(af.UnixTs)*1000)
				}
				audioIdx++
			} else {
				break
			}
		}

		// 发送视频帧
		videoData, _ := trec.ReadFrame(entry.RecFile, vf.FileOffset, vf.FrameSize)
		if videoData != nil {
			nalUnits := parseNALUnits(videoData)
			for _, nal := range nalUnits {
				nalData := stripStartCode(videoData[nal.offset : nal.offset+nal.size])
				s.sendVideoFrame(nalData, nal.nalType, int64(vf.UnixTs)*1000)
			}
		}

		currentTimeMs = int64(vf.UnixTs) * 1000
		frameCount++

		if time.Since(lastLogTime) >= time.Second {
			fps := float64(frameCount) / time.Since(lastLogTime).Seconds()
			fmt.Printf("[StreamAV] FPS: %.1f, 视频帧: %d/%d, 音频帧: %d/%d\n",
				fps, i, len(videoFrames), audioIdx, len(audioFrames))
			frameCount = 0
			lastLogTime = time.Now()
		}
	}

	s.sendJSON(map[string]interface{}{"type": "stream_end"})
}

func (s *StreamSession) findEntryForTime(timestamp int64, channel int) *models.VPSCacheEntry {
	entries := s.dvr.GetVPSCache()
	fmt.Printf("[findEntry] VPSCache 条目数: %d, 查找: ts=%d, ch=%d\n", len(entries), timestamp, channel)

	for i := range entries {
		e := &entries[i]
		if e.Channel == channel && e.StartTime <= timestamp && timestamp <= e.EndTime {
			fmt.Printf("[findEntry] 找到匹配(精确通道): FileIndex=%d, ch=%d, start=%d, end=%d\n",
				e.FileIndex, e.Channel, e.StartTime, e.EndTime)
			return e
		}
	}
	// 如果没找到指定通道，尝试任意通道
	for i := range entries {
		e := &entries[i]
		if e.StartTime <= timestamp && timestamp <= e.EndTime {
			fmt.Printf("[findEntry] 找到匹配(任意通道): FileIndex=%d, ch=%d, start=%d, end=%d\n",
				e.FileIndex, e.Channel, e.StartTime, e.EndTime)
			return e
		}
	}

	// 打印前5个条目用于调试
	fmt.Printf("[findEntry] 未找到匹配，打印前5个条目:\n")
	for i := 0; i < len(entries) && i < 5; i++ {
		e := &entries[i]
		fmt.Printf("  [%d] ch=%d, start=%d, end=%d\n", e.FileIndex, e.Channel, e.StartTime, e.EndTime)
	}

	return nil
}

// sendVideoFrame 发送视频帧
// 格式: Magic(4) + Timestamp(8) + FrameType(1) + DataLen(4) + Data
func (s *StreamSession) sendVideoFrame(nalData []byte, nalType int, timestampMs int64) {
	var frameType byte
	switch nalType {
	case NAL_VPS:
		frameType = 2
	case NAL_SPS:
		frameType = 3
	case NAL_PPS:
		frameType = 4
	case NAL_IDR_W_RADL, NAL_IDR_N_LP:
		frameType = 1
	default:
		frameType = 0
	}

	header := make([]byte, 17)
	copy(header[0:4], "H265")
	binary.BigEndian.PutUint64(header[4:12], uint64(timestampMs))
	header[12] = frameType
	binary.BigEndian.PutUint32(header[13:17], uint32(len(nalData)))

	s.sendBytes(append(header, nalData...))
}

// sendAudioFrame 发送音频帧
// 格式: Magic(4) + Timestamp(8) + SampleRate(2) + DataLen(4) + Data
func (s *StreamSession) sendAudioFrame(audioData []byte, timestampMs int64) {
	header := make([]byte, 18)
	copy(header[0:4], "G711")
	binary.BigEndian.PutUint64(header[4:12], uint64(timestampMs))
	binary.BigEndian.PutUint16(header[12:14], 8000)
	binary.BigEndian.PutUint32(header[14:18], uint32(len(audioData)))

	s.sendBytes(append(header, audioData...))
}

// NAL 类型常量
const (
	NAL_VPS        = 32
	NAL_SPS        = 33
	NAL_PPS        = 34
	NAL_IDR_W_RADL = 19
	NAL_IDR_N_LP   = 20
	NAL_TRAIL_R    = 1
	NAL_TRAIL_N    = 0
)

type nalUnit struct {
	offset  int
	size    int
	nalType int
}

// parseNALUnits 解析 NAL 单元
func parseNALUnits(data []byte) []nalUnit {
	var units []nalUnit
	startCode4 := []byte{0, 0, 0, 1}
	startCode3 := []byte{0, 0, 1}

	pos := 0
	for pos < len(data)-4 {
		var startLen int
		if pos+4 <= len(data) && equal(data[pos:pos+4], startCode4) {
			startLen = 4
		} else if pos+3 <= len(data) && equal(data[pos:pos+3], startCode3) {
			startLen = 3
		} else {
			pos++
			continue
		}

		start := pos
		nalBytePos := start + startLen
		if nalBytePos >= len(data) {
			break
		}
		nalType := (int(data[nalBytePos]) >> 1) & 0x3F

		// 找下一个起始码
		nextPos := pos + startLen
		for nextPos < len(data)-4 {
			if equal(data[nextPos:nextPos+4], startCode4) || equal(data[nextPos:nextPos+3], startCode3) {
				break
			}
			nextPos++
		}
		if nextPos >= len(data)-4 {
			nextPos = len(data)
		}

		units = append(units, nalUnit{
			offset:  start,
			size:    nextPos - start,
			nalType: nalType,
		})
		pos = nextPos
	}

	return units
}

func equal(a, b []byte) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// stripStartCode 去掉 NAL 起始码
func stripStartCode(data []byte) []byte {
	if len(data) >= 4 && data[0] == 0 && data[1] == 0 && data[2] == 0 && data[3] == 1 {
		return data[4:]
	}
	if len(data) >= 3 && data[0] == 0 && data[1] == 0 && data[2] == 1 {
		return data[3:]
	}
	return data
}
