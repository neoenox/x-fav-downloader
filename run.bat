@echo off
chcp 65001 >NUL
set PYTHONUTF8=1
cd /d "%~dp0"

where python >NUL 2>&1
if %errorlevel% neq 0 (
  echo [ERROR] Python not found. Please install Python 3.10+
  pause
  exit /b 1
)

if not exist ".venv" (
  echo [INFO] Creating venv...
  python -m venv .venv
)
call ".venv\Scripts\activate.bat"

echo [INFO] Installing packages...
python -m pip install -q --upgrade pip
pip install -q -r requirements.txt

if /i "%~1"=="login" (
  echo [INFO] Checking Chromium for login...
  python -m playwright install chromium
  if %errorlevel% neq 0 (
    echo [WARN] playwright install failed. Continuing...
  )
  python main.py --login
  goto done
)
if /i "%~1"=="dry" (
  python main.py --dry-run --count 5
  goto done
)
python main.py %*

:done
echo.
echo [DONE] Finished.
pause
