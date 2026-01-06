// Package seetong 天视通 DVR 算法库
//
// 统一管理所有 TPS 文件格式解析和视频数据处理算法。
// 完全移植自 Python 版本的 seetong_lib.py
package seetong

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"
)

// ============================================================================
// 常量定义
// ============================================================================

const (
	// TIndex00.tps 常量
	TPSIndexMagic      = 0x1F2E3D4C
	SegmentIndexOffset = 0x4FC
	FrameIndexOffset   = 0x84C0
	EntrySize          = 0x40

	// TRec 文件常量
	TRecFileSize           = 0x10000000 // 256MB
	TRecIndexRegionStart   = 0x0F900000 // 索引区域起始
	TRecFrameIndexMagic    = 0x4C3D2E1F
	TRecFrameIndexSize     = 44

	// 通道定义
	ChannelVideo1 = 2
	ChannelAudio  = 3
	ChannelVideo2 = 258

	// 帧类型
	FrameTypeI = 1
	FrameTypeP = 3

	// 有效时间戳下限：2020-01-01
	MinValidTimestamp = 1577836800
)

// NAL 类型
const (
	NalTrailN    = 0  // P帧 (非参考)
	NalTrailR    = 1  // P帧 (参考)
	NalIDRWRadl  = 19 // IDR 帧
	NalIDRNLP    = 20 // IDR 帧
	NalVPS       = 32 // 视频参数集
	NalSPS       = 33 // 序列参数集
	NalPPS       = 34 // 图像参数集
)

// NAL 起始码
var (
	NalStartCode4 = []byte{0x00, 0x00, 0x00, 0x01}
	NalStartCode3 = []byte{0x00, 0x00, 0x01}
	VPSPattern    = []byte{0x00, 0x00, 0x00, 0x01, 0x40}
)

// ============================================================================
// 数据结构定义
// ============================================================================

// FrameIndexRecord TRec 文件中的帧索引记录
type FrameIndexRecord struct {
	FrameType   uint32
	Channel     uint32
	FrameSeq    uint32
	FileOffset  uint32
	FrameSize   uint32
	TimestampUs uint64
	UnixTs      uint32
}

// SegmentRecord 段落索引记录
type SegmentRecord struct {
	FileIndex  int
	Channel    int
	StartTime  int64
	EndTime    int64
	FrameCount int
}

// NalUnit NAL 单元信息
type NalUnit struct {
	Offset  int
	Size    int
	NalType int
	Data    []byte // 可选，不含起始码
}

// CachedSegmentInfo 已缓存段落的完整信息
type CachedSegmentInfo struct {
	Segment      *SegmentRecord
	FrameIndex   []FrameIndexRecord
	VPSPositions []VPSPosition // VPS 位置及其精确时间
	AudioFrames  []FrameIndexRecord
}

// VPSPosition VPS 位置和时间
type VPSPosition struct {
	Offset int
	Time   int64
}

// VideoHeader 视频头信息
type VideoHeader struct {
	VPS            []byte
	SPS            []byte
	PPS            []byte
	IDR            []byte
	StreamStartPos int64
}

// ============================================================================
// NAL 工具函数
// ============================================================================

// IsVideoFrame 判断是否为视频帧 NAL
func IsVideoFrame(nalType int) bool {
	return nalType == NalTrailN || nalType == NalTrailR ||
		nalType == NalIDRWRadl || nalType == NalIDRNLP
}

// IsKeyframe 判断是否为关键帧
func IsKeyframe(nalType int) bool {
	return nalType == NalIDRWRadl || nalType == NalIDRNLP
}

// IsHeader 判断是否为头部 NAL
func IsHeader(nalType int) bool {
	return nalType == NalVPS || nalType == NalSPS || nalType == NalPPS
}

