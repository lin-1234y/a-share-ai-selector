@echo off
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
  echo Stock database was not found:
  echo %DB_PATH%
  pause
  exit /b 1
)

if "%DB_BROWSER_EXE%"=="" (
  echo DB Browser for SQLite was not found.
  echo Please install it first.
  pause
  exit /b 1
)

echo Opening stock database:
echo %DB_PATH%
start "" "%DB_BROWSER_EXE%" "%DB_PATH%"
