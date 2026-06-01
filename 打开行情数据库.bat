@echo off
chcp 65001 >nul
set "PROJECT_DIR=%~dp0"
set "DB_PATH=%PROJECT_DIR%data\stock.db"
set "DB_BROWSER_EXE="

if exist "%ProgramFiles%\DB Browser for SQLite\DB Browser for SQLite.exe" (
  set "DB_BROWSER_EXE=%ProgramFiles%\DB Browser for SQLite\DB Browser for SQLite.exe"
)

if exist "%ProgramFiles(x86)%\DB Browser for SQLite\DB Browser for SQLite.exe" (
  set "DB_BROWSER_EXE=%ProgramFiles(x86)%\DB Browser for SQLite\DB Browser for SQLite.exe"
)

if not exist "%DB_PATH%" (
  echo 没找到行情数据库：
  echo %DB_PATH%
  pause
  exit /b 1
)

if "%DB_BROWSER_EXE%"=="" (
  echo 没找到 DB Browser for SQLite。
  echo 请先完成安装，安装包位置：
  echo %PROJECT_DIR%tools\DB.Browser.for.SQLite-v3.13.1-win64.msi
  pause
  exit /b 1
)

echo 正在打开行情数据库...
echo %DB_PATH%
start "" "%DB_BROWSER_EXE%" "%DB_PATH%"
