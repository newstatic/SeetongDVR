import { useRef, useEffect, useCallback, forwardRef, useImperativeHandle } from 'react';

interface VideoPlayerProps {
  onFrameReceived: (callback: (frame: VideoFrame) => void) => void;
}

export interface VideoPlayerHandle {
  takeScreenshot: () => void;
  toggleFullscreen: () => void;
}

export const VideoPlayer = forwardRef<VideoPlayerHandle, VideoPlayerProps>(
  function VideoPlayer({ onFrameReceived }, ref) {
    const containerRef = useRef<HTMLDivElement>(null);
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const ctxRef = useRef<CanvasRenderingContext2D | null>(null);

    const handleFrame = useCallback((frame: VideoFrame) => {
      const canvas = canvasRef.current;
      if (!canvas) {
        frame.close();
        return;
      }

      // 更新 canvas 尺寸
      if (canvas.width !== frame.displayWidth || canvas.height !== frame.displayHeight) {
        canvas.width = frame.displayWidth;
        canvas.height = frame.displayHeight;
      }

      // 获取或创建 context
      if (!ctxRef.current) {
        ctxRef.current = canvas.getContext('2d');
      }

      // 绘制帧
      if (ctxRef.current) {
        ctxRef.current.drawImage(frame, 0, 0);
      }

      frame.close();
    }, []);

    useEffect(() => {
      onFrameReceived(handleFrame);
    }, [onFrameReceived, handleFrame]);

    // 截图功能
    const takeScreenshot = useCallback(() => {
      const canvas = canvasRef.current;
      if (!canvas) return;

      const link = document.createElement('a');
      link.download = `screenshot_${new Date().toISOString().replace(/[:.]/g, '-')}.png`;
      link.href = canvas.toDataURL('image/png');
      link.click();
    }, []);

    // 全屏功能
    const toggleFullscreen = useCallback(() => {
      const container = containerRef.current;
      if (!container) return;

      if (document.fullscreenElement) {
        document.exitFullscreen();
      } else {
        container.requestFullscreen();
      }
    }, []);

    // 暴露方法给父组件
    useImperativeHandle(ref, () => ({
      takeScreenshot,
      toggleFullscreen,
    }), [takeScreenshot, toggleFullscreen]);

    // 键盘快捷键
    useEffect(() => {
      const handleKeyDown = (e: KeyboardEvent) => {
        if (e.key === 'f' || e.key === 'F') {
          toggleFullscreen();
        }
      };

      window.addEventListener('keydown', handleKeyDown);
      return () => window.removeEventListener('keydown', handleKeyDown);
    }, [toggleFullscreen]);

    return (
      <div
        ref={containerRef}
        className="relative w-full aspect-video bg-black rounded-lg overflow-hidden shadow-xl"
        onDoubleClick={toggleFullscreen}
      >
        <canvas
          ref={canvasRef}
          className="w-full h-full object-contain"
        />
        {/* 底部渐变遮罩 */}
        <div className="absolute bottom-0 left-0 right-0 h-16 bg-gradient-to-t from-black/60 to-transparent pointer-events-none" />
        {/* 无视频时的占位提示 */}
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="text-slate-500 text-center" id="video-placeholder">
            <svg className="w-16 h-16 mx-auto mb-2 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1}
                d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"
              />
            </svg>
            <p className="text-sm">选择录像开始播放</p>
          </div>
        </div>
      </div>
    );
  }
);
