@echo off
chcp 65001 >nul
cd /d "%~dp0"

title A股智能选股
echo.
echo ==========================================
echo  A股智能选股
echo ==========================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo 未找到 .venv 虚拟环境，正在创建并安装依赖...
  python -m venv .venv
  if errorlevel 1 goto error
  call ".venv\Scripts\activate.bat"
  python -m pip install -r requirements.txt
  if errorlevel 1 goto error
  python -m pip install -e .
  if errorlevel 1 goto error
)

echo 电脑访问:
echo   http://127.0.0.1:8766/
echo.
echo 手机访问:
for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr /c:"IPv4"') do (
  for /f "tokens=* delims= " %%B in ("%%A") do echo   http://%%B:8766/
)
echo.
echo 手机和电脑需要连接同一个 Wi-Fi。
echo 如果 Windows 防火墙弹窗，请允许 Python 访问专用网络。
echo.
echo 正在启动服务，关闭本窗口会停止系统。
echo.

".venv\Scripts\python.exe" -m stock_selector serve --host 0.0.0.0 --port 8766
goto end

:error
echo.
echo 启动失败。请把本窗口里的错误信息发给 Codex。
pause

:end
