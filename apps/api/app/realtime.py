# -*- coding: utf-8 -*-
"""WebSocket 广播中枢。

本切片是**进程内**实现:单 worker 下正确。DESIGN.md §ADR-05 要求多实例通过
Redis Pub/Sub 广播 —— 部署多 worker 前必须替换,否则连在不同实例上的
教师端与大屏互相收不到消息。

连接按角色分组。这不是为了方便,是 DESIGN §10.1 的硬约束:大屏**不可读归因
聚合**。同一个房间里教师端要收完整 tally(哪道题多少人选、主导归因是什么),
大屏只该收到「已交多少人」这个进度数字 —— 否则一块任何人都能凑近看的屏幕
会把「全班都不会第 7 题」变成公开信息。
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from fastapi import WebSocket

log = logging.getLogger(__name__)

TEACHER, SCREEN = "teacher", "screen"


class Hub:
    def __init__(self) -> None:
        # room -> {ws: role}
        self._rooms: dict[str, dict[WebSocket, str]] = defaultdict(dict)
        self._lock = asyncio.Lock()

    async def join(self, room: str, ws: WebSocket, role: str = TEACHER) -> None:
        await ws.accept()
        async with self._lock:
            self._rooms[room][ws] = role

    async def leave(self, room: str, ws: WebSocket) -> None:
        async with self._lock:
            self._rooms.get(room, {}).pop(ws, None)
            if not self._rooms.get(room):
                self._rooms.pop(room, None)

    async def broadcast(self, room: str, message: dict, roles: set[str] | None = None) -> None:
        """roles=None 表示发给房间里所有人;给定集合时只发给这些角色。"""
        async with self._lock:
            targets = [ws for ws, role in self._rooms.get(room, {}).items()
                       if roles is None or role in roles]
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
                    self._rooms.get(room, {}).pop(ws, None)

    def room_size(self, room: str, role: str | None = None) -> int:
        members = self._rooms.get(room, {})
        return len(members) if role is None else sum(1 for r in members.values() if r == role)


hub = Hub()
