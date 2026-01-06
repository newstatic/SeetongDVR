"""
配置常量
"""

# 测试模式：只加载一个 TPS 文件，加快启动速度
TEST_MODE = True

# 服务器配置
DEFAULT_DVR_PATH = "/Volumes/NO NAME"
DEFAULT_TIMEZONE = "Asia/Shanghai"
HOST = "0.0.0.0"
PORT = 8152

# 音频配置
AUDIO_SAMPLE_RATE = 8000
AUDIO_FRAME_DURATION_MS = 160  # 每帧 160ms

# 视频配置
VIDEO_FPS = 25
VIDEO_FRAME_INTERVAL = 1.0 / VIDEO_FPS
