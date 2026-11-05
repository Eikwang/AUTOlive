# -*- coding: UTF-8 -*-
"""erro3 批次守卫（2026-11-04 /autoplan main-20261104-211016 T1-T6）。

覆盖：T3 点歌意图短串守卫（R3①②③④/E1 两路径）、T2 song 编排接线
（_song_announce_and_play/_play_cover_done → on_song）、T1 迁移标记
char_dir 本地化+meta 垃圾清理、T6 编排启动自检（E5 三断言/零输出态）、
R5 erro3 场景链（缺时长→60s 兜底→同键重入忽略，时钟注入）。
"""
import json
import os
from unittest.mock import MagicMock

import pytest

from tests.test_action_choreography import DottedConfig, AVAILABLE, make_rule


# ---------------------------------------------------------------------------
# T3/E1：match_song_intent 短串守卫
# ---------------------------------------------------------------------------

class TestMatchSongIntentShortGuard:
    TW = ["点歌", "唱首"]
    NAMES = ["一", "牧马城市", "你是我的眼（Live）"]

    def _match(self, text, names=NAMES, similarity=0.8):
        from utils.common.text_mixin import match_song_intent
        return match_song_intent(text, self.TW, names, similarity)

    def test_one_char_song_name_substring_not_matched(self):
        """R3④：1 字歌名「一」——候选「试一」子串与相似度两分支均不误命中。"""
        # 「点歌试一」：触发词「点歌」→候选「试一」；旧实现 name in candidate
        # （「一」in「试一」）与相似度（ratio≈0.667≥0.5）都会误命中
        assert self._match("点歌试一")[0] != "matched"

    def test_one_char_exact_still_works(self):
        """E1 精确相等豁免：1 字歌名全名点歌仍可用。"""
        assert self._match("点歌一")[0] == "matched"

    def test_normal_song_still_works(self):
        """R3③ 负向守卫：正常点歌不回归（真实歌名+触发词）。"""
        state, name, _ = self._match("唱首牧马城市")
        assert state == "matched" and name == "牧马城市"

    def test_no_trigger_word_silent(self):
        """R3①：无触发词不进点歌链。"""
        assert self._match("测试一") is None

    def test_start_cmd_newline_string_normalized(self):
        """E1+（2026-11-05 用户实测「测试一」误触发真正根因）：start_cmd 实存
        形态=换行分隔**字符串**——曾直接 for 迭代=逐字符当触发词（弹幕含
        「一」即误触发）。归一后：字符串按行拆分、逐字符不再匹配。"""
        from utils.common.text_mixin import match_song_intent
        start_cmd = "点一首\n唱一首\n唱一个\n会不会唱\n主播唱首\n来一首\n"
        # 「测试一」含触发词字符「一」——归一前必误触发；归一后整词不命中
        assert match_song_intent("测试一", start_cmd, self.NAMES, 0.8) is None
        # 正常点歌（触发词整词「唱一首」）不回归
        state, name, _ = match_song_intent("唱一首牧马城市", start_cmd, self.NAMES, 0.8)
        assert state == "matched" and name == "牧马城市"

    def test_long_song_substring_still_works(self):
        """E1 双向子串（短侧≥2）不回归：歌名含 Live 括注仍命中。"""
        state, name, _ = self._match("点歌你是我的眼")
        assert state == "matched" and name == "你是我的眼（Live）"


# ---------------------------------------------------------------------------
# T2：song 编排接线（_song_announce_and_play / _play_cover_done → on_song）
# ---------------------------------------------------------------------------

class _FakeHub:
    song_verdict = None
    choreo_accepted = False


class _FakeHandle:
    def __init__(self):
        self.config = DottedConfig({"choose_song": {"enable": True,
                                                    "match_success_copy": "{content}"}})
        self.common = type("C", (), {"merge_consecutive_asterisks": staticmethod(lambda u: u)})()
        self.audio_synthesis_handle = lambda msg: None
        self.webui_show_chat_log_callback = lambda *a, **k: None

    def get(self, *keys):
        return self.config.get(*keys)


