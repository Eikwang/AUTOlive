# -*- coding: utf-8 -*-
"""TST-2a E2E 驱动：RVC 六步训练全链路实跑（tests/sound → rvc_e2e 档案）。

用法：D:/AI/EDTalk/runtime312/python.exe tests/run_e2e_rvc_training.py
输出：实时步骤状态+日志尾；失败即停（管线语义），退出码=成败。
RESUME_FROM=preprocess|extract|train|index|export 预置前序步 SUCCESS（断点续跑）。
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.autolive_paths import AutolivePaths
from utils.config import Config
from utils.train_rvc_pipeline import (
    FAILED, PENDING, RUNNING, SKIPPED, SUCCESS, TrainRvcPipeline,
)

EXP = "rvc_e2e"


def main():
    cfg = Config("config.json")
    paths = AutolivePaths(cfg)
    p = TrainRvcPipeline(cfg, paths)
    p.prepare(
        dataset_dir=os.path.abspath("tests/sound"),
        exp_name=EXP,
        batch_size=8,
        already_dry=False,   # 带 BGM 素材 → 走 MDX 分离
        authorized=True,
    )
    if p.asset_issues:
        print("[E2E] 资产预检未通过：")
        for rel, note, _ in p.asset_issues:
            print(f"  ✗ {note} → {rel}")
        sys.exit(2)
    resume_from = os.environ.get("RESUME_FROM", "")
    if resume_from:
        ids = ["mdx", "preprocess", "extract", "train", "index", "export"]
        done = ids[:ids.index(resume_from)]
        p._states = {sid: SUCCESS for sid in done}
        print(f"[E2E] 断点续跑：跳过已完成步 {done}")
    print("[E2E] 预检通过，启动全链路（后台线程）…", flush=True)
    t0 = time.time()
    p.run_all()

    last_states, last_line_count, idle_since = {}, 0, None
    while True:
        time.sleep(20)
        states = p.stage_states()
        if states != last_states:
            marks = {PENDING: "·", RUNNING: "▶", SUCCESS: "✔", FAILED: "✖", SKIPPED: "—"}
            print(f"[{time.strftime('%H:%M:%S')}] " + " | ".join(
                f"{marks.get(st, '?')}{sid}:{st}" for sid, st in states.items()), flush=True)
            last_states = states
        for ln in p.log_lines[last_line_count:][-10:]:
            if ln.strip():
                print(f"    {ln[:160]}", flush=True)
        last_line_count = len(p.log_lines)
        running = [s for s in states.values() if s == RUNNING]
        failed = [s for s in states.values() if s == FAILED]
        terminal = [s for s in states.values() if s in (SUCCESS, FAILED, SKIPPED)]
        if not running and (failed or len(terminal) >= len(states)):
            if not failed and not p.running_stage and all(
                    s in (SUCCESS, SKIPPED) for s in states.values()):
                print(f"[E2E] 全链路完成 ✔ 总耗时 {(time.time()-t0)/60:.1f}min（TTHW 基线记录）", flush=True)
                sys.exit(0)
            idle_since = idle_since or time.time()
            if time.time() - idle_since > 120:
                print(f"[E2E] 链路停滞终态判定：{states} failed={failed}", flush=True)
                sys.exit(1 if failed else 0)
        else:
            idle_since = None


if __name__ == "__main__":
    main()
