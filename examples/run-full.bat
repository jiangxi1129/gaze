@echo off
REM Full mode: OCR + caption + audio subtitles + overlay + VPS push
REM Requires: .env with GLM_API_KEY + requirements-full.txt installed
REM           SSH host configured (GAZE_SSH_HOST in .env)

cd /d %~dp0..\gaze

python gaze_local.py ^
  --provider glm ^
  --interval 10 ^
  --ocr-interval 3 ^
  --auto-window ^
  --audio ^
  --audio-model tiny
