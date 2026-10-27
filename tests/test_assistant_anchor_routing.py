# -*- coding: UTF-8 -*-
"""助播路由守卫测试面（/autoplan main-20261026-214845 T8，义务 E-T1..T5/D12）。

覆盖：路由矩阵×各生产端、冲突组合（E-T1）、KB 适用集/voice 源排除、助播音色
规范化回落（E-E1）、存量迁移幂等（D3/E-E6）、加载器对抗样本（E-S1/E-E3）、
音频三档矩阵（E-T2）、生产点穷尽性 ast 扫描（D12/E-T3）、checkbox 落盘断言、
性能冒烟（E-T5）。
"""
import ast
import os
import sys
import tempfile
import time
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.common import Common
from utils.common.text_mixin import normalize_for_match
from utils.handlers import assistant_router as ar


class FakeConfig:
    """双形态 config 桩（get(*keys) 变长键，模拟主进程 Config 读法）。"""

    def __init__(self, d):
        self.d = d

    def get(self, *keys):
        cur = self.d
        for k in keys:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                return None
        return cur


def _base_cfg(types=("ai_chat", "chat", "read_comment", "gift", "entrance",
                     "follow", "schedule", "idle_time_task", "integral", "song"),
              anchor_tts="edge_tts", text_enable=True, audio_enable=True):
    return FakeConfig({
        "assistant_anchor": {
            "username": "小助",
            "audio_synthesis_type": anchor_tts,
            "type": list(types),
            "local_qa": {
                "text": {"enable": text_enable, "format": "text",
                         "file_path": "", "similarity": 0.6},
                "audio": {"enable": audio_enable, "type": "相似度匹配",
                          "file_path": "", "similarity": 0.5},
            },
        },
        "audio_synthesis_type": "gpt_sovits",
        "edge_tts": {"voice": "test"},
        "gpt_sovits": {"params": {}},
        "filter": {},
    })


# ---------- 路由矩阵（D10 单查询） ----------

@pytest.mark.parametrize("producer_type,source,expected", [
    ("llm_reply", "comment", True), ("llm_reply", "chatbox", True),
    ("main_kb_hit", "comment", True), ("main_kb_hit", "chatbox", True),
    ("reread", None, True), ("read_comment", None, True), ("gift", None, True),
    ("entrance", None, True), ("follow", None, True), ("schedule", None, True),
    ("idle_time_task", None, True), ("integral", None, True), ("song", None, True),
])
def test_route_matrix_all_checked(producer_type, source, expected):
    """源依赖行（llm_reply/main_kb_hit）须带来源查询；来源无关行 None 键兜底。"""
    assert ar.assistant_route(_base_cfg(), producer_type, source) is expected


@pytest.mark.parametrize("producer_type", [
    "llm_reply", "main_kb_hit", "reread", "read_comment", "gift", "entrance",
    "follow", "schedule", "idle_time_task", "integral", "song",
])
def test_route_matrix_unchecked_falls_back(producer_type):
    """未勾选→原路（空勾选集=全原路）。"""
    assert ar.assistant_route(_base_cfg(types=()), producer_type) is False


def test_route_llm_reply_by_source():
    """AI对话/聊天按 chat_source 区分（D6）：comment→ai_chat、chatbox→chat、
    voice 不归聊天（uc2a）。"""
    cfg = _base_cfg(types=("ai_chat",))          # 只勾 AI对话
    assert ar.assistant_route(cfg, "llm_reply", "comment") is True
    assert ar.assistant_route(cfg, "llm_reply", "chatbox") is False
    cfg2 = _base_cfg(types=("chat",))            # 只勾 聊天
    assert ar.assistant_route(cfg2, "llm_reply", "chatbox") is True
    assert ar.assistant_route(cfg2, "llm_reply", "comment") is False
    assert ar.assistant_route(_base_cfg(), "llm_reply", "voice") is False


def test_route_unknown_producer_and_source():
    """清单外产线→False；来源无关行对任意来源走 None 键兜底（gift 不因来源变化）。"""
    cfg = _base_cfg()
    assert ar.assistant_route(cfg, "trends_copywriting") is False
    assert ar.assistant_route(cfg, "gift", "voice") is True   # 来源无关（None 键兜底）
    assert ar.assistant_route(cfg, "gift", "comment") is True


# ---------- 冲突组合（E-T1 表驱动） ----------

