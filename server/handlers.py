"""
HTTP 和 WebSocket 请求处理器
"""

import asyncio
import json
import time
from typing import Optional

from aiohttp import web, WSMsgType
from zoneinfo import ZoneInfo

from .config import DEFAULT_TIMEZONE
from .dvr_server import DVRServer


# 全局服务器实例和时区设置
dvr_server: Optional[DVRServer] = None
current_timezone = DEFAULT_TIMEZONE


def set_dvr_server(server: DVRServer):
    """设置全局 DVR 服务器实例"""
    global dvr_server
    dvr_server = server


def get_dvr_server() -> Optional[DVRServer]:
    """获取全局 DVR 服务器实例"""
    return dvr_server


def set_timezone(tz_name: str):
    """设置全局时区"""
    global current_timezone
    current_timezone = tz_name


def get_timezone() -> str:
    """获取当前时区"""
    return current_timezone


# ==================== REST API 处理器 ====================

async def handle_get_dates(request: web.Request) -> web.Response:
    """GET /api/v1/recordings/dates - 获取有录像的日期列表"""
    channel = request.query.get('channel')
    channel_int = int(channel) if channel else None

    result = dvr_server.get_recording_dates(channel_int, current_timezone)
    return web.json_response(result)


async def handle_get_recordings(request: web.Request) -> web.Response:
    """GET /api/v1/recordings - 获取指定日期的录像列表"""
    date = request.query.get('date')
    channel = request.query.get('channel')

    if not date:
        return web.json_response({"error": "缺少 date 参数"}, status=400)

    channel_int = int(channel) if channel else None
    result = dvr_server.get_recordings(date, channel_int, current_timezone)
    return web.json_response(result)


async def handle_get_config(request: web.Request) -> web.Response:
    """GET /api/v1/config - 获取当前配置"""
    result = {
        "storagePath": str(dvr_server.dvr_path) if dvr_server else "",
        "loaded": dvr_server.loaded if dvr_server else False,
        "timezone": current_timezone,
    }
    if dvr_server and dvr_server.loaded and dvr_server.storage:
        result["entryCount"] = len(dvr_server.storage.segments)
        result["fileCount"] = len(list(dvr_server.dvr_path.glob('TRec*.tps')))
        result["cacheStatus"] = dvr_server.get_cache_status()
    return web.json_response(result)


async def handle_set_config(request: web.Request) -> web.Response:
    """POST /api/v1/config - 设置配置"""
    global dvr_server, current_timezone

    try:
        data = await request.json()
        new_path = data.get('storagePath')
        new_timezone = data.get('timezone')

        result = {
            "timezone": current_timezone,
        }

        # 更新时区
        if new_timezone:
            try:
                ZoneInfo(new_timezone)
                current_timezone = new_timezone
                result["timezone"] = current_timezone
                print(f"[Config] 时区已更改为: {current_timezone}")
            except Exception:
                return web.json_response({"error": f"无效的时区: {new_timezone}"}, status=400)

        # 更新存储路径
        if new_path:
            print(f"[Config] 正在加载新路径: {new_path}")
            new_server = DVRServer(new_path)
            if new_server.load():
                dvr_server = new_server
                # 标记为正在构建
                dvr_server._cache_building = True
                dvr_server._cache_total = len(dvr_server.storage.segments)
                dvr_server._cache_current = 0
                dvr_server._cache_progress = 0
                result.update({
                    "storagePath": str(dvr_server.dvr_path),
                    "loaded": True,
                    "entryCount": len(dvr_server.storage.segments),
                    "fileCount": len(list(dvr_server.dvr_path.glob('TRec*.tps'))),
                    "cacheStatus": dvr_server.get_cache_status(),
                })
                # 在后台线程中构建 VPS 缓存
                loop = asyncio.get_event_loop()
                loop.run_in_executor(None, dvr_server.build_vps_cache)
                print(f"[Config] 加载成功，共 {len(dvr_server.storage.segments)} 个段落")
            else:
                print(f"[Config] 加载失败: {new_path}")
                return web.json_response({
                    "storagePath": new_path,
                    "loaded": False,
                    "error": "无法加载指定路径的 DVR 数据"
                }, status=400)
        else:
            result.update({
                "storagePath": str(dvr_server.dvr_path) if dvr_server else "",
                "loaded": dvr_server.loaded if dvr_server else False,
            })
            if dvr_server and dvr_server.loaded and dvr_server.storage:
                result["entryCount"] = len(dvr_server.storage.segments)
                result["fileCount"] = len(list(dvr_server.dvr_path.glob('TRec*.tps')))

        return web.json_response(result)

    except json.JSONDecodeError:
        return web.json_response({"error": "无效的 JSON"}, status=400)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[Config] 异常: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_get_cache_status(request: web.Request) -> web.Response:
    """GET /api/v1/cache/status - 获取缓存构建状态"""
    if not dvr_server:
        return web.json_response({
            "status": "not_loaded",
            "progress": 0,
            "total": 0,
            "current": 0,
            "cached": 0,
        })
    return web.json_response(dvr_server.get_cache_status())


# ==================== WebSocket 处理器 ====================

async def handle_websocket(request: web.Request) -> web.WebSocketResponse:
    """WebSocket /api/v1/stream - 视频流"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    session_id = str(id(ws))
    stream_task: Optional[asyncio.Task] = None

    print(f"[WS] 新连接: {session_id}")

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    action = data.get('action')

                    if action == 'play':
                        channel = data.get('channel', 1)
                        timestamp = data.get('timestamp', int(time.time()))
                        speed = data.get('speed', 1.0)

                        if stream_task and not stream_task.done():
                            stream_task.cancel()

                        stream_task = asyncio.create_task(
                            dvr_server.stream_video_with_audio(ws, channel, timestamp, speed)
                        )
                        print(f"[WS] 开始播放: ch={channel}, ts={timestamp}, speed={speed}")

                    elif action == 'pause':
                        if stream_task and not stream_task.done():
                            stream_task.cancel()
                            stream_task = None
                        print(f"[WS] 暂停")

                    elif action == 'seek':
                        timestamp = data.get('timestamp')
                        channel = data.get('channel', 1)
                        speed = data.get('speed', 1.0)

                        if stream_task and not stream_task.done():
                            stream_task.cancel()

                        stream_task = asyncio.create_task(
                            dvr_server.stream_video_with_audio(ws, channel, timestamp, speed)
                        )
                        print(f"[WS] Seek: ts={timestamp}")

                    elif action == 'speed':
                        speed = data.get('rate', 1.0)
                        print(f"[WS] 速度变更: {speed}x")

                except json.JSONDecodeError:
                    await ws.send_json({"error": "无效的 JSON"})

            elif msg.type == WSMsgType.ERROR:
                print(f"[WS] 错误: {ws.exception()}")

    except asyncio.CancelledError:
        pass
    finally:
        if stream_task and not stream_task.done():
            stream_task.cancel()
        print(f"[WS] 断开连接: {session_id}")

    return ws
