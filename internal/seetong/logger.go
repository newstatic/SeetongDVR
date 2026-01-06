package seetong

import (
	"log/slog"
	"os"
	"sync"
)

var (
	logger    *slog.Logger
	loggerMu  sync.RWMutex
	debugMode bool
)

func init() {
	// 默认使用 Info 级别的文本处理器
	logger = slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{
		Level: slog.LevelInfo,
	}))
}

// SetDebugMode 设置调试模式
func SetDebugMode(enabled bool) {
	loggerMu.Lock()
	defer loggerMu.Unlock()
	debugMode = enabled

	level := slog.LevelInfo
	if enabled {
		level = slog.LevelDebug
	}

	logger = slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{
		Level: level,
	}))
}

// IsDebugMode 是否调试模式
func IsDebugMode() bool {
	loggerMu.RLock()
	defer loggerMu.RUnlock()
	return debugMode
}

// LogDebug 调试日志
func LogDebug(msg string, args ...any) {
	loggerMu.RLock()
	l := logger
	loggerMu.RUnlock()
	l.Debug(msg, args...)
}

// LogInfo 信息日志
func LogInfo(msg string, args ...any) {
	loggerMu.RLock()
	l := logger
	loggerMu.RUnlock()
	l.Info(msg, args...)
}

// LogWarn 警告日志
func LogWarn(msg string, args ...any) {
	loggerMu.RLock()
	l := logger
	loggerMu.RUnlock()
	l.Warn(msg, args...)
}

// LogError 错误日志
func LogError(msg string, args ...any) {
	loggerMu.RLock()
	l := logger
	loggerMu.RUnlock()
	l.Error(msg, args...)
}
