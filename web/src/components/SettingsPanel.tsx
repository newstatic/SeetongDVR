import { useState, useRef, useEffect } from 'react';
import { TIMEZONES, type TimezoneValue } from '../stores/settings';

interface SettingsPanelProps {
  timezone: TimezoneValue;
  storagePath: string;
  timeOffset: number;
  onTimezoneChange: (timezone: TimezoneValue) => void;
  onStoragePathChange: (path: string) => void;
  onTimeOffsetChange: (offset: number) => void;
}

export function SettingsPanel({
  timezone,
  storagePath,
  timeOffset,
  onTimezoneChange,
  onStoragePathChange,
  onTimeOffsetChange,
}: SettingsPanelProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [pathInput, setPathInput] = useState(storagePath);
  const [offsetInput, setOffsetInput] = useState(timeOffset.toString());
  const panelRef = useRef<HTMLDivElement>(null);

  // 同步外部路径变化
  useEffect(() => {
    setPathInput(storagePath);
  }, [storagePath]);

  // 同步外部偏移变化
  useEffect(() => {
    setOffsetInput(timeOffset.toString());
  }, [timeOffset]);

  // 点击外部关闭
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen]);

  const handlePathSubmit = () => {
    if (pathInput.trim() && pathInput !== storagePath) {
      onStoragePathChange(pathInput.trim());
    }
  };

  const handleOffsetSubmit = () => {
    const newOffset = parseInt(offsetInput, 10);
    if (!isNaN(newOffset) && newOffset !== timeOffset) {
      onTimeOffsetChange(newOffset);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handlePathSubmit();
    }
  };

  const handleOffsetKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleOffsetSubmit();
    }
  };

  // 快捷调整按钮
  const adjustOffset = (delta: number) => {
    const newOffset = timeOffset + delta;
    setOffsetInput(newOffset.toString());
    onTimeOffsetChange(newOffset);
  };

  const currentTz = TIMEZONES.find(t => t.value === timezone);

  return (
    <div ref={panelRef} className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={`p-2 rounded-lg transition-colors ${
          isOpen ? 'bg-primary-500/20 text-primary-400' : 'hover:bg-slate-700/50 text-slate-400'
        }`}
        title="设置"
      >
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"
          />
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
          />
        </svg>
      </button>

      {isOpen && (
        <div className="absolute right-0 top-full mt-2 w-80 bg-slate-800 rounded-xl border border-slate-700/50 shadow-xl z-50">
          <div className="p-4 space-y-4">
            <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              设置
            </h3>

            {/* 时区选择 */}
            <div>
              <label className="block text-xs text-slate-400 mb-1.5">时区</label>
              <select
                value={timezone}
                onChange={(e) => onTimezoneChange(e.target.value as TimezoneValue)}
                className="w-full bg-slate-900/50 border border-slate-600/50 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary-500/50"
              >
                {TIMEZONES.map((tz) => (
                  <option key={tz.value} value={tz.value}>
                    {tz.label}
                  </option>
                ))}
              </select>
              <p className="text-xs text-slate-500 mt-1">
                当前: {currentTz?.label}
              </p>
            </div>

            {/* 存储路径 */}
            <div>
              <label className="block text-xs text-slate-400 mb-1.5">DVR 存储路径</label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={pathInput}
                  onChange={(e) => setPathInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  onBlur={handlePathSubmit}
                  placeholder="/Volumes/NO NAME"
                  className="flex-1 bg-slate-900/50 border border-slate-600/50 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                />
                <button
                  onClick={handlePathSubmit}
                  className="px-3 py-2 bg-primary-500/80 hover:bg-primary-500 text-slate-900 rounded-lg text-sm font-medium transition-colors"
                >
                  应用
                </button>
              </div>
              <p className="text-xs text-slate-500 mt-1">
                修改后需要重新加载数据
              </p>
            </div>

            {/* 时间偏移 */}
            <div>
              <label className="block text-xs text-slate-400 mb-1.5">时间偏移校正（秒）</label>
              <div className="flex gap-2 items-center">
                <button
                  onClick={() => adjustOffset(-60)}
                  className="px-2 py-1 bg-slate-700/50 hover:bg-slate-600/50 text-slate-300 rounded text-xs"
                  title="-1分钟"
                >
                  -1m
                </button>
                <button
                  onClick={() => adjustOffset(-10)}
                  className="px-2 py-1 bg-slate-700/50 hover:bg-slate-600/50 text-slate-300 rounded text-xs"
                  title="-10秒"
                >
                  -10s
                </button>
                <input
                  type="number"
                  value={offsetInput}
                  onChange={(e) => setOffsetInput(e.target.value)}
                  onKeyDown={handleOffsetKeyDown}
                  onBlur={handleOffsetSubmit}
                  className="w-20 bg-slate-900/50 border border-slate-600/50 rounded-lg px-2 py-1 text-sm text-slate-200 text-center focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                />
                <button
                  onClick={() => adjustOffset(10)}
                  className="px-2 py-1 bg-slate-700/50 hover:bg-slate-600/50 text-slate-300 rounded text-xs"
                  title="+10秒"
                >
                  +10s
                </button>
                <button
                  onClick={() => adjustOffset(60)}
                  className="px-2 py-1 bg-slate-700/50 hover:bg-slate-600/50 text-slate-300 rounded text-xs"
                  title="+1分钟"
                >
                  +1m
                </button>
              </div>
              <p className="text-xs text-slate-500 mt-1">
                调整显示时间与视频水印的差异。正值使显示时间更晚。
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