class TestSongChoreoWiring:
    def _handler(self, monkeypatch, accepted, calls):
        from utils.handlers.feature_handler import FeatureHandler
        fh = FeatureHandler.__new__(FeatureHandler)
        fh.my_handle = _FakeHandle()
        fh._render_song_copy = lambda tpl, **kw: tpl or "x"
        fh._song_play_message = lambda *a, **k: calls.append(("play", a))
        fh._song_covers_hit = lambda name: None
        fh._song_enqueue_cover = lambda *a, **k: calls.append(("enqueue", a))
        import utils.handlers.feature_handler as fh_mod
        import utils.orchestration as orch

        class _FakeChoreo:
            def on_song(self, name):
                calls.append(("on_song", name))
                return accepted
        monkeypatch.setattr(orch, "get_choreographer", lambda: _FakeChoreo())
        monkeypatch.setattr(fh_mod, "get_choreographer",
                            lambda: _FakeChoreo(), raising=False)
        # feature_handler 内部 from utils.orchestration import get_choreographer
        # ——patch 源模块属性（import 语句每次执行取源模块属性）
        monkeypatch.setattr("utils.orchestration.get_choreographer",
                            lambda: _FakeChoreo())
        return fh

    def test_announce_play_calls_on_song(self, monkeypatch):
        """R2①：点歌命中播放入口必须调 on_song（song 规则接管挂点）。"""
        calls = []
        fh = self._handler(monkeypatch, True, calls)
        assert fh._song_announce_and_play("u", "牧马城市", "") is True
        assert ("on_song", "牧马城市") in calls

    def test_play_cover_done_calls_on_song(self, monkeypatch):
        """R2②：翻唱命中点同挂 on_song。"""
        calls = []
        fh = self._handler(monkeypatch, True, calls)
        fh._play_cover_done({"song": "牧马城市", "cover_path": "x.wav"})
        assert ("on_song", "牧马城市") in calls

    def test_announce_play_works_without_choreo(self, monkeypatch):
        """编排未装配=原路播放零变化（on_song 全兜底语义）。"""
        calls = []
        fh = self._handler(monkeypatch, False, calls)
        import utils.orchestration as orch
        monkeypatch.setattr(orch, "get_choreographer", lambda: None)
        assert fh._song_announce_and_play("u", "牧马城市", "") is True
        assert any(c[0] == "enqueue" for c in calls) or any(c[0] == "play" for c in calls)


# ---------------------------------------------------------------------------
# T1：迁移标记 char_dir 本地化 + meta 垃圾清理
# ---------------------------------------------------------------------------

