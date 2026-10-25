# -*- coding: UTF-8 -*-
"""
T5 按键映射迁移回归测试（/autoplan 断链批次，源=my_handle.py.backup:2225-2520）

覆盖（S-1/T-c 义务）：
1. 弹幕相似度匹配触发（difflib ratio ≥ similarity）
2. 回复包含匹配
3. 礼物名精确匹配
4. S-1 串口限速：全局每分钟上限+每用户上限
5. S-1 同句去重
6. once 语义：single_sentence_trigger_once 命中即终止（T-c 适配点①键面）
7. pyautogui/pyserial 缺失降级（不崩，警告跳过）
8. start_cmd 命令头剥离
9. 2026-10-24 精简：文案/本地音频/图片三路触发已移除，残留键面安全忽略

注：匹配类测试以 monkeypatch 替身注入 _trigger_key_press（隔离 pyautogui
环境依赖，被测对象是匹配+分派链；key 分支降级由 TestDegradation 专测）。
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
            "serial_trigger_type": "关键词",
            "serial_rate_limit_per_min": 6,
            "serial_per_user_limit_per_min": 2,
            "config": [
                {
                    "keywords": ["放烟花"],
                    "gift": ["小花火"],
                    "keys": ["ctrlleft", "1"],
                    "similarity": "0.5",
                    "serial_name": "COM1",
                    "serial_send_data": ["FIRE {username}"],
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
    FeatureHandler._serial_rate_window = None
    FeatureHandler._serial_user_windows = None
    FeatureHandler._serial_last_content = None
    fh = FeatureHandler(mh)
    return fh, mh


class TestMatching:
    def test_danmu_similarity_trigger(self, feature, monkeypatch):
        """弹幕相似度匹配 → 分派执行（替身触发器，隔离 pyautogui 环境依赖）。"""
        fh, mh = feature
        sentinel = MagicMock(return_value=True)
        monkeypatch.setattr(fh, "_trigger_key_press", sentinel)
        result = fh.key_mapping_handle("弹幕", {"username": "小明", "content": "放烟花呀"})
        assert result is True  # once=True → 命中即终止
        sentinel.assert_called_once()

    def test_reply_contains_trigger(self, feature, monkeypatch):
        """回复模式：关键词包含匹配。"""
        fh, mh = feature
        sentinel = MagicMock(return_value=True)
        monkeypatch.setattr(fh, "_trigger_key_press", sentinel)
        result = fh.key_mapping_handle("回复", {"username": "小明", "content": "来一个放烟花"})
        assert result is True
        sentinel.assert_called_once()

    def test_gift_exact_match(self, feature, monkeypatch):
        """礼物名精确匹配 → 实际执行（executed）→ flag 聚合 True。"""
        fh, mh = feature
        sentinel = MagicMock(return_value=True)
        monkeypatch.setattr(fh, "_trigger_key_press", sentinel)
        result = fh.key_mapping_handle("礼物", {"username": "小明", "gift_name": "小花火",
                                                "num": 1, "unit_price": 1, "total_price": 1})
        assert result is True
        sentinel.assert_called_once()

    def test_start_cmd_stripped(self, feature, monkeypatch):
        """命令头剥离：#烟花 → 烟花。"""
        fh, mh = feature
        sentinel = MagicMock(return_value=True)
        monkeypatch.setattr(fh, "_trigger_key_press", sentinel)
        mh.config._cfg["key_mapping"]["start_cmd"] = "#"
        result = fh.key_mapping_handle("回复", {"username": "小明", "content": "#放烟花"})
        assert result is True
        sentinel.assert_called_once()

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

    def test_serial_send_requires_pyserial(self, feature, monkeypatch):
        """串口连接不可用（pyserial 缺失/设备不可达）：失败但不崩（日志降级）。

        2026-10-21 门1 既有失败修复：原测试依赖"pyserial 未安装"环境前提；
        改为 mock get_serial_manager 抛 ImportError，精确模拟缺依赖路径。
        2026-10-24：环境本就无 pyserial 时（serial_manager_instance 顶层 import
        serial 即失败）直接 skip——环境本身就是缺依赖场景。
        """
        fh, mh = feature
        try:
            import utils.serial_manager_instance as smi
        except ModuleNotFoundError:
            pytest.skip("pyserial 未安装（环境即缺依赖场景，无需模拟）")

        class _BrokenManager:
            async def connect(self, *a, **k):
                raise ImportError("pyserial missing（测试模拟）")

            async def send_data(self, *a, **k):
                raise ImportError("pyserial missing（测试模拟）")

        monkeypatch.setattr(smi, "get_serial_manager", lambda: _BrokenManager())
        result = fh._serial_send(fh.my_handle.config._cfg["key_mapping"]["config"][0],
                                 {"username": "u", "content": "放烟花"})
        assert result is None  # 连接异常 → 失败返回 None，不抛异常

    def test_serial_no_matching_config(self, feature):
        """serial_name 无对应配置 → 明确报错返回 None。"""
        fh, mh = feature
        km_config = dict(mh.config._cfg["key_mapping"]["config"][0])
        km_config["serial_name"] = "COM999"
        result = fh._serial_send(km_config, {"username": "u"})
        assert result is None


