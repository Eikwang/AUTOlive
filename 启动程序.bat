@echo off
rem AUTOlive 中枢 UI（nicegui，端口 8086）—— runtime312 统一环境版
rem 自动化/无人值守场景可删除末尾 cmd /k（窗口保活仅为双击场景设计）
rem 本文件为 ANSI/GBK 编码（与中文 Windows cmd 原生码页一致），请勿改存为 UTF-8
rem T6: 解释器副本优先候选链——仓库副本 edtalk/runtime312 优先, 外部树兜底, RT 环境变量保留最高覆盖
if not defined RT if exist "%~dp0runtime312\python.exe" set "RT=%~dp0runtime312"
rem 2026-11-06 迁移：环境唯一正式位置=仓库根 runtime312（RT 环境变量可覆盖）
if not defined RT set "RT=%~dp0runtime312"
if not exist "%RT%\python.exe" (
  echo [预检失败] 未找到解释器: %RT%\python.exe
  echo 请检查项目根 runtime312 是否存在（2026-11-06 已迁入仓库根；异常请跑 python Scripts/migrate_runtime_check.py --dry-run）
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
