/**
 * G.711 μ-law 音频播放器
 *
 * 音频格式:
 * - 编码: G.711 μ-law
 * - 采样率: 8000 Hz
 * - 位深度: 8 bit -> 16 bit PCM
 * - 通道数: 1 (单声道)
 */

// G.711 μ-law 解码表 (ITU-T G.711 标准)
const ULAW_DECODE_TABLE = new Int16Array(256);
(function initUlawTable() {
  for (let i = 0; i < 256; i++) {
    // 注意：μ-law 编码中 bit7=1 表示正数，bit7=0 表示负数
    const sign = i & 0x80 ? 1 : -1;
    const exponent = (i >> 4) & 0x07;
    const mantissa = i & 0x0F;
    const magnitude = ((mantissa << 3) + 0x84) << exponent;
    ULAW_DECODE_TABLE[i] = sign * (magnitude - 0x84);
  }
})();

/**
 * 将 G.711 μ-law 数据解码为 PCM 16-bit
 */
function decodeUlaw(ulawData: Uint8Array): Int16Array {
  const pcmData = new Int16Array(ulawData.length);
  for (let i = 0; i < ulawData.length; i++) {
    // μ-law 编码是取反的
    pcmData[i] = ULAW_DECODE_TABLE[~ulawData[i] & 0xFF];
  }
  return pcmData;
}

/**
 * 将 PCM 16-bit 转换为 Float32 (-1.0 到 1.0)
 */
function pcmToFloat32(pcmData: Int16Array): Float32Array {
  const floatData = new Float32Array(pcmData.length);
  for (let i = 0; i < pcmData.length; i++) {
    floatData[i] = pcmData[i] / 32768.0;
  }
  return floatData;
}

export interface AudioPlayerCallbacks {
  onLog: (message: string, type?: 'info' | 'success' | 'error') => void;
}

export class AudioPlayer {
  private audioContext: AudioContext | null = null;
  private gainNode: GainNode | null = null;
  private isMuted = false;
  private volume = 1.0;
  private callbacks: AudioPlayerCallbacks;

  // 音频缓冲队列 - 存储 PCM 数据而非 AudioBuffer
  private pcmQueue: Int16Array[] = [];
  private isPlaying = false;
  private nextPlayTime = 0;

  // 合并参数
  private readonly MERGE_THRESHOLD = 4;  // 累积 4 帧后合并播放
  private readonly BUFFER_DELAY = 0.1;   // 100ms 初始缓冲
  private lastSampleRate = 8000;         // 记录最后的采样率

  constructor(callbacks: AudioPlayerCallbacks) {
    this.callbacks = callbacks;
  }

  async init(): Promise<void> {
    if (this.audioContext) {
      return;
    }

    try {
      this.audioContext = new AudioContext();
      this.gainNode = this.audioContext.createGain();
      this.gainNode.connect(this.audioContext.destination);
      this.gainNode.gain.value = this.volume;

      // 如果上下文被暂停（浏览器自动暂停策略），恢复它
      if (this.audioContext.state === 'suspended') {
        await this.audioContext.resume();
      }

      this.callbacks.onLog(`音频播放器初始化成功, 采样率: ${this.audioContext.sampleRate}Hz`, 'success');
    } catch (e) {
      this.callbacks.onLog(`音频播放器初始化失败: ${(e as Error).message}`, 'error');
      throw e;
    }
  }

  // 音频统计和诊断
  private audioFrameCount = 0;
  private lastAudioLogTime = 0;
  private totalAudioBytes = 0;
  private firstAudioTs = 0;
  private lastAudioTs = 0;
  private audioDropCount = 0;
  private audioDelayMs = 0;

  /**
   * 处理从服务器接收的音频数据
   *
   * 音频帧格式:
   * Magic (4 bytes): 'G711'
   * Timestamp (8 bytes): Unix 时间戳毫秒
   * SampleRate (2 bytes): 采样率 (8000)
   * DataLen (4 bytes): 音频数据长度
   * Data (N bytes): G.711 μ-law 编码的音频数据
   */
  processAudioData(data: ArrayBuffer): void {
    if (!this.audioContext || !this.gainNode) {
      return;
    }

    const view = new Uint8Array(data);

    // 检查 magic
    if (view.length < 18 ||
        view[0] !== 0x47 || view[1] !== 0x37 || view[2] !== 0x31 || view[3] !== 0x31) {
      // 不是音频帧
      return;
    }

    // 解析头部
    const dataView = new DataView(data);
    const timestampMs = Number(dataView.getBigUint64(4, false));
    const frameSampleRate = dataView.getUint16(12, false);
    const dataLen = dataView.getUint32(14, false);

    if (view.length < 18 + dataLen) {
      this.callbacks.onLog(`[Audio] ERROR: incomplete frame, expect ${18 + dataLen}, got ${view.length}`, 'error');
      return;
    }

    // 诊断：记录时间戳
    const now = Date.now();
    if (this.firstAudioTs === 0) {
      this.firstAudioTs = timestampMs;
      this.callbacks.onLog(`[Audio] First frame: ts=${timestampMs}, time=${new Date(timestampMs).toISOString()}`, 'info');
    }

    // 检测时间戳跳跃（可能的丢帧）
    if (this.lastAudioTs > 0 && timestampMs - this.lastAudioTs > 2000) {
      this.audioDropCount++;
      this.callbacks.onLog(`[Audio] WARN: timestamp jump ${this.lastAudioTs} -> ${timestampMs} (gap=${timestampMs - this.lastAudioTs}ms)`, 'error');
    }
    this.lastAudioTs = timestampMs;

    // 统计
    this.audioFrameCount++;
    this.totalAudioBytes += dataLen;

    // 计算音频延迟（当前时间 vs 音频时间戳）
    this.audioDelayMs = now - timestampMs;

    // 诊断日志（每秒打印一次）
    if (now - this.lastAudioLogTime >= 1000) {
      const audioTime = new Date(timestampMs).toISOString().substr(11, 8);
      const bufferStatus = this.audioContext ?
        `ctx=${this.audioContext.currentTime.toFixed(2)}s, next=${this.nextPlayTime.toFixed(2)}s` : 'no-ctx';
      const queueInfo = `queue=${this.pcmQueue.length}/${this.MERGE_THRESHOLD}`;

      this.callbacks.onLog(
        `[Audio] ${this.audioFrameCount}fps, ${(this.totalAudioBytes/1024).toFixed(1)}KB, ` +
        `ts=${audioTime}, delay=${this.audioDelayMs}ms, ${queueInfo}, ${bufferStatus}, drops=${this.audioDropCount}`,
        'info'
      );
      this.audioFrameCount = 0;
      this.totalAudioBytes = 0;
      this.lastAudioLogTime = now;
    }

    // 提取音频数据
    const audioData = view.slice(18, 18 + dataLen);

    // 解码 G.711 μ-law 到 PCM
    const pcmData = decodeUlaw(audioData);

    // 添加到 PCM 队列
    this.pcmQueue.push(pcmData);

    // 记录采样率
    this.lastSampleRate = frameSampleRate;

    // 累积足够帧数后合并播放
    if (this.pcmQueue.length >= this.MERGE_THRESHOLD) {
      this.flushAndPlay(frameSampleRate);
    }
  }

