@echo off
REM Snapshot-only: take one capture every 60s, no caption/OCR API calls
REM Useful for "manual snapshot on demand" workflow.

cd /d %~dp0..\gaze

python gaze_local.py ^
  --provider mock ^
  --no-push ^
  --no-overlay ^
  --no-ocr ^
  --interval 60 ^
  --auto-window
