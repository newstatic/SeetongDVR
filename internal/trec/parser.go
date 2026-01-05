package trec

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"

	"seetong-dvr/internal/models"
)

const (
	IndexRegionStart = 0xF900000            // 索引区起始位置
	IndexSearchSize  = 0x700000             // 搜索范围 7MB
	IndexEntrySize   = 44                   // 每条索引 44 字节
	FrameIndexMagic  = 0x4C3D2E1F           // 帧索引魔数
	MinValidTime     = 1577836800           // 2020-01-01
)

// 有效通道
var validChannels = map[uint32]bool{
	2:   true, // Video CH1
	3:   true, // Video CH2
	258: true, // Audio
}

// ParseFrameIndex 解析 TRec 文件的帧索引
func ParseFrameIndex(filePath string) ([]models.FrameIndexRecord, error) {
	f, err := os.Open(filePath)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	info, err := f.Stat()
	if err != nil {
		return nil, err
	}

	fileSize := info.Size()
	if fileSize < IndexRegionStart {
		return nil, fmt.Errorf("file too small: %d", fileSize)
	}

	// 在索引区域搜索帧索引起始位置
	indexStart := findFrameIndexStart(f, IndexRegionStart, IndexSearchSize)
	if indexStart < 0 {
		fmt.Printf("[TRec] 解析: %s (0 帧) - 未找到帧索引\n", filepath.Base(filePath))
		return nil, nil
	}

	// 从找到的位置开始解析
	if _, err := f.Seek(int64(indexStart), io.SeekStart); err != nil {
		return nil, err
	}

	var records []models.FrameIndexRecord
	entry := make([]byte, IndexEntrySize)
	magicBytes := make([]byte, 4)
	binary.LittleEndian.PutUint32(magicBytes, FrameIndexMagic)

	for {
		if _, err := io.ReadFull(f, entry); err != nil {
			break
		}

		// 检查 magic
		magic := binary.LittleEndian.Uint32(entry[0:4])
		if magic != FrameIndexMagic {
			break
		}

		// 解析索引条目
		// 结构: magic(4) + frame_type(4) + channel(4) + frame_seq(4) + offset(4) + size(4) + timestamp_us(8) + unix_ts(4) + reserved(8)
		frameType := binary.LittleEndian.Uint32(entry[4:8])
		channel := binary.LittleEndian.Uint32(entry[8:12])
		frameSeq := binary.LittleEndian.Uint32(entry[12:16])
		fileOffset := binary.LittleEndian.Uint32(entry[16:20])
		frameSize := binary.LittleEndian.Uint32(entry[20:24])
		timestampUs := binary.LittleEndian.Uint64(entry[24:32])
		unixTs := binary.LittleEndian.Uint32(entry[32:36])

		// 过滤有效记录
		if unixTs > MinValidTime && validChannels[channel] {
			records = append(records, models.FrameIndexRecord{
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

	// 按时间正序排列（原始是倒序）
	sort.Slice(records, func(i, j int) bool {
		return records[i].TimestampUs < records[j].TimestampUs
	})

	fmt.Printf("[TRec] 解析: %s (%d 帧)\n", filepath.Base(filePath), len(records))
	return records, nil
}

// findFrameIndexStart 搜索帧索引起始位置
func findFrameIndexStart(f *os.File, searchStart int64, searchSize int64) int64 {
	magicBytes := make([]byte, 4)
	binary.LittleEndian.PutUint32(magicBytes, FrameIndexMagic)

	if _, err := f.Seek(searchStart, io.SeekStart); err != nil {
		return -1
	}

	data := make([]byte, searchSize)
	n, err := f.Read(data)
	if err != nil && err != io.EOF {
		return -1
	}
	data = data[:n]

	// 搜索第一个 magic
	idx := bytes.Index(data, magicBytes)
	if idx >= 0 {
		return searchStart + int64(idx)
	}

	return -1
}

// ReadFrame 读取帧数据
func ReadFrame(filePath string, offset, size uint32) ([]byte, error) {
	f, err := os.Open(filePath)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	if _, err := f.Seek(int64(offset), io.SeekStart); err != nil {
		return nil, err
	}

	data := make([]byte, size)
	if _, err := io.ReadFull(f, data); err != nil {
		return nil, err
	}

	return data, nil
}
