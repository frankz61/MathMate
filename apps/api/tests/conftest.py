# -*- coding: utf-8 -*-
"""测试期全局约定。

**测试绝不打真实 LLM**:会花钱、会因网络抖动变红、会让「这条断言到底测的是
我的代码还是模型今天的心情」变成无解问题。需要验证 LLM 路径的用例一律用
monkeypatch 注入假客户端(见 test_llm.py),真实连通性由 `/api/v1/llm/health`
和 scripts/llm_smoke.py 单独验证。

必须在 import app.* 之前生效 —— app.config 在导入时就会读环境变量。
"""
from __future__ import annotations

import os

os.environ["MATHMATE_LLM_ENABLED"] = "0"


# ---------------------------------------------------------------- 鉴权辅助
# 所有端点都要凭证之后(DESIGN §10.1),用例不该在每个文件里重抄一遍登录流程。
# 这里给 TestClient 同时挂上教师 JWT 与大屏 device_token —— 两者读的是不同的
# 请求头,互不干扰,业务用例因此可以照常写。**权限边界本身**由 test_auth.py
# 用只带单一凭证的干净客户端单独验证。

DEMO_LOGIN = {"username": "teacher", "password": "mathmate"}


def login_teacher(client) -> str:
    r = client.post("/api/v1/auth/login", json=DEMO_LOGIN)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def pair_screen(client, class_id: str) -> str:
    """走完整的配对流程拿 device_token —— 需要客户端已带教师凭证。"""
    r = client.post("/api/v1/screens/pairings", json={"class_id": class_id})
    assert r.status_code == 201, r.text
    r2 = client.post("/api/v1/screens/pair", json={"code": r.json()["code"]})
    assert r2.status_code == 200, r2.text
    return r2.json()["device_token"]


def authenticate(client):
    """给测试客户端装上两类凭证,返回它自己。"""
    client.headers.update({"Authorization": f"Bearer {login_teacher(client)}"})
    class_id = client.get("/api/v1/classes").json()[0]["id"]
    client.headers.update({"X-Device-Token": pair_screen(client, class_id)})
    return client
