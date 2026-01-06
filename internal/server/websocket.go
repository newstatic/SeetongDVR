package server

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"sync"
	"sync/atomic"
	"time"

	"seetong-dvr/internal/seetong"

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
}

// StreamSession 流会话
type StreamSession struct {
	ws       *websocket.Conn
	handlers *Handlers          // 引用 Handlers 以获取最新的 DVR
	cancel   context.CancelFunc // 当前流的取消函数
	streamID uint64             // 当前流的 ID
	mu       sync.Mutex
	wg       sync.WaitGroup
}

var streamCounter uint64 // 全局流计数器

const audioSampleRate = 8000

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
		handlers: h,
	}

	sessionID := fmt.Sprintf("%p", ws)
	fmt.Printf("[WS] 新连接: %s\n", sessionID)

	for {
		_, message, err := ws.ReadMessage()
		if err != nil {
			if websocket.IsUnexpectedCloseError(err, websocket.CloseGoingAway, websocket.CloseAbnormalClosure) {
				fmt.Printf("[WS] Error: %v\n", err)
			}
			break
		}

		var msg WSMessage
		if err := json.Unmarshal(message, &msg); err != nil {
			session.sendJSON(map[string]interface{}{"error": "无效的 JSON"})
			continue
		}

		switch msg.Action {
		case "play":
			session.stop()
			if msg.Speed == 0 {
				msg.Speed = 1.0
			}
			fmt.Printf("[WS] 开始播放: ch=%d, ts=%d, speed=%.1f\n",
				msg.Channel, msg.Timestamp, msg.Speed)
			session.startStream(msg.Channel, msg.Timestamp, msg.Speed)

		case "pause":
			session.stop()
			fmt.Printf("[WS] 暂停\n")

		case "seek":
			session.stop()
			if msg.Speed == 0 {
				msg.Speed = 1.0
			}
			session.startStream(msg.Channel, msg.Timestamp, msg.Speed)
			fmt.Printf("[WS] Seek: ts=%d\n", msg.Timestamp)

		case "speed":
			fmt.Printf("[WS] 速度变更: %.1fx\n", msg.Speed)
		}
	}

	session.stop()
	fmt.Printf("[WS] 断开连接: %s\n", sessionID)
}

// stop 停止当前流并等待完成
func (s *StreamSession) stop() {
	s.mu.Lock()
	if s.cancel != nil {
		s.cancel()
		s.cancel = nil
	}
	s.mu.Unlock()

	// 等待流完成
	s.wg.Wait()
}

// startStream 启动新流
func (s *StreamSession) startStream(channel int, timestamp int64, speed float64) {
	// 创建新的 context 和 streamID
	ctx, cancel := context.WithCancel(context.Background())
	newStreamID := atomic.AddUint64(&streamCounter, 1)

	s.mu.Lock()
	s.cancel = cancel
	s.streamID = newStreamID
	s.mu.Unlock()

	s.wg.Add(1)
	go func() {
		defer s.wg.Done()
		s.streamVideoWithAudio(ctx, newStreamID, channel, timestamp, speed)
	}()
}

