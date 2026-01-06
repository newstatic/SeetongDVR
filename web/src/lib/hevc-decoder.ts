/**
 * HEVC WebCodecs 解码器
 */

// 位读取器 - 用于解析 HEVC NAL 单元
class BitReader {
  private data: Uint8Array;
  private byteOffset = 0;
  private bitOffset = 0;

  constructor(data: Uint8Array) {
    this.data = data;
  }

  readBits(n: number): number {
    let result = 0;
    for (let i = 0; i < n; i++) {
      if (this.byteOffset >= this.data.length) {
        throw new Error('BitReader: 超出数据范围');
      }
      const bit = (this.data[this.byteOffset] >> (7 - this.bitOffset)) & 1;
      result = (result << 1) | bit;
      this.bitOffset++;
      if (this.bitOffset === 8) {
        this.bitOffset = 0;
        this.byteOffset++;
      }
    }
    return result;
  }

  skip(n: number): void {
    this.bitOffset += n;
    this.byteOffset += Math.floor(this.bitOffset / 8);
    this.bitOffset = this.bitOffset % 8;
  }

  // 读取 Exp-Golomb 编码的无符号整数
  readUE(): number {
    let leadingZeros = 0;
    while (this.readBits(1) === 0 && leadingZeros < 32) {
      leadingZeros++;
    }
    if (leadingZeros === 0) {
      return 0;
    }
    const value = this.readBits(leadingZeros);
    return (1 << leadingZeros) - 1 + value;
  }

  // 读取 Exp-Golomb 编码的有符号整数
  readSE(): number {
    const ue = this.readUE();
    if (ue % 2 === 0) {
      return -(ue >> 1);
    } else {
      return (ue + 1) >> 1;
    }
  }
}

export interface DecoderStats {
  framesDecoded: number;
  totalBytes: number;
  fps: number;
  isConfigured: boolean;
  waitingForKeyframe: boolean;
}

export interface DecoderCallbacks {
  onFrame: (frame: VideoFrame) => void;
  onError: (error: Error) => void;
  onLog: (message: string, type?: 'info' | 'success' | 'error') => void;
  onStats: (stats: DecoderStats) => void;
  onTimeUpdate?: (timestampMs: number) => void;
}

export class HEVCDecoder {
  private decoder: VideoDecoder | null = null;
  private isConfigured = false;
  private waitingForKeyframe = true;

  // 参数集缓存
  private vps: Uint8Array | null = null;
  private sps: Uint8Array | null = null;
  private pps: Uint8Array | null = null;

  // 从 SPS 解析的分辨率
  private videoWidth = 0;
  private videoHeight = 0;

  // 统计
  private frameCount = 0;
  private totalBytes = 0;
  private timestamp = 0;
  private fpsCounter = 0;
  private lastFpsTime = Date.now();
  private currentFps = 0;

  private callbacks: DecoderCallbacks;

  constructor(callbacks: DecoderCallbacks) {
    this.callbacks = callbacks;
  }

  async checkSupport(): Promise<boolean> {
    if (!('VideoDecoder' in window)) {
      throw new Error('WebCodecs API 不支持，请使用 Chrome 94+');
    }

    const config = {
      codec: 'hvc1.1.6.L93.B0',
      hardwareAcceleration: 'prefer-hardware' as const,
    };

    const support = await VideoDecoder.isConfigSupported(config);
    if (!support.supported) {
      throw new Error('HEVC 解码不支持，请启用 chrome://flags/#enable-platform-hevc');
    }

    this.callbacks.onLog('HEVC 硬件解码支持: ✓', 'success');
    return true;
  }

  async init(): Promise<void> {
    await this.checkSupport();

    if (this.decoder && this.decoder.state !== 'closed') {
      this.decoder.close();
    }

    this.decoder = new VideoDecoder({
      output: (frame) => {
        this.frameCount++;
        this.fpsCounter++;
        // 调试：打印解码输出
        if (this.frameCount <= 10) {
          console.log(`[Decoder] Output frame #${this.frameCount}: ${frame.codedWidth}x${frame.codedHeight} timestamp=${frame.timestamp} duration=${frame.duration}`);
        }
        this.updateStats();
        this.callbacks.onFrame(frame);
      },
      error: (error) => {
        console.error(`[Decoder] Error callback:`, error);
        console.error(`[Decoder] State at error: configured=${this.isConfigured} waitingForKeyframe=${this.waitingForKeyframe}`);
        console.error(`[Decoder] Frames output before error: ${this.frameCount}`);
        console.error(`[Decoder] Frames submitted before error: ${this.decodedFrameCount}`);
        console.error(`[Decoder] Cached params at error: VPS=${this.vps?.length || 0}B SPS=${this.sps?.length || 0}B PPS=${this.pps?.length || 0}B`);
        this.callbacks.onError(error);
        this.waitingForKeyframe = true;
      },
    });

    this.isConfigured = false;
    this.waitingForKeyframe = true;
    this.callbacks.onLog('解码器已初始化', 'info');
  }