// ParseNalUnits 解析数据中的所有 NAL 单元
func ParseNalUnits(data []byte) []NalUnit {
	var results []NalUnit
	pos := 0

	for pos < len(data)-4 {
		var startLen int
		if bytes.Equal(data[pos:pos+4], NalStartCode4) {
			startLen = 4
		} else if pos+3 <= len(data) && bytes.Equal(data[pos:pos+3], NalStartCode3) {
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

		// 查找下一个起始码
		nextPos := pos + startLen
		for nextPos < len(data)-4 {
			if bytes.Equal(data[nextPos:nextPos+4], NalStartCode4) {
				break
			}
			if nextPos+3 <= len(data) && bytes.Equal(data[nextPos:nextPos+3], NalStartCode3) {
				break
			}
			nextPos++
		}
		if nextPos >= len(data)-4 {
			nextPos = len(data)
		}

		results = append(results, NalUnit{
			Offset:  start,
			Size:    nextPos - start,
			NalType: nalType,
		})
		pos = nextPos
	}

	return results
}

// StripStartCode 去掉 NAL 起始码
func StripStartCode(data []byte) []byte {
	if len(data) >= 4 && bytes.Equal(data[:4], NalStartCode4) {
		return data[4:]
	}
	if len(data) >= 3 && bytes.Equal(data[:3], NalStartCode3) {
		return data[3:]
	}
	return data
}

// FindVPSSPSPPSIDR 在数据中查找 VPS/SPS/PPS/IDR 序列
func FindVPSSPSPPSIDR(data []byte) *VideoHeader {
	nals := ParseNalUnits(data)

	// 找到 VPS 的位置
	vpsIdx := -1
	for i, nal := range nals {
		if nal.NalType == NalVPS {
			vpsIdx = i
			break
		}
	}

	if vpsIdx < 0 {
		return nil
	}

	var vps, sps, pps, idr []byte
	var idrEndOffset int

	for i := vpsIdx; i < len(nals); i++ {
		nal := nals[i]
		nalData := StripStartCode(data[nal.Offset : nal.Offset+nal.Size])

		switch nal.NalType {
		case NalVPS:
			if vps == nil {
				vps = nalData
			}
		case NalSPS:
			if sps == nil {
				sps = nalData
			}
		case NalPPS:
			if pps == nil {
				pps = nalData
			}
		case NalIDRWRadl, NalIDRNLP:
			idr = nalData
			idrEndOffset = nal.Offset + nal.Size
			goto done
		}
	}

done:
	if vps != nil && sps != nil && pps != nil && idr != nil {
		return &VideoHeader{
			VPS:            vps,
			SPS:            sps,
			PPS:            pps,
			IDR:            idr,
			StreamStartPos: int64(idrEndOffset),
		}
	}
	return nil
}

// ============================================================================
// 精确时间计算
// ============================================================================

// CalculatePreciseTime 根据字节偏移计算精确时间戳
func CalculatePreciseTime(seg *SegmentRecord, byteOffset int) int64 {
	if TRecIndexRegionStart <= 0 {
		return seg.StartTime
	}

	duration := seg.EndTime - seg.StartTime
	timeOffset := float64(byteOffset) / float64(TRecIndexRegionStart) * float64(duration)
	return seg.StartTime + int64(timeOffset)
}

// CalculatePreciseTimeFromIFrames 根据 I 帧列表计算目标偏移的精确时间
func CalculatePreciseTimeFromIFrames(iFrames []VPSPosition, targetOffset int, seg *SegmentRecord) int64 {
	if len(iFrames) == 0 {
		return seg.StartTime
	}

	if len(iFrames) == 1 {
		return CalculatePreciseTime(seg, targetOffset)
	}

	var prevFrame, nextFrame *VPSPosition

	for i := range iFrames {
		if iFrames[i].Offset <= targetOffset {
			prevFrame = &iFrames[i]
			if i+1 < len(iFrames) {
				nextFrame = &iFrames[i+1]
			}
		} else {
			if prevFrame == nil {
				prevFrame = &VPSPosition{Offset: 0, Time: seg.StartTime}
				nextFrame = &iFrames[i]
			}
			break
		}
	}

	if prevFrame == nil {
		return seg.StartTime
	}

	if nextFrame == nil {
		nextFrame = &VPSPosition{Offset: TRecIndexRegionStart, Time: seg.EndTime}
	}

	byteRange := nextFrame.Offset - prevFrame.Offset
	if byteRange <= 0 {
		return prevFrame.Time
	}

	timeRange := nextFrame.Time - prevFrame.Time
	byteOffsetInRange := targetOffset - prevFrame.Offset

	timeOffset := float64(byteOffsetInRange) / float64(byteRange) * float64(timeRange)
	return prevFrame.Time + int64(timeOffset)
}

// ============================================================================
// TRec 帧索引解析
// ============================================================================

// ParseTRecFrameIndex 解析 TRec 文件中的帧索引
func ParseTRecFrameIndex(recFilePath string) ([]FrameIndexRecord, error) {
	f, err := os.Open(recFilePath)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	// 搜索帧索引起始位置
	magicBytes := make([]byte, 4)
	binary.LittleEndian.PutUint32(magicBytes, TRecFrameIndexMagic)

	_, err = f.Seek(TRecIndexRegionStart, 0)
	if err != nil {
		return nil, err
	}

	searchData := make([]byte, 0x700000)
	n, err := f.Read(searchData)
	if err != nil && err != io.EOF {
		return nil, err
	}
	searchData = searchData[:n]

	idx := bytes.Index(searchData, magicBytes)
	if idx == -1 {
		return nil, nil
	}

	indexStart := int64(TRecIndexRegionStart + idx)
	_, err = f.Seek(indexStart, 0)
	if err != nil {
		return nil, err
	}

	var records []FrameIndexRecord
	buf := make([]byte, TRecFrameIndexSize)

	for {
		n, err := f.Read(buf)
		if err != nil || n < TRecFrameIndexSize {
			break
		}

		magic := binary.LittleEndian.Uint32(buf[0:4])
		if magic != TRecFrameIndexMagic {
			break
		}

		frameType := binary.LittleEndian.Uint32(buf[4:8])
		channel := binary.LittleEndian.Uint32(buf[8:12])
		frameSeq := binary.LittleEndian.Uint32(buf[12:16])
		fileOffset := binary.LittleEndian.Uint32(buf[16:20])
		frameSize := binary.LittleEndian.Uint32(buf[20:24])
		timestampUs := binary.LittleEndian.Uint64(buf[24:32])
		unixTs := binary.LittleEndian.Uint32(buf[32:36])

		if unixTs > MinValidTimestamp && (channel == ChannelVideo1 || channel == ChannelAudio || channel == ChannelVideo2) {
			records = append(records, FrameIndexRecord{
				FrameType:   frameType,
				Channel:     channel,
				FrameSeq:    frameSeq,
				FileOffset:  fileOffset,
				FrameSize:   frameSize,
				TimestampUs: timestampUs,
				UnixTs:      unixTs,
			})
		}
	}

	// 按时间正序排列
	sort.Slice(records, func(i, j int) bool {
		return records[i].TimestampUs < records[j].TimestampUs
	})

	return records, nil
}

// ScanVPSPositions 扫描文件中所有 VPS 位置（只扫描数据区域）
func ScanVPSPositions(filePath string) ([]int, error) {
	f, err := os.Open(filePath)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	// 只扫描数据区域 (0 ~ TRecIndexRegionStart)
	scanSize := TRecIndexRegionStart

	var vpsPositions []int
	chunkSize := 4 * 1024 * 1024 // 4MB chunks，更好的缓存利用
	chunk := make([]byte, chunkSize+4) // +4 用于跨边界检测
	offset := 0

	for offset < scanSize {
		readSize := chunkSize
		if offset+readSize > scanSize {
			readSize = scanSize - offset
		}

		// 多读 4 字节用于跨边界检测
		extraRead := 4
		if offset+readSize+extraRead > scanSize {
			extraRead = 0
		}

		f.Seek(int64(offset), 0)
		n, err := f.Read(chunk[:readSize+extraRead])
		if err != nil && err != io.EOF {
			return nil, err
		}
		if n == 0 {
			break
		}

		// 在当前块中搜索 VPS 模式
		searchEnd := n
		if extraRead > 0 {
			searchEnd = readSize // 不搜索额外读取的部分，留给下次
		}

		for pos := 0; pos < searchEnd; {
			idx := bytes.Index(chunk[pos:searchEnd+4], VPSPattern)
			if idx == -1 {
				break
			}
			actualPos := offset + pos + idx
			if actualPos < TRecIndexRegionStart {
				vpsPositions = append(vpsPositions, actualPos)
			}
			pos += idx + 1 // 移动到下一个可能的位置
		}

		offset += readSize
	}

	return vpsPositions, nil
}

// ============================================================================
// TIndex00.tps 解析
// ============================================================================

// ParseTIndex 解析 TIndex00.tps 主索引文件
func ParseTIndex(indexPath string) ([]SegmentRecord, int, int, error) {
	f, err := os.Open(indexPath)
	if err != nil {
		return nil, 0, 0, err
	}
	defer f.Close()

	// 读取文件头
	var magic uint32
	if err := binary.Read(f, binary.LittleEndian, &magic); err != nil {
		return nil, 0, 0, err
	}
	if magic != TPSIndexMagic {
		return nil, 0, 0, fmt.Errorf("invalid magic: %08X", magic)
	}

	f.Seek(0x10, 0)
	var fileCount, entryCount uint32
	binary.Read(f, binary.LittleEndian, &fileCount)
	binary.Read(f, binary.LittleEndian, &entryCount)

	// 读取段落索引
	f.Seek(SegmentIndexOffset, 0)

	var segments []SegmentRecord
	segmentIndex := 0
	entryData := make([]byte, EntrySize)

	for i := 0; i < int(entryCount)+20; i++ {
		n, err := f.Read(entryData)
		if err != nil || n < EntrySize {
			break
		}

		channel := int(entryData[4])
		frameCount := int(binary.LittleEndian.Uint16(entryData[6:8]))
		startTime := int64(binary.LittleEndian.Uint32(entryData[8:12]))
		endTime := int64(binary.LittleEndian.Uint32(entryData[12:16]))

		// 过滤无效记录
		if channel == 0 || channel == 0xFE {
			segmentIndex++
			continue
		}
		if startTime < MinValidTimestamp || endTime <= startTime {
			segmentIndex++
			continue
		}

		segments = append(segments, SegmentRecord{
			FileIndex:  segmentIndex,
			Channel:    channel,
			StartTime:  startTime,
			EndTime:    endTime,
			FrameCount: frameCount,
		})
		segmentIndex++
	}

	return segments, int(fileCount), int(entryCount), nil
}

// ============================================================================
// 视频流读取器
// ============================================================================

// VideoStreamReader 视频流读取器
type VideoStreamReader struct {
	f              *os.File
	streamPos      int64
	buffer         []byte
	bufferStartPos int64
	currentTimeMs  int64
	frameIntervalMs int64
	frameCount     int

	// 精确时间计算
	seg            *SegmentRecord
	frameOffsets   []VPSPosition
	usePreciseTime bool
}

// NewVideoStreamReader 创建视频流读取器
func NewVideoStreamReader(f *os.File, startPos int64, startTimeMs int64,
	seg *SegmentRecord, frameOffsets []VPSPosition) *VideoStreamReader {
	return &VideoStreamReader{
		f:              f,
		streamPos:      startPos,
		bufferStartPos: startPos,
		currentTimeMs:  startTimeMs,
		frameIntervalMs: 40, // 25fps
		seg:            seg,
		frameOffsets:   frameOffsets,
		usePreciseTime: seg != nil && len(frameOffsets) > 0,
	}
}

// SetFPS 设置帧率
func (r *VideoStreamReader) SetFPS(fps float64) {
	r.frameIntervalMs = int64(1000 / fps)
}

const (
	chunkSize     = 64 * 1024  // 64KB
	minBufferSize = 256 * 1024 // 256KB
)

func (r *VideoStreamReader) fillBuffer() bool {
	if len(r.buffer) >= minBufferSize {
		return true
	}

	r.f.Seek(r.streamPos, 0)
	chunk := make([]byte, chunkSize)
	n, err := r.f.Read(chunk)
	if err != nil || n == 0 {
		return false
	}

	if len(r.buffer) == 0 {
		r.bufferStartPos = r.streamPos
	}

	r.buffer = append(r.buffer, chunk[:n]...)
	r.streamPos += int64(n)
	return true
}

func (r *VideoStreamReader) getPreciseTimeMs(nalFileOffset int64) int64 {
	if !r.usePreciseTime {
		return r.currentTimeMs
	}

	preciseTime := CalculatePreciseTimeFromIFrames(r.frameOffsets, int(nalFileOffset), r.seg)
	return preciseTime * 1000
}

// NalResult NAL 读取结果
type NalResult struct {
	Data       []byte
	NalType    int
	TimestampMs int64
	FileOffset int64
}

// ReadNextNals 读取下一批 NAL 单元
func (r *VideoStreamReader) ReadNextNals() []NalResult {
	maxAttempts := 10
	for attempt := 0; attempt < maxAttempts; attempt++ {
		if !r.fillBuffer() {
			return nil
		}

		nalUnits := ParseNalUnits(r.buffer)
		if len(nalUnits) >= 2 {
			var results []NalResult

			// 发送除最后一个之外的所有 NAL
			for i := 0; i < len(nalUnits)-1; i++ {
				nal := nalUnits[i]
				nalData := StripStartCode(r.buffer[nal.Offset : nal.Offset+nal.Size])
				nalFileOffset := r.bufferStartPos + int64(nal.Offset)

				var timestampMs int64
				if r.usePreciseTime && IsVideoFrame(nal.NalType) {
					timestampMs = r.getPreciseTimeMs(nalFileOffset)
				} else {
					timestampMs = r.currentTimeMs
				}

				results = append(results, NalResult{
					Data:       nalData,
					NalType:    nal.NalType,
					TimestampMs: timestampMs,
					FileOffset: nalFileOffset,
				})

				if IsVideoFrame(nal.NalType) {
					r.frameCount++
					r.currentTimeMs += r.frameIntervalMs
				}
			}

			// 移除已处理的数据
			lastNalEnd := nalUnits[len(nalUnits)-2].Offset + nalUnits[len(nalUnits)-2].Size
			r.bufferStartPos += int64(lastNalEnd)
			r.buffer = r.buffer[lastNalEnd:]

			return results
		}

		if len(nalUnits) == 0 {
			r.buffer = r.buffer[:0]
		} else if len(nalUnits) == 1 {
			// 读取更多数据
			r.f.Seek(r.streamPos, 0)
			chunk := make([]byte, chunkSize*4)
			n, _ := r.f.Read(chunk)
			if n == 0 {
				return nil
			}
			r.buffer = append(r.buffer, chunk[:n]...)
			r.streamPos += int64(n)
		}
	}

	return nil
}

// Close 关闭读取器
func (r *VideoStreamReader) Close() {
	if r.f != nil {
		r.f.Close()
	}
}

// GetStreamPos 获取当前流位置
func (r *VideoStreamReader) GetStreamPos() int64 {
	return r.streamPos
}

// ============================================================================
// TPS 存储管理器
// ============================================================================

// TPSStorage TPS 存储管理器
type TPSStorage struct {
	dvrPath    string
	segments   []SegmentRecord
	fileCount  int
	entryCount int
	loaded     bool

	// 核心缓存
	cachedSegments map[int]*CachedSegmentInfo
	mu             sync.RWMutex

	// 缓存构建状态
	cacheBuilding  bool
	cacheProgress  int
	cacheTotal     int
	cacheCurrent   int
}

// NewTPSStorage 创建 TPS 存储管理器
func NewTPSStorage(dvrPath string) *TPSStorage {
	return &TPSStorage{
		dvrPath:        dvrPath,
		cachedSegments: make(map[int]*CachedSegmentInfo),
	}
}

// Load 加载主索引
func (s *TPSStorage) Load() error {
	indexPath := filepath.Join(s.dvrPath, "TIndex00.tps")
	if _, err := os.Stat(indexPath); os.IsNotExist(err) {
		return fmt.Errorf("索引文件不存在: %s", indexPath)
	}

	segments, fileCount, entryCount, err := ParseTIndex(indexPath)
	if err != nil {
		return fmt.Errorf("加载索引失败: %v", err)
	}

	s.segments = segments
	s.fileCount = fileCount
	s.entryCount = entryCount
	s.loaded = true

	LogInfo("已加载段落索引", "count", len(segments))
	return nil
}

// IsLoaded 是否已加载
func (s *TPSStorage) IsLoaded() bool {
	return s.loaded
}

// GetSegments 获取所有段落
func (s *TPSStorage) GetSegments() []SegmentRecord {
	return s.segments
}

// ==================== 缓存管理 ====================

// BuildCache 构建段落缓存（多线程并行）
func (s *TPSStorage) BuildCache(fileIndices []int, progressCallback func(current, total, fileIndex int)) int {
	return s.BuildCacheWithWorkers(fileIndices, progressCallback, 0)
}

// BuildCacheWithWorkers 构建段落缓存（指定线程数）
// workers=0 时使用 CPU 核心数
func (s *TPSStorage) BuildCacheWithWorkers(fileIndices []int, progressCallback func(current, total, fileIndex int), workers int) int {
	if !s.loaded {
		return 0
	}

	var segmentsToCache []*SegmentRecord
	if fileIndices == nil {
		for i := range s.segments {
			segmentsToCache = append(segmentsToCache, &s.segments[i])
		}
	} else {
		indexSet := make(map[int]bool)
		for _, idx := range fileIndices {
			indexSet[idx] = true
		}
		for i := range s.segments {
			if indexSet[s.segments[i].FileIndex] {
				segmentsToCache = append(segmentsToCache, &s.segments[i])
			}
		}
	}

	total := len(segmentsToCache)
	if total == 0 {
		return 0
	}

	// 默认使用 2 个线程（USB/机械硬盘优化）
	// 过多并发在 IO 密集型场景反而更慢
	if workers <= 0 {
		workers = 2
	}
	if workers > 4 {
		workers = 4 // 限制最大并发数，避免 IO 瓶颈
	}
	if workers > total {
		workers = total
	}

	LogInfo("缓存构建: 启动", "workers", workers, "segments", total)
	buildStart := time.Now()

	s.mu.Lock()
	s.cacheBuilding = true
	s.cacheTotal = total
	s.cacheCurrent = 0
	s.cacheProgress = 0
	s.mu.Unlock()

	// 创建工作队列
	type workItem struct {
		index int
		seg   *SegmentRecord
	}
	workChan := make(chan workItem, total)
	for i, seg := range segmentsToCache {
		workChan <- workItem{index: i, seg: seg}
	}
	close(workChan)

	// 结果收集
	type result struct {
		fileIndex int
		info      *CachedSegmentInfo
	}
	resultChan := make(chan result, total)

	// 启动工作线程
	var wg sync.WaitGroup
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for work := range workChan {
				cachedInfo, err := s.buildSegmentCache(work.seg)
				if err == nil && cachedInfo != nil {
					resultChan <- result{fileIndex: work.seg.FileIndex, info: cachedInfo}
				} else {
					resultChan <- result{fileIndex: work.seg.FileIndex, info: nil}
				}
			}
		}()
	}

	// 关闭结果通道
	go func() {
		wg.Wait()
		close(resultChan)
	}()

	// 收集结果并更新进度
	cachedCount := 0
	processed := 0
	for res := range resultChan {
		processed++
		if res.info != nil {
			s.mu.Lock()
			s.cachedSegments[res.fileIndex] = res.info
			s.mu.Unlock()
			cachedCount++
		}

		s.mu.Lock()
		s.cacheCurrent = processed
		s.cacheProgress = processed * 100 / total
		s.mu.Unlock()

		if progressCallback != nil {
			progressCallback(processed, total, res.fileIndex)
		}
	}

	s.mu.Lock()
	s.cacheBuilding = false
	s.cacheProgress = 100
	s.mu.Unlock()

	buildTime := time.Since(buildStart)
	LogInfo("缓存构建: 完成",
		"cached", cachedCount, "total", total,
		"duration", buildTime.Round(time.Millisecond),
		"rate", fmt.Sprintf("%.1f/s", float64(cachedCount)/buildTime.Seconds()))

	return cachedCount
}

