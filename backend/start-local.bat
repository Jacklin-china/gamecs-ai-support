@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 未找到 Python 虚拟环境。请先按照 README.md 安装依赖。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scripts\start_local.py
if errorlevel 1 echo 启动未完成，请检查上面的提示。
pause
