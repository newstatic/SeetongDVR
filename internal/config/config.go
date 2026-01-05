package config

const (
	// TRec 文件常量
	FileSize         = 0x10000000 // 256MB
	IndexRegionStart = 0x0F900000 // 索引区域起始
	FrameIndexMagic  = 0x4C3D2E1F
	FrameIndexSize   = 44 // 字节

	// 有效通道 (与 models 保持一致)
	ChannelVideo1 = 2
	ChannelVideo2 = 3
	ChannelAudio  = 258

	// 时间戳下限 (2020-01-01)
	MinValidTimestamp = 1577836800
)

var (
	// 默认配置
	DefaultDVRPath  = "/Volumes/DVR-2T/Seetong/Stream"
	DefaultTimezone = "Asia/Shanghai"
	Host            = "0.0.0.0"
	Port            = 8080
)

// ValidChannels 返回有效通道列表
func ValidChannels() []uint32 {
	return []uint32{ChannelVideo1, ChannelAudio, ChannelVideo2}
}

// IsValidChannel 检查通道是否有效
func IsValidChannel(ch uint32) bool {
	return ch == ChannelVideo1 || ch == ChannelVideo2 || ch == ChannelAudio
}
