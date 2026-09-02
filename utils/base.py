# -*- coding: UTF-8 -*-
"""
基础模块

提供共享的 logger，避免循环导入
"""

import logging

# 创建 logger
logger = logging.getLogger("luna_ai")

# 设置默认日志级别
logger.setLevel(logging.DEBUG)

# 如果没有处理器，添加一个控制台处理器
if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
