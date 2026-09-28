#!/bin/sh
# macOS / Linux 启动脚本：首次运行自动创建虚拟环境并安装依赖
cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/python ]; then
  echo "首次运行，正在安装所需组件，请稍候……"
  python3 -m venv .venv || { echo "未找到 Python 3.9+，请先安装。"; exit 1; }
  if ! .venv/bin/pip install -r requirements.txt; then
    .venv/bin/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple || {
      echo "组件安装失败，请检查网络后重试。"; rm -rf .venv; exit 1; }
  fi
fi

exec .venv/bin/python run.py "$@"
