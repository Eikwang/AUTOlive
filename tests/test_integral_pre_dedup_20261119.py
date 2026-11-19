# -*- coding: UTF-8 -*-
"""
积分记录前移守卫测试（2026-11-19 积分记录前移批次，用户裁决）

修复缺陷：积分记分原挂在限入（重复弹幕过滤/遗忘窗口）之后——限入时间较长时，
被过滤的入场/关注/礼物/签到事件漏记积分。目标设计：
- 记分：直接与弹幕监听对接——timer_handler.process_data 黑名单之后、去重/遗忘
  窗口之前调 integral_score_entry（异步 daemon 线程写库），监听到事件即记分，
  不受限入/去重/遗忘窗口影响
- 播报：仍在限入去重之后、与答谢/念弹幕同层级——只有通过限入（会被正常答谢/
  念弹幕处理）的事件才播报积分获得文案（挂载点消费 IntegralScoreContext），
  避免每条积分记录都播报

覆盖义务（任务书）：
1. 限入过滤掉的事件仍记分（is_data_repeat_in_limited_time 丢弃 → 积分已写库）
2. 通过限入才播报（丢弃事件 ctx 不播报；通过事件答谢链挂载点播报）
3. broadcast 开关三态在播报挂载点生效（开=播报+答谢抑制；关/缺省=零积分播报+答谢照旧）
4. 窗口限频在记分段生效（2026-11-19 积分窗口语义批次：签到/入场=自然日固定窗口、
   关注=一生一次；ctx.blocked，拦截=不写库不播报）
5. 异步记分不阻塞入口（写库进行中 process_data 已返回）
6. 防重（同一消息只记一次；限入后挂载点只消费不重复记分）
7. 黑名单用户不记分（名单内「不进行任何处理」含积分）；积分总开关关不挂 ctx

纪律：pytest tmp_path 独立测试 DB 文件（同 test_integral_broadcast_20261117.py）。
"""

import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.common import Common
from utils.db import SQLiteDB
from utils.keyword_hub import KeywordResult
import utils.handlers.timer_handler as timer_handler_module
from utils.handlers.feature_handler import FeatureHandler
from utils.handlers.timer_handler import TimerHandler

# 与 utils/my_handle.py 建表 DDL 一致（新装形态，含 last_entrance_ts + last_follow_ts）
FRESH_INTEGRAL_DDL = '''
CREATE TABLE IF NOT EXISTS integral (
    platform TEXT NOT NULL,
    username TEXT NOT NULL,
    uid TEXT NOT NULL,
    integral INT NOT NULL,
    view_num INT NOT NULL,
    sign_num INT NOT NULL,
    last_sign_ts DATETIME NOT NULL,
    total_price INT NOT NULL,
    last_ts DATETIME NOT NULL,
    last_entrance_ts DATETIME,
    last_follow_ts DATETIME,
    UNIQUE(platform, username, uid)
)
'''

THANKS_ENTRANCE = "答谢标记欢迎{username}入场"


class _StubConfig:
    """模拟 Config.get(*keys) 的嵌套取值语义（缺键返回 None）。"""

    def __init__(self, mapping):
        self.mapping = mapping

    def get(self, *keys):
        result = self.mapping
        for key in keys:
            if result is None or not isinstance(result, dict):
                return None
            result = result.get(key)
        return result


def base_config():
    """镜像 config.json 最小配置：积分 enable 全开（broadcast 缺省=关）、过滤
    遗忘窗口 10s（测试进程内不触发窗口末补发）。"""
    return {
        "integral": {
            "enable": True,
            "sign": {"enable": True, "cmd": ["签到", "打卡"], "get_integral": 1},
            "gift": {"enable": True, "get_integral_proportion": 10.0},
            "entrance": {"enable": True, "get_integral": 1},
            "follow": {"enable": True, "get_integral": 1},
            "crud": {"query": {"enable": True, "cmd": ["我的积分"],
                               "copywriting": ["{username}，您当前的积分是{integral}"]}},
        },
        "filter": {
            "comment_forget_duration": 0.2,
            "gift_forget_duration": 0.2,
            "entrance_forget_duration": 0.2,
            "follow_forget_duration": 0.2,
        },
        "audio_synthesis_type": "edge-tts",
        "edge-tts": {},
        "filter_copy": {},
    }


