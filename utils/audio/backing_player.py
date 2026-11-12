"""伴奏播放子进程薄入口（2026-11-10 R2 伴奏卡顿根修——音频链进程隔离第一步）。

根因：主进程 daemon 线程 pyaudio 阻塞写流（chunk=1024 帧≈23ms@44.1k，每秒
~43 次 write）在 TTS 合成（pydub CPU 段）等消费路径持 GIL 时被饿 → PortAudio
内部缓冲 underrun → 伴奏断续。子进程独立 GIL 彻底隔离资源竞争类根因。

复用意图（CEO F3）：本模块是「音频链路进程隔离」平台化决策债（TODOS 登记，
触发条件=下一次同类主进程阻塞事故）的第一个可复用边界——后续 TTS 合成/
其它重 CPU 音频工作可按同款「薄入口+跨进程原语」模式迁移。

spawn 契约（Eng FB1/F9）：
- 模块 import 面仅标准库+pyaudio/wave/pydub（全部函数内延迟 import），
  **不** import playback_manager 或项目重依赖——子进程 re-exec target 所在
  模块的 import 链，薄入口保证冷启动 <0.5s（首播记录实际耗时）。
- target=模块顶层函数（spawn 下必须可 pickle），mp 原语由主进程创建、经
  参数继承（spawn 下不可在子进程新建）。

跨进程契约（三原语，方向标注——Eng F1/D19：丢任一=11-04 人声滞后回归）：
- start_event（主→子，threading.Event 或 mp.Event）：起播门——主进程在
  人声 push_full 受理后置位（11-04 两阶段起播语义），子进程等门再
  start_stream；前奏场景主进程在人声推送前提前置位（伴奏先起播承担时间轴
  零点）。None=直调旧语义（open 即起播）。
- started_event（子→主，threading.Event 或 mp.Event）：真实起播回执。
- started_value（子→主，mp.Value('d') 或含 "t" 键的 dict）：起播时刻
  （time.monotonic()，Windows 下跨进程同基准）。**顺序契约：先写 Value →
  后 set Event**（防主进程 Event wait 返回后读到竞态旧值）。哨兵 0.0=无
  回执（所有退出路径统一置哨兵+set，防主进程白等门超时——含 vol<=0/加载
  失败/异常）。
- duck_value（主→子，2026-11-12 伴奏闪避批次；mp.Value('d') 或 dict["v"]，
  1.0=原音量）：闪避系数——前奏/间奏窗口借位插播时主进程写 0.5，写流循环
  **每 chunk 读取**并按系数缩放 PCM（audioop.mul，93ms 粒度即时生效）。
  播报播完写回 1.0。None/读失败=1.0 原样（fail-soft，不缩放）。

协作停止（Eng F4/D22）：stop_event 非 None 时写流循环每 chunk 检查 →
正常 close 退出（设备句柄干净释放）；主进程 terminate 仅作挂死兜底
（TerminateProcess 下 finally 不执行——tmp 残留由主进程侧清扫兜底）。
"""

import os
import threading


def _report_started(started_event, started_value, ts: float) -> None:
    """统一回执出口：先写 value（哨兵语义 0.0=无回执）→ 后 set event
    （顺序契约）；双形态兼容（mp.Value 用 .value、dict 用 ["t"]——F3/D21）。"""
    try:
        if started_value is not None:
            if isinstance(started_value, dict):
                started_value["t"] = ts
            else:
                started_value.value = ts
        if started_event is not None:
            started_event.set()
    except Exception:
        pass  # 回执失败不改变播放行为（门超时兜底 fail-open）


def _read_duck(duck_value) -> float:
    """闪避系数读取（2026-11-12 伴奏闪避批次）：双形态（mp.Value 用 .value、
    dict 用 ["v"]）+ clamp [0,1]（防撕裂/脏值直通缩放）+ fail-soft 1.0
    （读失败=不缩放，播放连续性优先）。写流循环每 chunk 调用（≈93ms 一次）。"""
    try:
        if duck_value is None:
            return 1.0
        if isinstance(duck_value, dict):
            v = duck_value.get("v", 1.0)
        else:
            v = duck_value.value
        return max(0.0, min(1.0, float(v)))
    except Exception:
        return 1.0


def _scale_chunk(data: bytes, width: int, factor: float) -> bytes:
    """PCM chunk 按系数缩放（audioop.mul，C 实现）。audioop=标准库且 pydub
    自身依赖（随 pydub 同生共死）——不扩大薄入口 import 面（spawn 冷启动契约）。
    缩放失败原样返回（闪避降级=不压，不影响播放）。"""
    try:
        import audioop as _audioop
        return _audioop.mul(data, width, factor)
    except Exception:
        return data


