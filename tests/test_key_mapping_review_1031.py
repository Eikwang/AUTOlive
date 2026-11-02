# -*- coding: UTF-8 -*-
"""
按键映射审查批次守卫测试（2026-10-31）

覆盖：
1. F1 触发类型来源区分恢复（T5 迁移回归修复守卫）——弹幕/回复源仅
   关键词|关键词+礼物 生效；礼物源仅 礼物|关键词+礼物 生效（原实现
   keyword_handle_trigger/gift_handle_trigger 双分派语义）
2. F4 空 keys 不再空转占位——按键路返回 None 放行同组串口触发
3. F2 空 serial_send_data 安全返回 None（原 random.choice([]) IndexError）
4. F3 礼物连发不受同句去重误伤——去重仅限弹幕/回复源
5. F5 前端 once 双开关恢复（key/serial_single_sentence_trigger_once）
6. 弹幕同句去重语义保持（S-1 原有行为回归）
"""

from unittest.mock import MagicMock

import pytest

from utils.handlers.feature_handler import FeatureHandler


def make_config(**overrides):
    cfg = MagicMock()
    cfg._cfg = {
        "key_mapping": {
            "enable": True,
            "type": "弹幕+回复",
            "start_cmd": "",
            "key_trigger_type": "关键词",
            "key_single_sentence_trigger_once": False,
            "serial_trigger_type": "不启用",
            "serial_single_sentence_trigger_once": False,
            "serial_rate_limit_per_min": 6,
            "serial_per_user_limit_per_min": 2,
            "config": [
                {
                    "keywords": ["放烟花"],
                    "gift": ["小花火"],
                    "keys": ["ctrlleft", "1"],
                    "similarity": "1",
                    "serial_name": "COM1",
                    "serial_send_data": ["FIRE"],
                }
            ],
        },
        "serial": {"config": [{"serial_name": "COM1", "baudrate": "115200", "serial_data_type": "ASCII"}]},
        "audio_synthesis_type": "none",
        "filter": {},
        "none": {},
    }
    cfg._cfg["key_mapping"].update(overrides)
    cfg.get = MagicMock(side_effect=lambda *keys: _get(cfg._cfg, keys))
    return cfg


def _get(node, keys):
    cur = node
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


class _OkSerialManager:
    """成功串口管理器替身（asyncio.run 内 connect+send_data）。"""

    async def connect(self, port, baudrate, timeout=1):
        return {"ret": True, "msg": f"已连接到 {port}"}

    async def send_data(self, port, data, data_type="ascii", timeout=1.0):
        return {"ret": True, "msg": f"发送{data_type}数据：{data}"}


@pytest.fixture
def feature():
    mh = MagicMock()
    mh.config = make_config()
    mh.common = MagicMock()
    mh.common.brackets_text_randomize.side_effect = lambda s: s
    mh.common.dynamic_variable_replacement.side_effect = lambda s, d: s
    mh.common.get_bj_time.return_value = "12:00:00"
    FeatureHandler._serial_rate_window = None
    FeatureHandler._serial_user_windows = None
    FeatureHandler._serial_last_content = None
    fh = FeatureHandler(mh)
    return fh, mh


@pytest.fixture
def serial_ok(monkeypatch):
    """注入串口管理器替身（环境可能无 pyserial——serial_manager_instance 顶层
    import serial 即失败；生产代码在 _connect_and_send 内惰性
    from-import get_serial_manager，替换 sys.modules 模块对象即可）。"""
    import sys
    import types
    fake_mod = types.ModuleType("utils.serial_manager_instance")
    fake_mod.get_serial_manager = lambda: _OkSerialManager()
    monkeypatch.setitem(sys.modules, "utils.serial_manager_instance", fake_mod)


# ==================== F1 触发类型来源区分恢复 ====================


