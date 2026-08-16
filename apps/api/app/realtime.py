# -*- coding: utf-8 -*-
"""WebSocket 广播中枢。

本切片是**进程内**实现:单 worker 下正确。DESIGN.md §ADR-05 要求多实例通过
Redis Pub/Sub 广播 —— 部署多 worker 前必须替换,否则连在不同实例上的
教师端与大屏互相收不到消息。
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from fastapi import WebSocket

log = logging.getLogger(__name__)


class Hub:
    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def join(self, room: str, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._rooms[room].add(ws)

    async def leave(self, room: str, ws: WebSocket) -> None:
        async with self._lock:
            self._rooms[room].discard(ws)
            if not self._rooms[room]:
                self._rooms.pop(room, None)

    async def broadcast(self, room: str, message: dict) -> None:
        async with self._lock:
            targets = list(self._rooms.get(room, ()))
        if not targets:
            return
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_json(message)
            except Exception:            # 连接已断:大屏断网/刷新是常态,不是异常
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._rooms[room].discard(ws)

    def room_size(self, room: str) -> int:
        return len(self._rooms.get(room, ()))


hub = Hub()
