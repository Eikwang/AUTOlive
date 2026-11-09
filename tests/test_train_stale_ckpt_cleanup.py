# -*- coding: utf-8 -*-
"""训练链陈旧产物清理+产物断言守卫（2026-11-08 用户实报训练失败批）

实报根因三件套：
1. RVC fork 缺 i18n/（runtime312 迁移后外部路径断供暴露）→ train.py 崩溃
2. GSV exp_name 大小写变体（wy vs WY）命中同目录（Windows 不分大小写）→
   s2/s1 自动续训旧 ckpt（epoch 已满）→ 零训练秒退 rc=0 且零产物 → export 失败
3. RVC 旧四件套混入新数据集（交集 689 < gt 1448 警告）
防御面：prepare 清理（全链=全新训练语义，单步重跑不经 prepare 不受影响）+
s2/s1 产物断言（rc=0 不代表有产出）+ i18n 进预检/基线 + _atomic_update 竞态重试。
"""
import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _FakeCfg:
    def get(self, *a, **k):
        return None


class _FakePaths:
    def __init__(self, root):
        self.gpt_sovits_root = str(root)
        self.gpt_sovits_python = "python.stub"
        self.autolive_home = str(root)


def _rvc_pipeline(tmp_path, exp="wy"):
    from utils.train_rvc_pipeline import TrainRvcPipeline

    p = TrainRvcPipeline(_FakeCfg(), MagicMock())
    p.rvc_root = str(tmp_path / "apps" / "rvc")
    p.autolive_home = str(tmp_path)
    p.params = {"dataset_dir": str(tmp_path / "ds"), "exp_name": exp}
    return p


def test_rvc_prepare_cleans_stale_products(tmp_path):
    """RVC 全链启动清旧四件套/filelist/added index；mute 共享资产不动。"""
    p = _rvc_pipeline(tmp_path)
    exp = os.path.join(p.rvc_root, "logs", "wy")
    for sub, fn in (("0_gt_wavs", "old.wav"), ("1_16k_wavs", "old.wav"),
                    ("2a_f0", "old.wav.npy"), ("2b-f0nsf", "old.wav.npy"),
                    ("3_feature768", "old.wav.npy")):
        os.makedirs(os.path.join(exp, sub), exist_ok=True)
        open(os.path.join(exp, sub, fn), "w").close()
    open(os.path.join(exp, "filelist.txt"), "w").close()
    open(os.path.join(exp, "added_IVF100_old.index"), "w").close()
    p.prepare(dataset_dir=str(tmp_path / "ds"), exp_name="wy", authorized=True)
    for sub in ("0_gt_wavs", "1_16k_wavs", "2a_f0", "2b-f0nsf", "3_feature768"):
        assert os.listdir(os.path.join(exp, sub)) == [], sub
    assert not os.path.exists(os.path.join(exp, "filelist.txt"))
    assert not os.path.exists(os.path.join(exp, "added_IVF100_old.index"))
    assert any("已清除" in line for line in p.log_lines), "清理动作须留日志痕迹"


def test_rvc_prepare_first_run_noop(tmp_path):
    """首次训练（实验目录不存在）清理静默跳过不炸。"""
    p = _rvc_pipeline(tmp_path)
    p.prepare(dataset_dir=str(tmp_path / "ds"), exp_name="fresh", authorized=True)
    assert not any("已清除" in line for line in p.log_lines)


def test_gsv_prepare_cleans_stale_state(tmp_path):
    """GSV 全链启动清空实验产物目录（2026-11-09 二轮实报根修）：
    训练 ckpt（续训零产出）+prepare 中间产物（BERT 对齐断言崩溃）全清。"""
    from utils.train_voice_pipeline import TrainVoicePipeline

    p = TrainVoicePipeline(_FakeCfg(), _FakePaths(tmp_path))
    exp = str(tmp_path / "logs" / "wy")
    s2 = os.path.join(exp, "logs_s2_v2ProPlus")
    s1_ckpt = os.path.join(exp, "logs_s1_v2ProPlus", "ckpt")
    bert = os.path.join(exp, "3-bert")
    os.makedirs(s2, exist_ok=True)
    os.makedirs(s1_ckpt, exist_ok=True)
    os.makedirs(bert, exist_ok=True)
    open(os.path.join(s2, "G_233333333333.pth"), "w").close()
    open(os.path.join(s1_ckpt, "epoch=19-step=1280.ckpt"), "w").close()
    open(os.path.join(bert, "old.wav.pt"), "w").close()  # 10/16 旧数据集 BERT 特征
    open(os.path.join(exp, "2-name2text.txt"), "w").close()
    p.params = {"dataset_dir": str(tmp_path / "ds"), "exp_name": "wy"}
    p.prepare(dataset_dir=str(tmp_path / "ds"), exp_name="wy", authorized=True)
    assert os.listdir(exp) == [], f"实验产物目录应整体清空，残留: {os.listdir(exp)}"
    assert any("已清空" in line for line in p.log_lines)


