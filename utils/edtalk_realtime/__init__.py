# -*- coding: utf-8 -*-
"""
EDTalk 实时推理集成模块

提供 AUTOlive 主程序（main.py 运行时）与 EDTalk realtime_serve 服务之间的
客户端对接，以及全局判定帮助函数。

契约：EDTalk/realtime_serve/EMBEDDING.md（v0.4）——
- /audio/push_full 是唯一有效整句音频通道（/audio/push 已死路径，勿对接）
- 16kHz 单声道 int16 PCM；过载语义未定义——嵌入方自行限流
- 错误体统一 {"problem", "cause", "fix"} 三段式
"""
from utils.edtalk_realtime.edtalk_client import EDTalkClient
from utils.edtalk_realtime.helpers import is_edtalk_active

__all__ = ["EDTalkClient", "is_edtalk_active"]