func (s *TPSStorage) buildSegmentCache(seg *SegmentRecord) (*CachedSegmentInfo, error) {
	startTotal := time.Now()

	recFile := s.GetRecFile(seg.FileIndex)
	if recFile == "" {
		return nil, fmt.Errorf("rec file not found")
	}

	// 加载帧索引（带 mmap 缓存）
	startFrameIndex := time.Now()
	frameIndex, err := ParseTRecFrameIndexWithCache(recFile)
	frameIndexTime := time.Since(startFrameIndex)
	if err != nil || len(frameIndex) == 0 {
		return nil, err
	}

	// 提取音频帧
	startAudio := time.Now()
	var audioFrames []FrameIndexRecord
	for _, f := range frameIndex {
		if f.Channel == ChannelAudio {
			audioFrames = append(audioFrames, f)
		}
	}
	sort.Slice(audioFrames, func(i, j int) bool {
		return audioFrames[i].FileOffset < audioFrames[j].FileOffset
	})
	audioTime := time.Since(startAudio)

	// 扫描 VPS 位置（带缓存）
	startVPS := time.Now()
	vpsOffsets, err := ScanVPSPositionsWithCache(recFile)
	if err != nil {
		return nil, err
	}

	var vpsPositions []VPSPosition
	for _, offset := range vpsOffsets {
		if offset < TRecIndexRegionStart {
			// 使用音频帧时间戳
			preciseTime := s.findAudioTimeForOffset(audioFrames, offset, seg)
			vpsPositions = append(vpsPositions, VPSPosition{
				Offset: offset,
				Time:   preciseTime,
			})
		}
	}
	vpsTime := time.Since(startVPS)

	totalTime := time.Since(startTotal)
	LogDebug("段落缓存构建",
		"segment", seg.FileIndex,
		"frames", len(frameIndex),
		"vps", len(vpsPositions),
		"audio", len(audioFrames),
		"t_index", frameIndexTime.Round(time.Millisecond),
		"t_audio", audioTime.Round(time.Millisecond),
		"t_vps", vpsTime.Round(time.Millisecond),
		"t_total", totalTime.Round(time.Millisecond))

	return &CachedSegmentInfo{
		Segment:      seg,
		FrameIndex:   frameIndex,
		VPSPositions: vpsPositions,
		AudioFrames:  audioFrames,
	}, nil
}

