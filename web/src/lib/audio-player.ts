/**
 * G.711 μ-law 音频播放器
 *
 * 音频格式:
 * - 编码: G.711 μ-law
 * - 采样率: 8000 Hz
 * - 位深度: 8 bit -> 16 bit PCM
 * - 通道数: 1 (单声道)
 */

// G.711 μ-law 解码表
const ULAW_DECODE_TABLE = new Int16Array(256);
(function initUlawTable() {
  for (let i = 0; i < 256; i++) {
    const sign = i & 0x80 ? -1 : 1;
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

  // 音频缓冲队列
  private audioQueue: { buffer: AudioBuffer }[] = [];
  private isPlaying = false;
  private nextPlayTime = 0;

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

  // 音频统计
  private audioFrameCount = 0;
  private lastAudioLogTime = 0;

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
      this.callbacks.onLog(`音频帧数据不完整: 期望 ${18 + dataLen}, 实际 ${view.length}`, 'error');
      return;
    }

    // 音频帧统计日志（每秒打印一次）
    this.audioFrameCount++;
    const now = Date.now();
    if (now - this.lastAudioLogTime >= 1000) {
      this.callbacks.onLog(`音频: ${this.audioFrameCount} 帧/秒, ${dataLen} 字节/帧, ${frameSampleRate}Hz, ts=${timestampMs}`, 'info');
      this.audioFrameCount = 0;
      this.lastAudioLogTime = now;
    }

    // 提取音频数据
    const audioData = view.slice(18, 18 + dataLen);

    // 解码 G.711 μ-law 到 PCM
    const pcmData = decodeUlaw(audioData);

    // 转换为 Float32
    const floatData = pcmToFloat32(pcmData);

    // 创建 AudioBuffer
    const buffer = this.audioContext.createBuffer(1, floatData.length, frameSampleRate);
    // 复制数据到 buffer 的通道
    const channelData = buffer.getChannelData(0);
    for (let i = 0; i < floatData.length; i++) {
      channelData[i] = floatData[i];
    }

    // 添加到队列
    this.audioQueue.push({ buffer });

    // 开始播放
    if (!this.isPlaying) {
      this.startPlayback();
    }
  }

  private startPlayback(): void {
    if (!this.audioContext || !this.gainNode || this.audioQueue.length === 0) {
      return;
    }

    this.isPlaying = true;
    this.nextPlayTime = this.audioContext.currentTime + 0.05; // 50ms 缓冲

    this.scheduleNextBuffer();
  }

  private scheduleNextBuffer(): void {
    if (!this.audioContext || !this.gainNode) {
      return;
    }

    while (this.audioQueue.length > 0) {
      const { buffer } = this.audioQueue.shift()!;

      // 创建 source 节点
      const source = this.audioContext.createBufferSource();
      source.buffer = buffer;
      source.connect(this.gainNode);

      // 计算播放时间
      const playTime = Math.max(this.nextPlayTime, this.audioContext.currentTime);
      source.start(playTime);

      // 更新下一个播放时间
      this.nextPlayTime = playTime + buffer.duration;
    }

    // 如果队列为空，等待更多数据
    if (this.audioQueue.length === 0) {
      // 检查是否需要继续等待
      setTimeout(() => {
        if (this.audioQueue.length > 0) {
          this.scheduleNextBuffer();
        } else {
          this.isPlaying = false;
        }
      }, 50);
    }
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
    this.audioQueue = [];
    this.isPlaying = false;
    this.nextPlayTime = 0;
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
