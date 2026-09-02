# -*- coding: UTF-8 -*-
"""
Luna AI WebUI - 入口文件
调用 frontend 模块化结构
"""

import sys
import os
from pathlib import Path

# 确保项目根目录在 Python 路径中
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 导入并运行模块化应用
from frontend.main import AIVtuberApp
from utils.my_log import logger

def main():
    """主函数"""
    try:
        app = AIVtuberApp()
        app.run()
    except Exception as e:
        logger.error(f"应用程序启动失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    main()
