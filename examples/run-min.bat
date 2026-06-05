@echo off
REM Minimal run: screenshot + OCR + local jsonl log (no VPS push)
REM Uses mock provider (no API key needed) -- verifies the loop works.

cd /d %~dp0..\gaze

python gaze_local.py ^
  --provider mock ^
  --no-push ^
  --no-overlay ^
  --interval 30 ^
  --auto-window

REM Output: %USERPROFILE%\.gaze\logs\*.jsonl
REM Press Ctrl+C to stop.
