@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist .venv\Scripts\python.exe goto run

echo 首次运行，正在安装所需组件，请稍候……
python -m venv .venv
if errorlevel 1 goto nopython
.venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 .venv\Scripts\python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 goto failed

:run
.venv\Scripts\python run.py %*
pause
exit /b 0

:nopython
echo 未找到 Python。请先安装 Python 3.9 或更高版本（安装时勾选 "Add Python to PATH"）。
pause
exit /b 1

:failed
echo 组件安装失败，请检查网络后重新运行。
rmdir /s /q .venv
pause
exit /b 1
