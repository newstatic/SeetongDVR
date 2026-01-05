package handlers

import (
	"encoding/binary"
	"fmt"
	"sync"
	"time"

	"github.com/kataras/iris/v12"
	"github.com/kataras/iris/v12/websocket"
	"github.com/kataras/neffos"

	"seetong-dvr/internal/models"
	"seetong-dvr/internal/server"
	"seetong-dvr/internal/trec"
)

// Handler API 处理器
type Handler struct {
	DVR *server.DVRServer
}

// NewHandler 创建处理器
func NewHandler(dvr *server.DVRServer) *Handler {
	return &Handler{DVR: dvr}
}

// GetDates 获取日期列表
func (h *Handler) GetDates(ctx iris.Context) {
	entries := h.DVR.GetVPSCache()

	dateMap := make(map[string]int)
	for _, entry := range entries {
		t := time.Unix(entry.UnixTs, 0)
		date := t.Format("2006-01-02")
		dateMap[date]++
	}

	type DateInfo struct {
		Date  string `json:"date"`
		Count int    `json:"count"`
	}

	var dates []DateInfo
	for date, count := range dateMap {
		dates = append(dates, DateInfo{Date: date, Count: count})
	}

	ctx.JSON(iris.Map{
		"dates": dates,
	})
}

// GetRecordings 获取录像列表
func (h *Handler) GetRecordings(ctx iris.Context) {
	date := ctx.URLParam("date")
	if date == "" {
		ctx.StatusCode(iris.StatusBadRequest)
		ctx.JSON(iris.Map{"error": "date required"})
		return
	}

	entries := h.DVR.GetVPSCache()

	type Recording struct {
		Index      int    `json:"index"`
		Date       string `json:"date"`
		StartTime  string `json:"start_time"`
		EndTime    string `json:"end_time"`
		Duration   int    `json:"duration"`
		FrameCount int    `json:"frame_count"`
	}

	var recordings []Recording
	for _, entry := range entries {
		t := time.Unix(entry.UnixTs, 0)
		if t.Format("2006-01-02") != date {
			continue
		}

		durationUs := entry.EndTimeUs - entry.StartTimeUs
		durationSec := int(durationUs / 1000000)

		recordings = append(recordings, Recording{
			Index:      entry.FileIndex,
			Date:       date,
			StartTime:  t.Format("15:04:05"),
			EndTime:    time.Unix(entry.UnixTs+int64(durationSec), 0).Format("15:04:05"),
			Duration:   durationSec,
			FrameCount: entry.FrameCount,
		})
	}

	ctx.JSON(iris.Map{
		"recordings": recordings,
	})
}

// GetConfig 获取配置
func (h *Handler) GetConfig(ctx iris.Context) {
	ctx.JSON(iris.Map{
		"status": "ok",
	})
}

// StreamSession WebSocket 流会话
type StreamSession struct {
	fileIndex  int
	channel    uint32
	currentIdx int
	playing    bool
	speed      float64
	records    []models.FrameIndexRecord
	filePath   string
	mu         sync.Mutex
}

// WebSocketHandler WebSocket 处理器
type WebSocketHandler struct {
	DVR      *server.DVRServer
	sessions map[*neffos.Conn]*StreamSession
	mu       sync.RWMutex
}

// NewWebSocketHandler 创建 WebSocket 处理器
func NewWebSocketHandler(dvr *server.DVRServer) *WebSocketHandler {
	return &WebSocketHandler{
		DVR:      dvr,
		sessions: make(map[*neffos.Conn]*StreamSession),
	}
}

// OnConnect 连接建立
func (ws *WebSocketHandler) OnConnect(c *neffos.NSConn, msg neffos.Message) error {
	fmt.Printf("[WS] 客户端连接: %s\n", c.Conn.ID())
	return nil
}

// OnDisconnect 连接断开
func (ws *WebSocketHandler) OnDisconnect(c *neffos.NSConn, msg neffos.Message) error {
	fmt.Printf("[WS] 客户端断开: %s\n", c.Conn.ID())
	ws.mu.Lock()
	delete(ws.sessions, c.Conn)
	ws.mu.Unlock()
	return nil
}

// OnOpen 打开文件
func (ws *WebSocketHandler) OnOpen(c *neffos.NSConn, msg neffos.Message) error {
	var req struct {
		FileIndex int    `json:"file_index"`
		Channel   uint32 `json:"channel"`
	}
	if err := msg.Unmarshal(&req); err != nil {
		return err
	}

	// 获取帧索引
	records := ws.DVR.GetFrameIndex(req.FileIndex)
	if records == nil {
		c.Emit("error", []byte(`{"error": "file not found"}`))
		return nil
	}

	// 获取文件路径
	var filePath string
	for _, entry := range ws.DVR.GetVPSCache() {
		if entry.FileIndex == req.FileIndex {
			filePath = entry.RecFile
			break
		}
	}

	// 过滤指定通道
	var filtered []models.FrameIndexRecord
	for _, r := range records {
		if r.Channel == req.Channel {
			filtered = append(filtered, r)
		}
	}

	session := &StreamSession{
		fileIndex:  req.FileIndex,
		channel:    req.Channel,
		currentIdx: 0,
		playing:    false,
		speed:      1.0,
		records:    filtered,
		filePath:   filePath,
	}

	ws.mu.Lock()
	ws.sessions[c.Conn] = session
	ws.mu.Unlock()

	c.Emit("opened", []byte(fmt.Sprintf(`{"frame_count": %d, "channel": %d}`,
		len(filtered), req.Channel)))

	return nil
}