func (s *TPSStorage) findAudioTimeForOffset(audioFrames []FrameIndexRecord, targetOffset int, seg *SegmentRecord) int64 {
	if len(audioFrames) == 0 {
		return CalculatePreciseTime(seg, targetOffset)
	}

	best := audioFrames[0]
	for _, af := range audioFrames {
		if int(af.FileOffset) <= targetOffset {
			best = af
		} else {
			break
		}
	}
	return int64(best.UnixTs)
}

// GetCacheStatus 获取缓存状态
func (s *TPSStorage) GetCacheStatus() map[string]interface{} {
	s.mu.RLock()
	defer s.mu.RUnlock()

	cachedIndices := make([]int, 0, len(s.cachedSegments))
	for idx := range s.cachedSegments {
		cachedIndices = append(cachedIndices, idx)
	}
	sort.Ints(cachedIndices)

	return map[string]interface{}{
		"building":           s.cacheBuilding,
		"progress":           s.cacheProgress,
		"total_segments":     len(s.segments),
		"cached_segments":    len(s.cachedSegments),
		"cached_file_indices": cachedIndices,
	}
}

// IsSegmentCached 检查段落是否已缓存
func (s *TPSStorage) IsSegmentCached(fileIndex int) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	_, ok := s.cachedSegments[fileIndex]
	return ok
}

