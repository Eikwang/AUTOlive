# -*- coding: UTF-8 -*-
"""后台任务执行器：单守护线程 + 有界队列（2026-10-28 优化批次，模块 2）。

用途：把消息处理热路径中的非关键 I/O 从主链路剥离——
  - danmu 写库（SQLite，虽为连接池毫秒级，仍占热路径）
  - webui 聊天记录回调（HTTP POST，网络抖动可达 30s 超时）
语义：fire-and-forget。队列满时降级同步执行（功能不静默丢失），
异常仅记日志不上抛（与原调用方 try/except 吞错行为一致）。
"""
import queue
import threading
import time
import traceback

from .base import logger

_MAX_PENDING = 1000

_queue = queue.Queue(maxsize=_MAX_PENDING)
_started = False
_start_lock = threading.Lock()


def _worker():
    while True:
        func, args, kwargs = _queue.get()
        try:
            func(*args, **kwargs)
        except Exception:
            logger.error(f"[background] 后台任务执行失败: {traceback.format_exc()}")
        finally:
            _queue.task_done()


def _ensure_started():
    global _started
    if _started:
        return
    with _start_lock:
        if _started:
            return
        threading.Thread(target=_worker, name="background-tasks", daemon=True).start()
        _started = True


def submit(func, *args, **kwargs):
    """投递任务到后台线程；队列满时降级同步执行。"""
    _ensure_started()
    try:
        _queue.put_nowait((func, args, kwargs))
    except queue.Full:
        logger.warning("[background] 后台队列已满，降级同步执行")
        try:
            func(*args, **kwargs)
        except Exception:
            logger.error(f"[background] 同步兜底执行失败: {traceback.format_exc()}")


def wait_all(timeout: float = 5.0) -> bool:
    """阻塞等待已入队任务全部完成（测试排空/优雅关停用）。

    返回 True=已排空；False=超时仍有未完成任务。
    """
    _ensure_started()
    deadline = time.monotonic() + timeout
    while _queue.unfinished_tasks:
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
    return True