  private configure(): boolean {
    if (!this.vps || !this.sps || !this.pps) {
      this.callbacks.onLog('等待完整参数集 (VPS/SPS/PPS)...', 'info');
      return false;
    }

    // 确保分辨率已解析
    if (this.videoWidth === 0 || this.videoHeight === 0) {
      this.callbacks.onLog('等待解析分辨率...', 'info');
      return false;
    }

    const description = this.buildHEVCDescription();

    // 根据分辨率确定 level
    const level = this.calculateLevel(this.videoWidth, this.videoHeight);

    const config: VideoDecoderConfig = {
      codec: `hvc1.1.6.L${level}.B0`,  // hvc1 = 参数集在 description 中
      codedWidth: this.videoWidth,
      codedHeight: this.videoHeight,
      hardwareAcceleration: 'prefer-hardware',
      description: description,
    };

    try {
      this.decoder!.configure(config);
      this.isConfigured = true;
      this.callbacks.onLog(`解码器配置成功: ${this.videoWidth}x${this.videoHeight}, HEVC Main Profile, Level ${level / 30}`, 'success');
      return true;
    } catch (e) {
      this.callbacks.onLog(`解码器配置失败: ${(e as Error).message}`, 'error');
      return false;
    }
  }

  // 根据分辨率计算 HEVC level
  private calculateLevel(width: number, height: number): number {
    const pixels = width * height;
    // Level 根据 luma samples per second 决定，这里简化为按像素数判断
    if (pixels <= 36864) return 30;      // Level 1 (128x96)
    if (pixels <= 122880) return 60;     // Level 2 (352x288)
    if (pixels <= 245760) return 63;     // Level 2.1 (640x360)
    if (pixels <= 552960) return 90;     // Level 3 (960x540)
    if (pixels <= 983040) return 93;     // Level 3.1 (1280x720)
    if (pixels <= 2228224) return 120;   // Level 4 (2048x1080)
    if (pixels <= 8912896) return 150;   // Level 5 (4096x2160)
    return 153;                           // Level 5.1+
  }

  private buildHEVCDescription(): Uint8Array {
    const arrays = [
      { type: 32, data: this.vps! },
      { type: 33, data: this.sps! },
      { type: 34, data: this.pps! },
    ];

    // 调试：打印参数集内容
    console.log(`[HEVC] Building description with:`);
    console.log(`[HEVC]   VPS (${this.vps!.length}B): ${Array.from(this.vps!.slice(0, 24)).map(b => b.toString(16).padStart(2, '0')).join(' ')}`);
    console.log(`[HEVC]   SPS (${this.sps!.length}B): ${Array.from(this.sps!.slice(0, 24)).map(b => b.toString(16).padStart(2, '0')).join(' ')}`);
    console.log(`[HEVC]   PPS (${this.pps!.length}B): ${Array.from(this.pps!).map(b => b.toString(16).padStart(2, '0')).join(' ')}`);

    let totalLength = 23;
    for (const arr of arrays) {
      totalLength += 3 + 2 + arr.data.length;
    }

    const buffer = new Uint8Array(totalLength);
    let offset = 0;

    // HEVCDecoderConfigurationRecord
    buffer[offset++] = 1; // configurationVersion
    buffer[offset++] = 0x01; // general_profile_space, tier_flag, profile_idc (Main)
    buffer[offset++] = 0x60; buffer[offset++] = 0x00;
    buffer[offset++] = 0x00; buffer[offset++] = 0x00; // profile_compatibility_flags
    buffer[offset++] = 0x90; buffer[offset++] = 0x00;
    buffer[offset++] = 0x00; buffer[offset++] = 0x00;
    buffer[offset++] = 0x00; buffer[offset++] = 0x00; // constraint_indicator_flags
    buffer[offset++] = 93; // general_level_idc (Level 3.1)
    buffer[offset++] = 0xF0; buffer[offset++] = 0x00; // min_spatial_segmentation_idc
    buffer[offset++] = 0xFC; // parallelismType
    buffer[offset++] = 0xFD; // chromaFormat (4:2:0)
    buffer[offset++] = 0xF8; // bitDepthLumaMinus8
    buffer[offset++] = 0xF8; // bitDepthChromaMinus8
    buffer[offset++] = 0x00; buffer[offset++] = 0x00; // avgFrameRate
    buffer[offset++] = 0x0F; // constantFrameRate, numTemporalLayers, lengthSizeMinusOne
    buffer[offset++] = arrays.length; // numOfArrays

    for (const arr of arrays) {
      buffer[offset++] = 0x80 | arr.type;
      buffer[offset++] = 0x00;
      buffer[offset++] = 0x01; // numNalus
      buffer[offset++] = (arr.data.length >> 8) & 0xFF;
      buffer[offset++] = arr.data.length & 0xFF;
      buffer.set(arr.data, offset);
      offset += arr.data.length;
    }

    return buffer;
  }