class TestMigrateMarkerLocal:
    def test_marker_in_char_dir_and_meta_cleaned(self, tmp_path, monkeypatch):
        """迁移标记写 char_dir 本地（tmp 隔离）+meta 存量 _migrated:* 剔除。"""
        import utils.edtalk_realtime.materials as materials
        char_dir = tmp_path / "char"
        (char_dir / "orifra" / "seg_a").mkdir(parents=True)
        meta_file = tmp_path / "meta.json"
        meta_file.write_text(json.dumps({
            "_migrated:C:\\pytest\\old1": True,
            "_migrated:C:\\pytest\\old2": True,
            "seg_a": {"type": "main", "duration_s": 3.0, "fps": 25.0},
        }, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(materials, "resolve_character_dir", lambda cfg: str(char_dir))
        monkeypatch.setattr(materials, "_meta_path", lambda: str(meta_file))
        rep = materials.migrate_material_types({}, cfg_override={})
        # marker 落 char_dir 本地
        assert (char_dir / "_types_migrated.marker").is_file()
        # meta 垃圾剔除、真实素材保留
        after = json.loads(meta_file.read_text(encoding="utf-8"))
        assert not [k for k in after if k.startswith("_migrated:")]
        assert after["seg_a"]["type"] == "main"
        # 幂等：marker 存在跳过
        rep2 = materials.migrate_material_types({}, cfg_override={})
        assert rep2.get("skipped") is True


# ---------------------------------------------------------------------------
# T6：编排启动自检（startup_smoke.check_choreography_materials）
# ---------------------------------------------------------------------------

class TestChoreographyStartupCheck:
    def test_missing_material_warns(self):
        """②引用段不在 meta/时长≤0 → 缺项清单 warn（erro3 场景）。"""
        from utils.startup_smoke import check_choreography_materials
        import utils.edtalk_realtime.materials as materials
        cfg = {"choreography": {"enable": True, "rules": [
            {"rule_id": "r1", "sequence": {
                "enter": {"segment": "不存在段"}, "main": {"segment": "m"},
                "exit": {}}}]}}
        monkey_meta = {"m": {"type": "main", "duration_s": 5.0}}
        orig = materials.get_material_meta
        import utils.startup_smoke as ss
        # startup_smoke 内部延迟 import——patch 源模块
        import utils.edtalk_realtime.materials as m_mod
        orig_get = m_mod.get_material_meta
        m_mod.get_material_meta = lambda seg: (
            monkey_meta.get(seg) or {"duration_s": None, "type": None})
        try:
            warns = check_choreography_materials(cfg)
        finally:
            m_mod.get_material_meta = orig_get
        assert any("r1:enter→不存在段" in w for w in warns)

    def test_disabled_zero_rules_silent(self):
        """enable=false/零规则=零输出（E5 显式态）。"""
        from utils.startup_smoke import check_choreography_materials
        assert check_choreography_materials({"choreography": {"enable": False}}) == []
        assert check_choreography_materials({"choreography": {"enable": True, "rules": []}}) == []


# ---------------------------------------------------------------------------
# R5：erro3 场景链（缺时长→60s 兜底→同键重入忽略）
# ---------------------------------------------------------------------------

class TestErro3Scenario:
    def _harness(self, tiers_meta_missing):
        """引擎 fixture：sidecar 缺时长（apply 物化留 None）→60s 兜底推进。"""
        from tests.test_action_choreography import gift_data
        from unittest.mock import MagicMock
        client = MagicMock()
        client.switch_segment.return_value = {"ok": True}
        client.schedule.return_value = {"ok": True}
        client.get_status.return_value = {"available_segments": sorted(AVAILABLE)}
        from utils.orchestration.choreographer import ActionChoreographer
        ch = ActionChoreographer(
            DottedConfig({"choreography": make_cfg_node()}), client,
            audio_enqueue=MagicMock(), thanks_generate=MagicMock(return_value=None),
            clock=lambda: 1000.0)
        ch._worker = None
        ch._ensure_worker = lambda enable=None: True
        ch.engine.on_edtalk_ready(AVAILABLE)
        return ch, client

    def test_missing_duration_falls_back_and_reentry_ignored(self):
        """T1 修复守卫（erro3 链）：sidecar 缺登记时 **保留 load 显式时长**
        ——曾无条件覆盖为 None→60s 兜底卡序列+同键重入忽略（erro3.log:221/369
        =「编排无法触发」根因）。修复后：显式时长保留、零 60s 兜底；sidecar
        有效值仍覆盖（R-ceo4 系统接管语义保持）。"""
        import unittest.mock
        import utils.edtalk_realtime.materials as m_mod
        ch, client = self._harness(True)
        node = make_cfg_node()
        node["rules"][0]["pred_version"] = "new"  # 物化仅处理 new 语义规则
        # make_rule 显式时长 4/30/4；sidecar 缺登记（get 缺省 None）
        with unittest.mock.patch.object(
                m_mod, "get_material_meta",
                return_value={"duration_s": None, "type": "main", "lip_flag": True,
                              "fps": 25.0, "closeup": False, "background": ""}):
            result = ch.apply_config(node)
            assert result["applied"] is True
            # load 显式时长保留（不被 sidecar 缺值覆盖为 None）
            assert ch.cfg.rules[0].enter.duration_s == 4.0
            assert ch.cfg.rules[0].main.duration_s == 30.0
            ch.on_gift({"gift_name": "小心心", "num": 1, "price": 1})
            assert ch.engine.state == "sequence"
            from utils.orchestration.choreographer import SwitchSegment
            while True:
                try:
                    item = ch._events.get_nowait()
                except Exception:
                    break
                if isinstance(item, SwitchSegment):
                    ch.engine.on_switch_result(item.step_id, True, 1000.0)
            # 显式时长在 → 零 60s 兜底（修复行为）
            assert ch.engine.degrade_total == 0

    def test_sidecar_valid_value_still_overrides(self):
        """R-ceo4 接管语义保持：sidecar 有效时长覆盖 load 显式配置。"""
        import unittest.mock
        import utils.edtalk_realtime.materials as m_mod
        ch, client = self._harness(True)
        node = make_cfg_node()
        node["rules"][0]["pred_version"] = "new"
        with unittest.mock.patch.object(
                m_mod, "get_material_meta",
                return_value={"duration_s": 12.5, "type": "main", "lip_flag": True,
                              "fps": 25.0, "closeup": False, "background": ""}):
            result = ch.apply_config(node)
            assert result["applied"] is True
            assert ch.cfg.rules[0].enter.duration_s == 12.5

    def test_init_backfills_missing_duration(self):
        """启动装载路径回填（2026-11-05 用户实测「测试二」根修）：管理页创建的
        new 规则 sequence 无 duration_s（R-ceo7 合法），**回填曾只挂在
        apply_config**——重启系统后规则以裸 None 进引擎，触发即 60s 兜底
        （sidecar 时长明明都在）。修复=__init__ load 后即物化。"""
        import unittest.mock
        import utils.edtalk_realtime.materials as m_mod
        node = make_cfg_node()
        node["rules"][0]["pred_version"] = "new"
        for n in ("enter", "main", "exit"):
            node["rules"][0]["sequence"][n].pop("duration_s", None)
        client = MagicMock()
        client.get_status.return_value = {"available_segments": sorted(AVAILABLE)}
        with unittest.mock.patch.object(
                m_mod, "get_material_meta",
                return_value={"duration_s": 1.32, "type": "enter", "lip_flag": True,
                              "fps": 25.0, "closeup": False, "background": ""}):
            from utils.orchestration.choreographer import ActionChoreographer
            ch = ActionChoreographer(
                DottedConfig({"choreography": node}), client,
                audio_enqueue=MagicMock(), thanks_generate=MagicMock(return_value=None),
                clock=lambda: 1000.0)
        # 启动装载即回填（无需点应用）
        assert ch.cfg.rules[0].enter.duration_s == 1.32
        assert ch.cfg.rules[0].main.duration_s == 1.32


def make_cfg_node():
    return {"enable": True, "default_mode": "rotate", "default_segments": [],
            "rules": [make_rule()]}
