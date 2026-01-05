import { useState, useCallback, useRef, useEffect } from 'react';
import { VideoPlayer, type VideoPlayerHandle } from './components/VideoPlayer';
import { Calendar } from './components/Calendar';
import { ChannelSelector } from './components/ChannelSelector';
import { Timeline, type Recording } from './components/Timeline';
import { PlayerControls } from './components/PlayerControls';
import { StatsPanel } from './components/StatsPanel';
import { LogPanel, type LogEntry } from './components/LogPanel';
import { SettingsPanel } from './components/SettingsPanel';
import { SetupWizard } from './components/SetupWizard';
import { useWebSocket } from './hooks/useWebSocket';
import { HEVCDecoder, type DecoderStats } from './lib/hevc-decoder';
import { AudioPlayer } from './lib/audio-player';
import {
  getRecordingDates,
  getRecordings,
  getStreamUrl,
  setStoragePath,
  setTimezone,
  setTimeOffset,
  type StreamCommand,
} from './api';
import {
  loadSettings,
  saveSettings,
  formatTime,
  type TimezoneValue,
} from './stores/settings';

function App() {
  // 启动向导状态
  const [setupComplete, setSetupComplete] = useState(false);

  // 加载保存的设置
  const initialSettings = loadSettings();

  // 状态
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [stats, setStats] = useState<DecoderStats>({
    framesDecoded: 0,
    totalBytes: 0,
    fps: 0,
    isConfigured: false,
    waitingForKeyframe: true,
  });
  const [recordingDates, setRecordingDates] = useState<string[]>([]);
  const [channels, setChannels] = useState<number[]>([]);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [selectedChannels, setSelectedChannels] = useState<number[]>([]);
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState<number | null>(null);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [showLogs, setShowLogs] = useState(false);
  const [currentChannel, setCurrentChannel] = useState<number>(1);

  // 设置状态
  const [timezone, setTimezoneState] = useState<TimezoneValue>(initialSettings.timezone);
  const [storagePath, setStoragePathState] = useState<string>(initialSettings.storagePath);
  const [timeOffset, setTimeOffsetState] = useState<number>(initialSettings.timeOffset);

  // 音频状态
  const [isMuted, setIsMuted] = useState(false);
  const [volume, setVolume] = useState(1.0);

  // Refs
  const decoderRef = useRef<HEVCDecoder | null>(null);
  const audioPlayerRef = useRef<AudioPlayer | null>(null);
  const frameCallbackRef = useRef<((frame: VideoFrame) => void) | null>(null);
  const videoPlayerRef = useRef<VideoPlayerHandle>(null);
  const logIdRef = useRef(0);
  const wsRef = useRef<WebSocket | null>(null);

  // 录像信息
  const currentRecording = recordings.find(r =>
    selectedChannels.includes(r.channel) &&
    currentTime !== null &&
    r.startTime <= currentTime &&
    r.endTime >= currentTime
  ) || (recordings.length > 0 ? recordings[0] : null);

  const startTime = currentRecording?.startTime ?? 0;
  const duration = currentRecording ? currentRecording.endTime - currentRecording.startTime : 0;

  const addLog = useCallback((message: string, type: 'info' | 'success' | 'error' = 'info') => {
    const time = formatTime(Math.floor(Date.now() / 1000), timezone);
    setLogs((prev) => [
      ...prev.slice(-99),
      { id: logIdRef.current++, time, message, type },
    ]);
  }, [timezone]);

  const clearLogs = useCallback(() => {
    setLogs([]);
  }, []);

  // 发送 WebSocket 命令
  const sendCommand = useCallback((command: StreamCommand) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(command));
    }
  }, []);

  const handleMessage = useCallback((data: ArrayBuffer) => {
    const view = new Uint8Array(data);

    // 检查是否是音频帧 (Magic: 'G711')
    if (view.length >= 18 &&
        view[0] === 0x47 && view[1] === 0x37 && view[2] === 0x31 && view[3] === 0x31) {
      // 音频数据
      if (audioPlayerRef.current) {
        audioPlayerRef.current.processAudioData(data);
      }
      return;
    }

    // 视频数据
    if (decoderRef.current) {
      decoderRef.current.processData(data);
    }
  }, []);

  const { status, connect, disconnect } = useWebSocket({
    onMessage: handleMessage,
    onLog: addLog,
    onWebSocket: (ws) => { wsRef.current = ws; },
  });

  // 自动连接 WebSocket 并初始化解码器（只执行一次）
  const hasConnectedRef = useRef(false);
  useEffect(() => {
    if (hasConnectedRef.current) return;
    hasConnectedRef.current = true;

    const init = async () => {
      // 初始化视频解码器
      if (!decoderRef.current) {
        decoderRef.current = new HEVCDecoder({
          onFrame: (frame) => {
            if (frameCallbackRef.current) {
              frameCallbackRef.current(frame);
            } else {
              frame.close();
            }
          },
          onError: (error) => {
            addLog(`解码错误: ${error.message}`, 'error');
          },
          onLog: addLog,
          onStats: setStats,
          onTimeUpdate: (timestampMs) => {
            // 更新当前播放时间（毫秒转秒）
            const ts = Math.floor(timestampMs / 1000);
            setCurrentTime(ts);
            // 调试日志：每10秒打印一次
            if (ts % 10 === 0) {
              const date = new Date(timestampMs);
              console.log(`[TimeUpdate] timestamp=${ts}, date=${date.toISOString()}, local=${date.toLocaleString()}`);
            }
          },
        });
      }
      await decoderRef.current.init();

      // 初始化音频播放器
      if (!audioPlayerRef.current) {
        audioPlayerRef.current = new AudioPlayer({
          onLog: addLog,
        });
      }
      try {
        await audioPlayerRef.current.init();
      } catch (e) {
        addLog(`音频初始化失败: ${(e as Error).message}`, 'error');
      }

      // 连接 WebSocket
      connect(getStreamUrl());
    };

    init();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 加载录像日期（在 setupComplete 后执行）
  useEffect(() => {
    if (!setupComplete) return;

    const loadDates = async () => {
      try {
        const data = await getRecordingDates();
        setRecordingDates(data.dates);
        setChannels(data.channels);
        if (data.channels.length > 0) {
          setSelectedChannels(data.channels);
          setCurrentChannel(data.channels[0]);
        }
        addLog(`已加载 ${data.dates.length} 个录像日期`, 'success');
      } catch (error) {
        addLog(`加载录像日期失败: ${(error as Error).message}`, 'error');
      }
    };
    loadDates();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setupComplete]);

  // 日期选择
  const handleDateSelect = useCallback(async (date: string) => {
    setSelectedDate(date);
    setCurrentTime(null); // 重置播放时间
    addLog(`正在加载 ${date} 的录像...`, 'info');

    try {
      const data = await getRecordings(date);
      const mappedRecordings: Recording[] = data.recordings.map(r => ({
        id: r.id,
        channel: r.channel,
        startTime: r.startTimestamp,
        endTime: r.endTimestamp,
      }));
      setRecordings(mappedRecordings);
      addLog(`已加载 ${mappedRecordings.length} 个录像片段`, 'success');
    } catch (error) {
      addLog(`加载录像失败: ${(error as Error).message}`, 'error');
    }
  }, [addLog]);

  // 通道切换
  const handleChannelToggle = useCallback((channel: number) => {
    setSelectedChannels((prev) => {
      if (prev.includes(channel)) {
        return prev.filter((c) => c !== channel);
      }
      return [...prev, channel];
    });
  }, []);

  // 时间线点击 - 开始播放
  const handleTimeClick = useCallback(async (timestamp: number) => {
    setCurrentTime(timestamp);

    // 找到对应的录像
    const recording = recordings.find(r =>
      selectedChannels.includes(r.channel) &&
      r.startTime <= timestamp &&
      r.endTime >= timestamp
    );

    if (recording) {
      setCurrentChannel(recording.channel);
      addLog(`跳转到 Ch${recording.channel} ${formatTime(timestamp, timezone)}`, 'info');

      // 重置解码器状态以准备新的视频流
      if (decoderRef.current) {
        await decoderRef.current.init();
      }

      // 恢复和重置音频播放器
      if (audioPlayerRef.current) {
        await audioPlayerRef.current.resume();
        audioPlayerRef.current.reset();
      }

      // 发送 seek 命令
      sendCommand({
        action: 'seek',
        channel: recording.channel,
        timestamp,
        speed: playbackRate,
        audio: true,
      });
      setIsPlaying(true);
    }
  }, [recordings, selectedChannels, playbackRate, sendCommand, addLog, timezone]);

  // 播放控制
  const handlePlayPause = useCallback(async () => {
    const newPlaying = !isPlaying;
    setIsPlaying(newPlaying);

    if (newPlaying) {
      const ts = currentTime ?? startTime;

      // 恢复音频上下文（需要用户交互后才能播放）
      if (audioPlayerRef.current) {
        await audioPlayerRef.current.resume();
        audioPlayerRef.current.reset(); // 清空旧的音频队列
      }

      sendCommand({
        action: 'play',
        channel: currentChannel,
        timestamp: ts,
        speed: playbackRate,
        audio: true, // 启用音频
      });
      addLog('开始播放', 'info');
    } else {
      sendCommand({ action: 'pause' });
      if (audioPlayerRef.current) {
        audioPlayerRef.current.reset();
      }
      addLog('暂停播放', 'info');
    }
  }, [isPlaying, currentTime, startTime, currentChannel, playbackRate, sendCommand, addLog]);

  const handleSeek = useCallback(async (timestamp: number) => {
    setCurrentTime(timestamp);

    // 重置解码器状态以准备新的视频流
    if (decoderRef.current) {
      await decoderRef.current.init();
    }

    // 重置音频播放器
    if (audioPlayerRef.current) {
      audioPlayerRef.current.reset();
    }

    sendCommand({
      action: 'seek',
      channel: currentChannel,
      timestamp,
      speed: playbackRate,
      audio: true,
    });
  }, [currentChannel, playbackRate, sendCommand]);

  const handleRateChange = useCallback((rate: number) => {
    setPlaybackRate(rate);
    sendCommand({ action: 'speed', rate });
    addLog(`播放速度: ${rate}x`, 'info');
  }, [sendCommand, addLog]);

  const handleFullscreen = useCallback(() => {
    videoPlayerRef.current?.toggleFullscreen();
  }, []);

  const handleScreenshot = useCallback(() => {
    videoPlayerRef.current?.takeScreenshot();
    addLog('已保存截图', 'success');
  }, [addLog]);

  const handleStepBackward = useCallback(() => {
    addLog('逐帧后退 (暂不支持)', 'info');
  }, [addLog]);

  const handleStepForward = useCallback(() => {
    addLog('逐帧前进 (暂不支持)', 'info');
  }, [addLog]);

  // 音量控制
  const handleMuteToggle = useCallback(() => {
    const newMuted = !isMuted;
    setIsMuted(newMuted);
    if (audioPlayerRef.current) {
      audioPlayerRef.current.setMuted(newMuted);
    }
    addLog(newMuted ? '已静音' : '已取消静音', 'info');
  }, [isMuted, addLog]);

  const handleVolumeChange = useCallback((newVolume: number) => {
    setVolume(newVolume);
    if (audioPlayerRef.current) {
      audioPlayerRef.current.setVolume(newVolume);
      // 如果调整音量到非零，取消静音
      if (newVolume > 0 && isMuted) {
        setIsMuted(false);
        audioPlayerRef.current.setMuted(false);
      }
    }
  }, [isMuted]);

  // 连接处理
  const handleConnect = useCallback(async () => {
    try {
      if (!decoderRef.current) {
        decoderRef.current = new HEVCDecoder({
          onFrame: (frame) => {
            if (frameCallbackRef.current) {
              frameCallbackRef.current(frame);
            } else {
              frame.close();
            }
          },
          onError: (error) => {
            addLog(`解码错误: ${error.message}`, 'error');
          },
          onLog: addLog,
          onStats: setStats,
        });
      }

      await decoderRef.current.init();
      connect(getStreamUrl());
    } catch (error) {
      addLog(`初始化失败: ${(error as Error).message}`, 'error');
    }
  }, [connect, addLog]);

  const handleDisconnect = useCallback(() => {
    disconnect();
    wsRef.current = null;
    if (decoderRef.current) {
      decoderRef.current.reset();
    }
    setStats({
      framesDecoded: 0,
      totalBytes: 0,
      fps: 0,
      isConfigured: false,
      waitingForKeyframe: true,
    });
    setIsPlaying(false);
  }, [disconnect]);

  const registerFrameCallback = useCallback((callback: (frame: VideoFrame) => void) => {
    frameCallbackRef.current = callback;
  }, []);

  // 设置变更处理
  const handleTimezoneChange = useCallback(async (newTimezone: TimezoneValue) => {
    addLog(`正在切换时区到 ${newTimezone}...`, 'info');
    try {
      // 同步到服务器
      await setTimezone(newTimezone);
      setTimezoneState(newTimezone);
      saveSettings({ timezone: newTimezone, storagePath, timeOffset });
      addLog(`时区已更改为 ${newTimezone}`, 'success');

      // 重新加载录像日期（因为时区变化可能影响日期边界）
      const data = await getRecordingDates();
      setRecordingDates(data.dates);

      // 如果当前有选中的日期，重新加载该日期的录像
      if (selectedDate) {
        const recordings = await getRecordings(selectedDate);
        const mappedRecordings: Recording[] = recordings.recordings.map(r => ({
          id: r.id,
          channel: r.channel,
          startTime: r.startTimestamp,
          endTime: r.endTimestamp,
        }));
        setRecordings(mappedRecordings);
      }
    } catch (error) {
      addLog(`时区更改失败: ${(error as Error).message}`, 'error');
    }
  }, [storagePath, selectedDate, addLog]);

  const handleStoragePathChange = useCallback(async (newPath: string) => {
    addLog(`正在切换存储路径到 ${newPath}...`, 'info');
    try {
      const result = await setStoragePath(newPath);
      if (result.loaded) {
        setStoragePathState(newPath);
        saveSettings({ timezone, storagePath: newPath, timeOffset });
        addLog(`存储路径已更改，已加载 ${result.entryCount} 个条目`, 'success');

        // 重新加载录像日期
        const data = await getRecordingDates();
        setRecordingDates(data.dates);
        setChannels(data.channels);
        if (data.channels.length > 0) {
          setSelectedChannels(data.channels);
          setCurrentChannel(data.channels[0]);
        }
        setSelectedDate(null);
        setRecordings([]);
        setCurrentTime(null);
      } else {
        addLog(`存储路径更改失败: ${result.error}`, 'error');
      }
    } catch (error) {
      addLog(`存储路径更改失败: ${(error as Error).message}`, 'error');
    }
  }, [timezone, timeOffset, addLog]);

  const handleTimeOffsetChange = useCallback(async (newOffset: number) => {
    addLog(`正在设置时间偏移到 ${newOffset}秒...`, 'info');
    try {
      await setTimeOffset(newOffset);
      setTimeOffsetState(newOffset);
      saveSettings({ timezone, storagePath, timeOffset: newOffset });
      addLog(`时间偏移已设置为 ${newOffset}秒`, 'success');
    } catch (error) {
      addLog(`时间偏移设置失败: ${(error as Error).message}`, 'error');
    }
  }, [timezone, storagePath, addLog]);

  // 键盘快捷键
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return;

      switch (e.key) {
        case ' ':
          e.preventDefault();
          handlePlayPause();
          break;
        case 'ArrowLeft':
          handleStepBackward();
          break;
        case 'ArrowRight':
          handleStepForward();
          break;
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handlePlayPause, handleStepBackward, handleStepForward]);

  // 检查 HEVC 支持
  useEffect(() => {
    const checkSupport = async () => {
      try {
        const decoder = new HEVCDecoder({
          onFrame: () => {},
          onError: () => {},
          onLog: addLog,
          onStats: () => {},
        });
        await decoder.checkSupport();
        decoder.close();
      } catch (error) {
        addLog((error as Error).message, 'error');
      }
    };
    checkSupport();
  }, [addLog]);

  // 显示启动向导
  if (!setupComplete) {
    return <SetupWizard onComplete={() => setSetupComplete(true)} />;
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900">
      {/* Header */}
      <header className="border-b border-slate-700/50 bg-slate-900/50 backdrop-blur sticky top-0 z-50">
        <div className="container mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 bg-primary-500 rounded-lg flex items-center justify-center">
              <svg className="w-5 h-5 text-slate-900" fill="currentColor" viewBox="0 0 24 24">
                <path d="M18 4l2 4h-3l-2-4h-2l2 4h-3l-2-4H8l2 4H7L5 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V4h-4z" />
              </svg>
            </div>
            <h1 className="text-lg font-semibold text-slate-200">天视通 DVR 查看器</h1>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowLogs(!showLogs)}
              className={`p-2 rounded-lg transition-colors ${
                showLogs ? 'bg-primary-500/20 text-primary-400' : 'hover:bg-slate-700/50 text-slate-400'
              }`}
              title="日志"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h7" />
              </svg>
            </button>
            <SettingsPanel
              timezone={timezone}
              storagePath={storagePath}
              timeOffset={timeOffset}
              onTimezoneChange={handleTimezoneChange}
              onStoragePathChange={handleStoragePathChange}
              onTimeOffsetChange={handleTimeOffsetChange}
            />
            <button
              className="p-2 hover:bg-slate-700/50 rounded-lg transition-colors text-slate-400"
              title="帮助"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </button>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <div className="container mx-auto px-4 py-6">
        <div className="flex gap-6">
          {/* 左侧边栏 */}
          <aside className="w-64 flex-shrink-0 space-y-4">
            {/* 日历 */}
            <Calendar
              recordingDates={recordingDates}
              selectedDate={selectedDate}
              onDateSelect={handleDateSelect}
            />

            {/* 通道选择 */}
            <ChannelSelector
              channels={channels}
              selectedChannels={selectedChannels}
              onChannelToggle={handleChannelToggle}
            />

            {/* 连接控制 */}
            <div className="bg-slate-800/50 rounded-xl border border-slate-700/50 p-4">
              <h3 className="text-sm font-medium text-slate-300 mb-3">服务器连接</h3>
              <button
                onClick={() => {
                  if (status === 'connected' || status === 'connecting') {
                    handleDisconnect();
                  } else {
                    handleConnect();
                  }
                }}
                className={`w-full py-2 rounded-lg font-medium text-sm transition-all ${
                  status === 'connected' || status === 'connecting'
                    ? 'bg-red-500/80 hover:bg-red-500 text-white'
                    : 'bg-primary-500/80 hover:bg-primary-500 text-slate-900'
                }`}
              >
                {status === 'connecting' ? '连接中...' : status === 'connected' ? '断开连接' : '连接服务器'}
              </button>
              <p className="text-xs text-slate-500 mt-2 text-center">
                {status === 'connected' ? '已连接' : status === 'error' ? '连接失败' : '未连接'}
              </p>
            </div>

            {/* 统计面板 */}
            <StatsPanel stats={stats} connectionStatus={status} />
          </aside>

          {/* 右侧主区域 */}
          <main className="flex-1 space-y-4">
            {/* 视频播放器 */}
            <VideoPlayer ref={videoPlayerRef} onFrameReceived={registerFrameCallback} />

            {/* 播放控制条 */}
            <PlayerControls
              isPlaying={isPlaying}
              currentTime={currentTime ?? startTime}
              duration={duration}
              startTime={startTime}
              playbackRate={playbackRate}
              isMuted={isMuted}
              volume={volume}
              onPlayPause={handlePlayPause}
              onSeek={handleSeek}
              onRateChange={handleRateChange}
              onFullscreen={handleFullscreen}
              onScreenshot={handleScreenshot}
              onStepBackward={handleStepBackward}
              onStepForward={handleStepForward}
              onMuteToggle={handleMuteToggle}
              onVolumeChange={handleVolumeChange}
            />

            {/* 时间线 */}
            <Timeline
              recordings={recordings}
              selectedChannels={selectedChannels}
              selectedDate={selectedDate}
              currentTime={currentTime}
              timezone={timezone}
              onTimeClick={handleTimeClick}
            />

            {/* 日志面板 */}
            {showLogs && (
              <LogPanel logs={logs} onClear={clearLogs} />
            )}
          </main>
        </div>
      </div>

      {/* Footer */}
      <footer className="border-t border-slate-700/50 py-4 mt-8">
        <div className="container mx-auto px-4 text-center text-sm text-slate-500">
          <p>
            需要启用{' '}
            <code className="px-1.5 py-0.5 bg-slate-800 rounded text-primary-400">
              chrome://flags/#enable-platform-hevc
            </code>
            {' '}以支持H.265硬件解码
          </p>
        </div>
      </footer>
    </div>
  );
}

export default App;