  processData(data: ArrayBuffer): void {
    this.totalBytes += data.byteLength;

    const view = new Uint8Array(data);

    // 检查是否是 HVCC 聚合帧格式: Magic(4) + Timestamp(8) + FrameType(1) + DataLen(4) + Data(N)
    // Magic = 'HVCC' = 0x48 0x56 0x43 0x43
    // Data 已经是 hvcC 格式（每个 NAL 前有 4 字节长度），可以直接提交给解码器
    if (view.length >= 17 &&
        view[0] === 0x48 && view[1] === 0x56 && view[2] === 0x43 && view[3] === 0x43) {
      const dataView = new DataView(data);
      const timestampMs = Number(dataView.getBigUint64(4, false));
      const frameType = view[12]; // 0=P帧, 1=IDR
      const dataLen = dataView.getUint32(13, false);

      if (view.length >= 17 + dataLen) {
        const frameData = view.slice(17, 17 + dataLen);
        this.processAggregatedFrame(frameData, frameType, timestampMs);
      }
      return;
    }

    // 检查是否是 H265 单 NAL 格式: Magic(4) + Timestamp(8) + FrameType(1) + DataLen(4) + Data(N)
    // Magic = 'H265' = 0x48 0x32 0x36 0x35
    if (view.length >= 17 &&
        view[0] === 0x48 && view[1] === 0x32 && view[2] === 0x36 && view[3] === 0x35) {
      // 解析时间戳 (8 bytes, big-endian)
      const dataView = new DataView(data);
      const timestampMs = Number(dataView.getBigUint64(4, false));

      // FrameType: 0=P帧, 1=I帧, 2=VPS, 3=SPS, 4=PPS
      const frameType = view[12];

      // DataLen (4 bytes, big-endian)
      const dataLen = (view[13] << 24) | (view[14] << 16) | (view[15] << 8) | view[16];

      if (view.length >= 17 + dataLen) {
        const nalData = view.slice(17, 17 + dataLen);
        this.processNALUnit(nalData, frameType, timestampMs);
      }
      return;
    }

    // 检查是否是分辨率信息消息 "RES:" + width(4) + height(4)
    if (view.length === 12 &&
        view[0] === 0x52 && view[1] === 0x45 && view[2] === 0x53 && view[3] === 0x3A) {
      // "RES:" prefix
      const width = (view[4] << 24) | (view[5] << 16) | (view[6] << 8) | view[7];
      const height = (view[8] << 24) | (view[9] << 16) | (view[10] << 8) | view[11];
      if (width > 0 && height > 0 && width <= 8192 && height <= 4320) {
        this.videoWidth = width;
        this.videoHeight = height;
        this.callbacks.onLog(`收到分辨率信息: ${width}x${height}`, 'success');
      }
      return;
    }

    // 检查是否是音频帧 "G711" - 忽略
    if (view.length >= 4 &&
        view[0] === 0x47 && view[1] === 0x37 && view[2] === 0x31 && view[3] === 0x31) {
      return;
    }

    // 兼容 Annex B 格式
    const nalUnits = this.parseNALUnits(data);

    for (const nalUnit of nalUnits) {
      if (nalUnit.length < 2) continue;
      const nalType = this.getNALType(nalUnit);
      this.processNALUnit(nalUnit, this.frameTypeFromNalType(nalType), 0);
    }
  }

