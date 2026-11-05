"""T5 语义切片+静音段数据文件守卫（2026-11-05 编排优化批次）。

覆盖裁决条款：
- d15：schema 往返（write→load）+旁挂命名（成品 → .silences.json）
- F-H：畸形拒绝（NaN/Infinity/负值/倒置/版本/非对象）+排序去重合并
- e12：损坏/半写/缺失 fail-soft（返回 None 不抛错）
- d1：语义切片两分支边界（有数据文件=人声段条目+gap_after_s；无=45s 回退；
  空 silences=整曲单段）
- d20/F-C：惰性解析时序（首播固定切片不等待/解析完成后第二播语义切片/
  in-flight 去重/超时放弃+观测桶/仅 covers 有 vocal 分轨才调度）
- 三态路由（F-D/e1）：EDTalk 激活=语义优先；未激活=45s 现行为（vocal 静音窗
  对整曲混音无意义）；uc4b 双轨逐片=伴奏仅首片承载
"""
import json
import os
import threading

import pytest

from utils.audio import synthesis_manager as sm
from utils.audio import vocal_silences as vs
from utils.audio.synthesis_manager import SynthesisMixin


# ------------------------------------------------- d15/F-H/e12 纯契约

def test_d15_round_trip(tmp_path):
    sp = str(tmp_path / "cover.silences.json")
    silences = [{"start_s": 1.0, "end_s": 2.5}, {"start_s": 10.0, "end_s": 13.0}]
    assert vs.write_silences_file(sp, silences) is True
    doc = json.loads(open(sp, encoding="utf-8").read())
    assert doc["version"] == 1 and doc["threshold_db"] == -40.0
    assert doc["min_silence_s"] == 3.0
    assert vs.load_silences(sp) == silences  # 往返一致（已排序）


def test_d15_sidecar_naming():
    assert vs.silences_path_for("covers/a__p1.wav") == "covers/a__p1.silences.json"


@pytest.mark.parametrize("doc", [
    {"version": 2, "threshold_db": -40, "min_silence_s": 3, "silences": []},
    {"version": 1, "threshold_db": "x", "min_silence_s": 3, "silences": []},
    {"version": 1, "threshold_db": -40, "min_silence_s": 3,
     "silences": [{"start_s": 2.0, "end_s": 1.0}]},           # 倒置
    {"version": 1, "threshold_db": -40, "min_silence_s": 3,
     "silences": [{"start_s": -1.0, "end_s": 1.0}]},          # 负起点
    {"version": 1, "threshold_db": -40, "min_silence_s": 3,
     "silences": [{"start_s": float("nan"), "end_s": 1.0}]},  # NaN
    {"version": 1, "threshold_db": -40, "min_silence_s": 3,
     "silences": [{"start_s": 1.0, "end_s": float("inf")}]},  # Infinity
    {"version": 1, "threshold_db": -40, "min_silence_s": 3,
     "silences": "oops"},                                      # 非列表
    [1, 2, 3],                                                 # 非对象
])
def test_fh_malformed_rejected(doc):
    assert vs.parse_silences_document(doc) is None


def test_fh_normalize_sorts_and_merges():
    out = vs.parse_silences_document({
        "version": 1, "threshold_db": -40, "min_silence_s": 3,
        "silences": [{"start_s": 5.0, "end_s": 7.0},
                     {"start_s": 1.0, "end_s": 3.0},
                     {"start_s": 2.5, "end_s": 4.5}]})
    assert out == [{"start_s": 1.0, "end_s": 4.5}, {"start_s": 5.0, "end_s": 7.0}]


def test_e12_corrupt_or_missing_fail_soft(tmp_path):
    bad = tmp_path / "bad.silences.json"
    bad.write_text('{"version": 1, "threshold_db": -40, "min_silence_s": 3, '
                   '"silences": [{"start_s": NaN, "end_s": 2}]}', encoding="utf-8")
    assert vs.load_silences(str(bad)) is None  # NaN 字面量（json 默认接受）→ 拒绝
    half = tmp_path / "half.silences.json"
    half.write_text('{"version": 1, "thres', encoding="utf-8")  # 半写
    assert vs.load_silences(str(half)) is None
    assert vs.load_silences(str(tmp_path / "missing.silences.json")) is None


