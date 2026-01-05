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
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      <StatCard
        label="连接状态"
        value={getStatusText()}
        indicator={<div className={`w-3 h-3 rounded-full ${getStatusColor()}`} />}
      />
      <StatCard
        label="FPS"
        value={stats.fps.toString()}
        sublabel={stats.isConfigured ? '解码中' : '等待配置'}
      />
      <StatCard
        label="已解码帧"
        value={stats.framesDecoded.toLocaleString()}
      />
      <StatCard
        label="接收数据"
        value={formatBytes(stats.totalBytes)}
      />
    </div>
  );
}

interface StatCardProps {
  label: string;
  value: string;
  sublabel?: string;
  indicator?: React.ReactNode;
}

function StatCard({ label, value, sublabel, indicator }: StatCardProps) {
  return (
    <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700/50">
      <div className="flex items-center gap-2 mb-1">
        {indicator}
        <span className="text-xs text-slate-400 uppercase tracking-wide">{label}</span>
      </div>
      <div className="text-2xl font-bold text-primary-400">{value}</div>
      {sublabel && (
        <div className="text-xs text-slate-500 mt-1">{sublabel}</div>
      )}
    </div>
  );
}
