@echo off
rem 백엔드 서버 실행 (처음 한 번은 가상환경과 패키지를 설치합니다)
cd /d %~dp0
if not exist .venv (
    python -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python -m uvicorn app.main:app --port 8000