class _PreDedupMyHandle:
    """消息入口/过滤链/限入后挂载点集成测试替身。

    - feature_handler：真实 FeatureHandler（记分段 + IntegralScoreContext）
    - is_data_repeat_in_limited_time：可控限入判定（True=限入丢弃）
    - comment/entrance handler 替身：与 comment_handler/event_handler 挂载点同语义
      （消费 ctx 播报/终结），用于验证「限入后链只消费不记分」
    """

    def __init__(self, config_mapping, db):
        self.config = _StubConfig(config_mapping)
        self.db = db
        self.common = Common()
        self.replies = []
        self.data_lock = threading.Lock()
        self.timers = {}
        self.is_handleing = 0
        self.live_data = {}
        # 限入判定可控开关：True=重复数据丢弃（模拟限入时间较长全过滤）
        self.dedup_drop = False
        # 黑名单可控开关：True=名单内用户（T7「不进行任何处理」）
        self.blacklist_hit = False
        self.feature_handler = FeatureHandler(self)
        self.dispatched = []          # (type, data)：通过限入到达限入后链的事件
        self.announce_results = []    # (module, announced)：挂载点消费结果
        self.sign_terminated = []     # 签到终结弹幕（积分链消费，不进 LLM）

    # ---- 过滤链依赖（timer_handler.process_data） ----

    def blacklist_handle(self, data):
        return self.blacklist_hit

    def is_data_repeat_in_limited_time(self, data, timer_flag):
        return self.dedup_drop

    # ---- 限入后链替身（与真实 handler 挂载点同语义：只消费 ctx，不重复记分） ----

    def _consume_announce(self, module, data):
        """event_handler 答谢链挂载点同语义：播报发生=抑制答谢（积分播报优先）；
        未发生=答谢照旧。"""
        announced = self.feature_handler.integral_announce_if_scored(data, module)
        self.announce_results.append((module, announced))
        if not announced:
            self.replies.append({"type": module, "content": f"答谢标记{data.get('username')}"})
        return announced

    def comment_handle(self, data):
        self.dispatched.append(("comment", data))
        rec = KeywordResult(source="comment")
        rec.integral_sign = True  # 中枢已判定签到命中（入口预匹配同源词表）
        if rec.integral_sign and self.feature_handler.integral_sign_consume(data):
            self.sign_terminated.append(data)
            self.announce_results.append(("sign", True))  # 消费完成标记（等待竞态同步）
            return
        self.replies.append({"type": "comment", "content": "LLM回复"})

    def gift_handle(self, data):
        self.dispatched.append(("gift", data))
        self._consume_announce("gift", data)

    def entrance_handle(self, data):
        self.dispatched.append(("entrance", data))
        self._consume_announce("entrance", data)

    def follow_handle(self, data):
        self.dispatched.append(("follow", data))
        self._consume_announce("follow", data)

    # ---- 录制 ----

    def audio_synthesis_handle(self, message):
        self.replies.append(message)

    def webui_show_chat_log_callback(self, data_type, data, resp_content):
        self.replies.append({"type": "webui_log", "data_type": data_type,
                             "content": resp_content})


@pytest.fixture
def db(tmp_path):
    database = SQLiteDB(str(tmp_path / "integral_pre_dedup.db"))
    assert database.execute(FRESH_INTEGRAL_DDL)
    yield database
    database.close()


@pytest.fixture
def fake(db):
    return _PreDedupMyHandle(base_config(), db)


@pytest.fixture
def th(fake):
    return TimerHandler(fake)


# 注：本文件无写库失败重试路径用例（_BrokenWriteDB 不在此处），不再全局 patch
# time.sleep——monkeypatch.setattr(feature_handler_module.time, "sleep", ...) 修改
# 的是 time 模块本身（全局生效），会使 db.py 清理线程的 60s sleep 变 no-op 空转
# 抢占 GIL，拖慢整个测试会话。


