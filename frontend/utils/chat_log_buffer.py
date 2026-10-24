# -*- coding: UTF-8 -*-
"""
聊天记录共享缓冲（webui 进程内）

UI-16 二轮验收（任务 14）：聊天记录断链修复——core 进程经
webui_show_chat_log_callback POST /callback（payload={"type":"llm","data":{
type,username,user_face,content_type,content,timestamp}}），webui 侧 /callback
此前为 TODO 空实现（frontend/main.py:1023），数据被丢弃，聊天记录框恒空。

本模块提供进程内环形缓冲：/callback 写入，talk 页签 ui.timer 轮询渲染
（nicegui3-eventloop-sync-requests-reload-loop 学习：timer 回调只读内存，
无同步 IO）。
"""
from collections import deque
from typing import Dict, List

_BUFFER: deque = deque(maxlen=200)


def append_chat(entry: Dict) -> None:
    """追加一条聊天记录（/callback 处理器调用；缺字段容错）。"""
    if not isinstance(entry, dict):
        return
    _BUFFER.append({
        "type": str(entry.get("type") or ""),
        "chat_source": str(entry.get("chat_source") or ""),
        "username": str(entry.get("username") or ""),
        "content_type": str(entry.get("content_type") or ""),
        "content": str(entry.get("content") or ""),
        "timestamp": str(entry.get("timestamp") or ""),
    })


def snapshot() -> List[Dict]:
    """当前缓冲快照（talk 页签轮询渲染用）。"""
    return list(_BUFFER)


def clear() -> None:
    _BUFFER.clear()