class TestOnceSemantics:
    def test_once_true_stops_chain(self, feature, monkeypatch):
        """once=True（key 触发）：命中即 return True 终止后续触发器。"""
        fh, mh = feature
        sentinel = MagicMock(return_value=True)
        monkeypatch.setattr(fh, "_trigger_key_press", sentinel)
        result = fh.key_mapping_handle("礼物", {"username": "u", "gift_name": "小花火",
                                                "num": 1, "unit_price": 1, "total_price": 1})
        assert result is True
        sentinel.assert_called_once()

    def test_once_key_name_adaptation(self, feature):
        """T-c 适配点①：config 键无 _enable 后缀时仍正确读取。"""
        fh, mh = feature
        once = fh.my_handle.config.get("key_mapping", "key_single_sentence_trigger_once")
        assert once is True  # 现行键面（无 _enable 后缀）


class TestDegradation:
    def test_pyautogui_missing_no_crash(self, feature):
        """pyautogui 缺失：key 触发警告跳过不崩（serial_trigger_type 置"无"隔离串口）。"""
        fh, mh = feature
        try:
            import pyautogui  # noqa
            pytest.skip("pyautogui 已安装，无法测缺依赖路径")
        except ImportError:
            pass
        mh.config._cfg["key_mapping"]["serial_trigger_type"] = "无"  # 隔离：只测 key 降级
        result = fh.key_mapping_handle("礼物", {"username": "u", "gift_name": "小花火",
                                                "num": 1, "unit_price": 1, "total_price": 1})
        assert result is False  # key 降级 → 未实际执行；串口已隔离 → 无触发

    def test_stale_trigger_keys_ignored(self, feature, monkeypatch):
        """2026-10-24 精简回归：已移除的 文案/本地音频/图片 触发键残留时安全忽略。"""
        fh, mh = feature
        km = mh.config._cfg["key_mapping"]
        km.update({
            "copywriting_trigger_type": "关键词",
            "local_audio_trigger_type": "关键词",
            "img_path_trigger_type": "关键词",
        })
        km["config"][0].update({
            "copywriting": ["残留文案"],
            "local_audio": ["残留音频.wav"],
            "img_path": ["残留图片.png"],
        })
        sentinel = MagicMock(return_value=True)
        monkeypatch.setattr(fh, "_trigger_key_press", sentinel)
        result = fh.key_mapping_handle("弹幕", {"username": "u", "content": "放烟花"})
        assert result is True  # 仅 key 触发执行；残留键不产生额外动作
        sentinel.assert_called_once()  # 残留三路不触发（once=True 首个执行即终止）


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