// GetCachedSegment 获取已缓存的段落信息
func (s *TPSStorage) GetCachedSegment(fileIndex int) *CachedSegmentInfo {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.cachedSegments[fileIndex]
}

// GetCachedSegments 获取所有已缓存段落
func (s *TPSStorage) GetCachedSegments() []*SegmentRecord {
	s.mu.RLock()
	defer s.mu.RUnlock()

	var result []*SegmentRecord
	for _, info := range s.cachedSegments {
		result = append(result, info.Segment)
	}
	return result
}

// GetAudioFrames 获取音频帧列表
func (s *TPSStorage) GetAudioFrames(fileIndex int) []FrameIndexRecord {
	s.mu.RLock()
	defer s.mu.RUnlock()

	if cached, ok := s.cachedSegments[fileIndex]; ok {
		return cached.AudioFrames
	}
	return nil
}

// ==================== 基础查询 ====================

// GetRecFile 获取录像文件路径
func (s *TPSStorage) GetRecFile(fileIndex int) string {
	filepath := filepath.Join(s.dvrPath, fmt.Sprintf("TRec%06d.tps", fileIndex))
	if _, err := os.Stat(filepath); os.IsNotExist(err) {
		return ""
	}
	return filepath
}

// FindSegmentByTime 根据时间戳查找段落
func (s *TPSStorage) FindSegmentByTime(timestamp int64, channel int, cachedOnly bool) *SegmentRecord {
	s.mu.RLock()
	defer s.mu.RUnlock()

	if cachedOnly {
		for _, info := range s.cachedSegments {
			seg := info.Segment
			if seg.Channel == channel && seg.StartTime <= timestamp && timestamp <= seg.EndTime {
				return seg
			}
		}
	} else {
		for i := range s.segments {
			seg := &s.segments[i]
			if seg.Channel == channel && seg.StartTime <= timestamp && timestamp <= seg.EndTime {
				return seg
			}
		}
	}
	return nil
}