// sendJSON 发送 JSON 消息（带 streamID 验证）
func (s *StreamSession) sendJSON(v interface{}) error {
	jsonData, err := json.Marshal(v)
	if err != nil {
		return err
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	return s.ws.WriteMessage(websocket.TextMessage, jsonData)
}

// sendBytesWithID 发送二进制数据（带 streamID 验证，如果 ID 不匹配则跳过）
func (s *StreamSession) sendBytesWithID(streamID uint64, data []byte) bool {
	s.mu.Lock()
	defer s.mu.Unlock()

	// 验证 streamID，如果不匹配说明已被新流替代
	if s.streamID != streamID {
		return false
	}

	s.ws.WriteMessage(websocket.BinaryMessage, data)
	return true
}

// getDVR 获取当前 DVR（线程安全）
func (s *StreamSession) getDVR() *DVRServer {
	s.handlers.mu.RLock()
	defer s.handlers.mu.RUnlock()
	return s.handlers.dvr
}

// streamVideoWithAudio 流式传输音视频数据
func (s *StreamSession) streamVideoWithAudio(ctx context.Context, streamID uint64, channel int, startTimestamp int64, speed float64) {
	dvr := s.getDVR()
	storage := dvr.GetStorage()
	if storage == nil || !dvr.IsLoaded() {
		s.sendJSON(map[string]interface{}{"error": "DVR 未加载"})
		return
	}

	// 1. 查找段落
	seg := storage.FindSegmentByTime(startTimestamp, channel, true)
	if seg == nil {
		s.sendJSON(map[string]interface{}{"error": "未找到指定时间的录像"})
		return
	}

	fileIndex := seg.FileIndex
	fmt.Printf("[Stream#%d] file_index=%d, 时间范围: %d - %d\n", streamID, fileIndex, seg.StartTime, seg.EndTime)

	// 通道映射
	frameChannel := seetong.ChannelVideo1
	if channel == 2 {
		frameChannel = seetong.ChannelVideo2
	}

	// 获取音频帧
	audioFrames := storage.GetAudioFrames(fileIndex)

	// 2. 使用音频帧时间戳找到目标时间对应的字节偏移
	var targetOffset int64 = 0
	if len(audioFrames) > 0 {
		for _, af := range audioFrames {
			if int64(af.UnixTs) >= startTimestamp {
				targetOffset = int64(af.FileOffset)
				break
			}
		}
		if targetOffset == 0 {
			targetOffset = int64(audioFrames[len(audioFrames)-1].FileOffset)
		}
	}

	// 3. 搜索视频头
	header := storage.ReadVideoHeader(fileIndex, targetOffset)
	if header == nil {
		s.sendJSON(map[string]interface{}{"type": "error", "message": "未找到视频头"})
		return
	}

	streamStartPos := header.StreamStartPos
	fmt.Printf("[Stream#%d] VPS=%d, SPS=%d, PPS=%d, IDR=%d, pos=%d\n",
		streamID, len(header.VPS), len(header.SPS), len(header.PPS), len(header.IDR), streamStartPos)

	// 计算精确起始时间
	var actualStartTime int64 = 0
	if len(audioFrames) > 0 {
		for _, af := range audioFrames {
			if int64(af.FileOffset) <= streamStartPos {
				actualStartTime = int64(af.UnixTs)
			} else {
				break
			}
		}
	}
	if actualStartTime == 0 {
		actualStartTime = startTimestamp
	}

	// 音频帧起始索引
	audioIdx := 0
	for i, af := range audioFrames {
		if int64(af.FileOffset) >= streamStartPos {
			audioIdx = i
			break
		}
	}
	fmt.Printf("[Stream#%d] 音频帧: %d, 起始索引: %d\n", streamID, len(audioFrames), audioIdx)

	// 检查是否已取消
	if ctx.Err() != nil {
		fmt.Printf("[Stream#%d] 启动前已取消\n", streamID)
		return
	}

	// 发送 stream_start
	s.sendJSON(map[string]interface{}{
		"type":            "stream_start",
		"channel":         channel,
		"startTime":       seg.StartTime,
		"endTime":         seg.EndTime,
		"actualStartTime": actualStartTime,
		"hasAudio":        len(audioFrames) > 0,
		"audioFormat":     "g711-ulaw",
		"audioSampleRate": audioSampleRate,
	})

	// 4. 发送视频头
	s.sendVideoFrameWithID(streamID, header.VPS, seetong.NalVPS, actualStartTime*1000)
	s.sendVideoFrameWithID(streamID, header.SPS, seetong.NalSPS, actualStartTime*1000)
	s.sendVideoFrameWithID(streamID, header.PPS, seetong.NalPPS, actualStartTime*1000)
	s.sendVideoFrameWithID(streamID, header.IDR, seetong.NalIDRWRadl, actualStartTime*1000)

	// 5. 创建流读取器
	streamReader := storage.CreateStreamReader(fileIndex, streamStartPos, actualStartTime*1000, frameChannel)
	if streamReader == nil {
		s.sendJSON(map[string]interface{}{"type": "error", "message": "无法创建流读取器"})
		return
	}
	defer streamReader.Close()

	fps := 25.0 * speed
	streamReader.SetFPS(fps)
	frameInterval := time.Duration(float64(time.Second) / fps)

	// 打开音频文件
	recFile := storage.GetRecFile(fileIndex)
	audioFile, err := os.Open(recFile)
	if err != nil {
		s.sendJSON(map[string]interface{}{"type": "error", "message": err.Error()})
		return
	}
	defer audioFile.Close()

	frameCount := 0
	totalFramesSent := 0
	lastLogTime := time.Now()

	// 主循环
	for {
		// 检查取消信号
		select {
		case <-ctx.Done():
			fmt.Printf("[Stream#%d] 已取消，发送了 %d 帧\n", streamID, totalFramesSent)
			return
		default:
		}

		// 读取 NAL 单元
		nals := streamReader.ReadNextNals()
		if len(nals) == 0 {
			fmt.Printf("[Stream#%d] 文件结束, 总共发送 %d 帧\n", streamID, totalFramesSent)
			break
		}

		for _, nal := range nals {
			// 每个 NAL 前检查取消
			if ctx.Err() != nil {
				fmt.Printf("[Stream#%d] NAL 循环中取消\n", streamID)
				return
			}

			if seetong.IsKeyframe(nal.NalType) {
				fmt.Printf("[Stream#%d] IDR @ offset=%d\n", streamID, nal.FileOffset)
			}

			// 发送时验证 streamID
			if !s.sendVideoFrameWithID(streamID, nal.Data, nal.NalType, nal.TimestampMs) {
				fmt.Printf("[Stream#%d] 发送失败（ID 不匹配），退出\n", streamID)
				return
			}

			if seetong.IsVideoFrame(nal.NalType) {
				frameCount++
				totalFramesSent++

				// 发送音频帧
				for audioIdx < len(audioFrames) {
					af := audioFrames[audioIdx]
					if int64(af.FileOffset) <= nal.FileOffset {
						audioData := make([]byte, af.FrameSize)
						audioFile.Seek(int64(af.FileOffset), 0)
						audioFile.Read(audioData)

						audioTsMs := int64(af.UnixTs) * 1000
						if !s.sendAudioFrameWithID(streamID, audioData, audioTsMs) {
							return
						}
						audioIdx++
					} else {
						break
					}
				}

				// 使用可中断的 sleep
				select {
				case <-ctx.Done():
					fmt.Printf("[Stream#%d] sleep 期间取消\n", streamID)
					return
				case <-time.After(frameInterval):
				}
			}
		}

		now := time.Now()
		if now.Sub(lastLogTime) >= time.Second {
			actualFPS := float64(frameCount) / now.Sub(lastLogTime).Seconds()
			fmt.Printf("[Stream#%d] FPS: %.1f, 音频: %d/%d, 总帧: %d\n",
				streamID, actualFPS, audioIdx, len(audioFrames), totalFramesSent)
			frameCount = 0
			lastLogTime = now
		}
	}

	s.sendJSON(map[string]interface{}{"type": "stream_end"})
}

// sendVideoFrameWithID 发送视频帧（带 ID 验证）
func (s *StreamSession) sendVideoFrameWithID(streamID uint64, nalData []byte, nalType int, timestampMs int64) bool {
	var frameType byte
	switch nalType {
	case seetong.NalVPS:
		frameType = 2
	case seetong.NalSPS:
		frameType = 3
	case seetong.NalPPS:
		frameType = 4
	case seetong.NalIDRWRadl, seetong.NalIDRNLP:
		frameType = 1
	default:
		frameType = 0
	}

	header := make([]byte, 17)
	copy(header[0:4], "H265")
	binary.BigEndian.PutUint64(header[4:12], uint64(timestampMs))
	header[12] = frameType
	binary.BigEndian.PutUint32(header[13:17], uint32(len(nalData)))

	return s.sendBytesWithID(streamID, append(header, nalData...))
}

// sendAudioFrameWithID 发送音频帧（带 ID 验证）
func (s *StreamSession) sendAudioFrameWithID(streamID uint64, audioData []byte, timestampMs int64) bool {
	header := make([]byte, 18)
	copy(header[0:4], "G711")
	binary.BigEndian.PutUint64(header[4:12], uint64(timestampMs))
	binary.BigEndian.PutUint16(header[12:14], audioSampleRate)
	binary.BigEndian.PutUint32(header[14:18], uint32(len(audioData)))

	return s.sendBytesWithID(streamID, append(header, audioData...))
}
