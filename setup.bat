@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY="
python --version >nul 2>nul && set "PY=python"
if not defined PY py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
  echo.
  echo  Python 이 설치돼 있지 않습니다.
  echo  열리는 페이지에서 Python 을 설치하세요. 설치 첫 화면에서 "Add python.exe to PATH" 를 꼭 체크하세요.
  echo  설치가 끝나면 이 파일^(setup.bat^)을 다시 실행하세요.
  start "" https://www.python.org/downloads/
  pause
  exit /b 1
)
%PY% setup_wizard.py
