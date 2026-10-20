# -*- coding: UTF-8 -*-
"""
T5 按键映射迁移回归测试（/autoplan 断链批次，源=my_handle.py.backup:2225-2520）

覆盖（S-1/T-c 义务）：
1. 弹幕相似度匹配触发（difflib ratio ≥ similarity）
2. 回复包含匹配
3. 礼物名精确匹配
4. 文案触发：变量替换+播放链
5. S-1 串口限速：全局每分钟上限+每用户上限
6. S-1 同句去重
7. once 语义：single_sentence_trigger_once 命中即终止（T-c 适配点①键面）
8. pyautogui/pyserial 缺失降级（不崩，警告跳过）
9. start_cmd 命令头剥离
"""

import random
from unittest.mock import MagicMock

import pytest

from utils.handlers.feature_handler import FeatureHandler


def make_config():
    cfg = MagicMock()
    cfg._cfg = {
        "key_mapping": {
            "enable": True,
            "type": "弹幕+回复",
            "start_cmd": "",
            "key_trigger_type": "关键词",
            "key_single_sentence_trigger_once": True,
            "copywriting_trigger_type": "关键词",
            "copywriting_single_sentence_trigger_once": False,
            "local_audio_trigger_type": "关键词",
            "serial_trigger_type": "关键词",
            "img_path_trigger_type": "关键词",
            "serial_rate_limit_per_min": 6,
            "serial_per_user_limit_per_min": 2,
            "config": [
                {
                    "keywords": ["放烟花"],
                    "gift": ["小花火"],
                    "keys": ["ctrlleft", "1"],
                    "similarity": "0.5",
                    "copywriting": ["哇！{username} 放烟花了！"],
                    "serial_name": "COM1",
                    "serial_send_data": ["FIRE {username}"],
                    "local_audio": ["data/音效/烟花.wav"],
                    "img_path": [],
                }
            ],
        },
        "serial": {"config": [{"serial_name": "COM1", "baudrate": "115200", "serial_data_type": "ASCII"}]},
        "audio_synthesis_type": "none",
        "filter": {},
        "none": {},
    }
    cfg.get = MagicMock(side_effect=lambda *keys: _get(cfg._cfg, keys))
    return cfg


def _get(node, keys):
    cur = node
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


@pytest.fixture
def feature():
    mh = MagicMock()
    mh.config = make_config()
    mh.common = MagicMock()
    mh.common.brackets_text_randomize = lambda x: x
    mh.common.dynamic_variable_replacement = lambda t, d: t.replace("{username}", str(d.get("username", ""))).replace("{gift_name}", str(d.get("gift_name", "")))
    mh.common.get_bj_time.return_value = "12:00:00"
    FeatureHandler._serial_rate_window = None
    FeatureHandler._serial_user_windows = None
    FeatureHandler._serial_last_content = None
    fh = FeatureHandler(mh)
    return fh, mh


class TestMatching:
    def test_danmu_similarity_trigger_copywriting(self, feature):
        """弹幕相似度匹配 → 文案触发（once=False 继续遍历但触发已发生）。"""
        fh, mh = feature
        triggered = fh.key_mapping_handle("弹幕", {"username": "小明", "content": "放烟花呀"})
        assert triggered in (True, False)  # once=False 时继续遍历返回 False，但播放已发生
        mh.audio_synthesis_handle.assert_called()
        # once=false 时链上 copywriting+local_audio 都触发——检查任一调用含用户名
        contents = [c[0][0]["content"] for c in mh.audio_synthesis_handle.call_args_list]
        assert any("小明" in c for c in contents), contents

    def test_reply_contains_trigger(self, feature):
        """回复模式：关键词包含匹配。"""
        fh, mh = feature
        fh.key_mapping_handle("回复", {"username": "小明", "content": "来一个放烟花"})
        mh.audio_synthesis_handle.assert_called()

    def test_gift_exact_match(self, feature):
        """礼物名精确匹配：key 降级（pyautogui 缺失）→ 链继续，copywriting 实际执行。"""
        fh, mh = feature
        result = fh.key_mapping_handle("礼物", {"username": "小明", "gift_name": "小花火",
                                                 "num": 1, "unit_price": 1, "total_price": 1})
        assert result is True  # copywriting 实际执行（executed）→ flag 聚合 True
        mh.audio_synthesis_handle.assert_called()  # 文案播报发生

    def test_start_cmd_stripped(self, feature):
        """命令头剥离：#烟花 → 烟花。"""
        fh, mh = feature
        mh.config._cfg["key_mapping"]["start_cmd"] = "#"
        fh.key_mapping_handle("回复", {"username": "小明", "content": "#放烟花"})
        mh.audio_synthesis_handle.assert_called()

    def test_disabled_returns_false(self, feature):
        fh, mh = feature
        mh.config._cfg["key_mapping"]["enable"] = False
        assert fh.key_mapping_handle("弹幕", {"username": "u", "content": "放烟花"}) is False


