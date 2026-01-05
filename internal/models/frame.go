package models

// FrameIndexRecord 帧索引记录 (32 bytes, 内存布局优化)
// 将 uint64 放在开头确保 8 字节对齐，避免 padding
type FrameIndexRecord struct {
	TimestampUs uint64 // 微秒时间戳 (8 bytes) - 放在开头确保对齐
	FrameType   uint32 // 帧类型: 1=I帧, 2=P帧, 3=音频
	Channel     uint32 // 通道号
	FrameSeq    uint32 // 帧序号
	FileOffset  uint32 // 文件偏移
	FrameSize   uint32 // 帧大小
	UnixTs      uint32 // Unix时间戳
}

// FrameType 帧类型常量
const (
	FrameTypeI     = 1
	FrameTypeP     = 2
	FrameTypeAudio = 3
)

// Channel 通道常量
const (
	ChannelVideo1 = 2   // 视频通道1
	ChannelAudio  = 3   // 音频通道
	ChannelVideo2 = 258 // 视频通道2
)

// VPSCacheEntry VPS缓存条目
type VPSCacheEntry struct {
	FileIndex   int    // 文件索引
	RecFile     string // 录像文件路径
	StartTimeUs int64  // 开始时间(微秒)
	EndTimeUs   int64  // 结束时间(微秒)
	UnixTs      int64  // Unix时间戳
	FrameCount  int    // 帧数
	Channel     int    // 通道号
	StartTime   int64  // 开始时间(Unix秒)
	EndTime     int64  // 结束时间(Unix秒)
}
