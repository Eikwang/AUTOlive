@echo off

set FFMPEG_PATH=D:\AI\EDTalk\runtime312\ffmpeg\bin
set PATH=%FFMPEG_PATH%;%PATH%
set CONDA_PATH=.\Miniconda3

CALL %CONDA_PATH%\scripts\activate.bat %CONDA_PATH%

SET KMP_DUPLICATE_LIB_OK=TRUE

python webui.py

cmd /k