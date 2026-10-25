# -*- coding: UTF-8 -*-
"""
配置组增删重构守卫（2026-10-24 批次）

覆盖三点：
1. auto_save 基建支持 list 下标键（descend_container/_container_write/_container_get）
2. key_mapping 配置组：增删按钮 + 9 字段 auto_save 落盘绑定（源级）
3. serial 配置组：配置驱动渲染 + 字段落盘 + 连接测试按钮接线（源级）
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _src(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


class FakeComponent:
    """auto_bind 行为级测试替身（on_value_change/on 捕获）。"""
    label = "测试组件"

    def __init__(self, value=None):
        self.value = value
        self._change_cb = None

    def on_value_change(self, cb):
        self._change_cb = cb

    def on(self, *args, **kwargs):
        pass


class TestDescendContainer:
    """基建：list 下标键支持。"""

    def test_dict_path_unchanged(self):
        from frontend.utils.config_auto_save import descend_container
        cfg = {"a": {"b": {}}}
        node = descend_container(cfg, ("a",))
        assert node is cfg["a"]
        # 缺失中间键自动补 {}（原行为保持）
        node2 = descend_container(cfg, ("x", "y"))
        assert cfg["x"]["y"] == {}

    def test_list_index_path(self):
        from frontend.utils.config_auto_save import descend_container
        cfg = {"key_mapping": {"config": [{"keywords": []}, {"keywords": []}]}}
        node = descend_container(cfg, ("key_mapping", "config", "1"))
        assert node is cfg["key_mapping"]["config"][1]

    def test_container_write_and_get_list_and_dict(self):
        from frontend.utils.config_auto_save import _container_write, _container_get
        lst = [{"v": 1}]
        _container_write(lst, "0", {"v": 2})
        assert lst[0] == {"v": 2}
        assert _container_get(lst, "0") == {"v": 2}
        assert _container_get(lst, "9") is None  # 越界安全读
        d = {}
        _container_write(d, "k", 3)
        assert d["k"] == 3 and _container_get(d, "k") == 3

    def test_auto_bind_writes_through_list_index(self):
        """行为级：keys 含 list 下标时，值变更写入列表元素并回调落盘。"""
        from frontend.utils import config_auto_save as cas
        captured = {}

        def fake_set_cb(*keys, value, _notify=True):
            captured["keys"] = keys
            captured["value"] = value
            return True

        cfg = {"key_mapping": {"config": [{"keywords": ["旧"]}]}}
        comp = FakeComponent()
        cas.auto_bind(comp, cfg, ("key_mapping", "config", "0", "keywords"),
                      fake_set_cb, tab_key="test", split_lines=True)
        nl = chr(10)
        comp._change_cb(type("V", (), {"value": f"动作1{nl}动作2{nl}"})())
        assert cfg["key_mapping"]["config"][0]["keywords"] == ["动作1", "动作2"]
        assert captured["keys"] == ("key_mapping", "config", "0", "keywords")
        assert captured["value"] == ["动作1", "动作2"]

    def test_auto_bind_similarity_type_coerce_through_list(self):
        """数字保型穿过 list 下标：float 旧值保型，int("0.8") 失败保留新值（_coerce_like 契约）。"""
        from frontend.utils import config_auto_save as cas

        cfg = {"key_mapping": {"config": [{"similarity": 0.9}]}}
        comp = FakeComponent()
        cas.auto_bind(comp, cfg, ("key_mapping", "config", "0", "similarity"), None)
        comp._change_cb(type("V", (), {"value": "0.8"})())
        assert cfg["key_mapping"]["config"][0]["similarity"] == 0.8  # float 旧值 → float("0.8") 保型


class TestKeyMappingGroupTab:
    """按键映射页：配置组增删 + 字段落盘（源级守卫）。"""

    def test_add_delete_buttons_exist(self):
        src = _src("frontend/ui/tabs/key_mapping.py")
        assert "增加配置组" in src
        assert "删除本组" in src
        assert "_add_group" in src and "_delete_group" in src
        assert "_render_groups" in src and "groups_box.clear()" in src

    def test_group_fields_bound_with_list_index_keys(self):
        """6 字段全部经 auto_save 绑定 ("key_mapping","config",下标,字段)。"""
        src = _src("frontend/ui/tabs/key_mapping.py")
        fields = ["keywords", "gift", "keys", "similarity",
                  "serial_name", "serial_send_data"]
        for field in fields:
            assert f'"config", str(index), "{field}")' in src, f"缺字段绑定: {field}"
        # 文案类 textarea 走 split_lines（防「每个字符一条」回归）
        assert src.count('split_lines=True') == 4

    def test_add_del_persist_whole_list(self):
        src = _src("frontend/ui/tabs/key_mapping.py")
        assert 'set_config_callback("key_mapping", "config", value=_get_groups())' in src

    def test_top_layout_2x2(self):
        """顶部参数 2×2：命令前缀|捕获类型 / 按键触发类型|串口触发类型。

        ui.grid(columns=2) 共 3 处：顶部参数卡 + 组内字段网格 + 组容器双列。
        """
        src = _src("frontend/ui/tabs/key_mapping.py")
        assert src.count("ui.grid(columns=2)") == 3
        for label in ["命令前缀", "捕获类型", "按键触发类型", "串口触发类型"]:
            assert f"label='{label}'" in src, f"缺顶部参数: {label}"

    def test_removed_triggers_absent_frontend(self):
        """2026-10-24 精简：文案/本地音频/图片 三路触发不再出现在前端。"""
        src = _src("frontend/ui/tabs/key_mapping.py")
        for gone in ["文案触发类型", "本地音频触发类型", "图片触发类型",
                     "copywriting_trigger_type", "local_audio_trigger_type",
                     "img_path_trigger_type", "copywriting", "local_audio", "img_path"]:
            assert gone not in src, f"残留: {gone}"

    def test_removed_triggers_absent_backend(self):
        """2026-10-24 精简：后端三路触发方法与分派项已移除。"""
        src = _src("utils/handlers/feature_handler.py")
        for gone in ["_trigger_copywriting", "_trigger_local_audio", "_trigger_img",
                     '"copywriting_trigger_type"', '"local_audio_trigger_type"',
                     '"img_path_trigger_type"']:
            assert gone not in src, f"残留: {gone}"
        # 分派链仅剩按键+串口（触发器列表收拢为类常量 _KEYMAP_TRIGGERS 单点定义；
        # 2026-10-25 识别中枢批次：礼物/弹幕型自匹配与中枢命中组四处分派循环共用）
        assert src.count('["key_trigger_type", "serial_trigger_type"]') == 1
        assert "_KEYMAP_TRIGGERS" in src

    def test_group_card_two_column_layout(self):
        """配置组卡片双列布局：组容器为 grid(columns=2)。"""
        src = _src("frontend/ui/tabs/key_mapping.py")
        assert "groups_box = ui.grid(columns=2)" in src
        # 同类输入框等高：textarea 统一 input-style 高度
        assert "input-style=\"height:" in src


class TestSerialGroupTab:
    """串口页：配置驱动 + 字段落盘 + 按钮接线（源级守卫）。"""

    def test_group_fields_bound_with_list_index_keys(self):
        src = _src("frontend/ui/tabs/serial.py")
        for field in ["serial_name", "baudrate", "serial_data_type"]:
            assert f'"config", str(index), "{field}")' in src, f"缺字段绑定: {field}"

    def test_buttons_wired_to_serial_manager(self):
        """4 个操作按钮接线到本进程 SerialManager 单例（原死按钮守卫）。"""
        src = _src("frontend/ui/tabs/serial.py")
        for btn in ["刷新串口", "打开串口", "关闭串口", "发送"]:
            assert f"'{btn}'" in src
        for fn in ["_refresh_ports", "_open_port", "_close_port", "_send_port"]:
            assert f"async def {fn}" in src
        assert "get_serial_manager" in src
        assert ".connect(" in src and ".disconnect(" in src and ".send_data(" in src

    def test_no_legacy_ui_only_globals(self):
        """旧实现 UI-only 全局（增删不落盘的病根）不回归。"""
        src = _src("frontend/ui/tabs/serial.py")
        assert "serial_config_var" not in src
        assert "_serial_config_add" not in src and "_serial_config_del" not in src


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