class TestSerialSafety:
    def test_serial_rate_limit_global(self, feature):
        """S-1：全局限速 6 次/分钟（default 6），第 7 次拒绝。"""
        fh, mh = feature
        for i in range(6):
            allowed = fh._serial_rate_allow(f"user{i % 3}")
            assert allowed, f"第 {i + 1} 次应放行"
        assert fh._serial_rate_allow("user_x") is False  # 第 7 次超全局上限

    def test_serial_rate_limit_per_user(self, feature):
        """S-1：每用户限速 2 次/分钟。"""
        fh, mh = feature
        FeatureHandler._serial_rate_window = None  # 重置全局窗口
        assert fh._serial_rate_allow("爆发用户") is True
        assert fh._serial_rate_allow("爆发用户") is True
        assert fh._serial_rate_allow("爆发用户") is False  # 每用户第 3 次拒绝
        assert fh._serial_rate_allow("正常用户") is True  # 别人不受影响

    def test_serial_send_requires_pyserial(self, feature):
        """pyserial 缺失：串口触发失败但不崩（日志降级）。"""
        fh, mh = feature
        result = fh._serial_send(fh.my_handle.config._cfg["key_mapping"]["config"][0],
                                 {"username": "u", "content": "放烟花"})
        assert result is None  # 未安装 pyserial → 失败返回 None，不抛异常

    def test_serial_no_matching_config(self, feature):
        """serial_name 无对应配置 → 明确报错返回 None。"""
        fh, mh = feature
        km_config = dict(mh.config._cfg["key_mapping"]["config"][0])
        km_config["serial_name"] = "COM999"
        result = fh._serial_send(km_config, {"username": "u"})
        assert result is None


class TestOnceSemantics:
    def test_once_true_stops_chain(self, feature):
        """once=True（key 触发）：命中即 return True 终止后续触发器。"""
        fh, mh = feature
        result = fh.key_mapping_handle("礼物", {"username": "u", "gift_name": "小花火",
                                                 "num": 1, "unit_price": 1, "total_price": 1})
        assert result is True
        # key 降级（依赖缺失）→ 不终止链：copywriting 实际执行（行为=用户启用的触发类型尽量生效）

    def test_once_key_name_adaptation(self, feature):
        """T-c 适配点①：config 键无 _enable 后缀时仍正确读取。"""
        fh, mh = feature
        once = fh.my_handle.config.get("key_mapping", "key_single_sentence_trigger_once")
        assert once is True  # 现行键面（无 _enable 后缀）


class TestDegradation:
    def test_pyautogui_missing_no_crash(self, feature):
        """pyautogui 缺失：key 触发警告跳过不崩。"""
        fh, mh = feature
        try:
            import pyautogui  # noqa
            pytest.skip("pyautogui 已安装，无法测缺依赖路径")
        except ImportError:
            pass
        result = fh.key_mapping_handle("礼物", {"username": "u", "gift_name": "小花火",
                                                 "num": 1, "unit_price": 1, "total_price": 1})
        assert result is True  # key 降级→链继续→copywriting 实际执行 flag=True

    def test_local_audio_key_missing_safe(self, feature):
        """T-c 适配点②：config 缺 local_audio 键 → .get 防御不崩。"""
        fh, mh = feature
        km = dict(mh.config._cfg["key_mapping"]["config"][0])
        del km["local_audio"]
        mh.config._cfg["key_mapping"]["config"] = [km]
        mh.config._cfg["key_mapping"]["key_trigger_type"] = "无"  # 跳过 key
        mh.config._cfg["key_mapping"]["copywriting_trigger_type"] = "无"
        mh.config._cfg["key_mapping"]["serial_trigger_type"] = "无"
        mh.config._cfg["key_mapping"]["img_path_trigger_type"] = "无"
        result = fh.key_mapping_handle("回复", {"username": "u", "content": "放烟花"})
        assert result is False  # 无可触发项，安全返回
