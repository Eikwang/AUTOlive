# -*- coding: UTF-8 -*-
"""AUTOlive frozen 重执行入口（NiceGUI script mode 专用）。

NiceGUI script mode（ui.run 未传 root + 全局作用域 UI）在每次页面连接时
runpy.run_path(sys.argv[0]) 重执行入口脚本以重建全局 UI。frozen 下
sys.argv[0] 指向 AUTOlive.exe（二进制，runpy 读出 null bytes）——webui.py
bootstrap_frozen 会在 exe 启动时把 sys.argv[0] 重定向到本文件。

本文件刻意不包含 bootstrap_frozen()：重执行发生在服务已启动后，端口预检
会因端口被自己占用而误自杀；chdir/PATH/日志/junction 均已就绪。
重执行语义与源码模式 runpy(webui.py) 同构（AutoliveApp 实例化重建全局 UI，
app.run() 内部对已启动实例安全返回）。
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from frontend.main import AutoliveApp
from utils.my_log import logger


def main():
    try:
        app = AutoliveApp()
        app.run()
    except Exception as e:
        logger.error(f"应用程序启动失败: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
