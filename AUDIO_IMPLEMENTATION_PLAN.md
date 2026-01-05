# 天视通 DVR 音频实现规划

## 1. 当前状态分析

### 1.1 存储结构分析结果

| 文件 | 内容 | 音频数据 |
|------|------|----------|
| `TRec*.tps` | 纯 H.265 视频流 | 无 |
| `TIndex00.tps` | 索引文件 | 无音频索引 |
| `TMsgFile.tps` | 消息文件（空） | 无 |
| `TFformat_ts` | 格式化时间戳 | 无 |
| `IPC_Log/` | 日志文件 | 无 |

### 1.2 结论

**当前 DVR 录像不包含音频数据**

可能原因：
1. DVR 硬件本身不支持音频录制
2. DVR 设置中音频录制被禁用
3. 录像时没有连接麦克风

### 1.3 DLL 逆向分析中的音频相关信息

`tps_storage_lib.py` 中定义了音频帧类型：
```python
class FrameType(IntEnum):
    VIDEO_I = 0x01  # I 帧
    VIDEO_P = 0x02  # P 帧
    VIDEO_B = 0x03  # B 帧
    AUDIO = 0x10    # 音频帧  <-- 存在音频帧类型定义
```

这说明 TPS 格式**设计上支持音频**，但当前录像数据中没有音频内容。

---

## 2. 音频支持实现方案

### 2.1 前提条件

要实现音频播放，需要满足以下条件之一：
1. **获取包含音频的 DVR 录像样本** - 确认 TPS 格式中的音频存储方式
2. **从 DVR 实时流中分析音频格式** - 通过 RTSP/私有协议获取

### 2.2 推测的音频格式

基于天视通产品线的常见配置，可能的音频格式：

| 格式 | 编码 | 特征 | 应用场景 |
|------|------|------|----------|
| G.711 μ-law | PCM | 8kHz, 8-bit | 最常见，兼容性好 |
| G.711 A-law | PCM | 8kHz, 8-bit | 欧洲标准 |
| AAC-LC | ADTS | 可变码率 | 高质量音频 |
| G.726 | ADPCM | 16/24/32/40 kbps | 带宽受限环境 |

### 2.3 实现架构（假设获取到音频数据）

```
┌─────────────────────────────────────────────────────────────────┐
│                        TRec File                                 │
├─────────────────────────────────────────────────────────────────┤
│  [VPS][SPS][PPS][IDR][P][P]...[AUDIO][P][P]...[AUDIO]...        │
│       └── H.265 Video ──┘    └ G.711 ┘      └ G.711 ┘           │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
              ┌───────────────┴───────────────┐
              │                               │
              ▼                               ▼
     ┌────────────────┐              ┌────────────────┐
     │  Video Stream  │              │  Audio Stream  │
     │  (WebSocket)   │              │  (WebSocket)   │
     └────────┬───────┘              └────────┬───────┘
              │                               │
              ▼                               ▼
     ┌────────────────┐              ┌────────────────┐
     │  WebCodecs     │              │  WebAudio API  │
     │  VideoDecoder  │              │  AudioWorklet  │
     └────────┬───────┘              └────────┬───────┘
              │                               │
              ▼                               ▼
     ┌────────────────┐              ┌────────────────┐
     │  Canvas 2D     │              │  AudioContext  │
     │  Rendering     │              │  Output        │
     └────────────────┘              └────────────────┘
```

---

## 3. 实现步骤（待条件满足后执行）

### Phase 1: 音频数据解析

**后端 (Python)**

1. **识别音频帧**
   - 在 `tps_storage_lib.py` 中添加音频帧检测
   - 解析帧索引中的 `FrameType.AUDIO` 标记

2. **提取音频数据**
   - 从 TRec 文件中提取音频帧
   - 识别音频编码格式（G.711/AAC）

3. **音视频同步**
   - 基于时间戳同步音视频帧
   - 处理音视频交织存储

### Phase 2: 服务端实现

**server.py 修改**

