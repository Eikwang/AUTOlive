@echo off
rem AI-Vtuber 中枢 UI（nicegui，端口 8086）—— runtime312 统一环境版
rem 自动化/无人值守场景可删除末尾 cmd /k（窗口保活仅为双击场景设计）
chcp 65001 >nul
if not defined RT set "RT=D:\AI\EDTalk\runtime312"
if not exist "%RT%\python.exe" (
  echo [预检失败] 未找到解释器: %RT%\python.exe
  echo 请检查 D:\AI\EDTalk\runtime312 是否存在（EDTalk 挪动/重装会导致此路径失效）
  pause
  exit /b 1
)
set FFMPEG_PATH=%RT%\ffmpeg\bin
set PATH=%RT%;%RT%\Scripts;%RT%\Library\bin;%FFMPEG_PATH%;%PATH%
SET KMP_DUPLICATE_LIB_OK=TRUE
SET HF_ENDPOINT=https://hf-mirror.com
cd /d %~dp0
echo 如启动报 ModuleNotFoundError，请按 specs/integration/runtime312启动脚本切换计划.md 第 2.3 节清单补装依赖
"%RT%\python.exe" webui.py
cmd /k