@pytest.fixture(autouse=True)
def isolate_global_side_effects(monkeypatch):
    """隔离 process_data 的全局副作用（最近互动用户名/闲时计时）。"""
    monkeypatch.setattr(timer_handler_module.my_global,
                        "add_username_to_last_username_list", lambda *_: None)
    monkeypatch.setattr(timer_handler_module.my_global,
                        "idle_time_auto_clear", lambda *_: None)


def wait_ctx(data, timeout=5.0):
    """等入口异步记分落定（IntegralScoreContext.ready）。"""
    ctx = data.get("integral_gain_ctx")
    assert ctx is not None, "入口应挂载 IntegralScoreContext"
    assert ctx.wait(timeout), "入口记分应在超时前完成（写库落定）"
    return ctx


def tts_contents(fake):
    return [r.get("content") for r in fake.replies
            if isinstance(r, dict) and r.get("type") != "webui_log"]


# ============ 1. 限入过滤掉的事件仍记分 ============

class TestPreDedupScoring:
    def test_dedup_dropped_entrance_still_scored(self, th, fake, db):
        """限入时间较长场景：入场事件被 is_data_repeat_in_limited_time 丢弃——
        限入后链（答谢/挂载点）未到达，但积分已在入口（黑名单后/去重前）写库。"""
        fake.dedup_drop = True
        data = {"platform": "dy", "username": "小明"}
        th.process_data(data, "entrance")
        ctx = wait_ctx(data)

        assert ctx.scored is True, "被限入过滤的事件仍记分（ctx.scored=True）"
        assert db.fetch_one("SELECT integral, view_num FROM integral") == (1, 1), \
            "限入丢弃事件的积分已写库（修复漏记）"
        assert fake.dispatched == [], "被限入过滤的事件不进入限入后链"

    def test_dedup_dropped_sign_still_scored(self, th, fake, db):
        """签到弹幕被限入丢弃：积分已写库（sign_num 自增），弹幕未进 comment 链。"""
        fake.dedup_drop = True
        data = {"platform": "dy", "username": "小明", "content": "签到"}
        th.process_data(data, "comment")
        ctx = wait_ctx(data)

        assert ctx.module == "sign" and ctx.scored is True, "签到词命中即入口记分"
        assert db.fetch_one("SELECT integral, sign_num FROM integral") == (1, 1)
        assert fake.dispatched == [], "被过滤弹幕不进入 comment 链"

    def test_forget_window_merged_event_still_scored(self, th, fake, db):
        """遗忘窗口合并形态：首条入场立即分发，窗口内第二条被合并——第二条事件
        同样已在入口记分（限频拦截在记分段，不重复写库）。分发/挂载消费时序由
        其它用例覆盖，此处只守卫记分段行为（0.35s 等窗口 Timer 补发落定，避免
        db 关闭后的残留线程访问）。"""
        first = {"platform": "dy", "username": "小明"}
        th.process_data(first, "entrance")
        wait_ctx(first)

        merged = {"platform": "dy", "username": "小明"}
        th.process_data(merged, "entrance")
        ctx = wait_ctx(merged)

        assert ctx.blocked is True, "同用户同自然日窗口在记分段拦截（第二场入场不计分）"
        assert ctx.scored is False, "拦截=不计分不写库"
        assert db.fetch_one("SELECT integral, view_num FROM integral") == (1, 1), \
            "窗口合并事件不重复写库"
        assert len(db.fetch_all("SELECT * FROM integral")) == 1, "单行不重复"
        time.sleep(0.35)  # 等遗忘窗口 Timer（0.2s）补发与挂载消费落定


# ============ 2. 通过限入才播报（丢弃事件 ctx 不播报） ============

