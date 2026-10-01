@echo off
rem Windows 작업 스케줄러가 매일 실행한다: 초안 생성 + 검토 시간 지난 초안 발행
cd /d %~dp0
set PYTHONUTF8=1
if not exist output\logs mkdir output\logs
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%d
echo ==== %DATE% %TIME% >> output\logs\%TODAY%.log
python main.py auto >> output\logs\%TODAY%.log 2>&1