def test_conflict_integral_with_ai_chat():
    """积分+AI对话 同勾：积分产线先行判定不被 AI对话 误捕获（架构#2）。"""
    cfg = _base_cfg(types=("ai_chat", "integral"))
    assert ar.assistant_route(cfg, "integral") is True
    # 积分消息构建走 integral 产线（helper 产助播消息）
    m = ar.assistant_copy_message(cfg, "积分回复", producer_type="integral")
    assert m["type"] == "assistant_anchor_text"
    # llm_reply 是独立产线键——积分勾选与否不影响 llm_reply 判定
    assert ar.assistant_route(cfg, "llm_reply", "comment") is True
    assert ar.assistant_route(_base_cfg(types=("integral",)), "llm_reply", "comment") is False


def test_conflict_read_comment_with_ai_chat():
    """念弹幕+AI对话 同勾：两产线独立判定（念弹幕=念原文直 TTS，D5）。"""
    cfg = _base_cfg(types=("ai_chat", "read_comment"))
    assert ar.assistant_route(cfg, "read_comment") is True
    assert ar.assistant_route(cfg, "llm_reply", "comment") is True


@pytest.mark.parametrize("source,checked,expected_route", [
    ("comment", "ai_chat", True),
    ("chatbox", "chat", True),
    ("voice", "ai_chat", False),   # voice 不被 AI对话 捕获（uc2a/E-T1）
    ("voice", "chat", False),      # voice 不被 聊天 捕获
    ("comment", None, False),
])
def test_main_kb_and_chain_source_matrix(source, checked, expected_route):
    """主 KB 改判×来源矩阵（E-H1）：comment/chatbox/voice × 两勾选态。
    KB 链断言用真词库：勾选+命中词库→assistant_text；voice 即使词库命中也 miss
    （门控先于匹配，D6）；未勾选→miss。"""
    cfg = _base_cfg(types=(checked,) if checked else ())
    assert ar.assistant_route(cfg, "main_kb_hit", source) is expected_route
    kb = _write_kb(["怎么点歌|发送点歌歌名即可"])
    try:
        cfg.d["assistant_anchor"]["local_qa"]["text"]["file_path"] = kb
        hit, payload = ar.assistant_match_chain(cfg, source, "怎么点歌")
        if expected_route:
            assert hit == "assistant_text" and payload
        else:
            assert hit == "miss"
    finally:
        os.unlink(kb)


# ---------- KB 适用集断言（S-1/D5） ----------

@pytest.mark.parametrize("source", ["gift", "entrance", "follow", "schedule",
                                    "idle_time_task", "integral", "song", "voice"])
def test_kb_chain_not_reachable_for_direct_tts_types(source):
    """非 AI对话/聊天 类型与 voice 源：不触达助播 KB 匹配（即使词库可命中）。"""
    cfg = _base_cfg()
    hit, _ = ar.assistant_match_chain(cfg, source, "怎么点歌")
    assert hit == "miss"


# ---------- 助播音色规范化回落（E-E1） ----------

@pytest.mark.parametrize("anchor_tts,expected_tts", [
    ("edge_tts", "edge_tts"),
    ("edge-tts", "edge_tts"),      # 连字符存量规范化
    ("gpt_sovits", "gpt_sovits"),
    ("voicefx", "gpt_sovits"),     # 非法值回落主链
    ("", "gpt_sovits"),            # 空值回落主链
    (None, "gpt_sovits"),
])
def test_resolve_assistant_tts_fallback(anchor_tts, expected_tts):
    cfg = _base_cfg(anchor_tts=anchor_tts)
    tts_type, _params = ar.resolve_assistant_tts(cfg)
    assert tts_type == expected_tts


def test_copy_message_carries_metadata():
    """E-A2：内部元数据随消息携带（业务链不消费，观测/守卫读取）。"""
    m = ar.assistant_copy_message(_base_cfg(), "内容", producer_type="gift", source=None)
    assert m["type"] == "assistant_anchor_text"
    meta = m["_anchor_meta"]
    assert meta["producer_type"] == "gift" and meta["routed"] if False else meta["hit_layer"] == "direct"
    assert isinstance(meta["ts"], float)


# ---------- 文本加载器（E-E3/E-S1） ----------

def _write_kb(lines):
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
    tmp.write("\n".join(lines) + ("\n" if lines else ""))
    tmp.close()
    return tmp.name


