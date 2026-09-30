# -*- coding: utf-8 -*-
"""
DanmakuListener 整合端到端冒烟（整合计划测试基线：serve 连通冒烟）

流程：托管拉起 serve（replay 模式，demo.jsonl 循环）→ 消费者连接 →
消息经 system 快路径/business 队列进 worker → stub my_handle 收到映射消息 → 关停。

用法（AUTOlive 根目录）：
    python -X utf8 tests/smoke_danmaku.py
退出码 0 = 冒烟通过。各步耗时输出（TTHW 可见，义务块 DX 分阶段上手路径）。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

t0 = time.time()
HEARTBEAT_FRESH_S = 30  # 心跳新鲜度断言（最近 30s 内有心跳 = 连接活跃）


def step(msg: str) -> None:
    print(f"[+{time.time() - t0:5.1f}s] {msg}", flush=True)


SECTION = {
    "enabled": True,
    "ws_port": 18765,
    "ws_bind": "127.0.0.1",
    "require_token": True,
    "token_injection": "env",
    "token_file_path": "",
    "platforms": {"bilibili": True, "douyu": True, "douyin": True,
                  "kuaishou": False, "huya": False, "wechat_channels": False},
    "login_entry": True,
    "legacy_adapter_retired": False,
    "component_root": "danmaku_listener",
    "room_id": "23058",
}


def main() -> int:
    step("冒烟开始（serve replay → WS 消费者 → stub 对话链）")

    from utils import danmaku_consumer as dc

    received = {"comment": 0, "gift": 0, "entrance": 0, "other": 0}
    system_events = []

    class _StubHandle:
        def process_data(self, data, type_):
            received[type_ if type_ in received else "other"] += 1

    class _StubGlobal:
        my_handle = _StubHandle()

    # stub my_global.my_handle（不启动完整主程序）
    import types
    from utils import my_global as _mg
    _mg.my_handle = _StubGlobal().my_handle  # type: ignore[attr-defined]

    svc = dc.DanmakuListenerService(lambda: dict(SECTION))

    # serve --replay 的 fixtures：用组件副本内 demo（copy 时未入 fixtures/，此处指向契约 examples）
    demo = os.path.join("docs", "danmaku_contract", "examples", "demo.jsonl")
    if not os.path.isfile(demo):
        print(f"[FAIL] 缺少 fixtures: {demo}")
        return 2
    # 把 demo.jsonl 放进组件副本 fixtures/（serve --replay 期望位置）
    dst = os.path.join("danmaku_listener", "fixtures", "demo.jsonl")
    if not os.path.isfile(dst):
        with open(demo, "rb") as fsrc, open(dst, "wb") as fdst:
            fdst.write(fsrc.read())
    step(f"fixtures 就绪: {dst}")

    # 托管拉起（含 TCP 探活就绪判定）
    from utils import my_global as _mg2
    # 冒烟期 my_global 可能无 my_handle 属性——提前设置避免世代重置异常
    if not hasattr(_mg2, "my_handle"):
        _mg2.my_handle = _StubGlobal().my_handle  # type: ignore[attr-defined]
    ok = svc.start(state_cb=lambda s: None)
    if not ok:
        print("[FAIL] serve 托管拉起失败（查 logs/services/danmaku_listener.log）")
        return 3
    step("serve 托管拉起成功（TCP 探活通过）")

    # 消费者回调接线：登录回调 + 状态回调
    cons = svc._consumer
    cons.set_login_callback(lambda payload: system_events.append(("needs_login", payload)))
    cons.set_status_callback(lambda kind, data: system_events.append((kind, data)))

    # 等待 replay 消息流经消费者进 stub 对话链
    deadline = time.time() + 30
    while time.time() < deadline:
        total = received["comment"] + received["gift"] + received["other"]
        if total >= 3:
            break
        time.sleep(0.5)
    snap = cons.worker_snapshot()
    step(f"消费者快照: {json.dumps(snap, ensure_ascii=False)}")
    step(f"stub 对话链收到: {json.dumps(received, ensure_ascii=False)}；system 事件 {len(system_events)} 条")

    tthw = time.time() - t0
    total = received["comment"] + received["gift"] + received["entrance"] + received["other"]
    passed = total >= 3 and snap.get("last_heartbeat_age_s", 999) < HEARTBEAT_FRESH_S
    svc.stop()
    step(f"关停完成。TTHW（冒烟全程）= {tthw:.1f}s")
    if passed:
        print(f"[PASS] 端到端冒烟通过（消息 serve→WS→消费者→映射链 全链打通）")
        return 0
    print("[FAIL] 未在时限内收到足够的 business 消息")
    return 4


if __name__ == "__main__":
    sys.exit(main())