// OnPlay 开始播放
func (ws *WebSocketHandler) OnPlay(c *neffos.NSConn, msg neffos.Message) error {
	ws.mu.RLock()
	session := ws.sessions[c.Conn]
	ws.mu.RUnlock()

	if session == nil {
		return nil
	}

	session.mu.Lock()
	session.playing = true
	session.mu.Unlock()

	go ws.streamFrames(c, session)
	return nil
}

// OnPause 暂停播放
func (ws *WebSocketHandler) OnPause(c *neffos.NSConn, msg neffos.Message) error {
	ws.mu.RLock()
	session := ws.sessions[c.Conn]
	ws.mu.RUnlock()

	if session != nil {
		session.mu.Lock()
		session.playing = false
		session.mu.Unlock()
	}
	return nil
}

// OnSeek 跳转
func (ws *WebSocketHandler) OnSeek(c *neffos.NSConn, msg neffos.Message) error {
	var req struct {
		Position float64 `json:"position"`
	}
	if err := msg.Unmarshal(&req); err != nil {
		return err
	}

	ws.mu.RLock()
	session := ws.sessions[c.Conn]
	ws.mu.RUnlock()

	if session != nil {
		session.mu.Lock()
		idx := int(float64(len(session.records)) * req.Position)
		if idx >= len(session.records) {
			idx = len(session.records) - 1
		}
		if idx < 0 {
			idx = 0
		}
		session.currentIdx = idx
		session.mu.Unlock()
	}
	return nil
}

// OnSpeed 设置速度
func (ws *WebSocketHandler) OnSpeed(c *neffos.NSConn, msg neffos.Message) error {
	var req struct {
		Speed float64 `json:"speed"`
	}
	if err := msg.Unmarshal(&req); err != nil {
		return err
	}

	ws.mu.RLock()
	session := ws.sessions[c.Conn]
	ws.mu.RUnlock()

	if session != nil {
		session.mu.Lock()
		session.speed = req.Speed
		session.mu.Unlock()
	}
	return nil
}

// streamFrames 流式发送帧
func (ws *WebSocketHandler) streamFrames(c *neffos.NSConn, session *StreamSession) {
	for {
		session.mu.Lock()
		if !session.playing {
			session.mu.Unlock()
			break
		}

		if session.currentIdx >= len(session.records) {
			session.playing = false
			session.mu.Unlock()
			c.Emit("ended", nil)
			break
		}

		record := session.records[session.currentIdx]
		speed := session.speed
		filePath := session.filePath

		var intervalUs int64 = 33333
		if session.currentIdx+1 < len(session.records) {
			next := session.records[session.currentIdx+1]
			intervalUs = int64(next.TimestampUs - record.TimestampUs)
		}

		session.currentIdx++
		session.mu.Unlock()

		data, err := trec.ReadFrame(filePath, record.FileOffset, record.FrameSize)
		if err != nil {
			continue
		}

		frame := make([]byte, 12+len(data))
		binary.LittleEndian.PutUint32(frame[0:4], record.FrameType)
		binary.LittleEndian.PutUint64(frame[4:12], record.TimestampUs)
		copy(frame[12:], data)

		c.EmitBinary("frame", frame)

		progress := float64(session.currentIdx) / float64(len(session.records))
		c.Emit("progress", []byte(fmt.Sprintf(`{"position": %.4f, "index": %d}`,
			progress, session.currentIdx)))

		sleepDuration := time.Duration(float64(intervalUs)/speed) * time.Microsecond
		if sleepDuration > 0 && sleepDuration < time.Second {
			time.Sleep(sleepDuration)
		}
	}
}

// RegisterEvents 注册 WebSocket 事件
func (ws *WebSocketHandler) RegisterEvents() websocket.Namespaces {
	return websocket.Namespaces{
		"stream": websocket.Events{
			websocket.OnNamespaceConnected:  ws.OnConnect,
			websocket.OnNamespaceDisconnect: ws.OnDisconnect,
			"open":  ws.OnOpen,
			"play":  ws.OnPlay,
			"pause": ws.OnPause,
			"seek":  ws.OnSeek,
			"speed": ws.OnSpeed,
		},
	}
}