// GetSegmentByFileIndex 根据文件索引查找段落
func (s *TPSStorage) GetSegmentByFileIndex(fileIndex int) *SegmentRecord {
	s.mu.RLock()
	if cached, ok := s.cachedSegments[fileIndex]; ok {
		s.mu.RUnlock()
		return cached.Segment
	}
	s.mu.RUnlock()

	for i := range s.segments {
		if s.segments[i].FileIndex == fileIndex {
			return &s.segments[i]
		}
	}
	return nil
}

// GetIFrameOffsets 获取 I 帧偏移列表
func (s *TPSStorage) GetIFrameOffsets(fileIndex int, channel int) []VPSPosition {
	s.mu.RLock()
	cached := s.cachedSegments[fileIndex]
	s.mu.RUnlock()

	if cached != nil && len(cached.VPSPositions) > 0 {
		return cached.VPSPositions
	}

	// 回退：从帧索引提取
	frameIndex := s.GetFrameIndex(fileIndex)
	var videoFrames []FrameIndexRecord
	for _, f := range frameIndex {
		if int(f.Channel) == channel {
			videoFrames = append(videoFrames, f)
		}
	}

	var iFrameOffsets []VPSPosition
	for _, vf := range videoFrames {
		if vf.FrameType == FrameTypeI {
			iFrameOffsets = append(iFrameOffsets, VPSPosition{
				Offset: int(vf.FileOffset),
				Time:   int64(vf.UnixTs),
			})
		}
	}

	sort.Slice(iFrameOffsets, func(i, j int) bool {
		return iFrameOffsets[i].Offset < iFrameOffsets[j].Offset
	})

	return iFrameOffsets
}

