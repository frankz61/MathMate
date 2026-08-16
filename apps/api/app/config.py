# -*- coding: utf-8 -*-
"""运行期配置。

配置来源优先级:进程环境变量 > 仓库根目录 `.env` > 代码内默认值。

`.env` 由本模块自行读取(而非依赖 python-dotenv),这样三种启动方式行为一致:
终端 `uvicorn`、VS Code / Cursor 调试器(它自己也会加载 envFile)、pytest。
**密钥只放 `.env`,该文件已在 .gitignore 中**;仓库里只提交 `.env.example`。
"""
from __future__ import annotations

import os

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))


def _load_dotenv() -> None:
    """极简 .env 解析:KEY=VALUE、# 注释、可选引号。不覆盖已存在的环境变量。"""
    path = os.path.join(REPO_ROOT, ".env")
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                # 已显式设置的环境变量优先 —— 调试器/CI 传进来的应当压过文件
                os.environ.setdefault(key, val)
    except OSError:
        pass


_load_dotenv()


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """LLM 相关配置。

    `enabled` 是三态的:显式 `MATHMATE_LLM_ENABLED=0` 关闭(测试固定走这条),
    未设置时以「有没有 api_key」判断 —— 没有密钥就自动退回 NFR-AV-07 降级路径,
    而不是在运行期抛错。
    """

    def __init__(self) -> None:
        self.llm_base_url = os.environ.get(
            "MATHMATE_LLM_BASE_URL", "https://kittycat.frankzhangs.top:12580/v1"
        ).rstrip("/")
        self.llm_api_key = os.environ.get("MATHMATE_LLM_API_KEY", "").strip()
        self.llm_model = os.environ.get("MATHMATE_LLM_MODEL", "my-combo").strip()
        self.llm_vision_model = os.environ.get(
            "MATHMATE_LLM_VISION_MODEL", self.llm_model
        ).strip()
        self.llm_timeout = float(os.environ.get("MATHMATE_LLM_TIMEOUT", "120"))
        self.llm_max_retries = int(os.environ.get("MATHMATE_LLM_MAX_RETRIES", "2"))
        self._llm_switch = os.environ.get("MATHMATE_LLM_ENABLED")

        # 批量导入时是否让 LLM 重排知识点候选。**默认关**:
        # 288 题留一法实测(scripts/llm_eval_kp.py)重排并不比检索更准
        # —— 同一子集上 50.4% vs 52.1%,区间几乎完全重叠 —— 却要多花钱多等几秒。
        # 瓶颈在检索召回(金标落在 top-8 候选内只有 72.1%),不在候选内的排序。
        # 教师在校对页单题点「AI 重推」不受此开关影响:那是人主动求助,且结果当场可判。
        self.llm_rerank_kp = _flag("MATHMATE_LLM_RERANK_KP", False)

    @property
    def llm_enabled(self) -> bool:
        if self._llm_switch is not None:
            return _flag("MATHMATE_LLM_ENABLED", True) and bool(self.llm_api_key)
        return bool(self.llm_api_key)

    def redacted(self) -> dict:
        """给 /health 用:确认配置是否到位,但不回显密钥。"""
        key = self.llm_api_key
        return {
            "enabled": self.llm_enabled,
            "base_url": self.llm_base_url,
            "model": self.llm_model,
            "vision_model": self.llm_vision_model,
            "rerank_kp": self.llm_rerank_kp,
            "api_key": f"{key[:6]}…{key[-4:]}" if len(key) > 12 else ("已设置" if key else None),
        }


settings = Settings()
