import { useCallback, useRef, useState } from 'react';

export type ConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'error';

interface UseWebSocketOptions {
  onMessage: (data: ArrayBuffer) => void;
  onLog: (message: string, type?: 'info' | 'success' | 'error') => void;
  onWebSocket?: (ws: WebSocket | null) => void;
}

export function useWebSocket({ onMessage, onLog, onWebSocket }: UseWebSocketOptions) {
  const [status, setStatus] = useState<ConnectionStatus>('disconnected');
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);

  const connect = useCallback((url: string) => {
    if (wsRef.current) {
      wsRef.current.close();
    }

    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
    }

    setStatus('connecting');
    onLog(`正在连接 ${url}...`);

    try {
      const ws = new WebSocket(url);
      ws.binaryType = 'arraybuffer';

      ws.onopen = () => {
        setStatus('connected');
        onLog('WebSocket 已连接', 'success');
      };

      ws.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) {
          onMessage(event.data);
        }
      };

      ws.onclose = (event) => {
        setStatus('disconnected');
        onLog(`连接已关闭 (${event.code})`, 'info');
        wsRef.current = null;
      };

      ws.onerror = () => {
        setStatus('error');
        onLog('WebSocket 连接错误', 'error');
      };

      wsRef.current = ws;
      onWebSocket?.(ws);
    } catch (error) {
      setStatus('error');
      onLog(`连接失败: ${(error as Error).message}`, 'error');
    }
  }, [onMessage, onLog, onWebSocket]);

  const disconnect = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
    }

    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
      onWebSocket?.(null);
    }

    setStatus('disconnected');
    onLog('已断开连接', 'info');
  }, [onLog, onWebSocket]);

  return {
    status,
    connect,
    disconnect,
  };
}
