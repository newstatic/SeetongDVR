/// <reference types="vite/client" />

interface VideoDecoderConfig {
  codec: string;
  codedWidth?: number;
  codedHeight?: number;
  hardwareAcceleration?: 'prefer-hardware' | 'prefer-software' | 'no-preference';
  description?: BufferSource;
}

interface VideoDecoderSupport {
  supported: boolean;
  config?: VideoDecoderConfig;
}

interface EncodedVideoChunkInit {
  type: 'key' | 'delta';
  timestamp: number;
  duration?: number;
  data: BufferSource;
}

declare class EncodedVideoChunk {
  constructor(init: EncodedVideoChunkInit);
  readonly type: 'key' | 'delta';
  readonly timestamp: number;
  readonly duration: number | null;
  readonly byteLength: number;
  copyTo(destination: BufferSource): void;
}

interface VideoDecoderInit {
  output: (frame: VideoFrame) => void;
  error: (error: DOMException) => void;
}

declare class VideoDecoder {
  constructor(init: VideoDecoderInit);
  static isConfigSupported(config: VideoDecoderConfig): Promise<VideoDecoderSupport>;
  readonly state: 'unconfigured' | 'configured' | 'closed';
  readonly decodeQueueSize: number;
  configure(config: VideoDecoderConfig): void;
  decode(chunk: EncodedVideoChunk): void;
  flush(): Promise<void>;
  reset(): void;
  close(): void;
}

interface VideoFrameInit {
  duration?: number;
  timestamp?: number;
  alpha?: 'discard' | 'keep';
}

declare class VideoFrame {
  constructor(image: CanvasImageSource, init?: VideoFrameInit);
  readonly format: string | null;
  readonly codedWidth: number;
  readonly codedHeight: number;
  readonly displayWidth: number;
  readonly displayHeight: number;
  readonly duration: number | null;
  readonly timestamp: number;
  close(): void;
}
