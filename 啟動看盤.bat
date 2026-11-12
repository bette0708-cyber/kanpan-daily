@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install -q pandas numpy requests >nul 2>&1
python 看盤.py %*
pause
