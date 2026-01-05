interface ChannelSelectorProps {
  channels: number[];
  selectedChannels: number[];
  onChannelToggle: (channel: number) => void;
}

export function ChannelSelector({
  channels,
  selectedChannels,
  onChannelToggle,
}: ChannelSelectorProps) {
  const isSelected = (channel: number) => selectedChannels.includes(channel);

  return (
    <div className="bg-slate-800/50 rounded-xl border border-slate-700/50 p-4">
      <h3 className="text-sm font-medium text-slate-300 mb-3">通道选择</h3>
      <div className="space-y-2">
        {channels.map((channel) => (
          <label
            key={channel}
            className="flex items-center gap-3 cursor-pointer group"
          >
            <div
              className={`
                w-5 h-5 rounded border-2 flex items-center justify-center transition-all
                ${isSelected(channel)
                  ? 'bg-primary-500 border-primary-500'
                  : 'border-slate-600 group-hover:border-slate-500'
                }
              `}
              onClick={() => onChannelToggle(channel)}
            >
              {isSelected(channel) && (
                <svg className="w-3 h-3 text-slate-900" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                </svg>
              )}
            </div>
            <span
              className={`text-sm transition-colors ${
                isSelected(channel) ? 'text-slate-200' : 'text-slate-400'
              }`}
            >
              Ch{channel}
            </span>
            {/* 通道颜色指示器 */}
            <span
              className={`w-3 h-3 rounded-full ml-auto`}
              style={{ backgroundColor: getChannelColor(channel) }}
            />
          </label>
        ))}
      </div>
      {channels.length === 0 && (
        <p className="text-sm text-slate-500">暂无通道信息</p>
      )}
    </div>
  );
}

// 根据通道号生成颜色
export function getChannelColor(channel: number): string {
  const colors = [
    '#3b82f6', // blue
    '#10b981', // emerald
    '#f59e0b', // amber
    '#ef4444', // red
    '#8b5cf6', // violet
    '#ec4899', // pink
    '#06b6d4', // cyan
    '#84cc16', // lime
  ];
  return colors[(channel - 1) % colors.length];
}
