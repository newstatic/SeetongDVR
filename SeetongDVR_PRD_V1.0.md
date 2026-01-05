# 天视通 DVR Web 查看器

## 产品需求规格说明书 (PRD)

**Seetong DVR Web Viewer**

| 项目 | 内容 |
|------|------|
| 文档版本 | V1.0 |
| 创建日期 | 2026-01-02 |
| 项目名称 | SeetongDVR Web Viewer |
| 文档状态 | 草稿 |

---

## 1. 项目概述

### 1.1 项目背景

天视通（Seetong）是国内知名的安防监控方案提供商，其NVR/DVR设备使用专有的TPS格式存储录像文件。目前用户查看录像需要使用天视通官方客户端或将存储设备连接到NVR进行回放，操作不够便捷。本项目旨在开发一个基于Web的录像查看器，支持用户通过浏览器直接浏览和播放DVR录像，提升用户体验。

### 1.2 项目目标

- 实现TPS格式索引文件解析，提取录像时间线信息
- 支持按日期、通道浏览录像列表
- 通过WebSocket实时传输H.265视频流
- 在浏览器中实现H.265视频解码和播放
- 提供时间轴拖拽、快进、慢放等播放控制功能

### 1.3 目标用户

- 使用天视通DVR/NVR设备的个人用户
- 安防监控系统运维人员
- 需要远程查看历史录像的管理人员

---

## 2. 系统架构

### 2.1 整体架构

系统采用前后端分离架构，后端使用Python开发，前端使用React框架。视频流通过WebSocket协议传输，前端使用WebCodecs API实现H.265硬件加速解码。

```
┌─────────────┐    WebSocket    ┌─────────────┐    File I/O    ┌─────────────┐
│   Browser   │ ◄────────────► │   Python    │ ◄────────────► │  TPS Files  │
│   (React)   │    H.265 NAL   │   Server    │    Raw Data    │  (SD Card)  │
└─────────────┘                └─────────────┘                └─────────────┘
```

### 2.2 技术栈

| 层级 | 技术选型 | 说明 |
|------|----------|------|
| 后端 | Python + WebSocket | TPS解析与视频流服务 |
| 前端框架 | React 18 + TypeScript | 响应式UI框架 |
| UI样式 | Tailwind CSS | 原子化CSS框架 |
| 视频解码 | WebCodecs API | 浏览器原生H.265硬件加速解码 |
| 通信协议 | WebSocket | 全双工实时通信 |
| 视频渲染 | Canvas 2D | 视频帧渲染 |
| 构建工具 | Vite | 快速开发构建工具 |

---

## 3. 功能需求

### 3.1 录像索引管理

#### 3.1.1 TPS索引解析

系统需要解析天视通TPS格式的索引文件（TIndex00.tps），提取以下信息：

- 录像文件数量和列表
- 每个录像片段的开始/结束时间戳
- 通道号信息
- 关键帧（I帧）数量
- 文件偏移量信息

#### 3.1.2 日历视图

- 以日历形式展示有录像的日期
- 有录像的日期高亮显示
- 点击日期显示当天的录像时间线
- 支持按通道筛选

#### 3.1.3 时间线视图

- 24小时时间轴展示当天录像分布
- 不同颜色区分不同通道
- 显示录像片段的起止时间
- 点击时间线跳转到对应时间点播放

### 3.2 视频播放

#### 3.2.1 WebSocket视频流

后端通过WebSocket推送H.265 NAL单元到前端：

- 支持按时间点请求视频流
- 自动定位到最近的关键帧
- 支持流控制（暂停/继续）
- 断线自动重连机制

#### 3.2.2 H.265解码播放

- 使用WebCodecs API实现H.265硬件加速解码
- Canvas 2D渲染解码后的VideoFrame
- 支持1080P及以上分辨率
- 目标帧率：25fps实时播放
- 需要Chrome 94+并启用 `chrome://flags/#enable-platform-hevc`

#### 3.2.3 播放控制

