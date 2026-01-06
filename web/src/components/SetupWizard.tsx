import { useState, useEffect, useCallback } from 'react';
import { getConfig, setStoragePath, getCacheStatus, type CacheStatus } from '../api';
import { loadSettings } from '../stores/settings';

type SetupStep = 'select_path' | 'building_cache' | 'ready';

interface SetupWizardProps {
  onComplete: () => void;
  initialPath?: string;  // 从 App 传入的初始路径
}

export function SetupWizard({ onComplete, initialPath }: SetupWizardProps) {
  // 优先使用传入的路径，否则使用 localStorage 中保存的路径
  const savedSettings = loadSettings();
  const defaultPath = initialPath || savedSettings.storagePath || '/Volumes/NO NAME';

  const [step, setStep] = useState<SetupStep>('select_path');
  const [storagePath, setStoragePathValue] = useState(defaultPath);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [cacheStatus, setCacheStatus] = useState<CacheStatus | null>(null);
  const [initialChecking, setInitialChecking] = useState(true);
  const [autoSubmit, setAutoSubmit] = useState(!!initialPath);  // 如果有初始路径，自动提交

  // 初始检查服务器状态
  useEffect(() => {
    const checkInitialState = async () => {
      try {
        const config = await getConfig();

        // 如果有初始路径（从设置页面切换过来），直接使用该路径加载
        if (initialPath) {
          // 直接开始加载新路径
          setInitialChecking(false);
          return;  // 让 autoSubmit effect 处理提交
        }

        if (config.loaded && config.cacheStatus) {
          setStoragePathValue(config.storagePath);
          setCacheStatus(config.cacheStatus);

          if (config.cacheStatus.status === 'ready') {
            // 已加载且缓存就绪，直接进入主界面
            onComplete();
          } else if (config.cacheStatus.status === 'building') {
            // 正在构建缓存
            setStep('building_cache');
          } else {
            // 已加载但未开始构建缓存
            setStep('select_path');
          }
        }
      } catch (e) {
        // 服务器未连接，显示路径选择
        console.error('检查初始状态失败:', e);
      } finally {
        setInitialChecking(false);
      }
    };
    checkInitialState();
  }, [onComplete, initialPath]);

  // 自动提交（当从设置页面切换路径时）
  useEffect(() => {
    if (autoSubmit && !initialChecking && storagePath) {
      setAutoSubmit(false);
      // 内联提交逻辑
      const doSubmit = async () => {
        setLoading(true);
        setError(null);
        try {
          const result = await setStoragePath(storagePath.trim());
          if (result.loaded) {
            setCacheStatus(result.cacheStatus || null);
            setStep('building_cache');
          } else {
            setError(result.error || '加载失败');
          }
        } catch (e) {
          setError((e as Error).message);
        } finally {
          setLoading(false);
        }
      };
      doSubmit();
    }
  }, [autoSubmit, initialChecking, storagePath]);

  // 轮询缓存构建进度
  useEffect(() => {
    if (step !== 'building_cache') return;

    const pollStatus = async () => {
      try {
        const status = await getCacheStatus();
        setCacheStatus(status);

        if (status.status === 'ready') {
          setStep('ready');
          // 短暂延迟后进入主界面
          setTimeout(onComplete, 500);
        }
      } catch (e) {
        console.error('获取缓存状态失败:', e);
      }
    };

    // 立即执行一次
    pollStatus();

    // 每 500ms 轮询一次
    const interval = setInterval(pollStatus, 500);
    return () => clearInterval(interval);
  }, [step, onComplete]);

  const handleSubmitPath = useCallback(async () => {
    if (!storagePath.trim()) {
      setError('请输入存储路径');
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const result = await setStoragePath(storagePath.trim());
      if (result.loaded) {
        setCacheStatus(result.cacheStatus || null);
        setStep('building_cache');
      } else {
        setError(result.error || '加载失败');
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [storagePath]);

  if (initialChecking) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center">
        <div className="text-center">
          <div className="w-16 h-16 border-4 border-primary-500 border-t-transparent rounded-full animate-spin mx-auto mb-4" />
          <p className="text-slate-400">正在检查服务器状态...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center p-4">
      <div className="bg-slate-800/50 rounded-2xl border border-slate-700/50 p-8 max-w-md w-full shadow-2xl">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="w-16 h-16 bg-primary-500 rounded-2xl flex items-center justify-center mx-auto mb-4">
            <svg className="w-10 h-10 text-slate-900" fill="currentColor" viewBox="0 0 24 24">
              <path d="M18 4l2 4h-3l-2-4h-2l2 4h-3l-2-4H8l2 4H7L5 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V4h-4z" />
            </svg>
          </div>
          <h1 className="text-2xl font-bold text-slate-100">天视通 DVR 查看器</h1>
          <p className="text-slate-400 mt-2">Seetong DVR Web Viewer</p>
        </div>

        {/* 步骤指示器 */}
        <div className="flex items-center justify-center gap-2 mb-8">
          <StepIndicator
            number={1}
            label="选择路径"
            active={step === 'select_path'}
            completed={step !== 'select_path'}
          />
          <div className="w-8 h-0.5 bg-slate-700" />
          <StepIndicator
            number={2}
            label="构建索引"
            active={step === 'building_cache'}
            completed={step === 'ready'}
          />
          <div className="w-8 h-0.5 bg-slate-700" />
          <StepIndicator
            number={3}
            label="完成"
            active={step === 'ready'}
            completed={false}
          />
        </div>

        {/* 步骤内容 */}
        {step === 'select_path' && (
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">
                DVR 存储路径
              </label>
              <input
                type="text"
                value={storagePath}
                onChange={(e) => setStoragePathValue(e.target.value)}
                placeholder="/Volumes/DVR_USB 或 /mnt/dvr"
                className="w-full px-4 py-3 bg-slate-700/50 border border-slate-600 rounded-lg text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                onKeyDown={(e) => e.key === 'Enter' && handleSubmitPath()}
              />
              <p className="text-xs text-slate-500 mt-2">
                输入包含 TIndex00.tps 的目录路径
              </p>
            </div>

            {error && (
              <div className="p-3 bg-red-500/20 border border-red-500/50 rounded-lg text-red-400 text-sm">
                {error}
              </div>
            )}

            <button
              onClick={handleSubmitPath}
              disabled={loading}
              className="w-full py-3 bg-primary-500 hover:bg-primary-400 disabled:bg-slate-600 disabled:cursor-not-allowed text-slate-900 font-medium rounded-lg transition-colors flex items-center justify-center gap-2"
            >
              {loading ? (
                <>
                  <div className="w-5 h-5 border-2 border-slate-900 border-t-transparent rounded-full animate-spin" />
                  加载中...
                </>
              ) : (
                '下一步'
              )}
            </button>
          </div>
        )}

        {step === 'building_cache' && cacheStatus && (
          <div className="space-y-6">
            <div className="text-center">
              <div className="w-20 h-20 mx-auto mb-4 relative">
                <svg className="w-20 h-20 transform -rotate-90">
                  <circle
                    cx="40"
                    cy="40"
                    r="36"
                    stroke="currentColor"
                    strokeWidth="8"
                    fill="none"
                    className="text-slate-700"
                  />
                  <circle
                    cx="40"
                    cy="40"
                    r="36"
                    stroke="currentColor"
                    strokeWidth="8"
                    fill="none"
                    strokeDasharray={226}
                    strokeDashoffset={226 - (226 * cacheStatus.progress) / 100}
                    className="text-primary-500 transition-all duration-300"
                    strokeLinecap="round"
                  />
                </svg>
                <div className="absolute inset-0 flex items-center justify-center">
                  <span className="text-xl font-bold text-slate-200">
                    {cacheStatus.progress}%
                  </span>
                </div>
              </div>
              <h3 className="text-lg font-medium text-slate-200">正在构建视频索引</h3>
              <p className="text-slate-400 text-sm mt-1">
                {cacheStatus.current} / {cacheStatus.total} 个文件
              </p>
            </div>

            <div className="w-full h-2 bg-slate-700 rounded-full overflow-hidden">
              <div
                className="h-full bg-primary-500 transition-all duration-300"
                style={{ width: `${cacheStatus.progress}%` }}
              />
            </div>

            <p className="text-center text-slate-500 text-xs">
              首次加载需要扫描所有视频文件，请耐心等待...
            </p>
          </div>
        )}

        {step === 'ready' && (
          <div className="text-center py-4">
            <div className="w-16 h-16 bg-green-500/20 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg className="w-8 h-8 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <h3 className="text-lg font-medium text-slate-200">准备就绪</h3>
            <p className="text-slate-400 text-sm mt-1">正在进入主界面...</p>
          </div>
        )}
      </div>
    </div>
  );
}

function StepIndicator({
  number,
  label,
  active,
  completed,
}: {
  number: number;
  label: string;
  active: boolean;
  completed: boolean;
}) {
  return (
    <div className="flex flex-col items-center">
      <div
        className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium transition-colors ${
          completed
            ? 'bg-primary-500 text-slate-900'
            : active
            ? 'bg-primary-500/20 text-primary-400 border-2 border-primary-500'
            : 'bg-slate-700 text-slate-500'
        }`}
      >
        {completed ? (
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
        ) : (
          number
        )}
      </div>
      <span className={`text-xs mt-1 ${active ? 'text-slate-300' : 'text-slate-500'}`}>
        {label}
      </span>
    </div>
  );
}
