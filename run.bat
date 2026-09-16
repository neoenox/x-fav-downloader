@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d %~dp0

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Pythonが見つかりません。Python 3.10+ をインストールしてください。
  pause
  exit /b 1
)

if not exist .venv (
  echo [INFO] venv作成中...
  python -m venv .venv
)
call .venv\Scripts\activate.bat

echo [INFO] パッケージ更新中...
python -m pip install -q --upgrade pip
pip install -q -r requirements.txt

echo [INFO] Chromium確認中...
python -m playwright install chromium --with-deps 2>nul
if errorlevel 1 (
  python -m playwright install chromium
)

if "%1"=="login" (
  python main.py --login
) else if "%1"=="dry" (
  python main.py --dry-run --count 5
) else (
  python main.py %*
)

echo.
echo [DONE] 終了しました。
pause