| 功能 | 操作 | 说明 |
|------|------|------|
| 播放/暂停 | 点击按钮/空格键 | 切换播放状态 |
| 进度跳转 | 拖拽进度条 | 跳转到指定时间点 |
| 倍速播放 | 选择倍速 | 0.5x / 1x / 2x / 4x |
| 逐帧 | ← → 方向键 | 暂停状态下逐帧查看 |
| 全屏 | 双击/F键 | 全屏播放模式 |
| 截图 | 截图按钮 | 保存当前帧为图片 |

---

## 4. 接口设计

### 4.1 REST API

#### 4.1.1 获取录像日期列表

```
GET /api/v1/recordings/dates?channel={channel}
```

响应示例：
```json
{
  "dates": ["2025-12-18", "2025-12-19", ...],
  "channels": [1, 2]
}
```

#### 4.1.2 获取指定日期录像列表

```
GET /api/v1/recordings?date={date}&channel={channel}
```

响应示例：
```json
{
  "recordings": [
    {
      "id": 1,
      "channel": 1,
      "start": "13:07:29",
      "end": "15:10:04",
      "duration": 7355
    },
    ...
  ]
}
```

### 4.2 WebSocket协议

#### 4.2.1 连接地址

```
ws://{host}:{port}/api/v1/stream
```

#### 4.2.2 消息格式

**客户端 → 服务端（JSON）：**

- `play`: `{ "action": "play", "channel": 1, "timestamp": 1766034449 }`
- `pause`: `{ "action": "pause" }`
- `seek`: `{ "action": "seek", "timestamp": 1766041804 }`
- `speed`: `{ "action": "speed", "rate": 2.0 }`

**服务端 → 客户端（二进制帧）：**

| 字段 | 大小 | 说明 |
|------|------|------|
| Magic | 4 bytes | 0x48323635 ('H265') |
| Timestamp | 8 bytes | Unix时间戳（毫秒） |
| FrameType | 1 byte | 0=P帧, 1=I帧, 2=VPS, 3=SPS, 4=PPS |
| DataLen | 4 bytes | NAL数据长度 |
| Data | N bytes | H.265 NAL单元数据（含起始码） |

---

## 5. 界面设计

### 5.1 页面布局

采用左右分栏布局，左侧为日历和录像列表，右侧为视频播放区域。

```
┌──────────────────────────────────────────────────────────────┐
│  [Logo] 天视通 DVR 查看器                    [设置] [帮助] │
├──────────────┬───────────────────────────────────────────────┤
│              │                                               │
│   日历组件   │                                               │
│  [< 2025年1月 >]│              视频播放区域                    │
│  日 一 二 三...│                                               │
│              │                                               │
├──────────────┼───────────────────────────────────────────────┤
│  通道选择    │  [|◄] [▶] [►|]  ▬▬▬●▬▬▬▬▬  00:15:30/02:05:15  │
│  [✓] Ch1     ├───────────────────────────────────────────────┤
│  [ ] Ch2     │  |0    3    6    9   12   15   18   21   24|  │
│              │  |████████████░░░░░████████████████░░░░░░░░|  │
├──────────────┴───────────────────────────────────────────────┤
└──────────────────────────────────────────────────────────────┘
```

### 5.2 组件说明

| 组件 | 功能描述 |
|------|----------|
| 日历组件 | 月历视图，有录像日期标记蓝点，当前选中日期高亮 |
| 通道选择 | 复选框列表，支持多选，筛选显示指定通道录像 |
| 播放器 | Canvas视频渲染，支持WebGL加速，16:9自适应 |
| 控制条 | 播放/暂停、进度条、时间显示、倍速、全屏、截图 |
| 时间线 | 24小时横向时间轴，录像片段色块展示，可点击定位 |

---

## 6. 性能要求

| 指标 | 目标值 | 说明 |
|------|--------|------|
| 索引解析时间 | < 500ms | 32MB索引文件 |
| 首帧显示时间 | < 1s | 点击播放到首帧显示 |
| 视频帧率 | ≥ 25fps | 1080P H.265解码 |
| Seek延迟 | < 500ms | 跳转到指定时间 |
| 内存占用 | < 500MB | 浏览器标签页 |
| WebSocket延迟 | < 100ms | 局域网环境 |

