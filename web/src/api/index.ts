/**
 * DVR API 客户端
 */

const API_BASE = import.meta.env.VITE_API_BASE || '';

export interface RecordingDatesResponse {
  dates: string[];
  channels: number[];
}

export interface Recording {
  id: number;
  channel: number;
  start: string;
  end: string;
  startTimestamp: number;
  endTimestamp: number;
  duration: number;
  frameCount: number;
}

export interface RecordingsResponse {
  recordings: Recording[];
  error?: string;
}

export interface CacheStatus {
  status: 'not_loaded' | 'building' | 'ready';
  progress: number;
  total: number;
  current: number;
  cached: number;
}

export interface ConfigResponse {
  storagePath: string;
  loaded: boolean;
  timezone?: string;
  timeOffset?: number;
  entryCount?: number;
  fileCount?: number;
  cacheStatus?: CacheStatus;
  pathHistory?: string[];  // 路径历史记录
  error?: string;
}

/**
 * 获取服务器配置
 */
export async function getConfig(): Promise<ConfigResponse> {
  const response = await fetch(`${API_BASE}/api/v1/config`);
  if (!response.ok) {
    throw new Error(`API 错误: ${response.status}`);
  }
  return response.json();
}

/**
 * 设置存储路径
 */
export async function setStoragePath(path: string): Promise<ConfigResponse> {
  const response = await fetch(`${API_BASE}/api/v1/config`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ storagePath: path }),
  });

  if (!response.ok) {
    throw new Error(`API 错误: ${response.status}`);
  }

  return response.json();
}

/**
 * 设置服务器时区
 */
export async function setTimezone(timezone: string): Promise<ConfigResponse> {
  const response = await fetch(`${API_BASE}/api/v1/config`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ timezone }),
  });

  if (!response.ok) {
    throw new Error(`API 错误: ${response.status}`);
  }

  return response.json();
}

/**
 * 设置时间偏移（秒）
 */
export async function setTimeOffset(offset: number): Promise<ConfigResponse> {
  const response = await fetch(`${API_BASE}/api/v1/config`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ timeOffset: offset }),
  });

  if (!response.ok) {
    throw new Error(`API 错误: ${response.status}`);
  }

  return response.json();
}

/**
 * 获取有录像的日期列表
 */
export async function getRecordingDates(channel?: number): Promise<RecordingDatesResponse> {
  const params = new URLSearchParams();
  if (channel !== undefined) {
    params.set('channel', channel.toString());
  }

  const url = `${API_BASE}/api/v1/recordings/dates${params.toString() ? '?' + params : ''}`;
  const response = await fetch(url);

  if (!response.ok) {
    throw new Error(`API 错误: ${response.status}`);
  }

  return response.json();
}

/**
 * 获取指定日期的录像列表
 */
export async function getRecordings(date: string, channel?: number): Promise<RecordingsResponse> {
  const params = new URLSearchParams({ date });
  if (channel !== undefined) {
    params.set('channel', channel.toString());
  }

  const url = `${API_BASE}/api/v1/recordings?${params}`;
  const response = await fetch(url);

  if (!response.ok) {
    throw new Error(`API 错误: ${response.status}`);
  }

  return response.json();
}

/**
 * WebSocket 命令
 */
export interface PlayCommand {
  action: 'play';
  channel: number;
  timestamp: number;
  speed?: number;
  audio?: boolean;
}

export interface PauseCommand {
  action: 'pause';
}

export interface SeekCommand {
  action: 'seek';
  channel: number;
  timestamp: number;
  speed?: number;
  audio?: boolean;
}

export interface SpeedCommand {
  action: 'speed';
  rate: number;
}

export type StreamCommand = PlayCommand | PauseCommand | SeekCommand | SpeedCommand;

/**
 * 获取缓存状态
 */
export async function getCacheStatus(): Promise<CacheStatus> {
  const response = await fetch(`${API_BASE}/api/v1/cache/status`);
  if (!response.ok) {
    throw new Error(`API 错误: ${response.status}`);
  }
  return response.json();
}

/**
 * 获取 WebSocket URL
 */
export function getStreamUrl(): string {
  // 使用当前页面的 host，支持同源部署
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/api/v1/stream`;
}
