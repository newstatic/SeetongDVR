package tindex

import (
	"encoding/binary"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"
)

const (
	IndexStartOffset = 0x4FC
	EntrySize        = 0x40
	MinValidTime     = 1577836800 // 2020-01-01
)

// IndexEntry 索引条目
type IndexEntry struct {
	Offset     int64
	Channel    int
	FrameCount int
	StartTime  int64 // Unix 时间戳
	EndTime    int64
	FileOffset int64
	EntryIndex int
}

// StartDateTime 返回开始时间
func (e *IndexEntry) StartDateTime() time.Time {
	return time.Unix(e.StartTime, 0)
}

// EndDateTime 返回结束时间
func (e *IndexEntry) EndDateTime() time.Time {
	return time.Unix(e.EndTime, 0)
}

// Duration 返回持续时间
func (e *IndexEntry) Duration() time.Duration {
	return time.Duration(e.EndTime-e.StartTime) * time.Second
}

// TPSIndexParser TPS 索引文件解析器
type TPSIndexParser struct {
	FilePath   string
	FileCount  int
	EntryCount int
	Entries    []IndexEntry
}

// NewTPSIndexParser 创建解析器
func NewTPSIndexParser(filePath string) *TPSIndexParser {
	return &TPSIndexParser{
		FilePath: filePath,
	}
}

// Parse 解析索引文件
func (p *TPSIndexParser) Parse() error {
	f, err := os.Open(p.FilePath)
	if err != nil {
		return err
	}
	defer f.Close()

	// 读取头部
	if err := p.parseHeader(f); err != nil {
		return err
	}

	// 读取条目
	if err := p.parseEntries(f); err != nil {
		return err
	}

	fmt.Printf("[TIndex] 解析完成: %d 个条目\n", len(p.Entries))
	return nil
}

func (p *TPSIndexParser) parseHeader(f *os.File) error {
	// 读取文件数量
	if _, err := f.Seek(0x10, io.SeekStart); err != nil {
		return err
	}

	var fileCount, entryCount uint32
	if err := binary.Read(f, binary.LittleEndian, &fileCount); err != nil {
		return err
	}
	if err := binary.Read(f, binary.LittleEndian, &entryCount); err != nil {
		return err
	}

	p.FileCount = int(fileCount)
	p.EntryCount = int(entryCount)
	return nil
}

func (p *TPSIndexParser) parseEntries(f *os.File) error {
	// 获取文件大小
	info, err := f.Stat()
	if err != nil {
		return err
	}
	fileSize := info.Size()

	offset := int64(IndexStartOffset)
	entryIndex := 0

	data := make([]byte, EntrySize)

	for offset+EntrySize <= fileSize {
		if _, err := f.Seek(offset, io.SeekStart); err != nil {
			break
		}

		if _, err := io.ReadFull(f, data); err != nil {
			break
		}

		fileOffset := binary.LittleEndian.Uint32(data[0:4])
		channel := int(data[4])
		// flags := data[5]
		frameCount := binary.LittleEndian.Uint16(data[6:8])
		startTime := binary.LittleEndian.Uint32(data[8:12])
		endTime := binary.LittleEndian.Uint32(data[12:16])

		// 跳过无效条目
		if channel == 0 || channel == 0xFE {
			offset += EntrySize
			entryIndex++
			continue
		}

		// 跳过无效时间
		if int64(startTime) < MinValidTime || int64(endTime) < MinValidTime {
			offset += EntrySize
			entryIndex++
			continue
		}

		// 跳过时间范围错误
		if endTime <= startTime {
			offset += EntrySize
			entryIndex++
			continue
		}

		entry := IndexEntry{
			Offset:     offset,
			Channel:    channel,
			FrameCount: int(frameCount),
			StartTime:  int64(startTime),
			EndTime:    int64(endTime),
			FileOffset: int64(fileOffset),
			EntryIndex: entryIndex,
		}

		p.Entries = append(p.Entries, entry)
		offset += EntrySize
		entryIndex++
	}

	return nil
}

// GetRecFile 根据条目索引获取录像文件路径
func (p *TPSIndexParser) GetRecFile(entryIndex int) string {
	dir := filepath.Dir(p.FilePath)
	return filepath.Join(dir, fmt.Sprintf("TRec%06d.tps", entryIndex))
}

// GetTimeRange 获取时间范围
func (p *TPSIndexParser) GetTimeRange() (start, end time.Time) {
	if len(p.Entries) == 0 {
		return
	}

	minTime := p.Entries[0].StartTime
	maxTime := p.Entries[0].EndTime

	for _, e := range p.Entries {
		if e.StartTime < minTime {
			minTime = e.StartTime
		}
		if e.EndTime > maxTime {
			maxTime = e.EndTime
		}
	}

	return time.Unix(minTime, 0), time.Unix(maxTime, 0)
}

// GetChannels 获取所有通道
func (p *TPSIndexParser) GetChannels() []int {
	channelMap := make(map[int]bool)
	for _, e := range p.Entries {
		channelMap[e.Channel] = true
	}

	var channels []int
	for ch := range channelMap {
		channels = append(channels, ch)
	}
	return channels
}