---

## 7. 开发计划

### 7.1 里程碑

| 阶段 | 时间 | 交付物 |
|------|------|--------|
| P1 | 第1-2周 | TPS解析库、索引API、基础前端框架 |
| P2 | 第3-4周 | WebSocket视频流、H.265解码集成 |
| P3 | 第5-6周 | 播放器UI、时间线、播放控制 |
| P4 | 第7-8周 | 性能优化、测试、文档 |

### 7.2 技术风险

| 风险项 | 影响 | 缓解措施 |
|--------|------|----------|
| H.265解码性能 | 低端设备卡顿 | 依赖硬件加速，降级到720P或降帧率 |
| TPS格式兼容性 | 不同固件版本差异 | 收集多版本样本测试 |
| 浏览器兼容性 | WebCodecs仅Chrome支持 | 提供Chrome推荐提示，需启用HEVC flag |

---

## 附录 A: TPS文件格式

### A.1 索引文件结构 (TIndex00.tps)

```
偏移 0x00:  Magic (8 bytes) - 文件标识
偏移 0x10:  FileCount (4 bytes, LE) - 录像文件数量
偏移 0x14:  EntryCount (4 bytes, LE) - 索引条目数
偏移 0x4FC: IndexEntries[] - 索引条目数组
```

#### 索引条目结构 (64 bytes)

```
+0x00: FileOffset  (4 bytes) - 文件内偏移
+0x04: Channel     (1 byte)  - 通道号
+0x05: Flags       (1 byte)  - 标志位
+0x06: FrameCount  (2 bytes) - I帧数量
+0x08: StartTime   (4 bytes) - 开始时间戳 (Unix)
+0x0C: EndTime     (4 bytes) - 结束时间戳 (Unix)
+0x10: Reserved    (48 bytes) - 保留
```

### A.2 录像文件格式 (TRec*.tps)

录像文件为纯H.265 Annex B格式裸流，每个文件固定256MB。

- NAL起始码: `0x00 0x00 0x00 0x01`
- 文件开头: VPS (0x40) → SPS (0x42) → PPS (0x44) → IDR帧 (0x26)
- 支持的NAL类型: VPS(32), SPS(33), PPS(34), IDR(19/20), TRAIL(0/1)

---

## 附录 B: 精确时间定位算法

### B.1 问题描述

TPS 索引文件的帧索引记录提供了时间范围 (`start_time`, `end_time`) 和字节范围 (`file_start_offset`, `file_end_offset`)，但这只是粗略的时间映射。实际视频中每个关键帧 (GOP/VPS) 的精确时间需要通过字节位置插值算法计算。

### B.2 核心算法

**字节位置线性插值公式：**

```
VPS_time = start_time + (vps_byte_offset - file_start_offset) / (file_end_offset - file_start_offset) × (end_time - start_time)
```

**Python 实现：**

```python
def calculate_vps_precise_time(frame_record, vps_offset: int) -> int:
    """根据 VPS 的字节位置计算其精确时间戳"""
    byte_offset = vps_offset - frame_record.file_start_offset
    total_bytes = frame_record.file_end_offset - frame_record.file_start_offset
    duration = frame_record.end_time - frame_record.start_time

    if total_bytes <= 0:
        return frame_record.start_time

    time_offset = (byte_offset / total_bytes) * duration
    return int(frame_record.start_time + time_offset)
```

### B.3 算法精度

基于 100 个样本的大规模验证结果：

| 指标 | 数值 |
|------|------|
| 完全匹配 (0秒误差) | 54.1% |
| ±1秒内 | 78.6% |
| ±2秒内 | 87.8% |
| 平均误差 | 0.76 秒 |
| 提取成功率 | 100% |

### B.4 GOP 间隔特性

DVR 录像的 GOP (关键帧组) 间隔是动态的：

- **静态场景**: 约 12 秒间隔
- **动态场景**: 约 7-8 秒间隔
- **平均间隔**: 约 8 秒

