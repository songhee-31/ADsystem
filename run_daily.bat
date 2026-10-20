@echo off
rem 지금 바로 수집하고 싶을 때 더블클릭 (매일 자동 실행은 setup.bat 에서 등록)
chcp 65001 >nul
cd /d "%~dp0"
set "PY=python"
python --version >nul 2>nul || set "PY=py -3"
%PY% main.py run
pause
