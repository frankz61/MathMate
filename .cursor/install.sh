#!/usr/bin/env bash
# MathMate 云端环境安装脚本(幂等)。
# 在 /workspace(仓库根)下运行:建虚拟环境 → 装后端依赖 → 生成演示数据。
set -euo pipefail

cd "$(dirname "$0")/.."

# Cursor 默认镜像的 Python 3.12 需要单独的 venv 包才能建虚拟环境。
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3-venv
fi

# 幂等:已存在可用的 venv 则复用。
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r apps/api/requirements.txt

# 生成演示数据(40 人班级 + 宠物 + 12 道真题);已有数据自动跳过。
( cd apps/api && ../../.venv/bin/python -m app.seed )