这意味着通过关键帧提取的时间精度受限于 GOP 间隔，无法做到任意秒级精确定位。

### B.5 VPS 查找方法

**VPS 模式识别：**

```
H.265 VPS NAL 起始码: 0x00 0x00 0x00 0x01 0x40 0x01
                      └─── 起始码 ───┘ └─ NAL Type 32 (VPS)
```

**在帧索引范围内查找 VPS：**

```python
def find_vps_in_range(file_handle, start: int, end: int) -> List[int]:
    """在文件范围内查找所有 VPS 位置"""
    VPS_PATTERN = bytes([0x00, 0x00, 0x00, 0x01, 0x40, 0x01])
    file_handle.seek(start)
    data = file_handle.read(end - start)

    positions = []
    pos = 0
    while True:
        found = data.find(VPS_PATTERN, pos)
        if found < 0:
            break
        positions.append(start + found)
        pos = found + 6

    return positions
```

### B.6 使用建议

1. **快速模式**: 使用字节插值算法，精度 ±2 秒，适用于大多数场景
2. **精确模式**: 结合 OCR 读取视频水印时间，精度 100%，适用于取证等需要精确时间的场景
3. **时间线显示**: 使用帧索引的 `start_time` 和 `end_time` 显示概览
4. **Seek 定位**: 使用字节插值算法定位到最近的关键帧

### B.7 相关代码模块

| 文件 | 功能 |
|------|------|
| `tps_storage_lib.py` | 核心库，包含 `calculate_vps_precise_time()` 等函数 |
| `precise_frame_extractor.py` | 精确帧提取器，封装完整的提取流程 |

---

## 附录 C: tpsrecordLib.dll 逆向分析

### C.1 存储系统架构

通过对官方 DLL 的汇编逆向分析，完整解析了存储系统的设计：

```
存储系统架构:
┌─────────────────────────────────────────────────────┐
│                    Storage Path                      │
├─────────────────────────────────────────────────────┤
│ TIndex00.tps    │ 主索引文件 (~32MB)                 │
│ TIndex00_bak    │ 索引备份                           │
├─────────────────────────────────────────────────────┤
│ TRec000000.tps  │ 录像文件 #0 (256MB)                │
│ TRec000001.tps  │ 录像文件 #1 (256MB)                │
│ ...             │ ...                                │
│ TRecNNNNNN.tps  │ 录像文件 #N (最大 255 个)          │
└─────────────────────────────────────────────────────┘
```

### C.2 关键常量

从汇编提取的关键常量：

| 常量 | 值 | 说明 |
|------|-----|------|
| SEGMENT_INDEX_OFFSET | 0x500 | 段落索引起始偏移 |
| FRAME_INDEX_OFFSET | 0x84C0 | 帧索引起始偏移 |
| RECORD_SIZE | 0x40 | 每条记录 64 字节 |
| MAX_SEGMENT_COUNT | 0xB9E | 最大段落数 2974 |
| MAX_FRAME_INDEX | 0x7B40 | 最大帧索引数 31,552 |
| INDEX_FILE_SIZE | 0x1FF84C0 | 索引文件大小 ~32MB |

### C.3 索引条目与文件映射

**关键发现：** 段落索引的 `entry_index` 直接对应 TRec 文件编号。

```
条目 0   → TRec000000.tps
条目 10  → TRec000010.tps
条目 50  → TRec000050.tps
```

### C.4 环回写入机制

DLL 实现了循环覆盖录像：

```c
if (iCurrFileRecNo >= iMaxAVFileNum) {
    iCurrFileRecNo = 0;  // 重置到第一个文件
}
```

### C.5 帧索引结构 (64 字节)

```
偏移    大小    字段名           描述
0x00    4       unknown_0        未知
0x04    4       unknown_1        未知
0x08    4       start_time       开始时间戳 (Unix)
0x0C    4       end_time         结束时间戳 (Unix)
0x10    4       file_start       文件起始偏移
0x14    4       file_end         文件结束偏移
0x18-3F 40      reserved         保留
```

---

*— 文档结束 —*
