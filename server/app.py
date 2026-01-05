"""
应用入口和初始化
"""

import argparse
from pathlib import Path

from aiohttp import web
import aiohttp_cors

from .config import DEFAULT_DVR_PATH, HOST, PORT
from .dvr_server import DVRServer
from .handlers import (
    handle_get_dates,
    handle_get_recordings,
    handle_get_config,
    handle_set_config,
    handle_get_cache_status,
    handle_websocket,
    set_dvr_server,
)

# 项目根目录（server/ 的父目录）
PROJECT_ROOT = Path(__file__).parent.parent


def create_app(dvr_path: str) -> web.Application:
    """创建 aiohttp 应用"""
    # 创建 DVR 服务器实例
    dvr_server = DVRServer(dvr_path)
    set_dvr_server(dvr_server)

    app = web.Application()

    # 设置 CORS
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods="*"
        )
    })

    # API 路由
    api_routes = [
        web.get('/api/v1/config', handle_get_config),
        web.post('/api/v1/config', handle_set_config),
        web.get('/api/v1/cache/status', handle_get_cache_status),
        web.get('/api/v1/recordings/dates', handle_get_dates),
        web.get('/api/v1/recordings', handle_get_recordings),
        web.get('/api/v1/stream', handle_websocket),
    ]

    for route in api_routes:
        cors.add(app.router.add_route(route.method, route.path, route.handler))

    # 静态文件（使用绝对路径）
    dist_path = PROJECT_ROOT / 'web' / 'dist'
    if dist_path.exists():
        async def handle_index(request: web.Request) -> web.Response:
            return web.FileResponse(dist_path / 'index.html')

        app.router.add_get('/', handle_index)
        app.router.add_static('/assets', dist_path / 'assets')
        print(f"静态文件目录: {dist_path}")
    else:
        print(f"警告: 静态文件目录不存在: {dist_path}")

    return app


async def on_startup(app: web.Application):
    """启动时初始化（不自动加载路径，等待前端设置）"""
    pass


def main():
    """主入口"""
    parser = argparse.ArgumentParser(description='天视通 DVR Web 服务器')
    parser.add_argument('--dvr-path', default=DEFAULT_DVR_PATH,
                        help=f'DVR 存储路径 (默认: {DEFAULT_DVR_PATH})')
    parser.add_argument('--host', default=HOST, help=f'监听地址 (默认: {HOST})')
    parser.add_argument('--port', type=int, default=PORT, help=f'监听端口 (默认: {PORT})')

    args = parser.parse_args()

    print("=" * 60)
    print("天视通 DVR Web 服务器")
    print("=" * 60)
    print(f"DVR 路径: {args.dvr_path}")
    print(f"监听地址: http://{args.host}:{args.port}")
    print("=" * 60)

    app = create_app(args.dvr_path)
    app.on_startup.append(on_startup)

    web.run_app(app, host=args.host, port=args.port)


if __name__ == '__main__':
    main()
