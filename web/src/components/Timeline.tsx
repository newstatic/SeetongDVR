import { useMemo, useRef, useCallback } from 'react';
import { getChannelColor } from './ChannelSelector';
import { type TimezoneValue, getDayStartTimestamp, formatTime } from '../stores/settings';

export interface Recording {
  id: number;
  channel: number;
  startTime: number; // Unix timestamp
  endTime: number; // Unix timestamp
}

interface TimelineProps {
  recordings: Recording[];
  selectedChannels: number[];
  selectedDate: string | null; // YYYY-MM-DD 格式
  currentTime: number | null; // 当前播放时间 Unix timestamp
  timezone: TimezoneValue; // 时区
  onTimeClick: (timestamp: number) => void;
}

export function Timeline({
  recordings,
  selectedChannels,
  selectedDate,
  currentTime,
  timezone,
  onTimeClick,
}: TimelineProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  // 过滤选中通道的录像
  const filteredRecordings = useMemo(() => {
    if (selectedChannels.length === 0) return recordings;
    return recordings.filter((r) => selectedChannels.includes(r.channel));
  }, [recordings, selectedChannels]);

  // 根据选中日期和时区计算当天的开始时间
  const dayStart = useMemo(() => {
    if (!selectedDate) return 0;
    return getDayStartTimestamp(selectedDate, timezone);
  }, [selectedDate, timezone]);

  const dayEnd = dayStart + 24 * 60 * 60;

  // 计算时间在时间轴上的位置百分比
  const getPositionPercent = useCallback(
    (timestamp: number): number => {
      if (dayStart === 0) return 0;
      const percent = ((timestamp - dayStart) / (24 * 60 * 60)) * 100;
      return Math.max(0, Math.min(100, percent));
    },
    [dayStart]
  );

  // 处理点击事件
  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!containerRef.current || dayStart === 0) return;

      const rect = containerRef.current.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const percent = x / rect.width;
      const timestamp = dayStart + Math.floor(percent * 24 * 60 * 60);

      onTimeClick(timestamp);
    },
    [dayStart, onTimeClick]
  );

  // 小时刻度 (0, 3, 6, 9, 12, 15, 18, 21, 24) - 共9个刻度
  const hours = [0, 3, 6, 9, 12, 15, 18, 21, 24];

  // 格式化时间（使用时区设置）
  const formatTimeStr = useCallback((timestamp: number): string => {
    return formatTime(timestamp, timezone);
  }, [timezone]);

  return (
    <div className="bg-slate-800/50 rounded-xl border border-slate-700/50 p-4">
      {/* 刻度 */}
      <div className="flex justify-between text-xs text-slate-500 mb-2 px-1">
        {hours.map((hour) => (
          <span key={hour}>{hour}</span>
        ))}
      </div>

      {/* 时间轴 */}
      <div
        ref={containerRef}
        className="relative h-8 bg-slate-900/50 rounded cursor-pointer"
        onClick={handleClick}
      >
        {/* 录像片段 */}
        {filteredRecordings.map((recording) => {
          const left = getPositionPercent(recording.startTime);
          const width = getPositionPercent(recording.endTime) - left;

          return (
            <div
              key={recording.id}
              className="absolute top-1 bottom-1 rounded-sm opacity-80 hover:opacity-100 transition-opacity"
              style={{
                left: `${left}%`,
                width: `${Math.max(width, 0.5)}%`,
                backgroundColor: getChannelColor(recording.channel),
              }}
              title={`Ch${recording.channel}: ${formatTimeStr(recording.startTime)} - ${formatTimeStr(recording.endTime)}`}
            />
          );
        })}

        {/* 当前播放位置指示器 */}
        {currentTime !== null && currentTime >= dayStart && currentTime <= dayEnd && (
          <div
            className="absolute top-0 bottom-0 w-0.5 bg-white z-10"
            style={{ left: `${getPositionPercent(currentTime)}%` }}
          >
            <div className="absolute -top-1 left-1/2 -translate-x-1/2 w-2 h-2 bg-white rounded-full" />
          </div>
        )}

        {/* 小时分隔线 */}
        {hours.slice(1).map((hour) => (
          <div
            key={hour}
            className="absolute top-0 bottom-0 w-px bg-slate-700/50"
            style={{ left: `${(hour / 24) * 100}%` }}
          />
        ))}
      </div>

      {/* 当前时间显示 */}
      {currentTime !== null && (
        <div className="mt-2 text-center text-sm text-slate-400">
          当前: {formatTimeStr(currentTime)}
          <span className="ml-4 text-slate-500 text-xs">
            (ts: {currentTime}, UTC: {new Date(currentTime * 1000).toISOString()})
          </span>
        </div>
      )}

      {/* 无录像提示 */}
      {filteredRecordings.length === 0 && (
        <div className="mt-2 text-center text-sm text-slate-500">
          当天无录像记录
        </div>
      )}
    </div>
  );
}