class TestAnnounceOnlyAfterDedup:
    def test_dropped_event_no_announce_but_scored(self, th, fake, db):
        """限入丢弃事件：记分发生但零播报（挂载点未到达——避免每条积分记录都
        播报），写库照常。"""
        fake.dedup_drop = True
        data = {"platform": "dy", "username": "小明"}
        th.process_data(data, "entrance")
        wait_ctx(data)

        assert fake.announce_results == [], "被过滤事件无挂载点消费（零播报）"
        assert tts_contents(fake) == []
        assert db.fetch_one("SELECT integral FROM integral") == (1,), "记分不受过滤影响"

    def test_passed_event_announces_at_mount_and_suppresses_thanks(self, th, fake, db):
        """通过限入事件：限入后链挂载点消费 ctx 播报（积分播报优先）+ 答谢抑制。"""
        data = {"platform": "dy", "username": "小明"}
        th.process_data(data, "entrance")
        ctx = wait_ctx(data)
        _wait_dispatch(fake, expect_announce=True)

        assert fake.dispatched == [("entrance", data)], "通过限入到达限入后链"
        assert fake.announce_results == [("entrance", False)], \
            "broadcast 缺省（关）=挂载点零播报，答谢照旧"
        assert ctx.scored is True
        assert db.fetch_one("SELECT integral FROM integral") == (1,)
        assert any(r.get("type") == "entrance" for r in fake.replies), "答谢照旧"

    def test_passed_sign_terminates_without_llm(self, th, fake, db):
        """通过限入的签到弹幕：comment 链消费点消费 ctx（计分成功=终结），不进
        LLM 替身回复。"""
        data = {"platform": "dy", "username": "小明", "content": "签到"}
        th.process_data(data, "comment")
        ctx = wait_ctx(data)
        _wait_dispatch(fake, expect_announce=True)

        assert fake.sign_terminated == [data], "签到弹幕被积分链消费（终结）"
        assert not any(r.get("content") == "LLM回复" for r in fake.replies), \
            "签到弹幕不流向 LLM（终结语义保持）"
        assert db.fetch_one("SELECT integral, sign_num FROM integral") == (1, 1)


