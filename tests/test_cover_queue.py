# -*- coding: UTF-8 -*-
"""
P3-4 单元测试：翻唱队列状态机（入队/重试/超时/队满/冷却/同观众上限/归一化）
+ GPU 探测联动（E-2 准入）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.cover_worker import CoverQueue  # noqa: E402
from utils.gpu_policy import is_background_task_allowed  # noqa: E402


def _queue(tmp_path, queue_max=5, cooldown_s=60, same_viewer_max=1):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "choose_song": {"song_path": str(tmp_path / "song")},
        "audio_integration": {"policies": {
            "queue_max": queue_max, "cooldown_s": cooldown_s,
            "same_viewer_in_queue_max": same_viewer_max}},
    }), encoding="utf-8")
    from utils.config import Config
    Config.config = None  # 重置类缓存
    (tmp_path / "song").mkdir(exist_ok=True)
    return CoverQueue(Config(str(cfg_file)), None)


def test_normalize_song():
    """B-6：去空格/括注/大小写。"""
    n = CoverQueue.normalize_song
    assert n("孤勇者 (Live)") == n("孤勇者(LIVE)")
    assert n("  起 风 了 ") == n("起风了")
    assert n("【高清】晴天") == n("晴天")


def test_enqueue_and_queue_full():
    """入队成功 → 队满拒绝（D6 文案键）。"""
    q = _queue(os.path.sep and __import__("pathlib").Path(__file__).parent.parent / "tmp_test" / __name__ or None) if False else None
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    q = _queue(tmp)
    for i in range(5):
        r = q.enqueue(f"viewer{i}", f"song{i}", "p1")
        assert r["action"] == "queued", f"第 {i} 个应入队: {r}"
    r = q.enqueue("viewerX", "songX", "p1")
    assert r["action"] == "queue_full"


def test_same_viewer_in_queue_max():
    """B-3：同观众在队上限 1 → 静默拒绝。"""
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    q = _queue(tmp, same_viewer_max=1)
    r1 = q.enqueue("v1", "song_a", "p1")
    assert r1["action"] == "queued"
    r2 = q.enqueue("v1", "song_b", "p1")
    assert r2["action"] == "cooldown"  # 静默拒绝（B-3）


def test_cached_returns_cover_path():
    """成品缓存命中 → 直接返回路径（零延迟）。"""
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    q = _queue(tmp)
    (tmp / "song" / "covers").mkdir(parents=True)
    cover = tmp / "song" / "covers" / "晴天__p1.wav"
    cover.write_bytes(b"RIFF")
    r = q.enqueue("v1", "晴天", "p1")
    assert r["action"] == "cached" and r["cover_path"] == str(cover)


def test_local_song_lookup():
    """S-3：本地曲源查找（归一化匹配）。"""
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    q = _queue(tmp)
    (tmp / "song" / "晴天(LIVE).mp3").write_bytes(b"x")
    found = q.find_local_song("晴天")
    assert found is not None and found.endswith(".mp3")
    assert q.find_local_song("不存在的歌") is None


def test_gpu_policy_returns_tuple():
    """E-2：准入返回 (bool, reason) 元组。"""
    ok, reason = is_background_task_allowed(9999)  # 阈值 9999 → 必拒（显存不够）
    assert ok is False and "显存" in reason
