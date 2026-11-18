@echo off
rem Build the standalone exe (PyInstaller one-folder). ASCII only.
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo venv missing: run  uv venv --python 3.12  then  uv pip install playwright pycaw comtypes pyinstaller
  exit /b 1
)
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --name DouyinAutoMessage ^
  --collect-all playwright --hidden-import comtypes --hidden-import pycaw ^
  --exclude-module numpy --exclude-module pandas --exclude-module matplotlib ^
  app.py
if errorlevel 1 exit /b 1
copy /y "使用说明.txt" "dist\DouyinAutoMessage\使用说明.txt" >nul 2>&1
echo.
echo Build done: dist\DouyinAutoMessage\DouyinAutoMessage.exe
exit /b 0