  /**
   * 强制刷新剩余的音频数据（在视频结束时调用）
   */
  flush(): void {
    if (this.pcmQueue.length > 0) {
      this.flushAndPlay(this.lastSampleRate);
    }
  }

  /**
   * 合并队列中的 PCM 数据并播放
   */
  private flushAndPlay(sampleRate: number): void {
    if (!this.audioContext || !this.gainNode || this.pcmQueue.length === 0) {
      return;
    }

    // 计算总长度
    let totalLength = 0;
    for (const pcm of this.pcmQueue) {
      totalLength += pcm.length;
    }

    // 合并所有 PCM 数据
    const mergedPcm = new Int16Array(totalLength);
    let offset = 0;
    for (const pcm of this.pcmQueue) {
      mergedPcm.set(pcm, offset);
      offset += pcm.length;
    }
    this.pcmQueue = [];

    // 转换为 Float32
    const floatData = pcmToFloat32(mergedPcm);

    // 创建 AudioBuffer
    const buffer = this.audioContext.createBuffer(1, floatData.length, sampleRate);
    const channelData = buffer.getChannelData(0);
    channelData.set(floatData);

    // 创建 source 节点并播放
    const source = this.audioContext.createBufferSource();
    source.buffer = buffer;
    source.connect(this.gainNode);

    // 计算播放时间
    if (!this.isPlaying) {
      this.isPlaying = true;
      this.nextPlayTime = this.audioContext.currentTime + this.BUFFER_DELAY;
    }

    const playTime = Math.max(this.nextPlayTime, this.audioContext.currentTime);
    source.start(playTime);

    // 更新下一个播放时间
    this.nextPlayTime = playTime + buffer.duration;
  }


  setVolume(volume: number): void {
    this.volume = Math.max(0, Math.min(1, volume));
    if (this.gainNode && !this.isMuted) {
      this.gainNode.gain.value = this.volume;
    }
  }

  getVolume(): number {
    return this.volume;
  }

  setMuted(muted: boolean): void {
    this.isMuted = muted;
    if (this.gainNode) {
      this.gainNode.gain.value = muted ? 0 : this.volume;
    }
  }

  isMutedState(): boolean {
    return this.isMuted;
  }

  toggleMute(): void {
    this.setMuted(!this.isMuted);
  }

  reset(): void {
    // 立即停止所有正在播放的音频
    // 通过断开并重新创建 GainNode 来实现
    if (this.audioContext && this.gainNode) {
      // 断开旧的 GainNode（这会立即停止所有连接到它的音频源）
      this.gainNode.disconnect();
      // 创建新的 GainNode
      this.gainNode = this.audioContext.createGain();
      this.gainNode.connect(this.audioContext.destination);
      this.gainNode.gain.value = this.isMuted ? 0 : this.volume;
    }

    // 清空队列和状态
    this.pcmQueue = [];
    this.isPlaying = false;
    this.nextPlayTime = 0;

    // Reset diagnostics
    this.audioFrameCount = 0;
    this.lastAudioLogTime = 0;
    this.totalAudioBytes = 0;
    this.firstAudioTs = 0;
    this.lastAudioTs = 0;
    this.audioDropCount = 0;
    this.audioDelayMs = 0;

    this.callbacks.onLog('[Audio] Reset: 已停止播放并清空缓存', 'info');
  }

  async resume(): Promise<void> {
    if (this.audioContext && this.audioContext.state === 'suspended') {
      await this.audioContext.resume();
      this.callbacks.onLog('音频上下文已恢复', 'info');
    }
  }

  close(): void {
    if (this.audioContext) {
      this.audioContext.close();
      this.audioContext = null;
      this.gainNode = null;
    }
    this.reset();
  }
}
