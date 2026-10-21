# -*- coding: utf-8 -*-
"""
动作编排实测驱动（真实 EDTalk 服务 + 真实切段，/autoplan 20261021 批次验收）

前置：EDTalk realtime_serve 已运行（GET /status 可用），OBS 捕获 UnityCapture 虚拟摄像头。
驱动方式：构造真实 ActionChoreographer（真实 EDTalkClient HTTP 切段），按场景触发
on_gift/on_keyword/on_song——与 handler 订阅点同构入口；none 步以 duration 完成真实链路。

用法（仓库根，用 runtime312 解释器）：
    <runtime312>/python.exe Scripts/live_test_choreography.py
    （runtime312 默认 D:\AI\EDTalk\runtime312，见 docs/开发环境搭建.md）
"""
import io
import json
import sys
import threading
import time

# Windows GBK 控制台兜底（✅/❌ 与中文标签）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from utils.edtalk_realtime.edtalk_client import EDTalkClient
from utils.orchestration.choreographer import ActionChoreographer

# ---------- 配置（真实服务参数取自 config.json，规则用 immediate 保证可见性） ----------

APP_CFG = json.load(open("config.json", encoding="utf-8"))
ERT = APP_CFG.get("edtalk_realtime", {})

CLIENT = EDTalkClient(ERT)


class DottedConfig:
    def __init__(self, cfg):
        self._cfg = cfg

    def get(self, *keys):
        cur = self._cfg
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return None
            cur = cur[k]
        return cur


def rule(rule_id, trigger_type, match, enter, main, exit_seg):
    return {
        "rule_id": rule_id, "trigger_type": trigger_type, "match": match, "min_price": 0,
        "sequence": {
            "enter": {"segment": enter, "switch_at": "immediate", "duration_s": 3},
            "main": {"segment": main, "switch_at": "immediate", "duration_s": 5},
            "exit": {"segment": exit_seg, "switch_at": "immediate", "duration_s": 3},
        },
    }


CFG = DottedConfig({"choreography": {
    "enable": True,
    "default_mode": "rotate",
    "default_segments": ["44", "55"],
    "min_retrigger_s": 2,
    "min_sequence_hold_s": 5,
    "step_timeout_s": 60,
    "rules": [
        rule("girl_A", "gift", "小心心", "55", "44", "55"),
        rule("girl_B", "gift", "玫瑰", "44", "55", "44"),
        rule("kw_dance", "keyword", "跳舞", "55", "44", "55"),
        rule("song_rule", "song", "", "44", "55", "44"),
    ],
}})

# ---------- 段监视（/status 轮询，记录切换轨迹） ----------

transitions = []          # [(t, from, to)]
monitor_stop = threading.Event()
T0 = time.monotonic()


def monitor():
    last = None
    while not monitor_stop.is_set():
        st = CLIENT.get_status() or {}
        cur = st.get("current_segment")
        if cur is not None and cur != last:
            transitions.append((round(time.monotonic() - T0, 1), last, cur))
            print(f"  [{time.monotonic()-T0:6.1f}s] 段切换: {last} → {cur}")
            last = cur
        time.sleep(0.4)