# ------------------------------------------------- compute_silences（内存音频）

def _loud_silent_loud():
    """1s 有声 + 1.2s 静音 + 1s 有声（无 ffmpeg——pydub 内存构造）。"""
    from pydub import AudioSegment
    from pydub.generators import Sine
    return (Sine(440).to_audio_segment(duration=1000)
            .append(AudioSegment.silent(duration=1200))
            .append(Sine(440).to_audio_segment(duration=1000)))


def test_compute_silences_detects_interlude(monkeypatch):
    from pydub import AudioSegment
    seg = _loud_silent_loud()
    monkeypatch.setattr(AudioSegment, "from_file",
                        classmethod(lambda cls, path, **k: seg))
    runs = vs.compute_silences("dummy.wav", min_silence_s=1.0)
    assert len(runs) == 1
    assert runs[0]["start_s"] == pytest.approx(1.0, abs=0.05)
    # 尾段有声=静音窗终点≈总长-1s（pydub append 帧量化可能缩 0.1~0.2s）
    assert runs[0]["end_s"] == pytest.approx(len(seg) / 1000.0 - 1.0, abs=0.2)


# ------------------------------------------------- 消费端 fixture

class _FakeConfig:
    def __init__(self, seg=45):
        self._single = {"audio_synthesis_type": "edge"}
        self._data = {("play_audio", "song_segment_seconds"): seg,
                      ("play_audio", "enable"): True,
                      ("filter", "emoji"): False}

    def get(self, *keys):
        if len(keys) == 1:
            return self._single.get(keys[0])
        return self._data.get(keys)


class _SynFixture(SynthesisMixin):
    def __init__(self, seg=45):
        self.config = _FakeConfig(seg)
        self.captured = []

    def data_priority_insert(self, type="等待合成消息", data_json=None):
        self.captured.append(data_json)
        return {"code": 200}


@pytest.fixture
def fake_pydub():
    """ffmpeg 缺失环境替身：from_file 返回注入段；export 落 dummy 文件。
    手动 patch/restore（monkeypatch 对 pydub 类属性 undo 走 mappingproxy 删除
    会 TypeError——from_file 定义在基类）。"""
    from pydub import AudioSegment
    holder = {"seg": AudioSegment.silent(duration=3000)}
    orig_from, orig_export = AudioSegment.from_file, AudioSegment.export
    AudioSegment.from_file = classmethod(lambda cls, path, **k: holder["seg"])
    AudioSegment.export = lambda self, path, **k: open(path, "wb").close()
    yield holder
    AudioSegment.from_file = orig_from
    AudioSegment.export = orig_export


# ------------------------------------------------- d1 语义切片两分支

def test_d1_semantic_slices_boundaries(tmp_path, fake_pydub):
    vocal = tmp_path / "a__p1_vocal.wav"
    vocal.write_bytes(b"fake")
    (tmp_path / "a__p1.wav").write_bytes(b"fake-mix")  # full 成品（sp 门）
    sp = tmp_path / "a__p1.silences.json"
    vs.write_silences_file(str(sp), [{"start_s": 1.0, "end_s": 2.0}])
    fx = _SynFixture(seg=45)
    segs, silences = fx._semantic_or_fixed_slices(str(vocal), {"full_path": str(tmp_path / "a__p1.wav")})
    assert silences == [{"start_s": 1.0, "end_s": 2.0}]
    assert len(segs) == 2  # 人声段 [0,1]+[2,3]（d1：人声段=条目）
    assert segs[0]["seq"] == 0 and segs[0]["total"] == 2
    assert segs[0]["gap_after_s"] == 1.0  # 间奏窗口元数据
    assert segs[1]["gap_after_s"] == 0.0