```python
# 帧类型标识
FRAME_TYPE_VIDEO_I = 0x01
FRAME_TYPE_VIDEO_P = 0x02
FRAME_TYPE_AUDIO = 0x10

# WebSocket 消息格式扩展
async def _send_audio_frame(self, ws, audio_data: bytes, timestamp_ms: int, codec: str):
    """发送音频帧

    格式: Magic(4) + Timestamp(8) + Codec(4) + DataLen(4) + Data(N)
    Magic = 'AUDI'
    Codec = 'G711' / 'AACC'
    """
    header = struct.pack(
        '>4sQ4sI',
        b'AUDI',
        timestamp_ms,
        codec.encode()[:4].ljust(4, b'\x00'),
        len(audio_data)
    )
    await ws.send_bytes(header + audio_data)
```

### Phase 3: 前端实现

**新增 audio-decoder.ts**

```typescript
// G.711 μ-law 解码
function decodeG711ulaw(data: Uint8Array): Float32Array {
  const samples = new Float32Array(data.length);
  for (let i = 0; i < data.length; i++) {
    samples[i] = ulawToLinear(data[i]);
  }
  return samples;
}

// WebAudio 播放
class AudioPlayer {
  private audioContext: AudioContext;
  private workletNode: AudioWorkletNode;

  async init() {
    this.audioContext = new AudioContext({ sampleRate: 8000 });
    await this.audioContext.audioWorklet.addModule('/audio-processor.js');
    this.workletNode = new AudioWorkletNode(this.audioContext, 'pcm-processor');
    this.workletNode.connect(this.audioContext.destination);
  }

  pushAudioData(samples: Float32Array) {
    this.workletNode.port.postMessage({ samples });
  }
}
```

**audio-processor.js (AudioWorklet)**

```javascript
class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = [];
    this.port.onmessage = (e) => {
      this.buffer.push(...e.data.samples);
    };
  }

  process(inputs, outputs, parameters) {
    const output = outputs[0][0];
    for (let i = 0; i < output.length; i++) {
      output[i] = this.buffer.shift() ?? 0;
    }
    return true;
  }
}
registerProcessor('pcm-processor', PCMProcessor);
```

### Phase 4: 音视频同步

```typescript
// 音视频同步策略
class AVSynchronizer {
  private videoTime: number = 0;
  private audioTime: number = 0;
  private syncThreshold: number = 100; // 100ms

  onVideoFrame(timestamp: number) {
    this.videoTime = timestamp;
    this.adjustSync();
  }

  onAudioFrame(timestamp: number) {
    this.audioTime = timestamp;
    this.adjustSync();
  }

  private adjustSync() {
    const drift = this.videoTime - this.audioTime;
    if (Math.abs(drift) > this.syncThreshold) {
      // 调整音频播放速率或丢弃帧
    }
  }
}
```

---

## 4. 技术细节

### 4.1 G.711 解码

G.711 是最常见的 VoIP 音频编码，分为 μ-law (北美/日本) 和 A-law (欧洲)。

**μ-law 解码公式:**
```
linear = sign × (exp(|compressed| / 127.0 × ln(1 + μ)) - 1) / μ
where μ = 255
```

### 4.2 AAC 解码

如果音频是 AAC 格式，可以使用：
- **WebCodecs AudioDecoder** (Chrome 94+)
- **Media Source Extensions (MSE)** with fMP4

### 4.3 音频缓冲策略

```
目标延迟: 200-500ms
缓冲区大小: 2-3 个音频帧
抖动处理: 自适应缓冲
```

---

## 5. 待办事项

### 立即可做

- [ ] 确认 DVR 型号是否支持音频录制
- [ ] 检查 DVR 设置中的音频选项
- [ ] 尝试连接麦克风后录制新样本

### 获取音频样本后

- [ ] 分析音频帧在 TRec 文件中的存储位置
- [ ] 确认音频编码格式
- [ ] 实现后端音频提取
- [ ] 实现前端音频解码播放
- [ ] 实现音视频同步

---

## 6. 参考资料

- [WebAudio API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API)
- [AudioWorklet](https://developer.mozilla.org/en-US/docs/Web/API/AudioWorklet)
- [G.711 Specification (ITU-T G.711)](https://www.itu.int/rec/T-REC-G.711)
- [WebCodecs AudioDecoder](https://developer.mozilla.org/en-US/docs/Web/API/AudioDecoder)