// GetFrameIndex 获取帧索引
func (s *TPSStorage) GetFrameIndex(fileIndex int) []FrameIndexRecord {
	s.mu.RLock()
	if cached, ok := s.cachedSegments[fileIndex]; ok {
		s.mu.RUnlock()
		return cached.FrameIndex
	}
	s.mu.RUnlock()
	return nil
}

// FindVPSForTime 使用 VPS 缓存查找目标时间对应的 VPS 位置
func (s *TPSStorage) FindVPSForTime(fileIndex int, targetTime int64) *VPSPosition {
	s.mu.RLock()
	cached := s.cachedSegments[fileIndex]
	s.mu.RUnlock()

	if cached == nil || len(cached.VPSPositions) == 0 {
		return nil
	}

	var bestVPS *VPSPosition
	for i := range cached.VPSPositions {
		vps := &cached.VPSPositions[i]
		if vps.Time <= targetTime {
			if bestVPS == nil || vps.Time > bestVPS.Time {
				bestVPS = vps
			}
		} else if bestVPS != nil {
			break
		}
	}

	if bestVPS == nil && len(cached.VPSPositions) > 0 {
		bestVPS = &cached.VPSPositions[0]
	}

	return bestVPS
}

// ReadVideoHeader 从 I 帧位置读取视频头
func (s *TPSStorage) ReadVideoHeader(fileIndex int, iframeOffset int64) *VideoHeader {
	recFile := s.GetRecFile(fileIndex)
	if recFile == "" {
		return nil
	}

	f, err := os.Open(recFile)
	if err != nil {
		return nil
	}
	defer f.Close()

	f.Seek(iframeOffset, 0)
	data := make([]byte, 512*1024) // 512KB
	n, err := f.Read(data)
	if err != nil || n == 0 {
		return nil
	}
	data = data[:n]

	header := FindVPSSPSPPSIDR(data)
	if header != nil {
		header.StreamStartPos = iframeOffset + header.StreamStartPos
	}
	return header
}

// CreateStreamReader 创建视频流读取器
func (s *TPSStorage) CreateStreamReader(fileIndex int, streamPos int64, startTimeMs int64, channel int) *VideoStreamReader {
	recFile := s.GetRecFile(fileIndex)
	if recFile == "" {
		return nil
	}

	f, err := os.Open(recFile)
	if err != nil {
		return nil
	}

	seg := s.GetSegmentByFileIndex(fileIndex)
	frameOffsets := s.GetIFrameOffsets(fileIndex, channel)

	return NewVideoStreamReader(f, streamPos, startTimeMs, seg, frameOffsets)
}