  // 将 NAL type 转换为 FrameType (0=P, 1=I, 2=VPS, 3=SPS, 4=PPS)
  private frameTypeFromNalType(nalType: number): number {
    if (nalType === 32) return 2; // VPS
    if (nalType === 33) return 3; // SPS
    if (nalType === 34) return 4; // PPS
    if (nalType >= 16 && nalType <= 21) return 1; // IDR
    return 0; // P 帧
  }

  private decodedFrameCount = 0;

  // NAL 类型名称映射
  private static readonly NAL_TYPE_NAMES: Record<number, string> = {
    0: 'TRAIL_N', 1: 'TRAIL_R', 2: 'TSA_N', 3: 'TSA_R',
    4: 'STSA_N', 5: 'STSA_R', 6: 'RADL_N', 7: 'RADL_R',
    8: 'RASL_N', 9: 'RASL_R',
    16: 'BLA_W_LP', 17: 'BLA_W_RADL', 18: 'BLA_N_LP',
    19: 'IDR_W_RADL', 20: 'IDR_N_LP', 21: 'CRA_NUT',
    32: 'VPS', 33: 'SPS', 34: 'PPS',
    35: 'AUD', 36: 'EOS', 37: 'EOB', 38: 'FD', 39: 'PREFIX_SEI', 40: 'SUFFIX_SEI',
  };

  private getNalTypeName(nalType: number): string {
    return HEVCDecoder.NAL_TYPE_NAMES[nalType] || `NAL${nalType}`;
  }

  // 处理聚合帧（已经是 hvcC 格式，每个 NAL 前有 4 字节长度）
  private processAggregatedFrame(frameData: Uint8Array, frameType: number, timestampMs: number): void {
    // frameType: 0=P帧, 1=IDR

    // 解析 NAL 结构
    const nals: Array<{type: number, len: number, offset: number}> = [];
    let offset = 0;
    while (offset + 4 <= frameData.length) {
      const nalLen = (frameData[offset] << 24) | (frameData[offset + 1] << 16) |
                     (frameData[offset + 2] << 8) | frameData[offset + 3];
      if (nalLen <= 0 || offset + 4 + nalLen > frameData.length) break;
      const nalType = (frameData[offset + 4] >> 1) & 0x3F;
      nals.push({type: nalType, len: nalLen, offset: offset});
      offset += 4 + nalLen;
    }

    // 检查是否包含 IDR NAL
    const hasIDR = nals.some(n => n.type === 19 || n.type === 20);
    // 检查是否包含参数集
    const hasVPS = nals.some(n => n.type === 32);
    const hasSPS = nals.some(n => n.type === 33);
    const hasPPS = nals.some(n => n.type === 34);

    // 调试：打印前 10 帧或关键帧的信息
    if (this.decodedFrameCount < 10 || hasIDR) {
      const nalInfo = nals.map(n => `${this.getNalTypeName(n.type)}(${n.len})`).join(', ');
      console.log(`[HVCC] Frame#${this.decodedFrameCount} type=${frameType}(${frameType === 1 ? 'KEY' : 'DELTA'}) size=${frameData.length} NALs: ${nalInfo}`);
      console.log(`[HVCC] hasVPS=${hasVPS} hasSPS=${hasSPS} hasPPS=${hasPPS} hasIDR=${hasIDR}`);
      console.log(`[HVCC] Decoder state: configured=${this.isConfigured} waitingForKeyframe=${this.waitingForKeyframe}`);
      console.log(`[HVCC] Cached params: VPS=${this.vps?.length || 0}B SPS=${this.sps?.length || 0}B PPS=${this.pps?.length || 0}B`);
      console.log(`[HVCC] First 32 bytes:`, Array.from(frameData.slice(0, 32)).map(b => b.toString(16).padStart(2, '0')).join(' '));
    }

    // 配置解码器
    if (!this.isConfigured) {
      if (!this.configure()) {
        this.callbacks.onLog(`收到聚合帧，但解码器未配置`, 'info');
        return;
      }
    }

    // 检查解码器状态
    if (!this.decoder || this.decoder.state === 'closed') {
      return;
    }

    // 等待关键帧
    const isKey = frameType === 1;
    if (this.waitingForKeyframe) {
      if (!isKey) return;
      this.waitingForKeyframe = false;
      this.callbacks.onLog('收到关键帧，开始解码', 'success');
    }

    // 解码
    try {
      const chunk = new EncodedVideoChunk({
        type: isKey ? 'key' : 'delta',
        timestamp: this.timestamp,
        data: frameData,  // 已经是 hvcC 格式，可直接提交
      });

      // 调试：打印解码参数
      if (this.decodedFrameCount < 10 || hasIDR) {
        console.log(`[HVCC] Decoding chunk: type=${isKey ? 'key' : 'delta'} timestamp=${this.timestamp} dataSize=${frameData.length}`);
        console.log(`[HVCC] Decoder queue before decode: decodeQueueSize=${this.decoder.decodeQueueSize}`);
      }

      this.decoder.decode(chunk);
      this.decodedFrameCount++;
      this.timestamp += 40000; // 25fps = 40ms per frame

      if (this.decodedFrameCount < 10 || hasIDR) {
        console.log(`[HVCC] Decode submitted, decodeQueueSize=${this.decoder.decodeQueueSize}, decodedFrameCount=${this.decodedFrameCount}`);
      }

      // 通知时间更新
      if (timestampMs > 0) {
        this.callbacks.onTimeUpdate?.(timestampMs);
      }
    } catch (e) {
      const err = e as Error;
      console.error(`[HVCC] Decode exception:`, err);
      console.error(`[HVCC] Frame info: type=${isKey ? 'key' : 'delta'} NALs=${nals.map(n => this.getNalTypeName(n.type)).join(',')}`);
      this.callbacks.onLog(`解码异常: ${err.message}`, 'error');
    }
  }