def _backing_process_main(backing_path: str, volume: float,
                          start_event=None, started_event=None,
                          started_value=None, stop_event=None,
                          duck_value=None) -> None:
    """伴奏播放子进程主函数（全部生命周期：加载→门等待→起播→写流→自退）。

    Args:
        backing_path: 伴奏 wav 路径（仅来自 cover 管线内部路径，不经用户文本）
        volume: 音量 0.0-1.0（<=0=静音退出——仍置回执哨兵，防主进程白等）
        start_event: 主→子起播门（None=直调 open 即起播，兼容直调旧语义）
        started_event: 子→主起播回执事件
        started_value: 子→主起播时刻载体（mp.Value('d') 哨兵 0.0 / dict）
        stop_event: 主→子协作停止（每 chunk 检查；None=不可协作停止）
        duck_value: 主→子闪避系数载体（2026-11-12 伴奏闪避批次；每 chunk
            读取，1.0=原音量，<1=按系数缩放；None=既有行为不缩放）
    """
    tmp_path = None
    pa = stream = wf = None
    started_reported = False
    try:
        import math
        import wave as _wave
        import pyaudio as _pa
        from pydub import AudioSegment

        seg = AudioSegment.from_file(backing_path)
        vol = max(0.0, min(1.0, float(volume or 0.8)))
        if vol <= 0 or not seg:
            return  # finally 统一置回执哨兵
        seg = seg.apply_gain(20.0 * math.log10(vol))
        tmp_path = backing_path + ".backing.tmp.wav"
        seg.export(tmp_path, format="wav")
        wf = _wave.open(tmp_path, "rb")
        pa = _pa.PyAudio()
        stream = pa.open(
            format=pa.get_format_from_width(wf.getsampwidth()),
            channels=wf.getnchannels(), rate=wf.getframerate(), output=True,
            start=(start_event is None))
        if start_event is not None:
            # 起播门：等待主进程人声受理锚点——超时兜底防悬挂（fail-open）
            if not start_event.wait(timeout=360.0):
                print("[song] 伴奏起播门超时（360s），照常起播")
            stream.start_stream()
        # 真实起播回执（顺序契约：先 Value 后 Event；Windows monotonic 跨
        # 进程同基准）
        import time as _time
        _report_started(started_event, started_value, _time.monotonic())
        started_reported = True
        # 写流循环：chunk 4096 帧（≈93ms@44.1k——调度点减少 4x）；协作停止
        # 每 chunk 检查（F4/D22：正常 close 释放设备句柄）；闪避系数每 chunk
        # 读取（2026-11-12 伴奏闪避批次——93ms 粒度即时生效），factor<1 才
        # 缩放（正常路径零复制零开销）
        chunk = 4096 * wf.getnchannels() * wf.getsampwidth()
        data = wf.readframes(4096)
        while data:
            if stop_event is not None and stop_event.is_set():
                break
            _factor = _read_duck(duck_value)
            if _factor < 0.999:
                data = _scale_chunk(data, wf.getsampwidth(), _factor)
            stream.write(data)
            data = wf.readframes(4096)
        print(f"[song] 伴奏外放完成：{backing_path}")
    except Exception as e:
        print(f"[song] 伴奏外放失败（主进程侧人声照唱）：{backing_path} → {e}")
    finally:
        if not started_reported:
            # 全退出路径统一置哨兵+回执（F9：含 vol<=0/加载失败/异常——防
            # 主进程每首歌白等门超时 10s）
            import time as _time
            _report_started(started_event, started_value, _time.monotonic())
        for closer in (lambda: stream.stop_stream() if stream else None,
                       lambda: stream.close() if stream else None,
                       lambda: wf.close() if wf else None,
                       lambda: pa.terminate() if pa else None):
            try:
                closer()
            except Exception:
                pass
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass  # terminate 强杀场景由主进程侧清扫兜底


def play_backing_in_process(backing_path: str, volume: float,
                            start_event, started_event, started_value,
                            stop_event=None, duck_value=None):
    """主进程侧便捷启动（mp.Process(target=本模块顶层函数)——target 可 pickle；
    返回未启动的 Process，调用方 start/terminate/引用管理）。独立函数便于
    测试桩替换。"""
    import multiprocessing as _mp
    return _mp.Process(
        target=_backing_process_main,
        args=(backing_path, float(volume or 0.8), start_event, started_event,
              started_value, stop_event, duck_value),
        daemon=True, name="song-backing")