def test_d1_empty_silences_whole_track(tmp_path, fake_pydub):
    vocal = tmp_path / "b__p1_vocal.wav"
    vocal.write_bytes(b"fake")
    (tmp_path / "b__p1.wav").write_bytes(b"fake-mix")
    vs.write_silences_file(str(tmp_path / "b__p1.silences.json"), [])  # 合法「无间奏」
    fx = _SynFixture(seg=45)
    segs, _ = fx._semantic_or_fixed_slices(str(vocal), {"full_path": str(tmp_path / "b__p1.wav")})
    assert len(segs) == 1  # 整曲单段（人工边界不引入）


def test_d1_fallback_fixed_when_no_silences(tmp_path, fake_pydub, monkeypatch):
    # 3s 音频 + 1s 固定切片 → 3 片（45s 回退分支可观测化）
    vocal = tmp_path / "c__p1_vocal.wav"
    vocal.write_bytes(b"fake")
    (tmp_path / "c__p1.wav").write_bytes(b"fake-mix")
    fx = _SynFixture(seg=1)  # song_segment_seconds=1（回退切片可观测）
    segs, silences = fx._semantic_or_fixed_slices(
        str(vocal), {"full_path": str(tmp_path / "c__p1.wav")})
    assert silences is None
    assert len(segs) == 3  # 45s（此处 1s）固定切片回退（d1）
    assert all("gap_after_s" not in s for s in segs)  # 固定切片无间奏语义


# ------------------------------------------------- F-C 惰性解析时序

@pytest.fixture
def lazy_env(tmp_path, monkeypatch):
    """covers 形态：成品 + vocal 分轨（假件）+ compute/write 同步桩。"""
    full = tmp_path / "d__p1.wav"
    full.write_bytes(b"fake-mix")
    vocal = tmp_path / "d__p1_vocal.wav"
    vocal.write_bytes(b"fake-vocal")
    calls = {"compute": 0}
    monkeypatch.setattr(sm, "_vs_compute",
                        lambda p: (calls.__setitem__("compute", calls["compute"] + 1),
                                   [{"start_s": 1.0, "end_s": 2.0}])[1])
    return {"full": str(full), "vocal": str(vocal), "calls": calls}


def test_fc_first_play_falls_back_and_schedules(tmp_path, lazy_env, monkeypatch, fake_pydub):
    fx = _SynFixture(seg=1)
    # 同步 Thread 桩：start() 即跑 target（确定性）
    created = []

    class _SyncThread:
        def __init__(self, target=None, name=None, daemon=None):
            self._target = target
            created.append(self)

        def start(self):
            self._target()

    monkeypatch.setattr(sm.threading, "Thread", _SyncThread)
    segs, silences = fx._semantic_or_fixed_slices(
        lazy_env["vocal"], {"full_path": lazy_env["full"]})
    assert silences is None and len(segs) == 3  # 首播：固定切片（不等待解析）
    assert lazy_env["calls"]["compute"] == 1    # 后台解析已执行（同步桩）
    assert vs.load_silences(vs.silences_path_for(lazy_env["full"])) is not None
    # 第二播：语义切片生效（d20/F-C）
    segs2, silences2 = fx._semantic_or_fixed_slices(
        lazy_env["vocal"], {"full_path": lazy_env["full"]})
    assert silences2 == [{"start_s": 1.0, "end_s": 2.0}]
    assert len(segs2) == 2 and segs2[0]["gap_after_s"] == 1.0


def test_fc_inflight_dedup_and_timeout(tmp_path, lazy_env, monkeypatch, fake_pydub):
    fx = _SynFixture(seg=1)
    created = []

    class _SyncThread:
        def __init__(self, target=None, name=None, daemon=None):
            self._target = target
            created.append(self)

        def start(self):
            pass  # 不执行（模拟长时间在飞）

    monkeypatch.setattr(sm.threading, "Thread", _SyncThread)
    sp = vs.silences_path_for(lazy_env["full"])
    fx._schedule_lazy_silence_parse(lazy_env["full"], lazy_env["vocal"])
    n1 = len(created)
    fx._schedule_lazy_silence_parse(lazy_env["full"], lazy_env["vocal"])
    assert len(created) == n1  # in-flight 去重（不重复调度）
    # 超时：在飞标记老化 → 放弃+重调度
    with sm.SynthesisMixin._silence_parse_lock:
        sm.SynthesisMixin._silence_parse_inflight[sp] -= sm.SynthesisMixin._SILENCE_PARSE_TIMEOUT_S + 1
    monkeypatch.setattr(sm.threading.Thread, "start",
                        lambda self: self._target())
    fx._schedule_lazy_silence_parse(lazy_env["full"], lazy_env["vocal"])
    assert vs.load_silences(sp) is not None  # 重调度已执行


