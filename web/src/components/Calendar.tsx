import { useState, useMemo } from 'react';

interface CalendarProps {
  recordingDates: string[]; // 格式: 'YYYY-MM-DD'
  selectedDate: string | null;
  onDateSelect: (date: string) => void;
}

export function Calendar({ recordingDates, selectedDate, onDateSelect }: CalendarProps) {
  const [currentMonth, setCurrentMonth] = useState(() => {
    const now = new Date();
    return { year: now.getFullYear(), month: now.getMonth() };
  });

  const recordingDateSet = useMemo(() => new Set(recordingDates), [recordingDates]);

  const { year, month } = currentMonth;

  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const firstDayOfWeek = new Date(year, month, 1).getDay();

  const monthName = new Date(year, month).toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: 'long',
  });

  const days = useMemo(() => {
    const result: (number | null)[] = [];
    // 填充月初空白
    for (let i = 0; i < firstDayOfWeek; i++) {
      result.push(null);
    }
    // 填充日期
    for (let i = 1; i <= daysInMonth; i++) {
      result.push(i);
    }
    return result;
  }, [daysInMonth, firstDayOfWeek]);

  const formatDate = (day: number): string => {
    return `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
  };

  const hasRecording = (day: number): boolean => {
    return recordingDateSet.has(formatDate(day));
  };

  const isSelected = (day: number): boolean => {
    return selectedDate === formatDate(day);
  };

  const isToday = (day: number): boolean => {
    const today = new Date();
    return (
      today.getFullYear() === year &&
      today.getMonth() === month &&
      today.getDate() === day
    );
  };

  const prevMonth = () => {
    setCurrentMonth((prev) => {
      if (prev.month === 0) {
        return { year: prev.year - 1, month: 11 };
      }
      return { year: prev.year, month: prev.month - 1 };
    });
  };

  const nextMonth = () => {
    setCurrentMonth((prev) => {
      if (prev.month === 11) {
        return { year: prev.year + 1, month: 0 };
      }
      return { year: prev.year, month: prev.month + 1 };
    });
  };

  const weekDays = ['日', '一', '二', '三', '四', '五', '六'];

  return (
    <div className="bg-slate-800/50 rounded-xl border border-slate-700/50 p-4">
      {/* 月份导航 */}
      <div className="flex items-center justify-between mb-4">
        <button
          onClick={prevMonth}
          className="p-1 hover:bg-slate-700/50 rounded transition-colors text-slate-400 hover:text-slate-200"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <span className="text-sm font-medium text-slate-200">{monthName}</span>
        <button
          onClick={nextMonth}
          className="p-1 hover:bg-slate-700/50 rounded transition-colors text-slate-400 hover:text-slate-200"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
        </button>
      </div>

      {/* 星期标题 */}
      <div className="grid grid-cols-7 gap-1 mb-2">
        {weekDays.map((day) => (
          <div key={day} className="text-center text-xs text-slate-500 py-1">
            {day}
          </div>
        ))}
      </div>

      {/* 日期网格 */}
      <div className="grid grid-cols-7 gap-1">
        {days.map((day, index) => (
          <div key={index} className="aspect-square">
            {day !== null && (
              <button
                onClick={() => hasRecording(day) && onDateSelect(formatDate(day))}
                disabled={!hasRecording(day)}
                className={`
                  w-full h-full flex flex-col items-center justify-center rounded-lg text-sm
                  transition-all relative
                  ${isSelected(day)
                    ? 'bg-primary-500 text-slate-900 font-medium'
                    : isToday(day)
                      ? 'bg-slate-700/50 text-slate-200'
                      : hasRecording(day)
                        ? 'hover:bg-slate-700/50 text-slate-200 cursor-pointer'
                        : 'text-slate-600 cursor-default'
                  }
                `}
              >
                {day}
                {/* 录像标记点 */}
                {hasRecording(day) && !isSelected(day) && (
                  <span className="absolute bottom-1 w-1 h-1 rounded-full bg-primary-400" />
                )}
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
