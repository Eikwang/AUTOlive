"""
音频播放管理模块 - 管理音频播放、文案播放等功能
"""

import asyncio
import copy
import math
import random
import threading
import traceback
import os
from ..my_log import logger
from ..edtalk_realtime import is_edtalk_active
from utils.web_captions import get_captions_manager

# 在役播放器分派值集合（义务 C2/Eng A2：模块级单一事实源——playback_manager
# 两处分派判断与测试矩阵枚举共用，防字面量共同漂移；audio_player_v2 为活值）
PLAYER_KEYS = ["audio_player", "audio_player_v2", "builtin"]

# 编排纯画面窗口音频等待上限（防呆兜底）：正常窗口 ≤ 三步 duration_s 之和
# （enter/main/exit，用户配置通常各 10-30s）；看门狗 F1 保证窗口必然推进，
# 超上限按异常降级照推（宁可口型出现不丢语音）。
_CHOREO_MUTE_MAX_WAIT = 300.0


def _coerce_interval(value, default):
    """播放间隔读侧类型归一化（2026-10-24 实测修复）。

    前端 ui.input 文本框把数字落盘为字符串（实测 play_audio.play_interval_max
    ='0.8'），原始值直传 get_random_value 抛 TypeError（float 与 str 比较）——
    出队后的待播条目被 only_play_audio 的 except 吞掉丢弃：合成成功但 EDTalk
    无推送、本地无声。str/None/非有限值一律归一化，非法回退 default（对齐
    builtin _post_play_interval 的 float() 读侧归一化既定模式）。
    """
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(result):
        return default
    return result


def _resolve_play_interval(config):
    """A4 新旧键回退 + 读侧类型归一化（2026-10-24 实测修复，见 _coerce_interval）。

    回退链：play_interval_min/max（新键）→ normal_interval_min/max（旧键，
    A4 一次性兼容）→ 默认 0.3/0.5（config.example.json 默认域）。
    """
    interval_min = config.get("play_audio", "play_interval_min")
    if interval_min is None:
        interval_min = config.get("play_audio", "normal_interval_min")
    interval_max = config.get("play_audio", "play_interval_max")
    if interval_max is None:
        interval_max = config.get("play_audio", "normal_interval_max")
    return _coerce_interval(interval_min, 0.3), _coerce_interval(interval_max, 0.5)


def _play_backing_local(backing_path: str, volume: float):
    """R6 双轨伴奏外放（/autoplan 2026-10-23 批次，EDTalk 激活场景）：点歌伴奏轨
    独立 pyaudio 流本地播放（人声从数字人端出、伴奏本地出——双设备分工），随播完
    自退。失败仅告警不抛（R6 降级语义=人声照唱，伴奏缺失不黑场；F-S5 可观测）。"""
    tmp_path = None
    pa = stream = wf = None
    try:
        import math
        import wave as _wave
        import pyaudio as _pa
        from pydub import AudioSegment
        seg = AudioSegment.from_file(backing_path)
        vol = max(0.0, min(1.0, float(volume or 0.8)))
        if vol <= 0 or not seg:
            return
        seg = seg.apply_gain(20.0 * math.log10(vol))
        tmp_path = backing_path + ".backing.tmp.wav"
        seg.export(tmp_path, format="wav")
        wf = _wave.open(tmp_path, "rb")
        pa = _pa.PyAudio()
        stream = pa.open(
            format=pa.get_format_from_width(wf.getsampwidth()),
            channels=wf.getnchannels(), rate=wf.getframerate(), output=True)
        chunk = 1024 * wf.getnchannels() * wf.getsampwidth()
        data = wf.readframes(1024)
        while data:
            stream.write(data)
            data = wf.readframes(1024)
        logger.info(f"[song] 伴奏外放完成：{backing_path}")
    except Exception as e:
        logger.warning(f"[song] 伴奏外放失败（人声照唱，伴奏缺失不黑场）：{backing_path} → {e}")
    finally:
        for closer in (lambda: stream.stop_stream(), lambda: stream.close(),
                       lambda: wf.close(), lambda: pa.terminate()):
            try:
                closer()
            except Exception:
                pass
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


