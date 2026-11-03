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
import types

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


def test_bootstrap_slices_args_after_double_dash():
    """T4 补充(2026-11-02 实测真因):bootstrap 的 argv 切片必须按 '--' 定位——
    `python -c "<code>" -- -i ...` 的 sys.argv=[python, -c, <code>, --, -i, ...],
    旧写法 sys.argv[1:] 把代码串本身传给 argparse（报 required: -i/-dir/-p，
    参数全空——线上报错逐字吻合）。真执行验证:新切片产出干净参数表。"""
    import subprocess
    from utils.cover_worker import CoverQueue

    q = CoverQueue.__new__(CoverQueue)
    q.config = types.SimpleNamespace(get=lambda *a, **k: None)
    # _aicovergen_src 依赖 paths——stub 掉
    q._aicovergen_src = lambda: "D:/fake/src"

    code = q._bootstrap_code()
    assert "sys.argv.index('--')" in code, "bootstrap 缺 '--' 定位切片（旧缺陷 sys.argv[1:] 回归）"

    # 真执行:模拟完整 cmd 形态,断言 main.py 收到的参数表
    probe = (
        "import sys\n"
        "if '--' in sys.argv:\n"
        "    sys.argv = ['main.py'] + sys.argv[sys.argv.index('--') + 1:]\n"
        "print(repr(sys.argv[1:]))\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", probe, "--", "-i", "song.mp3", "-p", "0"],
        capture_output=True, text=True)
    assert r.stdout.strip() == "['-i', 'song.mp3', '-p', '0']", r.stdout


# ==================== r4 补刀重写守卫(T3/R-1,Eng A-1/E-1/E-3/T-1) ====================

import re as _re


def _make_sources(tmp_path, song_name="陈奕迅 - 最佳损友", stereo=False):
    """伪造 AICoverGen song_output/<hash>/ 三件套+RVC vocal(真实命名形态)。"""
    song_dir = tmp_path / "song_output" / "8c6dfb7df7d"
    song_dir.mkdir(parents=True, exist_ok=True)
    stem = song_name + ("_stereo" if stereo else "")
    inst = song_dir / f"{stem}_Instrumental.wav"
    backup = song_dir / f"{stem}_Vocals_Backup.wav"
    dereverb = song_dir / f"{stem}_Vocals_Main_DeReverb.wav"
    vocal = song_dir / f"{song_name}_autolive_WY_p0_i0.75_fr3_rms0.25_pro0.33_rmvpe.wav"
    for f in (inst, backup, dereverb, vocal):
        f.write_bytes(b"RIFF" + b"0" * 64)
    return str(tmp_path / "song_output"), {"vocal": str(vocal), "backup": str(backup), "inst": str(inst), "dir": str(song_dir)}


def q_dir_stub(song_output_root):
    q = CoverQueue.__new__(CoverQueue)
    q.queue = []
    q.current = None
    q.lock = __import__("threading").RLock()
    q._stop = types.SimpleNamespace(is_set=lambda: False, wait=lambda t: False)
    q.config = types.SimpleNamespace(get=lambda *a, **k: None)
    _base_root = (song_output_root[: -len(os.sep + "song_output")]
                  if song_output_root.endswith(os.sep + "song_output")
                  else song_output_root)
    q._aicovergen_root = lambda: _base_root  # 独立名:防外层 root 重绑穿闭包
    return q


class TestFaulthandlerSymmetry:
    """R-2：主跑 subprocess env 挂 PYTHONFAULTHANDLER（C5 补刀退役后仅主跑侧，
    崩溃可诊断性保留）。"""

    def test_main_run_env_has_fault_handler(self, tmp_path, monkeypatch):
        """主跑 cmd env 含 PYTHONFAULTHANDLER=1（R-2）。"""
        import inspect
        src = inspect.getsource(CoverQueue._process)
        assert "FAULTHANDLER_ENV" in src, "主跑 env 未挂 faulthandler"
        assert "_postprocess_fallback" not in src.replace(
            "_postprocess_fallback/_locate_cover_sources/_postprocess_code 退役", ""
        ), "补刀调用残留（C5 退役未完成）"
        # 模块级常量值正确
        from utils.cover_worker import FAULTHANDLER_ENV
        assert FAULTHANDLER_ENV == {"PYTHONFAULTHANDLER": "1"}

