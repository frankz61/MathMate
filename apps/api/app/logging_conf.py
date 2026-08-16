# -*- coding: utf-8 -*-
"""日志配置。

之前完全没配 —— 模块里写了 `log.warning` 但根 logger 没有 handler,
uvicorn 只接管自己的 logger,于是识别失败时**服务端一行都不打**,
只能靠接口返回的那句话猜。这次排查那张 7.8MB 照片就吃了这个亏。

    MATHMATE_LOG_LEVEL=DEBUG   调整级别(默认 INFO)
    MATHMATE_LOG_FILE=path     同时写文件(默认只输出到控制台)

Windows 控制台默认 GBK,中文日志会抛 UnicodeEncodeError 并把日志吞掉,
所以 handler 显式指定 UTF-8 且 errors="replace"。
"""
from __future__ import annotations

import logging
import os
import sys

FMT = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"
DATEFMT = "%H:%M:%S"


class _RedactKey(logging.Filter):
    """兜底:任何 sk- 开头的长串在落盘前打码。

    正常路径不会把密钥写进日志,但日志经常被贴进工单/群里,
    这一层挡的是"某天有人顺手 log 了整个 headers"。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if "sk-" in msg:
            import re
            record.msg = re.sub(r"sk-[A-Za-z0-9\-_]{8,}",
                                lambda m: f"{m.group()[:6]}…{m.group()[-4:]}", msg)
            record.args = ()
        return True


def setup() -> None:
    level = os.environ.get("MATHMATE_LOG_LEVEL", "INFO").upper()
    root = logging.getLogger()
    if any(getattr(h, "_mathmate", False) for h in root.handlers):
        return                                  # --reload 会重复导入,别叠加 handler

    fmt = logging.Formatter(FMT, DATEFMT)
    redact = _RedactKey()

    stream = getattr(sys.stderr, "buffer", None)
    con = logging.StreamHandler(
        io_wrap(stream) if stream else sys.stderr)
    con.setFormatter(fmt)
    con.addFilter(redact)
    con._mathmate = True                        # type: ignore[attr-defined]
    root.addHandler(con)

    path = os.environ.get("MATHMATE_LOG_FILE")
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.addFilter(redact)
        fh._mathmate = True                     # type: ignore[attr-defined]
        root.addHandler(fh)

    root.setLevel(level)
    # httpx 每个请求打一行 INFO,和我们自己的调用日志重复
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("mathmate").setLevel(level)


def io_wrap(buffer):
    import io
    return io.TextIOWrapper(buffer, encoding="utf-8", errors="replace", line_buffering=True)
