import { useRef, useEffect } from 'react';

export interface LogEntry {
  id: number;
  time: string;
  message: string;
  type: 'info' | 'success' | 'error';
}

interface LogPanelProps {
  logs: LogEntry[];
  onClear: () => void;
}

export function LogPanel({ logs, onClear }: LogPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [logs]);

  const getTypeClass = (type: LogEntry['type']): string => {
    switch (type) {
      case 'success':
        return 'text-green-400';
      case 'error':
        return 'text-red-400';
      default:
        return 'text-slate-300';
    }
  };

  return (
    <div className="bg-slate-800/50 rounded-xl border border-slate-700/50 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 bg-slate-800/80 border-b border-slate-700/50">
        <h3 className="text-sm font-medium text-slate-300">日志</h3>
        <button
          onClick={onClear}
          className="text-xs text-slate-400 hover:text-slate-200 transition-colors"
        >
          清除
        </button>
      </div>
      <div
        ref={containerRef}
        className="h-48 overflow-y-auto p-3 font-mono text-xs"
      >
        {logs.length === 0 ? (
          <div className="text-slate-500 text-center py-8">暂无日志</div>
        ) : (
          logs.map((log) => (
            <div
              key={log.id}
              className={`py-0.5 ${getTypeClass(log.type)}`}
            >
              <span className="text-slate-500">[{log.time}]</span>{' '}
              {log.message}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