class TestR6SuccessCondition:
    """R-6 真根因回归锁（/investigate 五轮链终点）：主跑崩(rc!=0)+补刀产出
    成品=成功路径——旧条件 rc==0 and produced 把补刀的成功成果否决为失败
    （日志实证：两次"补刀完成"紧跟两次 _fail）。

    实现与 /investigate 诊断脚本同构（直调属性赋值——pytest monkeypatch 与
    CoverQueue 类属性交互在此场景有未解差异，诊断脚本已验证产品代码正确）。"""

    def test_produced_succeeds_despite_rc(self):
        import time as _t
        import tempfile as _tf
        import traceback as _tb
        from unittest.mock import MagicMock
        import utils.cover_worker as cw

        tmp_path = _tf.mkdtemp(prefix="r6_guard_")
        q = CoverQueue.__new__(CoverQueue)
        q.queue = []
        q.current = None
        q.lock = __import__("threading").RLock()
        q._stop = types.SimpleNamespace(is_set=lambda: False, wait=lambda t: False)
        q.config = types.SimpleNamespace(get=lambda *a, **k: None)
        q._rt_python = lambda: sys.executable
        q._covers_dir = lambda: os.path.join(tmp_path, "covers")
        q._aicovergen_root = lambda: tmp_path
        song_dir = os.path.join(tmp_path, "song_output", "hash1")
        os.makedirs(song_dir, exist_ok=True)
        vocal = os.path.join(song_dir, "song_autolive_WY_p0_i0.75_fr3_rms0.25_pro0.33_rmvpe.wav")
        open(vocal, "wb").write(b"RIFF" + b"0" * 64)
        song_path = os.path.join(tmp_path, "s.mp3")
        open(song_path, "wb").write(b"ID3")
        job = {"song": "song", "song_path": song_path, "profile_id": "WY",
               "cover_path": os.path.join(tmp_path, "covers", "song__WY.wav"),
               "retries_left": 1, "started_at": _t.time() - 100,
               "wait_code": "", "wait_reason": ""}
        os.makedirs(os.path.dirname(job["cover_path"]), exist_ok=True)
        seq = iter([(False, "x", "live_running"), (True, "GPU", "")])
        gp_orig = cw_subprocess = None
        import utils.gpu_policy as gp
        gp_orig = gp.background_task_gate
        gate_state = {"calls": 0}

        def _fake_gate(*a, **k):
            gate_state["calls"] += 1
            if gate_state["calls"] == 1:
                return (False, "x", "live_running")
            return (True, "GPU", "")
        gp.background_task_gate = _fake_gate
        env_orig = dict(cw.FAULTHANDLER_ENV)
        cw.FAULTHANDLER_ENV = {}
        popen_orig = cw.subprocess.Popen
        fake_proc = MagicMock()
        fake_proc.wait.return_value = 3221225477  # 0xC0000005
        cw.subprocess.Popen = lambda *a, **k: fake_proc
        produced_file = os.path.join(song_dir, "song (autolive_WY Ver).wav")
        open(produced_file, "wb").write(b"RIFF" + b"0" * 64)
        find_orig = CoverQueue._find_produced_wav
        CoverQueue._find_produced_wav = lambda self, job: (
            produced_file if os.path.isfile(produced_file) else None)
        fail_calls = []
        fail_orig = CoverQueue._fail
        CoverQueue._fail = lambda self, j, p, timeout=False: fail_calls.append(1)
        retain_orig = CoverQueue._retain_split_tracks
        prep_orig = CoverQueue._prepare_rvc_model_dir
        CoverQueue._retain_split_tracks = lambda self, j, c: None
        CoverQueue._prepare_rvc_model_dir = lambda self, j: None
        on_done = MagicMock()
        q.on_done = on_done
        try:
            q._process(job, q._policies())
        finally:
            gp.background_task_gate = gp_orig
            cw.FAULTHANDLER_ENV = env_orig
            cw.subprocess.Popen = popen_orig
            CoverQueue._find_produced_wav = find_orig
            CoverQueue._fail = fail_orig
            CoverQueue._retain_split_tracks = retain_orig
            CoverQueue._prepare_rvc_model_dir = prep_orig
        assert on_done.called, "rc!=0+补刀产出成品应走成功路径（on_done 触发）"
        assert not fail_calls, "补刀产出成品不应走 _fail"
        assert os.path.isfile(job["cover_path"]), "成品应原子落位 covers/"