def test_export_fixes_dangling_cold_start(tmp_path, monkeypatch):
    """export 联动（2026-11-09 实报根修）：冷启动 params 悬空时自动切到本次
    成品；有效配置不抢占。"""
    from utils.train_voice_pipeline import TrainVoicePipeline
    import utils.voice_profiles as vp

    cfg_path = tmp_path / "config.json"
    dangling = str(tmp_path / "gone" / "old.pth")  # 悬空指向
    cfg_path.write_text(json.dumps({
        "gpt_sovits": {"params": {"gpt_model_path": dangling,
                                  "sovits_model_path": dangling}}}),
        encoding="utf-8")

    p = TrainVoicePipeline(_FakeCfg(), _FakePaths(tmp_path))
    gsv_w = tmp_path / "GPT_weights_v2ProPlus"
    vits_w = tmp_path / "SoVITS_weights_v2ProPlus"
    gsv_w.mkdir()
    vits_w.mkdir()
    (gsv_w / "wy-e20.ckpt").write_bytes(b"g")
    (vits_w / "wy_e10_s1450.pth").write_bytes(b"s")
    sliced = tmp_path / "ds" / "sliced"
    sliced.mkdir(parents=True)
    (sliced / "a.wav").write_bytes(b"x")
    p.params = {"dataset_dir": str(tmp_path / "ds"), "exp_name": "wy"}
    monkeypatch.setattr(vp, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(vp, "get_profile", lambda pid, config_path=None: {})
    monkeypatch.setattr(vp, "upsert_profile", lambda prof, config_path=None: prof.get("id"))
    assert p._st_export() is True
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    params = data["gpt_sovits"]["params"]
    assert params["gpt_model_path"].endswith("wy-e20.ckpt"), "悬空 gpt 应联动到新成品"
    assert params["sovits_model_path"].endswith("wy_e10_s1450.pth"), "悬空 sovits 应联动到新成品"
    assert any("悬空" in line for line in p.log_lines)


def test_export_keeps_valid_cold_start(tmp_path, monkeypatch):
    """export 联动不抢占：params 指向真实存在的文件时保持不动。"""
    from utils.train_voice_pipeline import TrainVoicePipeline
    import utils.voice_profiles as vp

    cfg_path = tmp_path / "config.json"
    valid_g = tmp_path / "keep_g.ckpt"
    valid_s = tmp_path / "keep_s.pth"
    valid_g.write_bytes(b"g")
    valid_s.write_bytes(b"s")
    cfg_path.write_text(json.dumps({
        "gpt_sovits": {"params": {"gpt_model_path": str(valid_g),
                                  "sovits_model_path": str(valid_s)}}}),
        encoding="utf-8")

    p = TrainVoicePipeline(_FakeCfg(), _FakePaths(tmp_path))
    gsv_w = tmp_path / "GPT_weights_v2ProPlus"
    vits_w = tmp_path / "SoVITS_weights_v2ProPlus"
    gsv_w.mkdir()
    vits_w.mkdir()
    (gsv_w / "wy-e20.ckpt").write_bytes(b"g")
    (vits_w / "wy_e10_s1450.pth").write_bytes(b"s")
    p.params = {"dataset_dir": str(tmp_path / "ds"), "exp_name": "wy"}
    monkeypatch.setattr(vp, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(vp, "get_profile", lambda pid, config_path=None: {})
    monkeypatch.setattr(vp, "upsert_profile", lambda prof, config_path=None: prof.get("id"))
    assert p._st_export() is True
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    params = data["gpt_sovits"]["params"]
    assert params["gpt_model_path"] == str(valid_g), "有效配置不得被训练抢占"
    assert params["sovits_model_path"] == str(valid_s)


def _mk_s2_pipeline(tmp_path):
    from utils.train_voice_pipeline import TrainVoicePipeline

    p = TrainVoicePipeline(_FakeCfg(), _FakePaths(tmp_path))
    p.params = {"dataset_dir": str(tmp_path / "ds"), "exp_name": "wy",
                "batch_size": 4, "epochs_s2": 10, "text_low_lr_rate": 0.4,
                "save_every_s2": 5, "if_dpo": False, "already_dry": True}
    p.asset_issues = []
    tpl_dir = os.path.join(str(tmp_path), "GPT_SoVITS", "configs")
    os.makedirs(tpl_dir, exist_ok=True)
    os.makedirs(os.path.join(str(tmp_path), "logs", "wy"), exist_ok=True)  # _st_prepare 产物目录（真实链路由上一步建）
    with open(os.path.join(tpl_dir, "s2v2ProPlus.json"), "w", encoding="utf-8") as f:
        json.dump({"train": {}, "data": {}, "model": {}}, f)
    return p


def test_s2_zero_product_assertion(tmp_path):
    """s2 产物断言：rc=0 但无新成品 → False+三要素日志（续训静默零产出不再漏网）。"""
    p = _mk_s2_pipeline(tmp_path)
    weight_dir = os.path.join(str(tmp_path), "SoVITS_weights_v2ProPlus")
    os.makedirs(weight_dir, exist_ok=True)
    old = os.path.join(weight_dir, "wy_e5_s1.pth")
    open(old, "w").close()
    os.utime(old, (0, 0))  # mtime=1970（旧产物）
    p._gpu_gate = lambda: True
    p._run = lambda cmd, **kw: True  # 模拟续训静默秒退 rc=0 且零产出
    assert p._st_s2() is False, "rc=0 但零新成品必须判失败（export 不再背锅）"
    assert any("零成品权重" in line for line in p.log_lines)


def test_s2_new_product_passes(tmp_path):
    """s2 产物断言正向：本步产出新成品 → True。"""
    p = _mk_s2_pipeline(tmp_path)
    weight_dir = os.path.join(str(tmp_path), "SoVITS_weights_v2ProPlus")
    os.makedirs(weight_dir, exist_ok=True)

    def _run_and_produce(cmd, **kw):
        open(os.path.join(weight_dir, "wy_e10_s2870.pth"), "w").close()
        return True

    p._gpu_gate = lambda: True
    p._run = _run_and_produce
    assert p._st_s2() is True
    assert any("成品" in line for line in p.log_lines)


def test_i18n_in_required_assets_and_baseline():
    """i18n 缺失实报根修：预检清单+fork 基线收编（缺失即训练前拦截）。"""
    from utils.train_rvc_pipeline import FORK_BASELINE_FILES, REQUIRED_ASSETS

    assert any("i18n/i18n.py" in rel for rel, _ in REQUIRED_ASSETS)
    assert "i18n/i18n.py" in FORK_BASELINE_FILES
    # fork 副本现状：i18n 必须在位（本批已从 fork 源补齐）
    assert os.path.isfile(os.path.join(ROOT, "apps", "rvc", "i18n", "i18n.py")), \
        "apps/rvc/i18n/i18n.py 缺失——RVC train.py 将崩溃（ModuleNotFoundError）"


def test_atomic_update_retries_on_windows_lock(tmp_path):
    """_atomic_update 竞态重试：os.replace 被占用（PermissionError）重试后成功。"""
    from utils.voice_profiles import _atomic_update

    cfg = str(tmp_path / "config.json")
    with open(cfg, "w", encoding="utf-8") as f:
        json.dump({"other": 1}, f)
    real_replace = os.replace
    calls = {"n": 0}

    def _flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("目标被占用（webui 并发读）")
        real_replace(src, dst)

    with patch("utils.voice_profiles.time.sleep"), \
         patch("utils.voice_profiles.os.replace", side_effect=_flaky_replace):
        _atomic_update(cfg, lambda d: d.setdefault("voice_consent_log", []).append(
            {"exp": "wy"}))
    with open(cfg, encoding="utf-8") as f:
        data = json.load(f)
    assert data["other"] == 1, "其余节不受影响"
    assert data["voice_consent_log"][0]["exp"] == "wy"
    assert calls["n"] == 2, "首次 PermissionError 后应重试"


def test_atomic_update_exhausts_retries(tmp_path):
    """重试耗尽后抛最后异常（不再静默吞错，授权留档失败可被上层日志捕获）。"""
    from utils.voice_profiles import _atomic_update

    cfg = str(tmp_path / "config.json")
    with open(cfg, "w", encoding="utf-8") as f:
        json.dump({}, f)
    with patch("utils.voice_profiles.time.sleep"), \
         patch("utils.voice_profiles.os.replace",
               side_effect=PermissionError("持续占用")):
        try:
            _atomic_update(cfg, lambda d: None, _retries=3)
            raise AssertionError("重试耗尽必须抛异常")
        except PermissionError:
            pass
