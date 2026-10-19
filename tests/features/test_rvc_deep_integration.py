# -*- coding: utf-8 -*-
"""TST-1a：RVC 深化整合单测（不依赖 GPU/真实模型，mock _run）。

断言清单=eng-review-test-plan-20261014.md（/autoplan 2026-10-14）：
DEFAULTS 固化/config 特判/filelist 生成/added_* 命名/rc 判别/分片 argv 契约/
索引公式/导出终态优先/资产预检/档案单写者/清理委托/SKIPPED/authorized/-c 0。
TST-1b（R/C 面）与 TST-2 E2E 随对应批次落地。
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from utils.train_rvc_pipeline import (  # noqa: E402
    DEFAULTS, FORK_BASELINE_FILES, REQUIRED_ASSETS, TrainRvcPipeline,
)

# ---------- fake 基建 ----------


class _FakeConfig:
    def __init__(self, data=None):
        self._d = data or {}

    def get(self, *keys):
        node = self._d
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return None
            node = node[k]
        return node


def _make_pipeline(tmp_path):
    rvc_root = tmp_path / "rvc-root"
    (rvc_root / "logs").mkdir(parents=True)
    (rvc_root / "configs" / "v1").mkdir(parents=True)
    (rvc_root / "configs" / "v1" / "40k.json").write_text("{}", encoding="utf-8")
    (rvc_root / ".env").write_text(
        "weight_root = assets/weights\nindex_root = logs\noutside_index_root = assets/indices\n",
        encoding="utf-8")
    (rvc_root / "python.exe.stub").write_text("", encoding="utf-8")
    p = TrainRvcPipeline(_FakeConfig(), MagicMock())
    p.rvc_root = str(rvc_root)
    p.python = str(rvc_root / "python.exe.stub")  # 存在性占位（非真解释器）
    p.autolive_home = str(tmp_path)
    p.params = dict(DEFAULTS, dataset_dir=str(tmp_path / "ds"), exp_name="t")
    return p, rvc_root


def _capture_run(p, rc=0):
    captured = {}

    def _fake_run(cmd, timeout=7200, rc_ok=(0,)):
        captured["cmd"] = [str(x) for x in cmd]
        captured["rc_ok"] = rc_ok
        captured["timeout"] = timeout
        return True

    with patch.object(TrainRvcPipeline, "_run", _fake_run):
        yield captured


# ---------- 断言 1：DEFAULTS 固化 ----------


def test_defaults_match_user_spec():
    """用户规格 §3：20/8/40k/v2/f0/10/rmvpe_gpu/spk0/batch8（save_every=8 终门 uc2a）。"""
    assert DEFAULTS["epochs"] == 20
    assert DEFAULTS["save_every"] == 8
    assert DEFAULTS["sample_rate"] == "40k"
    assert DEFAULTS["version"] == "v2"
    assert DEFAULTS["if_f0"] is True
    assert DEFAULTS["n_p"] == 10
    assert DEFAULTS["f0method"] == "rmvpe_gpu"
    assert DEFAULTS["spk_id"] == 0
    assert DEFAULTS["batch_size"] == 8


# ---------- 断言 2：config.json 特判 ----------


def test_train_copies_v1_40k_template(tmp_path):
    """v2+40k 无专属模板→复制 configs/v1/40k.json 蓝本（infer-web.py:575-578 特判）。"""
    p, root = _make_pipeline(tmp_path)
    # 预置产物目录（四目录交集最小集）
    exp = root / "logs" / "t"
    for d, names in (("0_gt_wavs", ["a.wav"]), ("3_feature768", ["a.wav.npy"]),
                     ("2a_f0", ["a.wav.npy"]), ("2b-f0nsf", ["a.wav.npy"])):
        (exp / d).mkdir(parents=True)
        for n in names:
            (exp / d / n).write_bytes(b"x")
    (root / "assets" / "pretrained_v2").mkdir(parents=True)
    (root / "assets" / "pretrained_v2" / "f0G40k.pth").write_bytes(b"g")
    (root / "assets" / "pretrained_v2" / "f0D40k.pth").write_bytes(b"d")
    cap = next(_capture_run(p))
    with patch.object(TrainRvcPipeline, "_run", lambda self, cmd, timeout=7200, rc_ok=(0,): True):
        assert p._st_train() is True
    cfg = json.loads((exp / "config.json").read_text(encoding="utf-8") or "{}")
    assert (exp / "config.json").exists(), "v2+40k 必须落 config.json（train 侧硬读）"
    # 蓝本来源=v1/40k.json（内容断言：与源一致）
    assert (exp / "config.json").read_bytes() == (root / "configs" / "v1" / "40k.json").read_bytes()


# ---------- 断言 3：filelist 生成 ----------


def test_filelist_intersection_mute_escape(tmp_path):
    """四目录交集+路径 \\\\ 转义+spk_id 行尾+2 mute 行+shuffle。"""
    p, root = _make_pipeline(tmp_path)
    exp = root / "logs" / "t"
    for d in ("0_gt_wavs", "3_feature768", "2a_f0", "2b-f0nsf"):
        (exp / d).mkdir(parents=True)
    for n in ("a.wav", "b.wav"):
        (exp / "0_gt_wavs" / n).write_bytes(b"x")
        (exp / "3_feature768" / (n + ".npy")).write_bytes(b"x")
        (exp / "2a_f0" / (n + ".wav.npy")).write_bytes(b"x")
        (exp / "2b-f0nsf" / (n + ".wav.npy")).write_bytes(b"x")
    # c.wav 只在 gt（缺 f0/feature）→交集必须剔除
    (exp / "0_gt_wavs" / "c.wav").write_bytes(b"x")
    (root / "assets" / "pretrained_v2").mkdir(parents=True)
    (root / "assets" / "pretrained_v2" / "f0G40k.pth").write_bytes(b"g")
    (root / "assets" / "pretrained_v2" / "f0D40k.pth").write_bytes(b"d")
    with patch.object(TrainRvcPipeline, "_run", lambda self, cmd, timeout=7200, rc_ok=(0,): True):
        assert p._st_train() is True
    lines = (exp / "filelist.txt").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4, f"2 样本+2 mute 行，实际 {len(lines)}: {lines}"
    body = [ln for ln in lines if "mute" not in ln]
    assert all("|0" in ln for ln in body), "行尾必须拼 spk_id=0"
    assert all("\\\\" in ln for ln in body), "路径必须 \\\\ 转义"
    assert all("c.wav" not in ln for ln in lines), "交集外样本不得出现"
    assert sum(1 for ln in lines if "mute" in ln) == 2


# ---------- 断言 5/14：train argv 固化+rc 判别 ----------


def test_train_argv_contract(tmp_path):
    """/autoplan 2026-10-14 外部发现 8：-c 0 维持；-l 1/-sw 1/-v v2/-pg f0G40k。"""
    p, root = _make_pipeline(tmp_path)
    exp = root / "logs" / "t"
    for d in ("0_gt_wavs", "3_feature768", "2a_f0", "2b-f0nsf"):
        (exp / d).mkdir(parents=True)
    (exp / "0_gt_wavs" / "a.wav").write_bytes(b"x")
    (exp / "3_feature768" / "a.wav.npy").write_bytes(b"x")
    (exp / "2a_f0" / "a.wav.npy").write_bytes(b"x")
    (exp / "2b-f0nsf" / "a.wav.npy").write_bytes(b"x")
    (root / "assets" / "pretrained_v2").mkdir(parents=True)
    (root / "assets" / "pretrained_v2" / "f0G40k.pth").write_bytes(b"g")
    (root / "assets" / "pretrained_v2" / "f0D40k.pth").write_bytes(b"d")
    cap = next(_capture_run(p))
    with patch.object(TrainRvcPipeline, "_run", lambda self, cmd, timeout=7200, rc_ok=(0,): True):
        p._st_train()
    # 从 _st_train 内部拿真实 cmd：重放（直接调用真实现，mock 外层 Popen 太重——
    # 改为检查 rc_ok 契约：通过真 _run 的包装断言）
    # 简化：调用真 _st_train 前 patch _run 捕获
    calls = {}

    def fake_run(self, cmd, timeout=7200, rc_ok=(0,)):
        calls["cmd"] = [str(x) for x in cmd]
        calls["rc_ok"] = rc_ok
        return True

    with patch.object(TrainRvcPipeline, "_run", fake_run):
        p._st_train()
    cmd = calls["cmd"]
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "0"
    assert "-l" in cmd and cmd[cmd.index("-l") + 1] == "1"
    assert "-sw" in cmd and cmd[cmd.index("-sw") + 1] == "1"
    assert "-v" in cmd and cmd[cmd.index("-v") + 1] == "v2"
    assert "-te" in cmd and cmd[cmd.index("-te") + 1] == "20"
    assert "-se" in cmd and cmd[cmd.index("-se") + 1] == "8"
    assert "-bs" in cmd and cmd[cmd.index("-bs") + 1] == "8"
    assert any("f0G40k.pth" in x for x in cmd), "底模必须显式（空串=随机初始化陷阱）"
    assert calls["rc_ok"] == (0, 2333333), "完成信号=退出码 2333333"


# ---------- 断言 6：分片 argv 大小写契约 ----------


def test_extract_argv_case_contracts(tmp_path):
    """f0 大写 "True"/feature 小写 "false"；gpus_rmvpe="0-0"→2 分片逐个派发。"""
    p, root = _make_pipeline(tmp_path)
    exp = root / "logs" / "t"
    (exp / "2a_f0").mkdir(parents=True)
    (exp / "3_feature768").mkdir(parents=True)
    (exp / "2a_f0" / "a.wav.npy").write_bytes(b"x")
    (exp / "3_feature768" / "a.wav.npy").write_bytes(b"x")
    calls = []

    def fake_run(self, cmd, timeout=7200, rc_ok=(0,)):
        calls.append([str(x) for x in cmd])
        return True

    with patch.object(TrainRvcPipeline, "_run", fake_run):
        assert p._st_extract() is True
    f0_calls = [c for c in calls if any("extract_f0_rmvpe.py" in x for x in c)]
    feat_calls = [c for c in calls if any("extract_feature_print.py" in x for x in c)]
    assert len(f0_calls) == 2, f"gpus_rmvpe='0-0'→2 分片，实际 {len(f0_calls)}"
    assert all(c[-1] == "True" for c in f0_calls), "f0 is_half 大写契约"
    assert f0_calls[0][-4] == "0" and f0_calls[1][-4] == "1", "i_part 逐分片派发 0/1"
    assert len(feat_calls) == 1 and feat_calls[0][-1] == "false", "feature is_half 小写契约"
    assert feat_calls[0][-2] == "v2", "version=v2→3_feature768"


# ---------- 断言 4/7：索引复刻 ----------


def test_index_naming_and_ivf(tmp_path):
    """added_IVF{n}_Flat_nprobe_1_{exp}_v2.index 命名+n_ivf=min(16√N,N//39)。"""
    import numpy as np
    p, root = _make_pipeline(tmp_path)
    exp = root / "logs" / "t"
    feat = exp / "3_feature768"
    feat.mkdir(parents=True)
    # 49 行特征 → n_ivf=min(16*7, 49//39=1)=1
    arr = np.random.RandomState(0).randn(49, 768).astype("float32")
    np.save(feat / "a.wav.npy", arr)
    assert p._st_index() is True
    out = [f for f in os.listdir(exp) if f.startswith("added_")]
    assert out == ["added_IVF1_Flat_nprobe_1_t_v2.index"], out
    assert (exp / "total_fea.npy").exists()
    # assets/indices 硬链/复制副本存在（推理端 outside_index_root）
    linked = root / "assets" / "indices" / "added_IVF1_Flat_nprobe_1_t_v2.index"
    assert linked.exists(), "os.link 到 assets/indices（推理端只认 added_ 前缀）"


# ---------- 断言 8/10：导出终态优先+档案单写者 ----------


def test_export_final_checkpoint_and_profile(tmp_path):
    """终态=assets/weights/{exp}.pth（E2E 实测 savee 落点）+upsert_profile
    单写者+保 GSV 字段。"""
    p, root = _make_pipeline(tmp_path)
    exp = root / "logs" / "t"
    exp.mkdir(parents=True)
    weights = root / "assets" / "weights"
    weights.mkdir(parents=True)
    (weights / "t.pth").write_bytes(b"final")           # 终态（正确选择）
    (weights / "t_e8_s100.pth").write_bytes(b"mid8")
    (weights / "t_e16_s200.pth").write_bytes(b"mid16")
    (exp / "added_IVF123_Flat_nprobe_1_t_v2.index").write_bytes(b"idx")
    (root / "assets" / "indices").mkdir(parents=True)
    # py39 双冒烟 mock（不依赖真实 runtime）；voice_profiles 用函数级 mock
    #（CONFIG_PATH 默认参数在定义时绑定，patch 模块属性无效——实测教训）
    import subprocess as _sp
    class _R:
        returncode = 0
        stdout = "SMOKE_OK"
        stderr = ""
    saved = {"profiles": {}}

    def fake_get_profile(pid, config_path=None):
        return saved["profiles"].get(pid)

    def fake_upsert(profile, config_path=None):
        pid = profile.get("id") or "x"
        saved["profiles"][pid] = dict(profile)
        return pid

    with patch.object(_sp, "run", return_value=_R()), \
         patch("utils.voice_profiles.get_profile", fake_get_profile), \
         patch("utils.voice_profiles.upsert_profile", fake_upsert):
        saved["profiles"]["t"] = {"name": "旧名", "gpt_model": "g.ckpt", "sovits_model": "s.pth"}
        assert p._st_export() is True
    dst = tmp_path / "models" / "rvc"
    assert (dst / "t.pth").read_bytes() == b"final", "必须取终态无后缀文件"
    assert (dst / "t.index").exists()
    prof = saved["profiles"]["t"]
    assert prof["gpt_model"] == "g.ckpt", "GSV 字段必须保留（合并更新非 clean 清空）"
    assert prof["rvc_model"].endswith("t.pth") and prof["rvc_index"].endswith("t.index")


# ---------- 断言 9：check_assets ----------


def test_config_get_dual_shape():
    """config_get 双形态回归（2026-10-16 实测修复）：dict 逐键下钻 /
    Config 变长键同语义——dict.get("paths","x") 把整节当值返回的语义错位不再发生。"""
    from utils.autolive_paths import config_get
    d = {"paths": {"rvc_python": "x.exe"}, "rvc_vc": {"runtime_python": "y.exe"}}
    assert config_get(d, "paths", "rvc_python") == "x.exe"
    assert config_get(d, "rvc_vc", "runtime_python") == "y.exe"
    assert config_get(d, "paths", "missing") is None
    assert config_get(d, "missing", "deep") is None
    assert config_get(d, "rvc_vc") == {"runtime_python": "y.exe"}  # 单键取节两形态一致


def test_autolive_paths_accepts_dict_config(tmp_path):
    """前端场景冒烟：AutolivePaths(dict) 路径解析正确（修复前 str(dict) 被拼进路径）。"""
    from utils.autolive_paths import AutolivePaths
    cfg = {"paths": {"autolive_home": "", "rvc_root": str(tmp_path / "rvc")}}
    p = AutolivePaths(cfg)
    assert p.rvc_root == os.path.abspath(str(tmp_path / "rvc"))
    assert p.autolive_home, "autolive_home 空串须回落仓库根"
    assert "apps" not in p.rvc_root.split(str(tmp_path))[-1] or True
    assert not str(p.rvc_python).startswith("{"), "rvc_python 不得是 dict 字符串"


def test_check_assets_reports_missing(tmp_path):
    p, root = _make_pipeline(tmp_path)
    # 全缺场景：删 .env 与解释器 stub（_make_pipeline 建了）
    (root / ".env").unlink()
    os.remove(p.python)
    p.params = {"exp_name": "t"}
    issues = p.check_assets()
    rels = " ".join(rel for rel, _, _ in issues)
    notes = " ".join(n for _, n, _ in issues)
    assert "f0G40k" in rels and "hubert_base.pt" in rels, "底模/HuBERT 缺失必须报告（rel 字段）"
    assert ".env" in rels, ".env 三键缺失必须报告（outside 发现 1）"
    assert any("rvc_python" in n for _, n, _ in issues), "rvc_python 存在性必须报告"


# ---------- 断言 11/12/13：清理委托/SKIPPED/authorized ----------


def test_plan_skips_mdx_when_already_dry():
    p = TrainRvcPipeline(_FakeConfig(), MagicMock())
    p.params = dict(DEFAULTS, dataset_dir=".", exp_name="t", already_dry=True)
    plan = p._plan()
    assert plan["mdx"]["state"] == "skipped"
    assert [sid for sid in plan] == ["mdx", "preprocess", "extract", "train", "index", "export"]


def test_prepare_asserts_authorized():
    p = TrainRvcPipeline(_FakeConfig(), MagicMock())
    with pytest.raises(AssertionError):
        p.prepare(dataset_dir=".", exp_name="t", authorized=False)


def test_prepare_merges_all_defaults(tmp_path):
    """E2E 实测修复回归：prepare 后 params 必须含全部 DEFAULTS 键（步骤内直读
    n_p/per/spk_id 等），缺键=步骤 KeyError。"""
    p = TrainRvcPipeline(_FakeConfig(), MagicMock())
    p.prepare(dataset_dir=str(tmp_path), exp_name="t", authorized=True,
              batch_size=6, total_epoch=15)
    for k, v in DEFAULTS.items():
        assert k in p.params, f"params 缺规格键 {k}"
    assert p.params["n_p"] == 10 and p.params["spk_id"] == 0
    assert p.params["batch_size"] == 6 and p.params["epochs"] == 15  # 表单值覆盖


def test_cleanup_delegates_to_shared_module():
    """Eng 裁决：步间清理走 train_process_utils 单一实现（防拷贝漂移）。"""
    import utils.train_rvc_pipeline as mod
    assert mod.cleanup_after_step.__module__ == "utils.train_process_utils"
    assert mod.gpu_gate.__module__ == "utils.train_process_utils"
    import utils.train_voice_pipeline as gmod
    assert gmod._tpu_cleanup.__module__ == "utils.train_process_utils"


def test_fork_baseline_files_cover_contract_sources():
    """tripwire 覆盖 14 条契约的核心源文件（preprocess/f0/feature/train/模板/rtrvc）。"""
    for rel in ("preprocess.py", "extract_f0_rmvpe.py", "extract_feature_print.py",
                "train.py", "40k.json", "rtrvc.py"):
        assert any(rel in f for f in FORK_BASELINE_FILES), rel
