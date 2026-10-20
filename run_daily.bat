@echo off
rem 매일 실행: API 수집 -> 변경이력 엑셀 가져오기 -> output\index.html 생성
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python main.py run
