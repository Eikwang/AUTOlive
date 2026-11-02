# -*- coding: UTF-8 -*-
"""
答谢功能审查守卫（2026-10-31 答谢审查批次）

审查结论落实：
1. 三通道（gift/entrance/follow）周期触发退役（用户裁决：系统已有限入/去重/
   优先级队列）——timer_handler 三分支、task_data 缓冲、UI 周期区、config
   periodic_trigger 子节、settings 迁移逻辑全链删除 + _RETIRED_CONFIG_KEYS 登记；
   替代链路=即时分发（_thanks_dispatch 直通 + E-2 每分钟限流兜底）。
2. 保留机制回归（既有 test_t6_thanks_produce 锁定，本文件补退役与接线锚）：
   - 开关/文案/随机选取（random=true 随机、false 顺序游标）
   - 最低礼物价格门槛（thanks.lowest_price，total_price>=门槛才答谢）
   - 变量 {username}/{gift_name}/{gift_num}/{total_price}（dynamic_variable_replacement）
   - 编排接管短路（choreo_takeover 跳过答谢）与 extra_fields 注入（O6/O7）
   - 助播路由（assistant_route(type2)，生成时定 type）
"""

import json
import os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


class TestPeriodicTriggerRetired:
    """三通道周期触发退役全链守卫。"""

    def test_timer_branches_removed(self):
        src = _src("utils/handlers/timer_handler.py")
        assert '"thanks"' not in src
        assert "周期触发已退役" in src

    def test_buffer_removed(self):
        src = _src("utils/my_handle.py")
        assert '"thanks"' not in src

    def test_ui_controls_removed(self):
        src = _src("frontend/ui/tabs/thanks.py")
        assert "periodic_trigger" not in src
        assert "触发周期" not in src and "触发次数" not in src

    def test_template_clean_and_retired_registered(self):
        example = json.loads(_src("config.example.json"))
        for t in ("entrance", "gift", "follow"):
            assert "periodic_trigger" not in example["thanks"].get(t, {})
        src = _src("frontend/main.py")
        for t in ("entrance", "gift", "follow"):
            assert f'("thanks", "{t}", "periodic_trigger")' in src

    def test_settings_migration_removed(self):
        src = _src("frontend/config/settings.py")
        assert "_th_node" not in src
        assert "合并迁移逻辑随之删除" in src


class TestImmediateDispatchWiring:
    """即时分发接线（周期退役后唯一通道）。"""

    def _make_handler(self):
        """复用 t6 桩口径（答谢三通道即时分发，KeywordResult 空 rec）。"""
        from unittest.mock import MagicMock
        from utils.handlers.event_handler import EventHandler
        from utils.keyword_hub import KeywordResult
        from tests.test_t6_thanks_produce import make_config  # 复用配置桩
        mh = MagicMock()
        mh.config = make_config()
        mh.db = MagicMock()
        mh.integral_handle.return_value = False
        mh.keyword_recognize.return_value = KeywordResult(source="gift")
        mh.task_data = {"local_qa": {"data": [], "time": 0}}
        eh = EventHandler(mh)
        return eh, mh

    def test_dispatch_no_periodic_branch(self):
        """_thanks_dispatch 纯即时+E-2 限流（无缓冲分支）。"""
        src = _src("utils/handlers/event_handler.py")
        seg = src.split("def _thanks_dispatch")[1].split("def _thanks_generate")[0]
        assert "periodic_trigger" not in seg
        assert "答谢即时分发" in src
        assert "rate_limit_per_min" in seg
        assert "self.my_handle.task_data" not in seg

    def test_dispatch_immediate_synthesis(self):
        """开启/关闭周期键行为一致：全部即时合成（config 残留键零消费）。"""
        eh, mh = self._make_handler()
        mh.config._cfg.setdefault("thanks", {}).setdefault("gift", {})["periodic_trigger"] = {"enable": True}
        eh.gift_handle({"username": "用户A", "gift_name": "小火箭", "gift_num": 1,
                        "total_price": 10, "unit_price": 10})
        mh.audio_synthesis_handle.assert_called_once()  # 周期键不再缓冲

    def test_variable_params_wired(self):
        """礼物四变量接线：gift_num/total_price/unit_price 传入变量替换。"""
        src = _src("utils/handlers/event_handler.py")
        seg = src.split("def gift_handle")[1].split("def entrance_handle")[0]
        assert '"gift_name": gift_name' in seg
        assert '"gift_num": gift_num' in seg
        assert '"total_price": total_price' in seg
        # 兼容取值（弹幕链 num / webui gift_num 双形态）
        assert 'data.get("gift_num", data.get("num", 1))' in seg

    def test_lowest_price_gate_before_generate(self):
        """最低礼物价格门槛在答谢生成之前（低于门槛不触发答谢）。"""
        src = _src("utils/handlers/event_handler.py")
        seg = src.split("def gift_handle")[1].split("def entrance_handle")[0]
        assert 'lowest_price' in seg
        assert 'float(total_price or 0) >= lowest_price' in seg

    def test_copy_selection_preserved(self):
        """随机/顺序双模式保留（<type2>_random + 游标）。"""
        src = _src("utils/handlers/event_handler.py")
        assert 'config.get("thanks", f"{type2}_random")' in src
        assert "_copy_cursors" in src

    def test_choreo_takeover_short_circuit_preserved(self):
        """编排接管短路在答谢门槛之前（接管=true 跳过 _thanks_generate）。"""
        src = _src("utils/handlers/event_handler.py")
        seg = src.split("def gift_handle")[1].split("def entrance_handle")[0]
        assert "if not rec.choreo_takeover" in seg
