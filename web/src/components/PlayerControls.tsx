import { useState, useCallback, useRef } from 'react';

interface PlayerControlsProps {
  isPlaying: boolean;
  currentTime: number; // 当前播放时间 Unix timestamp
  duration: number; // 总时长（秒）
  startTime: number; // 开始时间 Unix timestamp
  playbackRate: number;
  isMuted?: boolean;
  volume?: number;
  onPlayPause: () => void;
  onSeek: (timestamp: number) => void;
  onRateChange: (rate: number) => void;
  onFullscreen: () => void;
  onScreenshot: () => void;
  onStepBackward: () => void;
  onStepForward: () => void;
  onMuteToggle?: () => void;
  onVolumeChange?: (volume: number) => void;
}

export function PlayerControls({
  isPlaying,
  currentTime,
  duration,
  startTime,
  playbackRate,
  isMuted = false,
  volume = 1,
  onPlayPause,
  onSeek,
  onRateChange,
  onFullscreen,
  onScreenshot,
  onStepBackward,
  onStepForward,
  onMuteToggle,
  onVolumeChange,
}: PlayerControlsProps) {
  const [showRateMenu, setShowRateMenu] = useState(false);
  const progressRef = useRef<HTMLDivElement>(null);

  const rates = [0.5, 1, 2, 4];

  // 计算播放进度百分比
  const progressPercent = duration > 0
    ? ((currentTime - startTime) / duration) * 100
    : 0;

  // 格式化时间
  const formatDuration = (seconds: number): string => {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) {
      return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
    }
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  const currentOffset = currentTime - startTime;

  // 处理进度条点击
  const handleProgressClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!progressRef.current || duration === 0) return;

      const rect = progressRef.current.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const percent = x / rect.width;
      const newTime = startTime + Math.floor(percent * duration);

      onSeek(newTime);
    },
    [startTime, duration, onSeek]
  );

  return (
    <div className="bg-slate-900/80 backdrop-blur rounded-lg p-3 space-y-3">
      {/* 进度条 */}
      <div
        ref={progressRef}
        className="relative h-2 bg-slate-700 rounded cursor-pointer group"
        onClick={handleProgressClick}
      >
        {/* 已播放部分 */}
        <div
          className="absolute left-0 top-0 h-full bg-primary-500 rounded transition-all"
          style={{ width: `${progressPercent}%` }}
        />
        {/* 拖动手柄 */}
        <div
          className="absolute top-1/2 -translate-y-1/2 w-4 h-4 bg-primary-400 rounded-full
                     opacity-0 group-hover:opacity-100 transition-opacity shadow-lg"
          style={{ left: `calc(${progressPercent}% - 8px)` }}
        />
      </div>

      {/* 控制按钮 */}
      <div className="flex items-center gap-4">
        {/* 左侧：播放控制 */}
        <div className="flex items-center gap-2">
          {/* 上一帧 */}
          <button
            onClick={onStepBackward}
            className="p-2 hover:bg-slate-700/50 rounded-lg transition-colors text-slate-400 hover:text-slate-200"
            title="上一帧 (←)"
          >
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M6 6h2v12H6V6zm3.5 6l8.5 6V6l-8.5 6z" />
            </svg>
          </button>

          {/* 播放/暂停 */}
          <button
            onClick={onPlayPause}
            className="p-3 bg-primary-500 hover:bg-primary-400 rounded-full transition-colors text-slate-900"
            title={isPlaying ? '暂停 (空格)' : '播放 (空格)'}
          >
            {isPlaying ? (
              <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M6 4h4v16H6V4zm8 0h4v16h-4V4z" />
              </svg>
            ) : (
              <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M8 5v14l11-7L8 5z" />
              </svg>
            )}
          </button>

          {/* 下一帧 */}
          <button
            onClick={onStepForward}
            className="p-2 hover:bg-slate-700/50 rounded-lg transition-colors text-slate-400 hover:text-slate-200"
            title="下一帧 (→)"
          >
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M6 18l8.5-6L6 6v12zm10-12v12h2V6h-2z" />
            </svg>
          </button>
        </div>

        {/* 中间：时间显示 */}
        <div className="flex-1 text-center text-sm text-slate-300 font-mono">
          {formatDuration(currentOffset)} / {formatDuration(duration)}
        </div>

        {/* 音量控制 */}
        {onMuteToggle && (
          <div className="flex items-center gap-2">
            <button
              onClick={onMuteToggle}
              className="p-2 hover:bg-slate-700/50 rounded-lg transition-colors text-slate-400 hover:text-slate-200"
              title={isMuted ? '取消静音' : '静音'}
            >
              {isMuted ? (
                <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z" />
                </svg>
              ) : volume > 0.5 ? (
                <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z" />
                </svg>
              ) : volume > 0 ? (
                <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M18.5 12c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM5 9v6h4l5 5V4L9 9H5z" />
                </svg>
              ) : (
                <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M7 9v6h4l5 5V4l-5 5H7z" />
                </svg>
              )}
            </button>
            {onVolumeChange && (
              <input
                type="range"
                min="0"
                max="1"
                step="0.1"
                value={isMuted ? 0 : volume}
                onChange={(e) => onVolumeChange(parseFloat(e.target.value))}
                className="w-16 h-1 bg-slate-700 rounded-lg appearance-none cursor-pointer
                  [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3
                  [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:bg-primary-400
                  [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:cursor-pointer"
                title={`音量: ${Math.round((isMuted ? 0 : volume) * 100)}%`}
              />
            )}
          </div>
        )}

        {/* 右侧：工具按钮 */}
        <div className="flex items-center gap-2">
          {/* 倍速选择 */}
          <div className="relative">
            <button
              onClick={() => setShowRateMenu(!showRateMenu)}
              className="px-3 py-1.5 hover:bg-slate-700/50 rounded-lg transition-colors text-slate-300 text-sm"
            >
              {playbackRate}x
            </button>
            {showRateMenu && (
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 bg-slate-800 border border-slate-700 rounded-lg shadow-xl py-1">
                {rates.map((rate) => (
                  <button
                    key={rate}
                    onClick={() => {
                      onRateChange(rate);
                      setShowRateMenu(false);
                    }}
                    className={`block w-full px-4 py-1.5 text-sm text-left hover:bg-slate-700 transition-colors
                      ${rate === playbackRate ? 'text-primary-400' : 'text-slate-300'}`}
                  >
                    {rate}x
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* 截图 */}
          <button
            onClick={onScreenshot}
            className="p-2 hover:bg-slate-700/50 rounded-lg transition-colors text-slate-400 hover:text-slate-200"
            title="截图"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z"
              />
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M15 13a3 3 0 11-6 0 3 3 0 016 0z"
              />
            </svg>
          </button>

          {/* 全屏 */}
          <button
            onClick={onFullscreen}
            className="p-2 hover:bg-slate-700/50 rounded-lg transition-colors text-slate-400 hover:text-slate-200"
            title="全屏 (F)"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 8V4m0 0h4M4 4l5 5m11-5h-4m4 0v4m0 0l-5-5m-7 14H4m0 0v-4m0 4l5-5m11 5v-4m0 4h-4m0 0l5-5"
              />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}
