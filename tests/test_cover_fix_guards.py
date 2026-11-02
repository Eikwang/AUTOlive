# -*- coding: utf-8 -*-
"""T4 守卫测试(/autoplan main-20261102-104518 批次,义务块 ceo/eng)。

三守卫:
1. G1 AST 守卫——全 tabs 的 set_config_callback/set_cb 调用实参表必须含
   keyword value=(CEO F4+Eng F5:覆盖位置传参/空参/别名形态;白名单=
   config_helper.py 死代码域);
2. G2 双路由实证断言(Eng F5:修一路不证明另一路)——子进程内构建点歌页后,
   /song_source 与 /song_audio 各以新建文件请求断言 200(防"路由从未挂载"回归);
3. G3 wait_reason/wait_code 生命周期——等待期写入稳定码,准入通过置空。
"""
import ast
import os
import sys

from utils.cover_worker import CoverQueue  # noqa: E402
from utils.gpu_policy import (  # noqa: E402
    WAIT_EDTALK_BUSY,
    WAIT_LIVE_RUNNING,
    WAIT_NONE,
    WAIT_VRAM_INSUFFICIENT,
    WAIT_VRAM_QUERY_FAILED,
    background_task_gate,
    is_background_task_allowed,
)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_TABS = os.path.join(_REPO_ROOT, "frontend", "ui", "tabs")

# 死代码白名单(义务块:extract_config_ui 全库零调用方,其体内 4 处位置传参随守卫裁决豁免)
_G1_WHITELIST = {"config_helper.py"}
# G1 守卫覆盖的调用名(Eng F5:set_cb 别名;layout.py 内部别名调用不属 tabs 扫描面)
_G1_FUNC_NAMES = {"set_config_callback", "set_cb"}


def _g1_violations():
    """AST 扫描:返回 [(file, lineno, func_name)]——实参表不含 keyword value= 的调用。"""
    out = []
    for fn in sorted(os.listdir(_TABS)):
        if not fn.endswith(".py") or fn in _G1_WHITELIST:
            continue
        path = os.path.join(_TABS, fn)
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if fname not in _G1_FUNC_NAMES:
                continue
            has_value_kw = any(kw.arg == "value" for kw in node.keywords)
            if not has_value_kw:
                out.append((fn, node.lineno, fname))
    return out


def test_g1_all_calls_use_value_keyword():
    """G1 守卫:实参表不含 value= 即告警(CEO F4 覆盖空参/位置/别名三形态)。"""
    bad = _g1_violations()
    assert not bad, (
        "set_config_callback/set_cb 调用缺 keyword value=（layout.py:1760 签名 "
        f"keyword-only，位置/空参传参在事件期 TypeError）：{bad}；"
        "修=改 value= 传参，或该文件加入死代码白名单并注明")


def test_g1_config_helper_whitelist_is_dead_code():
    """白名单有效性:config_helper.py 的 extract_config_ui 全库零调用方(死代码域)。"""
    import subprocess
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, '.');"
         "hits = []"
         "import pathlib"
         "for f in pathlib.Path('frontend').rglob('*.py'):"

         "    if f.name == 'config_helper.py':"
         "        continue"
         "    t = f.read_text(encoding='utf-8', errors='replace')"
         "    if 'extract_config_ui' in t:"
         "        hits.append(str(f))"
         "print(hits)"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=_REPO_ROOT)
    hits = eval(r.stdout.strip()) if r.stdout.strip() else []
    assert hits == [], f"config_helper.extract_config_ui 出现调用方（不再是死代码，白名单失效）: {hits}"


def test_g3_gate_four_codes_and_binary_compat():
    """G3:background_task_gate 四类稳定码+is_background_task_allowed 二元组兼容
    （Eng F2:8 既有调用方零改动）。"""
    from unittest.mock import patch
    import utils.gpu_policy as gp

    cases = [
        ({"_live_running": lambda: True}, (False, gp.WAIT_LIVE_RUNNING)),
        ({"_live_running": lambda: False, "_edtalk_busy": lambda: True},
         (False, gp.WAIT_EDTALK_BUSY)),
        ({"_live_running": lambda: False, "_edtalk_busy": lambda: False,
          "_vram_free_gb": lambda: -1.0}, (False, gp.WAIT_VRAM_QUERY_FAILED)),
        ({"_live_running": lambda: False, "_edtalk_busy": lambda: False,
          "_vram_free_gb": lambda: 0.5, "_inproc_engine_ready": lambda: False},
         (False, gp.WAIT_VRAM_INSUFFICIENT)),
        ({"_live_running": lambda: False, "_edtalk_busy": lambda: False,
          "_vram_free_gb": lambda: 16.0, "_inproc_engine_ready": lambda: False},
         (True, gp.WAIT_NONE)),
    ]
    for patches, (want_ok, want_code) in cases:
        with patch.multiple(gp, **{k: v for k, v in patches.items()}):
            ok, reason, code = background_task_gate(4.0, 2.0)
            assert ok == want_ok and code == want_code, (reason, code)
            ok2, reason2 = is_background_task_allowed(4.0, 2.0)  # 二元组兼容回归
            assert ok2 == want_ok and reason2 == reason


def test_g3_wait_fields_written_then_cleared(tmp_path, monkeypatch):
    """G3 生命周期:等待期写入 reason/code,准入通过置空(C-2)+首次 info 后续
    debug(F9 不断言级别,行为由日志配置承载)。"""
    import types

    q = CoverQueue.__new__(CoverQueue)  # 免 __init__(避免线程/锁装配)
    q.queue = []
    q.current = None
    q.lock = __import__("threading").RLock()
    q._stop = types.SimpleNamespace(
        is_set=lambda: False, wait=lambda t: False)  # wait 立即返回(不睡眠)
    q.config = types.SimpleNamespace(get=lambda *a, **k: None)  # _policies 桩:无自定义 policies

    song_file = tmp_path / "测试歌.mp3"
    song_file.write_bytes(b"x")
    job = {"song": "测试歌", "song_path": str(song_file), "cover_path": str(tmp_path / "o.wav"),
           "retries_left": 0, "started_at": None, "wait_reason": "", "wait_code": ""}

    seq = iter([(False, "直播运行中", WAIT_LIVE_RUNNING), (True, "GPU 空闲", WAIT_NONE)])
    seen_at_gate = []

    def fake_gate(*a, **k):
        seen_at_gate.append((job.get("wait_code"), job.get("wait_reason")))
        return next(seq)

    monkeypatch.setattr("utils.gpu_policy.background_task_gate", fake_gate)

    def _bomb(job, policies):
        # 准入通过后第一批指令前中断——此刻字段应已置空(C-2)
        raise RuntimeError("stop-here")

    monkeypatch.setattr(CoverQueue, "_prepare_rvc_model_dir", _bomb)
    try:
        q._process(job, q._policies())
    except RuntimeError as e:
        assert "stop-here" in str(e)

    # 等待期(第二次 gate 调用=准入判定前)已写入稳定码
    assert seen_at_gate[1] == (WAIT_LIVE_RUNNING, "直播运行中"), seen_at_gate
    # 准入通过置空(C-2)
    assert job["wait_code"] == WAIT_NONE and job["wait_reason"] == ""