  // 处理单个 NAL 单元
  private processNALUnit(nalUnit: Uint8Array, frameType: number, timestampMs: number): void {
    if (nalUnit.length < 2) return;

    const nalType = this.getNALType(nalUnit);

    // 调试：打印 NAL 信息
    if (this.decodedFrameCount < 10) {
      console.log(`[H265] NAL: type=${this.getNalTypeName(nalType)}(${nalType}) frameType=${frameType} size=${nalUnit.length}`);
    }

    // 缓存参数集
    if (frameType === 2 || nalType === 32) {
      this.vps = nalUnit;
      this.callbacks.onLog('收到 VPS', 'info');
      return;
    } else if (frameType === 3 || nalType === 33) {
      this.sps = nalUnit;
      this.callbacks.onLog('收到 SPS', 'info');
      // 如果还没有分辨率信息，尝试从 SPS 解析
      if (this.videoWidth === 0 || this.videoHeight === 0) {
        const resolution = this.parseSPS(nalUnit);
        if (resolution) {
          this.videoWidth = resolution.width;
          this.videoHeight = resolution.height;
        }
      }
      return;
    } else if (frameType === 4 || nalType === 34) {
      this.pps = nalUnit;
      this.callbacks.onLog('收到 PPS', 'info');
      return;
    }

    // 配置解码器
    if (!this.isConfigured) {
      if (!this.configure()) return;
    }

    // 检查解码器状态
    if (!this.decoder || this.decoder.state === 'closed') {
      return;
    }

    // 等待关键帧
    const isKey = frameType === 1 || this.isKeyFrame(nalType);
    if (this.waitingForKeyframe) {
      if (!isKey) return;
      this.waitingForKeyframe = false;
      this.callbacks.onLog('收到关键帧，开始解码', 'success');
    }

    // 解码
    try {
      // WebCodecs 使用 hvcC 格式: 4字节长度前缀 (大端序) + NAL 数据
      const frameData = new Uint8Array(4 + nalUnit.length);
      const nalLength = nalUnit.length;
      frameData[0] = (nalLength >> 24) & 0xFF;
      frameData[1] = (nalLength >> 16) & 0xFF;
      frameData[2] = (nalLength >> 8) & 0xFF;
      frameData[3] = nalLength & 0xFF;
      frameData.set(nalUnit, 4);

      const chunk = new EncodedVideoChunk({
        type: isKey ? 'key' : 'delta',
        timestamp: this.timestamp,
        data: frameData,
      });

      // 调试：打印解码参数
      if (this.decodedFrameCount < 10 || isKey) {
        console.log(`[H265] Decoding chunk: type=${isKey ? 'key' : 'delta'} nalType=${this.getNalTypeName(nalType)}(${nalType}) timestamp=${this.timestamp} dataSize=${frameData.length}`);
        console.log(`[H265] Decoder state before decode: state=${this.decoder.state} configured=${this.isConfigured}`);
        console.log(`[H265] NAL first 16 bytes: ${Array.from(nalUnit.slice(0, 16)).map(b => b.toString(16).padStart(2, '0')).join(' ')}`);
        console.log(`[H265] hvcC frame first 16 bytes: ${Array.from(frameData.slice(0, 16)).map(b => b.toString(16).padStart(2, '0')).join(' ')}`);
      }

      this.decoder.decode(chunk);
      this.decodedFrameCount++;
      this.timestamp += 40000; // 25fps = 40ms per frame

      if (this.decodedFrameCount <= 10 || isKey) {
        console.log(`[H265] Decode submitted successfully, decodedFrameCount=${this.decodedFrameCount}`);
      }

      // 通知时间更新
      if (timestampMs > 0) {
        this.callbacks.onTimeUpdate?.(timestampMs);
      }
    } catch (e) {
      const err = e as Error;
      console.error(`[H265] Decode exception:`, err);
      console.error(`[H265] Frame info: type=${isKey ? 'key' : 'delta'} nalType=${this.getNalTypeName(nalType)} size=${nalUnit.length}`);
      this.callbacks.onLog(`解码异常: ${err.message}`, 'error');
    }
  }

