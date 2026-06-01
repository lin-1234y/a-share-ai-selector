@echo off
chcp 65001 >nul
set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

echo A股智能选股 - 导出个股行情
echo.
set /p SYMBOL=请输入股票代码，例如 000792：
if "%SYMBOL%"=="" (
  echo 没有输入股票代码。
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo 没找到 Python 环境，请先双击 启动A股智能选股.bat。
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m stock_selector export-stock-quotes --symbol "%SYMBOL%"
if errorlevel 1 (
  echo 导出失败。
  pause
  exit /b 1
)

echo.
echo 已导出，正在打开 exports 文件夹。
start "" "%PROJECT_DIR%exports"
pause
