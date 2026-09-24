@echo off
rem ============================================================
rem AUTOlive 一键启动器（P1-5）
rem 按序拉起：AUTOlive webui → 托管服务（gpt-sovits 随系统自动拉起，
rem 见 main.py _init_modules / gpt_sovits.hosted）→ 就绪探活
rem 就绪判定复用托管服务的健康检查（GET /docs 端口探活）。
rem 用户动作：双击本文件 → 等 webui 打开 → 首屏出声测试（CEO-D11）
rem ============================================================
setlocal
rem 根目录解析（P1-2）：本 bat 位于 AUTOlive 根；config paths.autolive_home 可覆盖
set AUTOLIVE_HOME=%~dp0
cd /d "%AUTOLIVE_HOME%"

rem 运行时：主程序用 EDTalk 共享环境（runtime312）；GPT-SoVITS 用 runtime312-clone
set MAIN_PY=D:\AI\EDTalk\runtime312\python.exe

echo [1/3] 启动 AUTOlive webui（托管服务将随系统自动拉起）...
start "AUTOlive-webui" /MIN cmd /c ""%MAIN_PY%" webui.py > logs\webui_launcher.log 2>&1"

echo [2/3] 等待 webui 就绪（8086 端口探活）...
set /a TRIES=0
:wait_webui
timeout /t 3 /nobreak >nul
set /a TRIES+=1
curl -s -o nul -w "%%{http_code}" --max-time 3 http://127.0.0.1:8086/ 2>nul | findstr "200 301 302" >nul
if %errorlevel%==0 goto webui_ok
if %TRIES% geq 40 goto webui_fail
goto wait_webui
:webui_ok
echo [OK] webui 就绪（第 %TRIES% 次探测，约 %TRIES%*3 秒）

echo [3/3] 等待托管 GPT-SoVITS 就绪（9880 端口探活，模型加载约 15-40 秒）...
set /a TRIES=0
:wait_tts
timeout /t 3 /nobreak >nul
set /a TRIES+=1
curl -s -o nul -w "%%{http_code}" --max-time 3 http://127.0.0.1:9880/docs 2>nul | findstr "200" >nul
if %errorlevel%==0 goto tts_ok
if %TRIES% geq 30 goto tts_fail
goto wait_tts
:tts_ok
echo [OK] GPT-SoVITS 就绪——首屏出声测试可执行
echo.
echo 全部就绪。浏览器打开 http://127.0.0.1:8086 进入工作台。
pause
exit /b 0

:webui_fail
echo [FAIL] webui 120 秒未就绪——查看 logs\webui_launcher.log
pause
exit /b 1

:tts_fail
echo [WARN] GPT-SoVITS 90 秒未就绪——webui 已可用，查看工作台状态条胶囊日志
echo        常见原因见 docs/开发环境搭建.md 第 5 节
pause
exit /b 0
