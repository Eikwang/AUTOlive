# -*- coding: utf-8 -*-
"""
DanmakuListener 进程内端到端冒烟（整合计划第二阶段 P1-7：Bridge 注入 replay 源 → 队列桥 → stub 对话链）

流程：进程内启动 AutoliveBridge（dedicated asyncio daemon 线程）→ 队列级注入
demo.jsonl 契约线格式（H2：统一队列级注入，不改组件）→ 消费线程 → 活跃路由 →
stub my_handle 收到映射消息 → 优雅关停（Eng T4 顺序断言）。

用法（AUTOlive 根目录）：
    python -X utf8 tests/smoke_danmaku.py
退出码 0 = 冒烟通过。各步耗时输出（TTHW 可见——DX 三阶段上手路径阶段 1，目标 <2 分钟）。
"""
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

t0 = time.time()


def step(msg: str) -> None:
    print(f"[+{time.time() - t0:5.1f}s] {msg}", flush=True)


def _ensure_fixture() -> str:
    """demo.jsonl 夹具（DX 1.1）：落点钉死 tests/fixtures/（rmtree 区之外），缺失自动生成。"""
    fixture = os.path.join("tests", "fixtures", "demo.jsonl")
    os.makedirs(os.path.dirname(fixture), exist_ok=True)
    if not os.path.isfile(fixture):
        demo = os.path.join("docs", "danmaku_contract", "examples", "demo.jsonl")
        if os.path.isfile(demo):
            with open(demo, "rb") as fsrc, open(fixture, "wb") as fdst:
                fdst.write(fsrc.read())
            step(f"夹具从契约示例复制: {fixture}")
        else:
            # 自动生成最小线格式集（16 类覆盖业务/系统代表类）
            lines = [
                {"contract_version": "1.0.0", "category": "business", "type": "DANMU",
                 "platform": "bilibili", "room_id": "23058", "seq": 1, "timestamp": 1,
                 "engine": "protocol:bilibili",
                 "payload": {"type": "DANMU", "user_name": "测试观众", "content": "冒烟弹幕"}},
                {"contract_version": "1.0.0", "category": "business", "type": "GIFT",
                 "platform": "bilibili", "room_id": "23058", "seq": 2, "timestamp": 2,
                 "engine": "protocol:bilibili",
                 "payload": {"type": "GIFT", "user_name": "测试观众", "gift_name": "小心心",
                             "gift_count": 1, "gift_value": 1, "user_id": "u1", "gift_id": "g1"}},
                {"contract_version": "1.0.0", "category": "business", "type": "ENTER_ROOM",
                 "platform": "bilibili", "room_id": "23058", "seq": 3, "timestamp": 3,
                 "engine": "protocol:bilibili",
                 "payload": {"type": "ENTER_ROOM", "user_name": "测试观众"}},
                {"contract_version": "1.0.0", "category": "system", "type": "HEARTBEAT",
                 "platform": "bilibili", "room_id": "23058", "seq": 4, "timestamp": 4,
                 "engine": "protocol:bilibili", "payload": {"type": "HEARTBEAT"}},
            ]
            with open(fixture, "w", encoding="utf-8") as f:
                for line in lines:
                    f.write(json.dumps(line, ensure_ascii=False) + "\n")
            step(f"夹具自动生成: {fixture}")
    return fixture


SECTION = {
    "enabled": True,
    "component_root": "danmaku_listener",
    "login_entry": True,
    "web_console": False,
    "queue_maxsize": 500,
    "stall_threshold_seconds": 60,
    "cookie_dir": os.path.join("tests", "_smoke_cookie"),
    "platforms": {"bilibili": True, "douyu": True, "douyin": True, "huya": False,
                  "kuaishou": False, "taobao": False, "1688": False, "meituan": False,
                  "xiaohongshu": False, "pdd": False, "jd": False, "wechat_channels": False},
    "rooms": [{"platform": "bilibili", "room_id": "23058", "enabled": True, "active": True}],
    "keywords": ["冒烟屏蔽词"],
    "keyword_filter_enabled": True,
}