def _wait_dispatch(fake, timeout=5.0, expect_announce=False):
    """等异步分发线程完成（dispatched/announce_results 录制落定）。

    expect_announce=True：等待挂载点消费结果落定（announce_results 非空）——
    dispatch 线程 append dispatched 与消费挂载点之间存在窗口，直接断言会竞态。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if expect_announce:
            if fake.announce_results:
                return
        elif fake.dispatched:
            return
        time.sleep(0.01)
    # 未分发也放行（丢弃场景由调用方断言 dispatched 为空）


# ============ 3. broadcast 开关三态在播报挂载点生效 ============

class TestBroadcastSwitchAtMount:
    def _pass_event(self, th, fake):
        data = {"platform": "dy", "username": "小明"}
        th.process_data(data, "entrance")
        wait_ctx(data)
        _wait_dispatch(fake, expect_announce=True)
        return data

    def test_broadcast_on_announces_and_suppresses_thanks(self, th, fake):
        """开=播报（内置默认文案兜底）+ 答谢抑制（挂载点返回 True）。"""
        # 档位文案优先语义：配置无 copywriting 的档位块让档位路径拿不到文案，
        # 统一门走内置默认文案兜底（2026-11-19 播报文案收敛批次：兜底文案源收敛
        # 为内置默认文案；验证兜底播报与答谢抑制）
        fake.config.mapping["integral"]["entrance"].update(
            {"broadcast": True,
             "copywriting": [{"entrance_num_interval": "0-999999999"}]})
        self._pass_event(th, fake)

        assert fake.announce_results == [("entrance", True)], "挂载点播报实际发生"
        assert "欢迎 小明 回来，积分+1" in tts_contents(fake), "内置默认文案兜底播报"
        assert not any(r.get("type") == "entrance" for r in fake.replies), "答谢被抑制"

    def test_broadcast_off_zero_announce_thanks_unchanged(self, th, fake):
        """关=零积分播报 + 答谢照旧（挂载点返回 False）。"""
        fake.config.mapping["integral"]["entrance"].update({"broadcast": False})
        self._pass_event(th, fake)

        assert fake.announce_results == [("entrance", False)], "挂载点零播报"
        assert not any("欢迎 小明 回来" in c for c in tts_contents(fake))
        assert any(r.get("type") == "entrance" for r in fake.replies), "答谢照旧"

    def test_broadcast_missing_key_zero_announce(self, th, fake):
        """缺省（键缺失=关，存量配置升级形态）=零积分播报 + 答谢照旧。"""
        assert self._pass_event(th, fake) is not None
        fake.config.mapping["integral"]["entrance"].pop("broadcast", None)
        assert fake.announce_results == [("entrance", False)], "开关缺省=关，零播报"
        assert any(r.get("type") == "entrance" for r in fake.replies), "答谢照旧"


# ============ 4. 窗口限频在记分段生效（自然日窗口/一生一次，拦截=不写库不播报） ============

class TestCooldownInScoreSegment:
    def test_second_entrance_blocked_in_score_segment(self, th, fake, db):
        """同用户同自然日窗口内第二次入场：记分段拦截（ctx.blocked，不写库），
        即使通过限入到达挂载点也零播报、答谢照旧。"""
        first = {"platform": "dy", "username": "小明"}
        th.process_data(first, "entrance")
        wait_ctx(first)
        _wait_dispatch(fake)
        assert db.fetch_one("SELECT integral, view_num FROM integral") == (1, 1)

        fake.config.mapping["integral"]["entrance"].update(
            {"broadcast": True})
        fake.dispatched.clear()
        fake.announce_results.clear()
        fake.replies.clear()

        second = {"platform": "dy", "username": "小明"}
        time.sleep(0.3)  # 等遗忘窗口（0.2s）关闭——second 作为窗口开启后的首条立即分发
        th.process_data(second, "entrance")
        ctx = wait_ctx(second)
        _wait_dispatch(fake, expect_announce=True)

        assert ctx.blocked is True and ctx.scored is False, "限频拦截在记分段（不计分）"
        assert db.fetch_one("SELECT integral, view_num FROM integral") == (1, 1), "拦截=不写库"
        assert fake.announce_results == [("entrance", False)], "拦截=零播报（挂载点按未记分处理）"
        assert any(r.get("type") == "entrance" for r in fake.replies), "答谢照旧"


# ============ 5. 异步记分不阻塞入口 ============

class TestAsyncNonBlocking:
    def test_entry_returns_before_db_write_completes(self, th, fake, db, monkeypatch):
        """入口记分异步：写库进行中 process_data 已返回（监听不被写库阻塞），
        写库最终落定（ctx.ready 置位、积分入库）。"""
        gate = threading.Event()
        release = threading.Event()
        real_execute = db.execute

        def slow_execute(sql, args=None):
            if "INSERT INTO integral" in str(sql) or "UPDATE integral" in str(sql):
                gate.set()
                release.wait(5.0)  # 模拟慢写库（写库重试不阻塞监听）
                return real_execute(sql, args)
            return real_execute(sql, args)

        monkeypatch.setattr(fake.db, "execute", slow_execute)

        data = {"platform": "dy", "username": "小明"}
        th.process_data(data, "entrance")  # 应立即返回（不等写库）

        assert gate.wait(5.0), "异步记分线程已启动并进入写库"
        ctx = data.get("integral_gain_ctx")
        assert ctx is not None
        assert not ctx.ready.is_set(), "process_data 返回时写库仍在进行（入口未被阻塞）"

        release.set()
        assert ctx.wait(5.0), "写库最终落定（挂载点可安全消费）"
        assert ctx.scored is True
        assert db.fetch_one("SELECT integral, view_num FROM integral") == (1, 1)

    def test_score_entry_runs_in_daemon_thread(self, th, fake):
        """入口记分线程为 daemon（不阻塞进程退出——监听链路异步语义）。"""
        observed = {}

        def probe(type, data, ctx):
            observed["daemon"] = threading.current_thread().daemon
            observed["name"] = threading.current_thread().name
            try:
                # 真实记分语义照常执行（probe 只观测线程属性）
                fake.feature_handler.__class__.integral_handle(
                    fake.feature_handler, type, data)
            except Exception:
                pass
            finally:
                ctx.ready.set()  # worker finally 语义保持（挂载点可消费）

        fake.feature_handler._integral_score_worker = probe
        data = {"platform": "dy", "username": "小明"}
        fake.feature_handler.integral_score_entry("entrance", data)
        ctx = wait_ctx(data)
        assert observed.get("daemon") is True, "记分线程 daemon=True"
        assert observed.get("name") == "integral-score-entrance", "记分线程具名（可观测）"
        assert ctx.scored is True


# ============ 6. 防重：同一消息只记一次 ============

class TestScoreOncePerEvent:
    def test_same_event_dict_scored_once(self, th, fake, db):
        """防重：同一事件 dict 重复进入入口（理论形态）不重复记分——已挂
        IntegralScoreContext 直接跳过（幂等）。"""
        data = {"platform": "dy", "username": "小明"}
        th.process_data(data, "entrance")
        wait_ctx(data)
        assert db.fetch_one("SELECT integral FROM integral") == (1,)

        fake.feature_handler.integral_score_entry("entrance", data)  # 重复入口调用
        assert ctx_wait_quiet(data), "重复调用不重启记分（ctx 已就绪未变）"
        assert db.fetch_one("SELECT integral FROM integral") == (1,), "单事件单次记分"

    def test_mount_consume_never_rescores(self, th, fake, db):
        """限入后挂载点只消费 ctx 播报，不重复记分（原调用点防重）。"""
        fake.config.mapping["integral"]["entrance"].update(
            {"broadcast": True})
        data = {"platform": "dy", "username": "小明"}
        th.process_data(data, "entrance")
        wait_ctx(data)
        _wait_dispatch(fake, expect_announce=True)

        # 挂载点重复消费（理论形态：答谢链多次消费同一 ctx）不产生二次写库
        announced = fake.feature_handler.integral_announce_if_scored(data, "entrance")
        assert announced is True
        row = db.fetch_one("SELECT integral, view_num FROM integral")
        assert row == (1, 1), "挂载点消费不重复记分（单行单分）"
        assert len(db.fetch_all("SELECT * FROM integral")) == 1, "不产生重复行"


def ctx_wait_quiet(data, timeout=1.0):
    ctx = data.get("integral_gain_ctx")
    return ctx is not None and ctx.wait(timeout)


# ============ 7. 黑名单/总开关：入口不记分 ============

class TestEntryGates:
    def test_blacklisted_user_not_scored(self, th, fake, db):
        """黑名单用户（T7「不进行任何处理」含积分）：入口记分插桩点位于黑名单
        之后——不挂 ctx、不写库、不进限入后链。"""
        fake.blacklist_hit = True
        data = {"platform": "dy", "username": "黑名单用户"}
        th.process_data(data, "entrance")

        assert data.get("integral_gain_ctx") is None, "黑名单用户不挂记分上下文"
        assert db.fetch_one("SELECT integral FROM integral") is None, "黑名单用户不写库"
        assert fake.dispatched == []

    def test_integral_master_off_no_ctx_no_score(self, th, fake, db):
        """积分总开关关：入口不启动记分（无 ctx）——挂载点消费=答谢照旧、零播报
        （积分手关语义一致）。"""
        fake.config.mapping["integral"]["enable"] = False
        data = {"platform": "dy", "username": "小明"}
        th.process_data(data, "entrance")
        _wait_dispatch(fake)

        assert data.get("integral_gain_ctx") is None, "积分手关不挂记分上下文"
        assert db.fetch_one("SELECT integral FROM integral") is None, "不写库"
        assert fake.announce_results == [("entrance", False)], "挂载点零播报"
        assert any(r.get("type") == "entrance" for r in fake.replies), "答谢照旧"

    def test_non_integral_type_no_ctx(self, th, fake, db):
        """非积分类型（like/room_stats）：入口不挂 ctx 不记分。"""
        for flag in ("like", "room_stats"):
            data = {"platform": "dy", "username": "小明"}
            th.process_data(data, flag)
            assert data.get("integral_gain_ctx") is None, f"{flag} 不涉及积分记分"
        assert db.fetch_one("SELECT integral FROM integral") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