def test_fc_skip_without_vocal_stem(tmp_path, lazy_env, monkeypatch, fake_pydub):
    created = []

    class _SyncThread:
        def __init__(self, target=None, name=None, daemon=None):
            created.append(self)

        def start(self):
            pass

    monkeypatch.setattr(sm.threading, "Thread", _SyncThread)
    os.remove(lazy_env["vocal"])  # 无分轨（用户上传原曲形态）
    fx = _SynFixture(seg=1)
    fx._schedule_lazy_silence_parse(lazy_env["full"], lazy_env["vocal"])
    assert created == []  # 不调度（45s 永久回退，d20）


# ------------------------------------------------- 三态路由 + uc4b 双轨

def _song_message(full, backing=""):
    return {"type": "song", "content": full, "full_path": full,
            "backing_path": backing, "backing_volume": 0.8}


def test_route_edtalk_active_semantic(tmp_path, fake_pydub, monkeypatch):
    vocal = tmp_path / "e__p1_vocal.wav"
    vocal.write_bytes(b"fake")
    (tmp_path / "e__p1.wav").write_bytes(b"fake-mix")
    vs.write_silences_file(str(tmp_path / "e__p1.silences.json"),
                           [{"start_s": 1.0, "end_s": 2.0}])
    fx = _SynFixture(seg=45)
    monkeypatch.setattr(sm, "is_edtalk_active", lambda cfg: True)
    fx.audio_synthesis(_song_message(str(tmp_path / "e__p1.wav")))
    assert len(fx.captured) == 2
    assert fx.captured[0]["song_segment"]["seq"] == 0
    assert fx.captured[0]["gap_after_s"] == 1.0  # 语义切片（无 backing 键=纯人声路径）


def test_route_edtalk_inactive_fixed(tmp_path, fake_pydub, monkeypatch):
    full = tmp_path / "f__p1.wav"
    full.write_bytes(b"fake")
    vs.write_silences_file(str(tmp_path / "f__p1.silences.json"),
                           [{"start_s": 1.0, "end_s": 2.0}])  # 有数据文件也不用于 builtin
    fx = _SynFixture(seg=1)
    monkeypatch.setattr(sm, "is_edtalk_active", lambda cfg: False)
    fx.audio_synthesis(_song_message(str(full)))
    assert len(fx.captured) == 3  # 45s（此处 1s）固定切片（F-D：builtin 不走语义切片）
    assert all("gap_after_s" not in c for c in fx.captured)


def test_uc4b_dual_track_backing_on_first_slice_only(tmp_path, fake_pydub, monkeypatch):
    vocal = tmp_path / "g__p1_vocal.wav"
    vocal.write_bytes(b"fake")
    (tmp_path / "g__p1.wav").write_bytes(b"fake-mix")
    vs.write_silences_file(str(tmp_path / "g__p1.silences.json"),
                           [{"start_s": 1.0, "end_s": 2.0}])
    fx = _SynFixture(seg=45)
    monkeypatch.setattr(sm, "is_edtalk_active", lambda cfg: True)
    backing = tmp_path / "g__p1_inst.wav"
    backing.write_bytes(b"fake-inst")
    fx.audio_synthesis(_song_message(str(tmp_path / "g__p1.wav"), backing=str(backing)))
    assert len(fx.captured) == 2
    assert fx.captured[0].get("backing_path") == str(backing)  # 伴奏仅首片承载（uc4b）
    assert "backing_path" not in fx.captured[1]
    assert fx.captured[0]["gap_after_s"] == 1.0
