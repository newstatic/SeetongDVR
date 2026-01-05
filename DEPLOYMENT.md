# Deployment Guide / 部署指南

Seetong DVR Web Viewer deployment documentation.

## Quick Start / 快速开始

### One-click Setup / 一键部署

```bash
git clone https://github.com/newstatic/SeetongDVR.git
cd SeetongDVR
./setup.sh
```

### Start Server / 启动服务

```bash
source .venv/bin/activate
python server.py /path/to/dvr/storage
```

Open browser: http://localhost:8080

---

## Manual Installation / 手动安装

### Prerequisites / 环境要求

| Component | Version | Required |
|-----------|---------|----------|
| Python | 3.9+ | Yes |
| Node.js | 18+ | Yes |
| npm | 8+ | Yes |
| 7z | any | Optional (OCR training) |
| Tesseract | 4.0+ | Optional (OCR) |

### Step 1: Clone Repository / 克隆仓库

```bash
git clone https://github.com/newstatic/SeetongDVR.git
cd SeetongDVR
```

### Step 2: Python Environment / Python 环境

```bash
# Create virtual environment
python3 -m venv .venv

# Activate
source .venv/bin/activate  # Linux/Mac
# or
.venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt
```

### Step 3: Build Frontend / 构建前端

```bash
cd web
npm install
npm run build
cd ..
```

### Step 4: Run Server / 运行服务

```bash
python server.py /path/to/dvr/storage
```

---

## Configuration / 配置

### Server Options / 服务器选项

```bash
python server.py <storage_path> [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `storage_path` | Required | Path to DVR storage (TIndex00.tps location) |
| `--host` | `0.0.0.0` | Server bind address |
| `--port` | `8080` | Server port |

### Environment Variables / 环境变量

```bash
export DVR_HOST=0.0.0.0
export DVR_PORT=8080
```

---

## Storage Path / 存储路径

The DVR storage path should contain:

```
/path/to/dvr/storage/
├── TIndex00.tps      # Index file (required)
├── TRec000000.tps    # Video data file
├── TRec000001.tps
├── ...
└── TMsgFile.tps      # Message file
```

### Common Paths / 常见路径

| Platform | Path Example |
|----------|--------------|
| USB Drive (Mac) | `/Volumes/DVR_USB` |
| USB Drive (Linux) | `/media/username/DVR_USB` |
| Network Share | `/mnt/dvr_share` |
| Local Copy | `~/dvr_backup` |

---

## Production Deployment / 生产部署

### Using systemd (Linux)

Create service file `/etc/systemd/system/seetong-dvr.service`:

```ini
[Unit]
Description=Seetong DVR Web Viewer
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/SeetongDVR
Environment=PATH=/opt/SeetongDVR/.venv/bin
ExecStart=/opt/SeetongDVR/.venv/bin/python server.py /mnt/dvr_storage
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable seetong-dvr
sudo systemctl start seetong-dvr
```

### Using Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install Node.js
RUN apt-get update && apt-get install -y nodejs npm && rm -rf /var/lib/apt/lists/*

# Copy project
COPY . .

# Setup Python
RUN pip install -r requirements.txt

# Build frontend
RUN cd web && npm install && npm run build

# Expose port
EXPOSE 8080

# Run server (mount DVR storage at /dvr)
CMD ["python", "server.py", "/dvr"]
```

Build and run:

```bash
docker build -t seetong-dvr .
docker run -d -p 8080:8080 -v /path/to/dvr:/dvr seetong-dvr
```

### Reverse Proxy (Nginx)

```nginx
server {
    listen 80;
    server_name dvr.example.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## Browser Requirements / 浏览器要求

H.265/HEVC hardware decoding requires:

| Browser | Minimum Version | Notes |
|---------|-----------------|-------|
| Chrome | 107+ | Recommended |
| Edge | 107+ | Recommended |
| Safari | 16+ | macOS 13+ |
| Firefox | Not supported | No WebCodecs HEVC |

---

## Troubleshooting / 故障排除

### Server won't start / 服务无法启动

```bash
# Check if port is in use
lsof -i :8080

# Try different port
python server.py /path/to/dvr --port 8081
```

### No video playback / 无法播放视频

1. Check browser supports H.265 WebCodecs
2. Verify DVR storage path is correct
3. Check console for errors (F12 -> Console)

### Index file not found / 找不到索引文件

```bash
# Verify TIndex00.tps exists
ls -la /path/to/dvr/TIndex00.tps
```

### Permission denied / 权限不足

```bash
# Linux: add user to disk group
sudo usermod -a -G disk $USER

# Or change ownership
sudo chown -R $USER:$USER /path/to/dvr
```

---

## Development / 开发模式

### Frontend Development

```bash
cd web
npm run dev
```

This starts Vite dev server with hot reload at http://localhost:5173

### Install Dev Dependencies

```bash
pip install -r requirements-dev.txt
```

---

## Support / 支持

- GitHub Issues: https://github.com/newstatic/SeetongDVR/issues
- Documentation: See `OCR_TRAINING_GUIDE.md` for OCR training
