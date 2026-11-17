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
# 变更版本号：append/clear 单调递增。渲染层不能用 len(_BUFFER) 做变更检测——
# deque 满载后 len 恒为 maxlen（200），新消息再入队 len 不变，轮询比对 len
# 永远相等导致渲染冻结（多平台监听消息量大，缓冲很快打满，之后新弹幕/回复
# 全部不显示——多平台只显示先监听平台消息的直接根因）
_VERSION = 0


def append_chat(entry: Dict) -> None:
    """追加一条聊天记录（/callback 处理器调用；缺字段容错）。"""
    global _VERSION
    if not isinstance(entry, dict):
        return
    _VERSION += 1
    _BUFFER.append({
        "type": str(entry.get("type") or ""),
        "chat_source": str(entry.get("chat_source") or ""),
        "username": str(entry.get("username") or ""),
        "platform": str(entry.get("platform") or ""),
        "content_type": str(entry.get("content_type") or ""),
        "content": str(entry.get("content") or ""),
        "timestamp": str(entry.get("timestamp") or ""),
    })


def version() -> int:
    """当前缓冲版本号（变更检测键，talk 页签轮询比对）。"""
    return _VERSION


def snapshot() -> List[Dict]:
    """当前缓冲快照（talk 页签轮询渲染用）。"""
    return list(_BUFFER)


def clear() -> None:
    global _VERSION
    _VERSION += 1
    _BUFFER.clear()