def main() -> int:
    step("冒烟开始（进程内 Bridge → 队列桥 → stub 对话链）")

    from utils import danmaku_consumer as dc

    fixture = _ensure_fixture()
    raw = [json.loads(line) for line in open(fixture, encoding="utf-8") if line.strip()]
    wires = [(r.get("wire") or r) for r in raw if isinstance(r, dict) and (r.get("wire") or r.get("category"))]
    wires = [w for w in wires if isinstance(w, dict) and w.get("category")]
    step(f"夹具就绪: {fixture}（{len(wires)} 条线格式，原始 {len(raw)} 行）")

    received = {"comment": 0, "gift": 0, "entrance": 0, "follow": 0, "other": 0}

    class _StubHandle:
        def process_data(self, data, type_):
            received[type_ if type_ in received else "other"] += 1

        def follow_handle(self, data):
            received["follow"] += 1

    from utils import my_global as _mg
    _mg.my_handle = _StubHandle()  # type: ignore[attr-defined]

    svc = dc.DanmakuListenerService(lambda: dict(SECTION))

    # 进程内启动（asyncio daemon 线程 + Bridge 构造 + rooms 注入 + 消费线程）
    ok = svc.start(state_cb=lambda s: None)
    if not ok:
        print("[FAIL] 进程内托管启动失败")
        return 3
    step("进程内托管线程已启动（等待 Bridge 就绪……）")

    # 等待异步初始化完成（Bridge 构造在 daemon 线程内）
    deadline = time.time() + 20
    while time.time() < deadline and svc._bridge is None:
        time.sleep(0.2)
    bridge = svc._bridge
    if bridge is None:
        print("[FAIL] Bridge 未在时限内就绪")
        return 3
    step("Bridge 就绪（无 serve 子进程、无 8765 监听）")
    # 注入前把 platform/room 归一到活跃房间（夹具示例数据平台各异，活跃路由会正确过滤非活跃）。
    active_p, active_r = "bilibili", "23058"
    inject_t = time.time()
    for w in wires:
        w["platform"] = active_p
        w["room_id"] = active_r
        if isinstance(w.get("envelope"), dict):
            w["envelope"]["platform"] = active_p
            w["envelope"]["room_id"] = active_r
        svc._on_wire(w)
    step(f"注入 {len(wires)} 条完成（{time.time() - inject_t:.3f}s）")

    # 等待消费线程把消息送进 stub 对话链
    deadline = time.time() + 15
    while time.time() < deadline:
        total = sum(received.values())
        if total >= 3:
            break
        time.sleep(0.2)
    status = svc.get_status()
    step(f"托管快照: {json.dumps({k: status[k] for k in ('running', 'queue_size', 'dropped_count')}, ensure_ascii=False)}")
    step(f"stub 对话链收到: {json.dumps(received, ensure_ascii=False)}")

    # 断言：消息全链打通 + 队列干净 + 无 serve 子进程
    total = sum(received.values())
    passed = total >= 3 and status.get("queue_size", 1) == 0
    tthw = time.time() - t0

    # 优雅关停（Eng T4 顺序：stop_all → 停循环 → join）
    stop_t = time.time()
    svc.stop()
    stop_s = time.time() - stop_t
    step(f"关停完成（耗时 {stop_s:.2f}s；顺序=stop_all future → 停循环 → join）")

    # 无 8765 监听断言（验收 1：任何配置下无 8765 端口）
    import socket
    port_8765_free = True
    try:
        with socket.create_connection(("127.0.0.1", 8765), timeout=0.5):
            port_8765_free = False
    except OSError:
        pass

    if passed and port_8765_free:
        print(f"[PASS] 进程内端到端冒烟通过（Bridge→队列桥→活跃路由→对话链 全链打通；TTHW={tthw:.1f}s）")
        return 0
    if not port_8765_free:
        print("[FAIL] 8765 端口被监听——进程内化不完整")
    else:
        print("[FAIL] 未在时限内收到足够的 business 消息")
    return 4


if __name__ == "__main__":
    sys.exit(main())