def wait_transitions(pred, timeout, label):
    """等待轨迹满足 pred，返回命中时刻（超时 None）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for (t, f, to) in transitions:
            if pred(t, f, to):
                return t
        time.sleep(0.3)
    print(f"  !! 超时未等到：{label}（已观测 {len(transitions)} 次切换）")
    return None


def gift(name, price=1):
    return {"username": "实测观众", "gift_name": name, "gift_num": 1, "num": 1,
            "total_price": price, "unit_price": price}


results = []


def check(label, ok, detail=""):
    results.append((label, ok))
    print(f"  {'✅' if ok else '❌'} {label}" + (f"——{detail}" if detail else ""))


def settle(seconds):
    """等序列走完+恢复稳定（按段长轮询由 monitor 记录）。"""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        time.sleep(0.3)


def transitions_after(t0):
    return [x for x in transitions if x[0] >= t0]


def main():
    global T0
    threading.Thread(target=monitor, daemon=True).start()
    print("=== 动作编排实测（真实 EDTalk 切段）===")
    print(f"规则：A(小心心):55→44→55 | B(玫瑰):44→55→44 | kw(跳舞):55→44→55 | song:44→55→44")
    print(f"hold=5s 节流=2s 步长 3/5/3s（immediate 硬切）\n")

    # ---------- S1 条件1：单触发全序列 ----------
    print("S1 条件1 全序列：触发 A（预期 44→55→44→55→恢复44）")
    t0 = time.monotonic() - T0
    ch.on_gift(gift("小心心"))
    t_exit = wait_transitions(lambda t, f, to: to == "55" and t > t0 + 8, 20, "A exit(55)")
    t_restore = wait_transitions(lambda t, f, to: to == "44" and t_exit and t > t_exit, 15, "恢复(44)")
    check("S1 enter/main/exit 序列", t_exit is not None)
    check("S1 条件1 回默认轮播", t_restore is not None)
    st = ch.engine.get_state()
    check("S1 计数保留=1（切回默认不清零 D-4）", st["counters"].get("girl_A", 0) == 1)
    settle(2)

    # ---------- S2 条件2：main 中同键再触发 → 忽略不计数 ----------
    print("\nS2 条件2 main 中同键再触发（预期序列时长不变：main 5s 后照常 exit，计数仍=2？否=仍 1+重入=2？）")
    print("  （预期：忽略不计数 WP-6 → exit 按原时刻出现）")
    transitions.clear()
    t0 = time.monotonic() - T0
    ch.on_gift(gift("小心心"))            # 第 2 次触发（计数→2，新序列）
    time.sleep(4)                          # 进入 main（3s enter 后）
    ch.on_gift(gift("小心心"))            # main 中同键再触发 → 忽略不计数
    t_enter = wait_transitions(lambda t, f, to: f == "44" and to == "55" and t > t0, 15, "enter")
    # main 期间（enter 后 3s 起 5s）：exit 应在 enter 后 8s 出现（忽略则不变）
    t_exit = wait_transitions(lambda t, f, to: f == "44" and to == "55" and t_enter and t > t_enter + 6, 20, "exit")
    ok = t_exit is not None and t_enter is not None and 7.0 <= (t_exit - t_enter) <= 9.5
    check("S2 main 中再触发被忽略（exit 在 enter+8s±1.5s）", ok,
          f"间隔={round(t_exit - t_enter, 1) if t_exit and t_enter else '?'}s")
    st = ch.engine.get_state()
    check("S2 计数=2（再触发未 +1，WP-6）", st["counters"].get("girl_A", 0) == 2)
    wait_transitions(lambda t, f, to: to == "44", 12, "恢复")
    settle(2)

    # ---------- S3 hold 窗口：他键触发被忽略 ----------
    print("\nS3 hold 窗口（5s 内他键忽略）：触发 A 后 2s 触发 B（预期 B 被忽略，A 走完）")
    transitions.clear()
    t0 = time.monotonic() - T0
    ch.on_gift(gift("小心心"))
    time.sleep(2)
    ch.on_gift(gift("玫瑰"))              # hold 内 → 忽略
    # A 序列：55→44→55→恢复；若 B 未忽略会拼 [A exit 55, B enter 44, B main 55, B exit 44]
    t_exit = wait_transitions(lambda t, f, to: f == "44" and to == "55" and t > t0 + 6, 20, "A exit")
    after_exit = [x for x in transitions_after(t_exit)] if t_exit else []
    # A exit(55) 之后应直接恢复 44（rotate），且不再出现 55 的 B main
    b_main = [x for x in after_exit if x[2] == "55" and x[0] > (t_exit or 0)]
    check("S3 hold 内他键被忽略（A exit 后无 B main）", t_exit is not None and not b_main,
          f"exit 后轨迹={[(x[2]) for x in after_exit]}")
    wait_transitions(lambda t, f, to: to == "44", 12, "恢复")
    settle(2)

    # ---------- S4 条件3：出 hold 他键触发 → 拼接 ----------
    print("\nS4 条件3 拼接：触发 A，6s 后（main 中）触发 B（预期 44→55(A exit)→44(B enter)→55(B main)→44(B exit)）")
    transitions.clear()
    t0 = time.monotonic() - T0
    ch.on_gift(gift("小心心"))
    time.sleep(6)                          # 出 hold（5s），A main 中
    ch.on_gift(gift("玫瑰"))
    # 期望轨迹（自触发点）：44→55（A exit）→44（B enter）→55（B main）→44（B exit）→恢复
    seq_expect = ["55", "44", "55", "44"]
    deadline = time.monotonic() + 30
    got = []
    while time.monotonic() < deadline and len(got) < 4:
        time.sleep(0.3)
        got = [x[2] for x in transitions_after(t0) if x[2] in ("44", "55")]
        # 去重相邻
        dedup = [got[0]] if got else []
        for s in got[1:]:
            if s != dedup[-1]:
                dedup.append(s)
        got = dedup[1:] if dedup and dedup[0] == "44" else dedup  # 跳过触发时的 44（A main 中已是 44）
    check("S4 拼接序列 55→44→55→44", got[:4] == seq_expect, f"观测={got}")
    st = ch.engine.get_state()
    check("S4 A 键计数清零（D-4）", st["counters"].get("girl_A", 0) == 0)
    wait_transitions(lambda t, f, to: to == "44", 12, "恢复")
    settle(2)

    # ---------- S5 keyword 与 song 规则 ----------
    print("\nS5 keyword/song 规则：弹幕'跳舞'触发 → 序列；点歌命中触发 → 序列")
    transitions.clear()
    t0 = time.monotonic() - T0
    ch.on_keyword("大家好来跳舞吧")
    t1 = wait_transitions(lambda t, f, to: to == "55" and t > t0, 12, "kw enter(55)")
    check("S5 keyword 命中触发序列", t1 is not None)
    wait_transitions(lambda t, f, to: to == "44", 20, "kw 恢复")
    settle(1)
    transitions.clear()
    t0 = time.monotonic() - T0
    ch.on_song("任意歌名")
    t2 = wait_transitions(lambda t, f, to: to == "44" and t > t0 + 2, 12, "song enter(44)")
    check("S5 song 命中触发序列", t2 is not None)
    wait_transitions(lambda t, f, to: to == "55", 20, "song 恢复")

    # ---------- 汇总 ----------
    print("\n=== 实测汇总 ===")
    passed = sum(1 for _, ok in results if ok)
    for label, ok in results:
        print(f"  {'✅' if ok else '❌'} {label}")
    print(f"\n{passed}/{len(results)} 通过")
    st = ch.engine.get_state()
    print(f"终态：state={st['state']} counters={st['counters']} degrade_total={st['degrade_total']}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    ch = ActionChoreographer(CFG, CLIENT, audio_enqueue=lambda m: None,
                             thanks_generate=lambda *a, **k: None)
    print("编排器已构造（enable=True，真实 EDTalk 客户端）")
    # 等就绪探测完成
    for _ in range(20):
        if ch.engine.get_state()["ready"]:
            break
        time.sleep(0.5)
    print("就绪:", ch.engine.get_state()["ready"])
    sys.exit(main())
