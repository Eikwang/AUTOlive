# -*- coding: utf-8 -*-
"""TST-2 E2E 驱动：六步训练全链路实跑（tests/sound → soundtest 档案）。

用法：runtime312 python tests/run_e2e_training.py
输出：实时步骤状态+日志尾；失败即停（管线语义），退出码=成败。
"""
import os
import sys
import time

# Windows 控制台默认 GBK——状态标记 ▶/✔ 会 UnicodeEncodeError，强制 UTF-8
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.makedirs("音频输出", exist_ok=True)

from utils.config import Config
from utils.autolive_paths import AutolivePaths
from utils.train_voice_pipeline import TrainVoicePipeline, PENDING, RUNNING, SUCCESS, FAILED, SKIPPED

EXP = "soundtest"

def main():
    cfg = Config("config.json")
    paths = AutolivePaths(cfg)
    p = TrainVoicePipeline(cfg, paths)
    p.prepare(
        dataset_dir=os.path.abspath("tests/sound"),
        exp_name=EXP,
        batch_size=4,
        already_dry=False,          # 带 BGM 素材 → 走 MDX 分离（T-1 实测）
        authorized=True,
        asr_lang="zh",
        epochs_s2=10, epochs_s1=20, text_low_lr_rate=0.4,
        save_every_s2=5, save_every_s1=10, if_dpo=True,
    )
    if p.asset_issues:
        print("[E2E] 资产预检未通过：")
        for rel, note, _ in p.asset_issues:
            print(f"  ✗ {note} → {rel}")
        sys.exit(2)
    # 断点续跑：RESUME_FROM=asr 时预置 mdx/slice 为成功（run_all 跳过 SUCCESS 步）
    resume_from = os.environ.get("RESUME_FROM", "")
    if resume_from:
        ids = ["mdx", "slice", "asr", "prepare", "s2", "s1", "export"]
        done = ids[:ids.index(resume_from)]
        p._states = {sid: SUCCESS for sid in done}
        print(f"[E2E] 断点续跑：跳过已完成步 {done}")
    print("[E2E] 资产预检通过，启动全链路（后台线程）…")
    p.run_all()

    last_line_count = 0
    last_states = {}
    idle_since = None
    while True:
        time.sleep(30)
        states = p.stage_states()
        if states != last_states:
            marks = {PENDING: "·", RUNNING: "▶", SUCCESS: "✔", FAILED: "✖", SKIPPED: "—"}
            print(f"[{time.strftime('%H:%M:%S')}] " + " | ".join(
                f"{marks.get(st, '?')}{sid}:{st}" for sid, st in states.items()), flush=True)
            last_states = states
        new_lines = p.log_lines[last_line_count:]
        for ln in new_lines[-12:]:
            if ln.strip():
                print(f"    {ln[:160]}", flush=True)
        last_line_count = len(p.log_lines)
        # 终态判定：无运行中步骤且（有失败 或 全部到终态）
        running = [s for s in states.values() if s == RUNNING]
        failed = [s for s in states.values() if s == FAILED]
        terminal = [s for s in states.values() if s in (SUCCESS, FAILED, SKIPPED)]
        if not running and (failed or len(terminal) >= len(states)):
            if not failed and not p.running_stage and all(
                    s in (SUCCESS, SKIPPED) for s in states.values()):
                print("[E2E] 全链路完成 ✔", flush=True)
                sys.exit(0)
            # running 空但未全终态：可能 worker 已死——等待 2 轮确认
            idle_since = idle_since or time.time()
            if time.time() - idle_since > 90:
                print(f"[E2E] 链路停滞终态判定：{states} failed={failed}", flush=True)
                sys.exit(1 if failed else 0)
        else:
            idle_since = None


if __name__ == "__main__":
    main()