  private parseNALUnits(data: ArrayBuffer): Uint8Array[] {
    const nalUnits: Uint8Array[] = [];
    const view = new Uint8Array(data);
    let start = 0;

    for (let i = 0; i < view.length - 4; i++) {
      let startCodeLen = 0;

      if (view[i] === 0 && view[i + 1] === 0 && view[i + 2] === 0 && view[i + 3] === 1) {
        startCodeLen = 4;
      } else if (view[i] === 0 && view[i + 1] === 0 && view[i + 2] === 1) {
        startCodeLen = 3;
      }

      if (startCodeLen > 0) {
        if (start < i && start > 0) {
          nalUnits.push(view.slice(start, i));
        }
        start = i + startCodeLen;
        i += startCodeLen - 1;
      }
    }

    if (start < view.length) {
      nalUnits.push(view.slice(start));
    }

    if (nalUnits.length === 0 && view.length > 0) {
      let offset = 0;
      if (view[0] === 0 && view[1] === 0 && view[2] === 0 && view[3] === 1) {
        offset = 4;
      } else if (view[0] === 0 && view[1] === 0 && view[2] === 1) {
        offset = 3;
      }
      nalUnits.push(view.slice(offset));
    }

    return nalUnits;
  }

  private getNALType(nalUnit: Uint8Array): number {
    return (nalUnit[0] >> 1) & 0x3F;
  }

  private isKeyFrame(nalType: number): boolean {
    return (nalType >= 16 && nalType <= 21) || (nalType >= 32 && nalType <= 34);
  }