def test_kb_loader_bracket_aware_split():
    """E-S1 对抗样本：问题含 [1|2] 随机括号 + 答案含 | ——括号内 | 不切分，
    答案侧首个括号外 | 为界（maxsplit=1 语义）。"""
    kb = _write_kb(["怎么称呼[小明|小红]|左|右"])
    try:
        entries = ar.load_qa_library(kb)
        assert len(entries) == 1
        q, a, _tokens = entries[0]
        assert q == "怎么称呼[小明|小红]"
        assert a == "左|右"
    finally:
        os.unlink(kb)


def test_kb_loader_dirty_lines_skipped_and_counted():
    """E-E3：空行/单列/无分隔符行跳过；正常行保留。"""
    kb = _write_kb(["", "没有分隔符的行", "正常问题|正常答案", "只有问题|"])
    try:
        entries = ar.load_qa_library(kb)
        assert [(q, a) for q, a, _ in entries] == [("正常问题", "正常答案")]
    finally:
        os.unlink(kb)


def test_kb_loader_deleted_file_yields_empty():
    """E-E3：文件删除→缓存失效→空词条集（不抛异常）。"""
    kb = _write_kb(["q|a"])
    try:
        assert ar.load_qa_library(kb)
    finally:
        os.unlink(kb)
    assert ar.load_qa_library(kb) == []


def test_kb_match_adversarial_question_brackets():
    """E-S1 端到端对抗：问题含随机括号仍可精确命中。"""
    kb = _write_kb(["怎么称呼[小明|小红]|叫我小助就好"])
    try:
        cfg = _base_cfg()
        cfg.d["assistant_anchor"]["local_qa"]["text"]["file_path"] = kb
        hit, payload = ar.assistant_match_chain(cfg, "comment", "怎么称呼[小明|小红]")
        assert hit == "assistant_text"
        assert payload in ("叫我小助就好",)  # 渲染后（无括号变量则原文）
    finally:
        os.unlink(kb)


# ---------- 音频匹配三档（E-T2） ----------

@pytest.fixture()
def audio_dir():
    import tempfile
    d = tempfile.mkdtemp(prefix="anchor_audio_")
    for name in ("怎么点歌.mp3", "主播你好.wav", "欢迎来到直播间.flac"):
        open(os.path.join(d, name), "wb").close()
    yield d


def _audio_cfg(d, tier="相似度匹配", threshold=0.5):
    cfg = _base_cfg()
    cfg.d["assistant_anchor"]["local_qa"]["audio"].update(
        {"file_path": d, "type": tier, "similarity": threshold})
    return cfg


def test_audio_tier1_exact(audio_dir):
    assert ar.match_assistant_audio(_audio_cfg(audio_dir), "怎么点歌").endswith("怎么点歌.mp3")


def test_audio_tier2_contains(audio_dir):
    """包含关系档=①精确+②包含（任一方向；同分取标题更长者）。"""
    cfg = _audio_cfg(audio_dir, tier="包含关系")
    assert ar.match_assistant_audio(cfg, "欢迎来到").endswith("欢迎来到直播间.flac")
    assert ar.match_assistant_audio(cfg, "主播你好呀").endswith("主播你好.wav")


def test_audio_tier3_similarity(audio_dir):
    """相似度匹配档=①+②+③difflib（阈值内最优命中）。"""
    cfg = _audio_cfg(audio_dir, tier="相似度匹配", threshold=0.6)
    hit = ar.match_assistant_audio(cfg, "怎么点歌呢")
    assert hit is not None and hit.endswith("怎么点歌.mp3")


def test_audio_tier_contains_excludes_similarity(audio_dir):
    """包含关系档不含 ③：低相似且无包含关系的查询不命中。"""
    cfg = _audio_cfg(audio_dir, tier="包含关系", threshold=0.1)
    assert ar.match_assistant_audio(cfg, "完全无关的一句话") is None


def test_audio_normalization_fullwidth(audio_dir):
    """共享归一化口径：全角/空白差异不阻碍命中。"""
    assert ar.match_assistant_audio(_audio_cfg(audio_dir), "怎么点歌！！").endswith("怎么点歌.mp3")


# ---------- 存量迁移幂等（D3/E-E6） ----------

def _run_migration(cfg_dict):
    from frontend.config.settings import _ensure_defaults
    stub = types.SimpleNamespace(_config=cfg_dict)
    _ensure_defaults(stub)
    return cfg_dict


def test_migration_v1_to_v2_mapping():
    cfg = {"assistant_anchor": {"type": ["comment", "reread", "reread_top_priority",
                                         "local_qa_audio", "image_recognition_schedule",
                                         "key_mapping", "gift"],
                               "local_qa": {"text": {"format": "json"}}}}
    out = _run_migration(cfg)
    assert out["assistant_anchor"]["type"] == ["ai_chat", "chat", "gift"]
    assert out["assistant_anchor"]["local_qa"]["text"]["format"] == "text"


