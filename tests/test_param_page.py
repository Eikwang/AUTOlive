# -*- coding: utf-8 -*-
"""D4 新功能 UI + D5 参数页重组 纯函数单测（2026-10-19 切片 3）

覆盖：过滤总开关默认 True（get_nested_value 缺省约定）/ 优先级预设值函数与
有序条目（缺省过滤+降序）/ 礼物点歌映射匹配逻辑（含预演同函数）/ 任务组
结构校验与存量单任务迁移。UI 渲染部分不测（NiceGUI slot 上下文）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from frontend.ui.components.config_helper import get_nested_value
from frontend.ui.tabs.filter_queue import (
    PRIORITY_GROUPS,
    build_priority_presets,
    iter_priority_entries,
)
from frontend.ui.tabs.choose_song import list_song_names, match_gift_to_song
from frontend.ui.tabs.schedule import (
    migrate_tasks_to_groups,
    validate_schedule_groups,
)


# ---------- ① 过滤总开关默认 True（filter.filter_master_enable） ----------

def test_filter_master_enable_defaults_true_when_missing():
    """未配置键时总开关初值为 True——与存量行为一致（D-20 默认值列）。"""
    cfg = {"filter": {}}
    assert get_nested_value(cfg, "filter", "filter_master_enable", default=True) is True


def test_filter_master_enable_missing_config_true():
    """filter 节整体缺失（旧配置）时同样默认 True。"""
    assert get_nested_value({}, "filter", "filter_master_enable", default=True) is True


def test_filter_master_enable_false_persists():
    """用户显式关闭（False）可读回，不被默认值覆盖。"""
    cfg = {"filter": {"filter_master_enable": False}}
    assert get_nested_value(cfg, "filter", "filter_master_enable", default=True) is False


# ---------- ② 优先级预设值函数（D5） ----------

def _all_group_keys():
    return [key for _g, items in PRIORITY_GROUPS for key, _n in items]


def test_presets_cover_all_group_keys():
    """三个预设覆盖全部分组键（预设值写入各键的前提）。"""
    keys = set(_all_group_keys())
    presets = build_priority_presets()
    assert set(presets) == {"互动优先", "内容优先", "省资源"}
    for name, vals in presets.items():
        assert set(vals) == keys, f"预设 {name} 键集合与分组不一致"


def test_presets_values_positive_ints():
    """预设值全为正整数（任务书：请使用正整数）。"""
    for vals in build_priority_presets().values():
        assert all(isinstance(v, int) and v > 0 for v in vals.values())


def test_preset_direction_interactive_vs_content():
    """方向校验：互动优先时弹幕回复>定时任务；内容优先时定时任务>弹幕回复。"""
    presets = build_priority_presets()
    assert presets["互动优先"]["comment"] > presets["互动优先"]["schedule"]
    assert presets["内容优先"]["schedule"] > presets["内容优先"]["comment"]
    # 最高优先级复读各预设恒高位（安全兜底不随场景降）；
    # abnormal_alarm 组已随 2026-10-26 报警重构退役，不再入预设
    for vals in presets.values():
        assert "abnormal_alarm" not in vals
        assert vals["reread_top_priority"] == 999


def test_preset_resource_saving_flattens():
    """省资源预设：除恒高两项外全部拉平为最小档。"""
    vals = build_priority_presets()["省资源"]
    flat = {k: v for k, v in vals.items() if k not in ("reread_top_priority",)}
    assert set(flat.values()) == {10}


# ---------- ③ 优先级有序条目（D5：统一序号+降序+缺省过滤） ----------

def test_priority_entries_sorted_desc_and_ranked():
    cfg = {"filter": {"priority_mapping": {"comment": 20, "gift": 40, "talk": 30}}}
    entries = iter_priority_entries(cfg)
    assert [e[0] for e in entries] == [1, 2, 3]  # 统一序号
    assert [e[2] for e in entries] == ["gift", "talk", "comment"]  # 值降序
    assert [e[4] for e in entries] == [40, 30, 20]


def test_priority_entries_skip_missing_keys():
    """config 读不到的键跳过（缺省过滤，任务书"UI 读不到就跳过"）。"""
    cfg = {"filter": {"priority_mapping": {"comment": 20}}}
    keys = {e[2] for e in iter_priority_entries(cfg)}
    assert keys == {"comment"}


def test_priority_entries_excludes_retired_items():
    """uc3 裁决：图像识别/文案两项不再出现在条目清单。"""
    keys = set(_all_group_keys())
    assert "image_recognition_schedule" not in keys
    assert "copywriting" not in keys


def test_priority_entries_group_labels():
    """条目带来源分组标注（直播互动/本地输入/系统任务/助播）。"""
    cfg = {"filter": {"priority_mapping": {"comment": 20, "schedule": 10,
                                           "talk": 30, "assistant_anchor_text": 25}}}
    groups = {e[2]: e[1] for e in iter_priority_entries(cfg)}
    assert groups["comment"] == "直播互动"
    assert groups["talk"] == "本地输入"
    assert groups["schedule"] == "系统任务"
    assert groups["assistant_anchor_text"] == "助播"


# ---------- ④ 礼物点歌映射匹配（D4，含预演同函数） ----------

_GIFT_MAP = {"小火箭": "晴天", "大啤酒": "海阔天空"}
_SONGS = ["晴天", "海阔天空", "七里香"]


def test_gift_match_exact():
    hit = match_gift_to_song("小火箭", _GIFT_MAP, _SONGS, 0.5)
    assert hit == ("晴天", 1.0, "映射[小火箭]→包含命中")


def test_gift_match_contains_longest_key_first():
    """礼物名包含映射键即命中，最长键优先（超级小火箭 命中 小火箭）。"""
    hit = match_gift_to_song("超级小火箭", _GIFT_MAP, _SONGS, 0.5)
    assert hit is not None and hit[0] == "晴天"


def test_gift_match_similarity_fallback():
    """映射歌曲不在曲库时按相似度兜底（与 utils find_best_match 同口径）。"""
    m = {"火箭": "七里箱"}  # 曲库为 七里香
    hit = match_gift_to_song("火箭", m, _SONGS, 0.5)
    assert hit is not None and hit[0] == "七里香"
    assert hit[2].endswith("相似度命中")


def test_gift_match_below_threshold_returns_none():
    m = {"火箭": "七里箱"}
    assert match_gift_to_song("火箭", m, _SONGS, 0.9) is None


def test_gift_match_no_mapping_returns_none():
    assert match_gift_to_song("玫瑰花", _GIFT_MAP, _SONGS, 0.5) is None
    assert match_gift_to_song("小火箭", {}, _SONGS, 0.5) is None
    assert match_gift_to_song("", _GIFT_MAP, _SONGS, 0.5) is None


def test_list_song_names_only_audio(tmp_path):
    """曲库候选只收音频扩展名（与 utils get_dir_audios_filename 口径一致）。"""
    (tmp_path / "晴天.mp3").write_bytes(b"x")
    (tmp_path / "海阔天空.wav").write_bytes(b"x")
    (tmp_path / "说明.txt").write_bytes(b"x")
    (tmp_path / "封面.jpg").write_bytes(b"x")
    names = list_song_names(str(tmp_path))
    assert names == ["晴天", "海阔天空"]


# ---------- ⑤ 任务组结构校验与存量迁移（D4：schedule_groups） ----------

def _valid_group(**over):
    g = {"name": "默认组", "min_interval": 60, "max_interval": 120,
         "copywriting_list": ["你好", "欢迎观看"]}
    g.update(over)
    return g


def test_group_validate_ok():
    assert validate_schedule_groups([_valid_group()]) == []


def test_group_validate_missing_name():
    errs = validate_schedule_groups([_valid_group(name="  ")])
    assert any("缺少组名" in e for e in errs)


def test_group_validate_interval_order():
    errs = validate_schedule_groups([_valid_group(min_interval=200, max_interval=100)])
    assert any("最大循环周期应不小于最小值" in e for e in errs)


def test_group_validate_empty_copy():
    errs = validate_schedule_groups([_valid_group(copywriting_list=[])])
    assert any("文案列表为空" in e for e in errs)


def test_group_validate_not_a_list():
    assert validate_schedule_groups({"name": "x"}) == ["schedule_groups 应为列表"]


def test_migrate_tasks_to_groups():
    """存量单任务映射：字段名迁移 time_min/time_max/copy → 组字段；首组「默认组」。"""
    tasks = [
        {"enable": True, "time_min": 600.0, "time_max": 900.0, "copy": ["报时{time}", "欢迎{user_num}"]},
        {"enable": False, "time_min": 60, "time_max": 120, "copy": ["感谢观看"]},
    ]
    groups = migrate_tasks_to_groups(tasks)
    assert len(groups) == 2
    assert groups[0]["name"] == "默认组"
    assert groups[0]["min_interval"] == 600.0
    assert groups[0]["max_interval"] == 900.0
    assert groups[0]["copywriting_list"] == ["报时{time}", "欢迎{user_num}"]
    assert groups[1]["name"] == "默认组#2"


def test_migrate_tasks_empty():
    assert migrate_tasks_to_groups([]) == []
    assert migrate_tasks_to_groups(None) == []


def test_migrated_groups_pass_validation():
    """迁移产物过结构校验（闭环）。"""
    groups = migrate_tasks_to_groups([
        {"enable": True, "time_min": 600.0, "time_max": 900.0, "copy": ["a"]},
    ])
    assert validate_schedule_groups(groups) == []
