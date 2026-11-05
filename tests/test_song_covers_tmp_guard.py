# -*- coding: UTF-8 -*-
"""点歌 covers 查找 tmp 残留守卫（2026-11-04 用户实测「人声不播」根修）。

根因：_song_covers_hit 倒序扫描先命中 `.backing.tmp.wav` 播放中间产物
（残留非成品），其 _vocal/_inst 探测必失败 →「分轨=无」→ 伴奏 tmp 切片
被当整轨播出（纯伴奏无人声）。修复=查找跳过 tmp 残留。
"""
import os


class TestSongCoversTmpGuard:
    def _hit(self, tmp_path, files, monkeypatch=None):
        from utils.handlers.feature_handler import FeatureHandler
        import utils.autolive_paths as ap
        covers = tmp_path / "covers"
        covers.mkdir(parents=True, exist_ok=True)
        for fn in files:
            (covers / fn).write_bytes(b"")
        fh = FeatureHandler.__new__(FeatureHandler)
        fh.my_handle = type("H", (), {"config": {}})()
        if monkeypatch is not None:
            monkeypatch.setattr(ap, "song_audio_root", lambda cfg=None, home=None: str(tmp_path))
        return fh._song_covers_hit("牧马城市")

    def test_backing_tmp_residue_skipped(self, tmp_path, monkeypatch):
        """残留 backing.tmp 抢先命中 → 曾「分轨=无」播纯伴奏；修复后跳过残留、
        命中主成品三分轨。"""
        hit = self._hit(tmp_path, [
            "牧马城市__WY.wav",
            "牧马城市__WY_vocal.wav",
            "牧马城市__WY_inst.wav",
            "牧马城市__WY_inst.wav.backing.tmp.wav",  # 残留（倒序在前）
        ], monkeypatch)
        assert hit is not None
        full, vocal, inst = hit
        assert os.path.basename(full) == "牧马城市__WY.wav"
        assert vocal and os.path.basename(vocal) == "牧马城市__WY_vocal.wav"
        assert inst and os.path.basename(inst) == "牧马城市__WY_inst.wav"

    def test_genuine_split_hit_unchanged(self, tmp_path, monkeypatch):
        """无残留时三分轨命中不回归。"""
        hit = self._hit(tmp_path, [
            "牧马城市__WY.wav",
            "牧马城市__WY_vocal.wav",
            "牧马城市__WY_inst.wav",
        ], monkeypatch)
        assert hit is not None and hit[1] and hit[2]

    def test_no_vocal_still_degrades(self, tmp_path, monkeypatch):
        """真缺人声轨（仅伴奏）→「分轨=无」降级语义不回归。"""
        hit = self._hit(tmp_path, ["牧马城市__WY.wav", "牧马城市__WY_inst.wav"], monkeypatch)
        assert hit is not None and hit[1] is None and hit[2] is None