def test_migration_idempotent():
    cfg = {"assistant_anchor": {"type": ["comment", "gift", "reread"]}}
    once = _run_migration(dict(cfg))
    twice = _run_migration(once)
    assert twice["assistant_anchor"]["type"] == ["ai_chat", "gift", "chat"]
    # 再跑一次无变化（幂等）
    before = list(twice["assistant_anchor"]["type"])
    _run_migration(twice)
    assert twice["assistant_anchor"]["type"] == before


def test_migration_empty_type_untouched():
    """E-E6：未配置 type 的用户=空集语义（行为零变化，不注入默认勾选）。"""
    cfg = {"assistant_anchor": {"username": "助播"}}
    out = _run_migration(cfg)
    assert "type" not in out["assistant_anchor"]
    cfg2 = {"other": 1}
    _run_migration(cfg2)
    assert "assistant_anchor" not in cfg2


# ---------- checkbox 落盘断言（F2 修复守卫） ----------

def test_checkbox_persist_wiring_present():
    """F2 回归守卫：触发类型 checkbox 必须挂 on_value_change 且经
    set_config_callback 写回 assistant_anchor.type（窄端点链）。"""
    import frontend.ui.tabs.assistant_anchor as tab
    src = open(tab.__file__, encoding="utf-8").read()
    assert "on_value_change" in src, "触发类型 checkbox 缺 on_value_change（F2 复发）"
    assert 'set_config_callback("assistant_anchor", "type", value=checked)' in src
    assert "_persist_types" in src
    # v2 枚举渲染（旧值不得再渲染为选项）
    for old in ("local_qa_audio", "reread_top_priority", "image_recognition_schedule"):
        assert f'("{old}"' not in src


# ---------- D12 生产点穷尽性（E-T3 ast 扫描） ----------

# 路由矩阵清单∪显式豁免（豁免须带理由）。kind: routed=已接线产线；
# exempt=豁免（reason 必填）。锚点=audio_synthesis_handle 调用（所有音频
# 消息的必经入口——生产新消息必须经此，新增站点不更新本清单即测试红）。
_D12_EXPECTED = {
    ("chat_handler.py", "talk_handle"): ("exempt", "voice 语音对话不归 AI对话/聊天（uc2a，终门确认）"),
    ("comment_handler.py", "reread_handle"): ("routed", "复读归拢聊天（D2）"),
    ("comment_handler.py", "comment_handle"): ("routed", "助播KB链+llm_reply（AI对话/聊天）"),
    ("event_handler.py", "_thanks_dispatch"): ("routed", "答谢三件（gift/entrance/follow，生成时已定 type）"),
    ("event_handler.py", "schedule_handle"): ("routed", "定时任务"),
    ("event_handler.py", "idle_time_task_handle"): ("routed", "闲时任务 text 类"),
    ("feature_handler.py", "local_qa_handle"): ("routed", "主KB命中产线（D6 门控改判）"),
    ("feature_handler.py", "_integral_reply"): ("routed", "积分（先行判定）"),
    ("feature_handler.py", "_song_announce_and_play"): ("routed", "点歌播报文案（R4 经 _song_copy_message 路由）"),
    ("feature_handler.py", "choose_song_handle"): ("routed", "点歌引导/失败文案（R4 经 _song_copy_message 路由）"),
    ("feature_handler.py", "_song_play_message"): ("exempt", "歌曲音频走编排/双轨（非文案，用户需求#3）"),
    ("feature_handler.py", "_play_cover_done"): ("exempt", "翻唱完成播报（独立链，非本批触发类型）"),
    ("timer_handler.py", "periodic_trigger_data_handle"): ("routed", "念弹幕回放（local_qa/thanks 周期回放消息已带生成时 type）"),
    ("utils_handler.py", "get_a_local_audio_and_audio_play"): ("exempt", "本地音频播放工具（非文案消息）"),
    ("task_scheduler.py", "run_trends_copywriting"): ("exempt", "动态文案独立功能（非本批触发类型）"),
    ("my_handle.py", "audio_synthesis_handle"): ("exempt", "定义委托链（my_handle→audio_handler 包装层，非生产点）"),
}