class TestSourceGating:
    """触发类型×来源 矩阵：T5 迁移合并分派丢失来源区分的回归守卫。"""

    def test_gift_skipped_when_key_mode_keyword(self, feature, monkeypatch):
        """按键触发类型=关键词：礼物命中不触发按键（原实现 gift_handle_trigger 门控）。"""
        fh, mh = feature
        assert mh.config._cfg["key_mapping"]["key_trigger_type"] == "关键词"
        sentinel = MagicMock(return_value=True)
        monkeypatch.setattr(fh, "_trigger_key_press", sentinel)
        result = fh.key_mapping_handle("礼物", {"username": "u", "gift_name": "小花火",
                                                "num": 1, "unit_price": 1, "total_price": 1})
        assert result is False
        sentinel.assert_not_called()

    def test_comment_skipped_when_key_mode_gift(self, feature, monkeypatch):
        """按键触发类型=礼物：弹幕关键词命中不触发按键。"""
        fh, mh = feature
        mh.config._cfg["key_mapping"]["key_trigger_type"] = "礼物"
        sentinel = MagicMock(return_value=True)
        monkeypatch.setattr(fh, "_trigger_key_press", sentinel)
        result = fh.key_mapping_handle("弹幕", {"username": "u", "content": "放烟花"})
        assert result is False
        sentinel.assert_not_called()

    def test_reply_source_gated_like_danmu(self, feature, monkeypatch):
        """回复源与弹幕同属文本源：按键触发类型=礼物 时不触发。"""
        fh, mh = feature
        mh.config._cfg["key_mapping"]["key_trigger_type"] = "礼物"
        sentinel = MagicMock(return_value=True)
        monkeypatch.setattr(fh, "_trigger_key_press", sentinel)
        result = fh.key_mapping_handle("回复", {"username": "u", "content": "放烟花啦"})
        assert result is False
        sentinel.assert_not_called()

    def test_gift_fires_when_mode_gift(self, feature, monkeypatch):
        """按键触发类型=礼物：礼物命中触发按键。"""
        fh, mh = feature
        mh.config._cfg["key_mapping"]["key_trigger_type"] = "礼物"
        sentinel = MagicMock(return_value=True)
        monkeypatch.setattr(fh, "_trigger_key_press", sentinel)
        assert fh.key_mapping_handle("礼物", {"username": "u", "gift_name": "小花火",
                                              "num": 1, "unit_price": 1, "total_price": 1}) is True
        sentinel.assert_called_once()

    def test_both_mode_fires_both_sources(self, feature, monkeypatch):
        """按键触发类型=关键词+礼物：双来源均触发（不启用=均不触发）。"""
        fh, mh = feature
        mh.config._cfg["key_mapping"]["key_trigger_type"] = "关键词+礼物"
        sentinel = MagicMock(return_value=True)
        monkeypatch.setattr(fh, "_trigger_key_press", sentinel)
        assert fh.key_mapping_handle("礼物", {"username": "u", "gift_name": "小花火",
                                              "num": 1, "unit_price": 1, "total_price": 1}) is True
        assert fh.key_mapping_handle("弹幕", {"username": "u", "content": "放烟花"}) is True
        assert sentinel.call_count == 2
        mh.config._cfg["key_mapping"]["key_trigger_type"] = "不启用"
        assert fh.key_mapping_handle("弹幕", {"username": "u", "content": "放烟花"}) is False

    def test_dispatch_gates_serial_for_gift_source(self, feature, monkeypatch, serial_ok):
        """分派层门控：礼物源×串口触发类型=关键词 → serial 不发送。"""
        fh, mh = feature
        mh.config._cfg["key_mapping"]["key_trigger_type"] = "不启用"
        mh.config._cfg["key_mapping"]["serial_trigger_type"] = "关键词"
        sentinel = MagicMock(return_value=True)
        monkeypatch.setattr(fh, "_serial_send", sentinel)
        result = fh.key_mapping_handle("礼物", {"username": "u", "gift_name": "小花火",
                                                "num": 1, "unit_price": 1, "total_price": 1})
        assert result is False
        sentinel.assert_not_called()


# ==================== F4 空 keys 放行串口 ====================


class TestEmptyKeys:
    def test_empty_keys_returns_none(self, feature):
        """空 keys（未填/全空白行）→ _trigger_key_press 返回 None（不占位）。"""
        fh, mh = feature
        assert fh._trigger_key_press({"keys": []}, {}) is None
        assert fh._trigger_key_press({"keys": ["", "  "]}, {}) is None

    def test_empty_keys_falls_through_to_serial(self, feature, monkeypatch, serial_ok):
        """keys 空但串口配置就绪：按键路放行，串口路触发（once=False 聚合 True）。"""
        fh, mh = feature
        mh.config._cfg["key_mapping"]["config"][0]["keys"] = ["  "]
        mh.config._cfg["key_mapping"]["serial_trigger_type"] = "关键词"
        sentinel = MagicMock(return_value=None)  # 模拟空 keys 按键路
        monkeypatch.setattr(fh, "_trigger_key_press", sentinel)
        result = fh.key_mapping_handle("弹幕", {"username": "u", "content": "放烟花"})
        assert result is True  # 串口实际执行 → flag 聚合
        sentinel.assert_called_once()