class PlaybackMixin:
    """音频播放管理Mixin类"""
    
    def stop_audio(self, type: str="pygame", mixer_normal: bool=True, mixer_copywriting: bool=True):
        """停止音频播放

        A3（2026-10-22 审查批次）：mixer_copywriting 分支退役移除（文案模块已弃用，
        copywriting mixer 与 normal 同为 pygame.mixer 副本、双停本就冗余）；参数保留
        兼容既有调用方（conversation_manager/audio_processor 均传 True,True）。
        """
        try:
            if type == "pygame":
                if mixer_normal:
                    self.mixer_normal.music.stop()
                    logger.info("停止普通音频播放")
                return True
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"停止音频播放失败：{e}")
            return False

    async def only_play_audio(self):
        """只进行普通音频播放"""
        try:
            try:
                if self.config.get("play_audio", "player") in ["pygame"]:
                    self.mixer_normal.init()
            except Exception as e:
                logger.error(traceback.format_exc())
                logger.error("pygame mixer_normal初始化失败，普通音频将无法正常播放，请检查声卡是否正常！")

            while True:
                try:
                    # 获取线程锁，避免同时操作
                    with self.voice_tmp_path_queue_lock:
                        while not self.voice_tmp_path_queue:
                            # 消费者在消费完一个消息后，如果列表为空，则调用wait()方法阻塞自己，直到有新消息到来
                            self.voice_tmp_path_queue_not_empty.wait()  # 阻塞直到列表非空
                        data_json = self.voice_tmp_path_queue.pop(0)

                    logger.debug(f"普通音频播放队列 即将播放音频 data_json={data_json}")

                    voice_tmp_path = data_json["voice_path"]

                    # A7/T1（2026-10-22 审查批次）：999 单实例 active 标记——条目离开
                    # 待播放队列即视为进入播放系统；完成/跳过/清空时释放（pygame/EDTalk
                    # 在本函数释放，builtin 经 release hook 在播放器线程释放）。
                    _was_999 = False
                    try:
                        _p = self.config.get("filter", "priority_mapping").get(data_json.get("type"))
                        _was_999 = (_p is not None and int(_p) == 999)
                    except Exception:
                        _was_999 = False
                    if _was_999:
                        self.active_reread_top_999 = True

                    # 字幕推送时序对齐（2026-10-25 用户实测批次）：原出队公共段 push
                    # 已退役——EDTalk 音频尚需播放间隔 + 编排静默窗口等待（可达分钟级）
                    # 才进数字人服务器，字幕系统性超前。现推送点=三个开播精确位：
                    # EDTalk→push_full 前（_captions_push）；外部播放器→play 前
                    # （builtin 除外）；pygame→music.play 前。builtin 仍由 F3 开播
                    # 回调在真实起播时刻推送（audio_core 接线，最精确）。

                    # A4（2026-10-22 审查批次，义务 R2-A4/R7）：播放间隔改名——新键
                    # play_interval_min/max，旧键 normal_interval_min/max 读侧一次性
                    # 兼容回退（不静默回落默认；旧键保留不删=回滚友好，前端保存侧写新键）。
                    # A3（设计项3+义务 R2-A3/E5）：interval_num 重复次数参数退役——
                    # 间隔=单次随机取值；默认值域下节奏由 0.3~1.0s 变为 0.3~0.5s
                    # （F-10，已入发布说明与回归声明）。
                    # 2026-10-24 实测修复：回退后做读侧类型归一化（_resolve_play_interval）
                    # ——前端文本框落盘字符串（实测 play_interval_max='0.8'）直传比较
                    # 抛 TypeError，出队条目被下方 except 吞掉丢弃：合成成功但 EDTalk
                    # 无推送、本地无声（口型与声音全无的根因）。
                    interval_min, interval_max = _resolve_play_interval(self.config)
                    normal_interval = self.common.get_random_value(interval_min, interval_max)

                    # 不仅仅是说话间隔，还是等待文本捕获刷新数据
                    await asyncio.sleep(normal_interval)

                    # A2（2026-10-22 审查批次）：pydub 随机变速路径已退役删除（义务 R2-A2，
                    # 不留开关）——随机语速源迁移至合成侧（synthesis_manager
                    # ._resolve_random_synth_speed，经 TTS 引擎参数 rate/pitch/speed_factor
                    # 生效），条目经 synth_speed 标记识别。变速仅适用 TTS 合成音频；
                    # 本地音频/点歌（tts_type=none）不经合成侧，天然不再变速（边界声明）。
                    # （P26 双路径过渡态终结：实测确认 inproc speed_factor 生效后退役回退路径）

                    # print(voice_tmp_path)

                    # 根据接入的虚拟身体类型执行不同逻辑（2026-10-22：metahuman_stream 驱动退役，EDTalk 分流为唯一特殊路径）
                    # EDTalk 实时推理模式（EDTalk功能集成计划 §4.3.3）：跳过本地播放——
                    # EDTalk 端 sounddevice 帧锁定播放（本地播放会双声），TTS 落盘
                    # （含变速）后仅推送 /audio/push_full。推送协程内阻塞等待完成
                    # 判定（背压），失败降级记录不阻塞直播主流程。
                    # 根据播放器类型进行区分
                    if is_edtalk_active(self.config):
                        # R6 双轨（/autoplan 2026-10-23 批次）：song 消息带 backing_path
                        # 伴奏轨时——人声轨推 EDTalk 驱动对口型（数字人端出声），伴奏轨
                        # 本地外放并行起播（粗同步，偏差判据 <0.5s=R6 义务）；伴奏线程
                        # 随文件播完自退，push_full 返回=人声飞行完成。R12 时序：伴奏与
                        # 人声同起，无三路并发（文案播报已在队列前序完成）。
                        _backing_path = data_json.get("backing_path") or ""
                        if _backing_path and os.path.isfile(_backing_path):
                            threading.Thread(
                                target=_play_backing_local,
                                args=(_backing_path, float(data_json.get("backing_volume", 0.8) or 0.8)),
                                name="song-backing", daemon=True).start()
                        elif data_json.get("backing_path"):
                            logger.warning(f"[song] 伴奏轨缺失，人声照唱：{_backing_path}")
                        # 口型推理联动（2026-10-25 goal）：编排纯画面窗口
                        # （audio_mode=none 的跳舞/转场片段）内延迟推送——数字人
                        # 只播画面不出声不做口型；音频在此等待（队列背压天然保序
                        # 不丢失），窗口由编排看门狗（F1 duration 上限）保证必然
                        # 结束。带语音步骤（tts/file）不构成窗口，正常驱动口型。
                        _choreo_guard = None
                        try:
                            from utils.orchestration import get_choreographer
                            _choreo_guard = get_choreographer()
                        except Exception:
                            _choreo_guard = None
                        if _choreo_guard is not None and _choreo_guard.in_mute_window():
                            _mute_remaining = _choreo_guard.mute_window_remaining_s()
                            logger.info(
                                f"[EDTalk] 编排纯画面窗口内，音频延迟推送"
                                f"（剩余约 {_mute_remaining if _mute_remaining is not None else '?'}s）："
                                f"{data_json.get('content', '')[:20]}")
                            _waited = 0.0
                            while _choreo_guard.in_mute_window() and _waited < _CHOREO_MUTE_MAX_WAIT:
                                await asyncio.sleep(0.2)
                                _waited += 0.2
                            if _choreo_guard.in_mute_window():
                                # 防呆上限兜底：仍处窗口也照推（宁可口型出现不丢语音）
                                logger.warning(
                                    f"[EDTalk] 纯画面窗口等待超 {_CHOREO_MUTE_MAX_WAIT}s，降级照推"
                                    f"（problem=窗口未按时结束；cause=编排看门狗异常；fix=检查编排状态）")
                        # 字幕时序对齐：推送点=音频进入数字人服务器前一刻
                        # （历经播放间隔与编排静默窗口等待后）——数字人开口即出字。
                        await self._captions_push(data_json)
                        await self.edtalk_client.push_full(
                            voice_tmp_path, content=data_json.get("content", "")
                        )
                        # 动作编排完成回调（/autoplan 2026-10-21 批次，义务 CL-2/D-2）：
                        # EDTalk push 路径每条目完成处（push_full 返回=单飞行句完成判定）。
                        # builtin 的 play() 为入队背压非播放结束——编排完成观测仅声明
                        # 支持 EDTalk 激活场景（前置条件 is_edtalk_active，RUNBOOK 边界）。
                        try:
                            from utils.orchestration import get_choreographer
                            _choreo = get_choreographer()
                            if _choreo is not None:
                                _choreo.on_audio_complete(
                                    data_json.get("type", ""), data_json.get("content", ""))
                        except Exception as _choreo_err:
                            logger.debug(f"动作编排完成回调异常（依赖 F1 超时兜底）：{_choreo_err}")
                        if _was_999:
                            self.active_reread_top_999 = False
                        # A7/F-5 短路声明：EDTalk 激活时 999 暂停-续播链路显式不可达——
                        # 本分支不进 builtin 播放器（push_full 帧锁定播放），999 按队列
                        # 优先级自然排序推送；999 单实例约束仍由 queue_manager 层保证。
                    elif self.config.get("play_audio", "player") in PLAYER_KEYS:
                        if "insert_index" in data_json:
                            data_json = {
                                "type": data_json["type"],
                                "voice_path": voice_tmp_path,
                                "content": data_json["content"],
                                "random_speed": {
                                    "enable": False,
                                    "max": 1.3,
                                    "min": 0.8
                                },
                                "speed": 1,
                                "insert_index": data_json["insert_index"]
                            }
                        else:
                            data_json = {
                                "type": data_json["type"],
                                "voice_path": voice_tmp_path,
                                "content": data_json["content"],
                                "random_speed": {
                                    "enable": False,
                                    "max": 1.3,
                                    "min": 0.8
                                },
                                "speed": 1
                            }
                        # 外部播放器：play() 即近似开播点；builtin 的字幕由 F3 开播
                        # 回调在真实起播时刻推送（play()=入队非同步播放，此处推会
                        # 随队列积压超前），跳过。
                        if self.config.get("play_audio", "player") != "builtin":
                            await self._captions_push(data_json)
                        self.audio_player.play(data_json)
                    else:
                        logger.debug(f"voice_tmp_path={voice_tmp_path}")
                        import pygame

                        try:
                            # 使用pygame播放音频
                            self.mixer_normal.music.load(voice_tmp_path)
                            # 字幕时序对齐：本地播放起点（load 成功后、开播前一刻）
                            await self._captions_push(data_json)
                            self.mixer_normal.music.play()
                            while self.mixer_normal.music.get_busy():
                                pygame.time.Clock().tick(10)
                            self.mixer_normal.music.stop()
                            # A3：send_audio_play_info_to_callback 回调随 info_to_callback
                            # 参数退役移除（回路死链，2026-10-22 审查批次）
                        except pygame.error as e:
                            logger.error(traceback.format_exc())
                            # 如果发生 pygame.error 异常，则捕获并处理它
                            logger.error(f"无法加载音频文件:{voice_tmp_path}。请确保文件格式正确且文件未损坏。可能原因是TTS配置有误或者TTS服务端有问题，可以去服务端排查一下问题")
                        finally:
                            if _was_999:
                                # A7/T1：pygame 路径 999 完成释放（builtin 走 release hook）
                                self.active_reread_top_999 = False

                except Exception as e:
                    logger.error(traceback.format_exc())
            self.mixer_normal.quit()
        except Exception as e:
            logger.error(traceback.format_exc())


    async def _captions_push(self, data_json):
        """字幕推送（时序对齐版，2026-10-25 用户实测批次）：调用点=音频进
        数字人服务器/系统开播前一刻。原出队公共段推送随队列积压与编排静默
        窗口系统性超前（EDTalk 场景可达分钟级），已退役。全捕获（S4：字幕
        错误不得传播进播放循环）。"""
        try:
            manager = get_captions_manager()
            if manager is not None:
                await manager.push(data_json)
        except Exception:
            logger.error("字幕推送异常（已隔离，不影响播放）\n" + traceback.format_exc())

    def stop_current_audio(self):
        """停止当前播放的音频

        注意（EDTalk 模式语义变化，native E6）：edtalk_realtime 激活时本地播放
        已被跳过，弹幕打断只作用于本地 mixer/audio_player；EDTalk 端无
        flush-audio 契约端点，打断不会停止其音频播放。已列入 edtalk/README
        语义变化清单。
        """
        if is_edtalk_active(self.config):
            # EDTalk 契约无音频 flush 端点——静默跳过（保持既有调用方兼容）
            return
        if self.config.get("play_audio", "player") in PLAYER_KEYS:
            self.audio_player.skip_current_stream()
        else:
            self.mixer_normal.music.fadeout(1000)
