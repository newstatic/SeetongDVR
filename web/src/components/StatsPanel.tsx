import type { DecoderStats } from '../lib/hevc-decoder';
import type { ConnectionStatus } from '../hooks/useWebSocket';

interface StatsPanelProps {
  stats: DecoderStats;
  connectionStatus: ConnectionStatus;
}

export function StatsPanel({ stats, connectionStatus }: StatsPanelProps) {
  const formatBytes = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  };

  const getStatusColor = (): string => {
    switch (connectionStatus) {
      case 'connected':
        return 'bg-green-500';
      case 'connecting':
        return 'bg-yellow-500 animate-pulse';
      case 'error':
        return 'bg-red-500';
      default:
        return 'bg-gray-500';
    }
  };

  const getStatusText = (): string => {
    switch (connectionStatus) {
      case 'connected':
        return '已连接';
      case 'connecting':
        return '连接中...';
      case 'error':
        return '连接错误';
      default:
        return '未连接';
    }
  };

  return (
    <div className="bg-slate-800/50 rounded-xl p-3 border border-slate-700/50 space-y-2">
      <div className="flex items-center justify-between gap-3">
        <StatItem
          label="状态"
          value={getStatusText()}
          indicator={<div className={`w-2 h-2 rounded-full ${getStatusColor()}`} />}
        />
        <StatItem
          label="FPS"
          value={stats.fps.toString()}
        />
      </div>
      <div className="text-xs text-slate-400 space-y-1">
        <div className="flex justify-between">
          <span>帧数</span>
          <span className="text-primary-400">{stats.framesDecoded.toLocaleString()}</span>
        </div>
        <div className="flex justify-between">
          <span>传输数据</span>
          <span className="text-primary-400">{formatBytes(stats.totalBytes)}</span>
        </div>
      </div>
    </div>
  );
}

interface StatItemProps {
  label: string;
  value: string;
  indicator?: React.ReactNode;
  sublabel?: string;
}

function StatItem({ label, value, indicator, sublabel }: StatItemProps) {
  return (
    <div className="text-center flex-1">
      <div className="flex items-center justify-center gap-1 mb-0.5">
        {indicator}
        <span className="text-[10px] text-slate-500">{label}</span>
      </div>
      <div className="text-sm font-semibold text-primary-400">{value}</div>
      {sublabel && <div className="text-[10px] text-slate-500">{sublabel}</div>}
    </div>
  );
}