# ==================== F2 空 serial_send_data 安全 ====================


class TestEmptySerialPool:
    def test_empty_pool_returns_none_no_crash(self, feature):
        """serial_send_data 空/全空白行 → 返回 None，不抛 IndexError。"""
        fh, mh = feature
        group = {"serial_name": "COM1", "serial_send_data": []}
        assert fh._serial_send(group, {"username": "u", "content": "x"}) is None
        group2 = {"serial_name": "COM1", "serial_send_data": ["", "  "]}
        assert fh._serial_send(group2, {"username": "u", "content": "x"}) is None

    def test_blank_rows_filtered(self, feature, serial_ok):
        """空白行被过滤，剩余有效行参与随机。"""
        fh, mh = feature
        group = {"serial_name": "COM1", "serial_send_data": ["", "  ", "GO"]}
        result = fh._serial_send(group, {"username": "u"})
        assert result == "GO"


# ==================== F3 礼物连发不被同句去重误伤 ====================


class TestSerialDedupScope:
    def test_consecutive_same_gift_not_deduped(self, feature, serial_ok):
        """连续同礼物触发串口：每次都发送（原实现仅首个触发，其余被误判同句）。

        每用户限速调高以隔离限速因素（本测验证的是去重作用域，限速另测）。
        """
        fh, mh = feature
        mh.config._cfg["key_mapping"]["serial_per_user_limit_per_min"] = 10
        group = mh.config._cfg["key_mapping"]["config"][0]
        gift_data = {"username": "u", "gift_name": "小花火", "num": 1}
        assert fh._serial_send(group, dict(gift_data)) == "FIRE"
        assert fh._serial_send(group, dict(gift_data)) == "FIRE"
        assert fh._serial_send(group, dict(gift_data)) == "FIRE"

    def test_danmu_same_sentence_dedup_kept(self, feature, serial_ok):
        """S-1 同句去重语义保持：同弹幕内容不重复触发（弹幕/回复源）。"""
        fh, mh = feature
        group = mh.config._cfg["key_mapping"]["config"][0]
        danmu = {"username": "u", "content": "放烟花"}
        assert fh._serial_send(group, dict(danmu)) == "FIRE"
        assert fh._serial_send(group, dict(danmu)) is None  # 同句去重
        assert fh._serial_send(group, dict(danmu)) is None  # 持续去重（状态未更新）


# ==================== F5 前端 once 双开关恢复 ====================


class TestFrontendOnceSwitches:
    def test_once_switches_present_in_ui(self):
        """前端 key_mapping tab 渲染 once 双开关（上游旧 UI 有、前端重写丢失）。"""
        from pathlib import Path
        src = Path("frontend/ui/tabs/key_mapping.py").read_text(encoding="utf-8")
        assert '("key_mapping", "key_single_sentence_trigger_once")' in src
        assert '("key_mapping", "serial_single_sentence_trigger_once")' in src
        assert "单句仅触发一次（按键）" in src
        assert "单句仅触发一次（串口）" in src

    def test_source_gating_anchored_in_handler(self):
        """分派层来源门控锚点：allowed_modes 来源区分在位（防再次合并回归）。"""
        from pathlib import Path
        src = Path("utils/handlers/feature_handler.py").read_text(encoding="utf-8")
        assert 'allowed_modes = ("礼物", "关键词+礼物") if source == "礼物"' in src
        assert '("关键词", "关键词+礼物")' in src
        # 四处分派调用均携带来源参数
        assert src.count("trigger_dispatch(trigger, key_mapping_config, matched_text, \"礼物\")") == 1
        assert src.count("trigger_dispatch(trigger, key_mapping_config, gift, \"礼物\")") == 1
        assert src.count("trigger_dispatch(trigger, key_mapping_config, matched_text, type)") == 1
        assert src.count("trigger_dispatch(trigger, key_mapping_config, keyword, type)") == 1


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
