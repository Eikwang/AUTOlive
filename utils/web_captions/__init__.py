# -*- coding: UTF-8 -*-
"""
内置 web 字幕打印机模块

单例注册模式（照 utils/edtalk_realtime 的 register_client 先例）：
- Audio（主系统进程）初始化时 create + register CaptionsManager；
- utils/web_server.py 经 get_captions_manager() 惰性获取（sio 挂载与
  FastAPI startup 捕获 loop），未注册时返回 None（连接方自行降级）。

sio/loop 桥接：web_server 挂载时经 register_sio/register_loop 登记；
CaptionsManager 创建时若已登记则自挂（两侧初始化顺序无论先后均成立）。
"""

from utils.my_log import logger

_captions_manager = None
_sio = None
_loop = None


def register_captions_manager(manager):
    """注册全局单例（audio_core 初始化时调用一次；若 sio/loop 已登记则自挂）"""
    global _captions_manager
    _captions_manager = manager
    if _sio is not None:
        manager.attach(_sio)
    if _loop is not None:
        manager.set_loop(_loop)
    logger.info("CaptionsManager 已注册为全局单例")


def get_captions_manager():
    """获取全局单例；未注册（未运行系统/未初始化）返回 None"""
    return _captions_manager


def register_sio(sio):
    """web_server 挂载 socket.io 时登记；已存在的 manager 立即 attach"""
    global _sio
    _sio = sio
    if _captions_manager is not None:
        _captions_manager.attach(sio)


def register_loop(loop):
    """FastAPI startup 捕获 event loop 时登记；已存在的 manager 立即 set_loop"""
    global _loop
    _loop = loop
    if _captions_manager is not None:
        _captions_manager.set_loop(loop)
