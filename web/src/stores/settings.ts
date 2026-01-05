/**
 * 全局设置存储
 */

// 常用时区列表
export const TIMEZONES = [
  { value: 'Asia/Shanghai', label: '北京时间 (UTC+8)', offset: 8 },
  { value: 'Asia/Tokyo', label: '东京时间 (UTC+9)', offset: 9 },
  { value: 'Asia/Seoul', label: '首尔时间 (UTC+9)', offset: 9 },
  { value: 'Asia/Singapore', label: '新加坡时间 (UTC+8)', offset: 8 },
  { value: 'Asia/Hong_Kong', label: '香港时间 (UTC+8)', offset: 8 },
  { value: 'Asia/Taipei', label: '台北时间 (UTC+8)', offset: 8 },
  { value: 'Europe/London', label: '伦敦时间 (UTC+0)', offset: 0 },
  { value: 'Europe/Paris', label: '巴黎时间 (UTC+1)', offset: 1 },
  { value: 'Europe/Berlin', label: '柏林时间 (UTC+1)', offset: 1 },
  { value: 'America/New_York', label: '纽约时间 (UTC-5)', offset: -5 },
  { value: 'America/Los_Angeles', label: '洛杉矶时间 (UTC-8)', offset: -8 },
  { value: 'UTC', label: 'UTC (UTC+0)', offset: 0 },
] as const;

export type TimezoneValue = typeof TIMEZONES[number]['value'];

export interface Settings {
  timezone: TimezoneValue;
  storagePath: string;
  timeOffset: number;  // 时间偏移（秒），用于校正显示时间与视频水印的差异
}

const STORAGE_KEY = 'dvr-settings';

// 默认设置
const defaultSettings: Settings = {
  timezone: 'Asia/Shanghai',
  storagePath: '/Volumes/NO NAME',
  timeOffset: 0,  // 默认无偏移
};

// 从 localStorage 加载设置
export function loadSettings(): Settings {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      return { ...defaultSettings, ...JSON.parse(saved) };
    }
  } catch (e) {
    console.error('Failed to load settings:', e);
  }
  return defaultSettings;
}

// 保存设置到 localStorage
export function saveSettings(settings: Settings): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch (e) {
    console.error('Failed to save settings:', e);
  }
}

// 获取时区偏移量（小时）
export function getTimezoneOffset(timezone: TimezoneValue): number {
  const tz = TIMEZONES.find(t => t.value === timezone);
  return tz?.offset ?? 8;
}

// 格式化时间（使用指定时区）
export function formatTime(timestamp: number, timezone: TimezoneValue): string {
  const date = new Date(timestamp * 1000);
  return date.toLocaleTimeString('zh-CN', {
    timeZone: timezone,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

// 格式化日期时间（使用指定时区）
export function formatDateTime(timestamp: number, timezone: TimezoneValue): string {
  const date = new Date(timestamp * 1000);
  return date.toLocaleString('zh-CN', {
    timeZone: timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

// 获取指定日期在指定时区的开始时间戳（0点）
export function getDayStartTimestamp(dateStr: string, timezone: TimezoneValue): number {
  const [year, month, day] = dateStr.split('-').map(Number);
  const offset = getTimezoneOffset(timezone);
  // 创建 UTC 时间，然后减去时区偏移得到指定时区0点对应的 UTC 时间戳
  const utcDate = new Date(Date.UTC(year, month - 1, day, 0, 0, 0));
  return Math.floor(utcDate.getTime() / 1000) - offset * 60 * 60;
}
