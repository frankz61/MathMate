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
