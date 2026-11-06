@echo off
rem AUTOlive 中枢 UI 测试启动（nicegui，端口 8086）—— 同启动程序.bat
rem 本文件为 ANSI/GBK 编码 + CRLF 行尾（cmd 批处理硬要求），请勿改存为 UTF-8 或 LF
rem 2026-11-06 环境迁移：解释器唯一正式位置=仓库根 runtime312（RT 环境变量可覆盖）
if not defined RT set "RT=%~dp0runtime312"
if not exist "%RT%\python.exe" (
  echo [预检失败] 未找到解释器: %RT%\python.exe
  echo 请检查项目根 runtime312 是否存在；异常时运行: python Scripts\migrate_runtime_check.py --dry-run
  pause
  exit /b 1
)
set FFMPEG_PATH=%RT%\ffmpeg\bin
set PATH=%RT%;%RT%\Scripts;%RT%\Library\bin;%FFMPEG_PATH%;%PATH%
SET KMP_DUPLICATE_LIB_OK=TRUE
SET HF_ENDPOINT=https://hf-mirror.com
cd /d %~dp0
"%RT%\python.exe" webui.py
cmd /k