  // HEVC SPS 解析 - 获取视频分辨率
  // 使用更健壮的解析方法，处理 RBSP (移除 emulation prevention bytes)
  private parseSPS(sps: Uint8Array): { width: number; height: number } | null {
    try {
      // 先移除 emulation prevention bytes (0x00 0x00 0x03 -> 0x00 0x00)
      const rbsp = this.removeEmulationPreventionBytes(sps);
      const reader = new BitReader(rbsp);

      // 跳过 NAL header (2 bytes)
      reader.skip(16);

      // sps_video_parameter_set_id (4 bits)
      reader.skip(4);
      // sps_max_sub_layers_minus1 (3 bits)
      const maxSubLayersMinus1 = reader.readBits(3);
      // sps_temporal_id_nesting_flag (1 bit)
      reader.skip(1);

      // profile_tier_level - 固定部分 (general profile/tier/level)
      // general_profile_space (2) + general_tier_flag (1) + general_profile_idc (5)
      reader.skip(8);
      // general_profile_compatibility_flags (32)
      reader.skip(32);
      // general_progressive_source_flag + general_interlaced_source_flag +
      // general_non_packed_constraint_flag + general_frame_only_constraint_flag (4)
      reader.skip(4);
      // constraint flags (44 bits total for Main profile)
      reader.skip(44);
      // general_level_idc (8)
      reader.skip(8);

      // sub_layer flags
      for (let i = 0; i < maxSubLayersMinus1; i++) {
        reader.skip(2); // sub_layer_profile_present_flag + sub_layer_level_present_flag
      }
      if (maxSubLayersMinus1 > 0) {
        for (let i = maxSubLayersMinus1; i < 8; i++) {
          reader.skip(2); // reserved_zero_2bits
        }
      }

      // 跳过 sub_layer profile/level 数据 (简化处理，假设 maxSubLayersMinus1 = 0)

      // sps_seq_parameter_set_id (ue(v))
      reader.readUE();

      // chroma_format_idc (ue(v))
      const chromaFormatIdc = reader.readUE();
      if (chromaFormatIdc === 3) {
        // separate_colour_plane_flag (1 bit)
        reader.skip(1);
      }

      // pic_width_in_luma_samples (ue(v))
      const picWidth = reader.readUE();
      // pic_height_in_luma_samples (ue(v))
      const picHeight = reader.readUE();

      // conformance_window_flag (1 bit)
      const conformanceWindowFlag = reader.readBits(1);
      let cropLeft = 0, cropRight = 0, cropTop = 0, cropBottom = 0;
      if (conformanceWindowFlag) {
        cropLeft = reader.readUE();
        cropRight = reader.readUE();
        cropTop = reader.readUE();
        cropBottom = reader.readUE();
      }

      // 计算实际分辨率 (考虑裁剪)
      const subWidthC = chromaFormatIdc === 1 || chromaFormatIdc === 2 ? 2 : 1;
      const subHeightC = chromaFormatIdc === 1 ? 2 : 1;

      const width = picWidth - (cropLeft + cropRight) * subWidthC;
      const height = picHeight - (cropTop + cropBottom) * subHeightC;

      // 验证解析结果是否合理
      if (width <= 0 || height <= 0 || width > 8192 || height > 4320) {
        this.callbacks.onLog(`SPS 解析结果异常: ${width}x${height}，使用原始值: ${picWidth}x${picHeight}`, 'info');
        // 如果裁剪后结果异常，使用原始分辨率
        if (picWidth > 0 && picHeight > 0 && picWidth <= 8192 && picHeight <= 4320) {
          return { width: picWidth, height: picHeight };
        }
        return null;
      }

      this.callbacks.onLog(`SPS 解析: ${picWidth}x${picHeight}, 裁剪后: ${width}x${height}`, 'info');
      return { width, height };
    } catch (e) {
      this.callbacks.onLog(`SPS 解析失败: ${(e as Error).message}`, 'error');
      return null;
    }
  }

  // 移除 RBSP 中的 emulation prevention bytes
  private removeEmulationPreventionBytes(data: Uint8Array): Uint8Array {
    const result: number[] = [];
    let i = 0;
    while (i < data.length) {
      if (i + 2 < data.length && data[i] === 0 && data[i + 1] === 0 && data[i + 2] === 3) {
        // 0x00 0x00 0x03 -> 0x00 0x00
        result.push(0);
        result.push(0);
        i += 3;
      } else {
        result.push(data[i]);
        i++;
      }
    }
    return new Uint8Array(result);
  }

  private updateStats(): void {
    const now = Date.now();
    if (now - this.lastFpsTime >= 1000) {
      this.currentFps = this.fpsCounter;
      this.fpsCounter = 0;
      this.lastFpsTime = now;
    }

    this.callbacks.onStats({
      framesDecoded: this.frameCount,
      totalBytes: this.totalBytes,
      fps: this.currentFps,
      isConfigured: this.isConfigured,
      waitingForKeyframe: this.waitingForKeyframe,
    });
  }

  reset(): void {
    this.vps = null;
    this.sps = null;
    this.pps = null;
    this.videoWidth = 0;
    this.videoHeight = 0;
    this.isConfigured = false;
    this.waitingForKeyframe = true;
    this.frameCount = 0;
    this.totalBytes = 0;
    this.timestamp = 0;
    this.decodedFrameCount = 0;
  }

  close(): void {
    if (this.decoder && this.decoder.state !== 'closed') {
      try {
        this.decoder.close();
      } catch {
        // 忽略关闭错误
      }
    }
    this.decoder = null;
    this.reset();
  }
}
