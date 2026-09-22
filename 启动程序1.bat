@echo off
rem AI-Vtuber 旧版单体界面入口（webui-bak.py，nicegui 1.x 时代产物）
rem ============================================================
rem 警告：此入口在 runtime312 上待适配（nicegui 3.x 不兼容，实测必崩）
rem 日常启动请用 启动程序.bat ；勿在直播中依赖本脚本
rem ============================================================
rem 自动化/无人值守场景可删除末尾 cmd /k（窗口保活仅为双击场景设计）
rem 本文件为 ANSI/GBK 编码（与中文 Windows cmd 原生码页一致），请勿改存为 UTF-8
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
"%RT%\python.exe" webui-bak.py
cmd /k