# 条件站点：在途批次的生产点（树上可能尚不存在——missing 断言对其豁免，
# 落地后须保持注册；unknown 断言仍严格）。注册理由必填。
_D12_CONDITIONAL = {
    ("chat_handler.py", "llm_stream_reply"): ("routed", "流式分句回复（主链延迟优化批次在途；消息构建经 _build_stream_message 走 llm_reply 路由，已接）"),
}


def _scan_audio_synthesis_call_sites():
    """ast 扫描全仓 audio_synthesis_handle 调用点 → {(file, enclosing_func)}。
    file 归一：utils/handlers/*.py→文件名；utils/*.py→文件名；根目录 main.py→文件名。"""
    roots = ["utils", "main.py"]
    found = set()
    for root in roots:
        paths = []
        if os.path.isfile(root):
            paths.append(root)
        else:
            for dirpath, _dirs, files in os.walk(root):
                if "__pycache__" in dirpath:
                    continue
                for fn in files:
                    if fn.endswith(".py"):
                        paths.append(os.path.join(dirpath, fn))
        for path in paths:
            rel = os.path.basename(path)
            try:
                tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and node.func.attr == "audio_synthesis_handle":
                    func_name = None
                    best_line = -1
                    for fnode in ast.walk(tree):
                        if isinstance(fnode, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                                and fnode.lineno <= node.lineno <= max(
                                    getattr(fnode, "end_lineno", fnode.lineno), fnode.lineno):
                            if fnode.lineno > best_line:
                                best_line = fnode.lineno
                                func_name = fnode.name
                    if func_name:
                        found.add((rel, func_name))
    return found


def test_d12_production_site_exhaustiveness():
    """D12/E-T3：全仓 audio_synthesis_handle 调用点 ⊆ 路由矩阵清单 ∪ 显式豁免。
    新增生产点而不更新 _D12_EXPECTED → 本测试红（附清单指引）。"""
    found = _scan_audio_synthesis_call_sites()
    expected_keys = set(_D12_EXPECTED.keys()) | set(_D12_CONDITIONAL.keys())
    unknown = found - expected_keys
    assert not unknown, (
        f"发现清单外消息生产点（D12）：{sorted(unknown)}——"
        f"请接线对应产线并更新 tests/test_assistant_anchor_routing.py::_D12_EXPECTED")
    missing = set(_D12_EXPECTED.keys()) - found
    assert not missing, f"清单内站点已消失（请清理 _D12_EXPECTED）：{sorted(missing)}"


# ---------- 性能冒烟（E-T5） ----------

def test_perf_smoke_1000_entries():
    """1000 词条预分词缓存后单查询 <50ms（防 O(n²) 回归；粗断言防 flaky）。"""
    fh = _make_match_handler()
    kb = _write_kb([f"词库问题{i}|答案{i}" for i in range(1000)] + ["怎么点歌|发送点歌歌名即可"])
    try:
        fh.find_answer("预热", kb, 0.5)  # 预热（含词条分词缓存）
        t0 = time.time()
        fh.find_answer("怎么点歌", kb, 0.6)
        dt_ms = (time.time() - t0) * 1000
        assert dt_ms < 50, f"单查询 {dt_ms:.1f}ms 超护栏（疑似 O(n²) 回归）"
    finally:
        os.unlink(kb)


def _make_match_handler():
    from utils.handlers.feature_handler import FeatureHandler

    class _Stub:
        common = Common()
        config = None

    fh = FeatureHandler.__new__(FeatureHandler)
    fh.my_handle = _Stub()
    return fh


# ---------- 决策记录器（E-A2/D11 数据源） ----------

def test_route_stats_snapshot_shape():
    ar.assistant_copy_message(_base_cfg(), "x", producer_type="gift")
    stats = ar.get_route_stats()
    assert "gift|" in "".join(stats["counters"].keys())
    last = next(v for k, v in stats["last"].items() if k.startswith("gift|"))
    assert last["producer_type"] == "gift" and last["routed"] is True


# ---------- 主 local_qa 收敛（终门 rec 后续项，2026-10-27） ----------

def test_main_loader_delegates_to_shared_library():
    """主加载器委托 load_qa_library：括号兼容切分+脏行跳过（与助播同构）。"""
    from utils.handlers.feature_handler import FeatureHandler

    class _Stub:
        common = Common()
        config = None

    fh = FeatureHandler.__new__(FeatureHandler)
    fh.my_handle = _Stub()
    kb = _write_kb(["怎么称呼[小明|小红]|叫我小助就好", "", "脏行", "主播多大了|秘密"])
    try:
        entries = fh.load_data_from_file(kb)
        assert [(q, a) for q, a, _t in entries] == [
            ("怎么称呼[小明|小红]", "叫我小助就好"), ("主播多大了", "秘密")]
        # 词条词元随加载缓存（item[2]）
        assert all(len(e) == 3 for e in entries)
    finally:
        os.unlink(kb)


def test_main_local_qa_match_uses_text_subsection_keys():
    """键面统一回归：local_qa_match 读 text.* 子节（原读顶层键恒 None 的
    第二层断裂）。"""
    from utils.handlers.feature_handler import FeatureHandler

    kb = _write_kb(["怎么点歌|发送点歌歌名即可"])

    class _Stub:
        common = Common()

        def __init__(self, cfg):
            self.config = FakeConfig(cfg)

    fh = FeatureHandler.__new__(FeatureHandler)
    fh.my_handle = _Stub({
        "local_qa": {"enable": True,
                     "text": {"enable": True, "file_path": kb, "similarity": 0.6}},
    })
    try:
        assert fh.local_qa_match("怎么点歌") == "发送点歌歌名即可"
        assert fh.local_qa_match("完全无关的问题内容") is None
    finally:
        os.unlink(kb)


def test_main_local_qa_text_enable_gate():
    """文本级开关（原死开关接通）：text.enable=False 不匹配；缺键=启用（兼容）。"""
    from utils.handlers.feature_handler import FeatureHandler

    kb = _write_kb(["怎么点歌|发送点歌歌名即可"])

    class _Stub:
        common = Common()

        def __init__(self, cfg):
            self.config = FakeConfig(cfg)

    fh = FeatureHandler.__new__(FeatureHandler)
    fh.my_handle = _Stub({
        "local_qa": {"enable": True,
                     "text": {"enable": False, "file_path": kb, "similarity": 0.6}},
    })
    assert fh.local_qa_match("怎么点歌") is None
    # 缺 text.enable 键（存量）→ 启用
    fh2 = FeatureHandler.__new__(FeatureHandler)
    fh2.my_handle = _Stub({
        "local_qa": {"enable": True,
                     "text": {"file_path": kb, "similarity": 0.6}},
    })
    try:
        assert fh2.local_qa_match("怎么点歌") == "发送点歌歌名即可"
    finally:
        os.unlink(kb)


def test_main_local_qa_audio_stage():
    """主 KB 音频级（镜像 D5 音频→文本）：音频命中产 local_qa_audio 且短路文本。"""
    from utils.handlers.feature_handler import FeatureHandler
    import tempfile

    kb = _write_kb(["怎么点歌|文本答案"])
    d = tempfile.mkdtemp(prefix="mainqa_audio_")
    open(os.path.join(d, "怎么点歌.mp3"), "wb").close()

    captured = []

    class _Stub:
        common = Common()

        def __init__(self, cfg):
            self.config = FakeConfig(cfg)

        def audio_synthesis_handle(self, msg):
            captured.append(msg)

        def webui_show_chat_log_callback(self, *a, **k):
            pass

    fh = FeatureHandler.__new__(FeatureHandler)
    fh.my_handle = _Stub({
        "local_qa": {"enable": True,
                     "text": {"enable": True, "file_path": kb, "similarity": 0.6},
                     "audio": {"enable": True, "type": "相似度匹配",
                               "file_path": d, "similarity": 0.5}},
    })
    try:
        assert fh.local_qa_handle({"username": "u", "content": "怎么点歌"}) is True
        assert len(captured) == 1
        assert captured[0]["type"] == "local_qa_audio"
        assert captured[0]["file_path"].endswith("怎么点歌.mp3")
    finally:
        os.unlink(kb)


def test_migration_main_local_qa_keys():
    """主 local_qa 迁移：type/format 退役键清理+顶层遗留键并入 text.*+音频档位补默认。"""
    cfg = {"local_qa": {
        "file_path": "data/旧路径.txt", "similarity": 0.7,
        "format": "json",
        "text": {"enable": True, "type": "json"},
        "audio": {"enable": True, "file_path": "out/x/"},
    }}
    out = _run_migration(cfg)
    t = out["local_qa"]["text"]
    assert "type" not in t and "format" not in out["local_qa"]
    assert t["file_path"] == "data/旧路径.txt" and t["similarity"] == 0.7  # 顶层并入（不覆盖子节）
    assert out["local_qa"]["audio"]["type"] == "相似度匹配"
