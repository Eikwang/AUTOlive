"""
音频合成管理模块 - 管理TTS合成、变声等功能
"""

import asyncio
import aiohttp
import os
import random
import traceback
from copy import deepcopy
from ..my_log import logger
from ..edtalk_realtime import is_edtalk_active
from ..audio_handle.tts_params import clamp_speed_factor


class SynthesisMixin:
    """音频合成管理Mixin类"""
    
    async def message_queue_thread(self):
        """音频合成消息队列线程"""
        logger.info("创建音频合成消息队列线程")
        while True:  # 无限循环，直到队列为空时退出
            try:
                # 获取线程锁，避免同时操作
                with self.message_queue_lock:
                    while not self.message_queue:
                        # 消费者在消费完一个消息后，如果列表为空，则调用wait()方法阻塞自己，直到有新消息到来
                        self.message_queue_not_empty.wait()  # 阻塞直到列表非空
                    message = self.message_queue.pop(0)
                logger.debug(message)

                # 此处的message数据，是等待合成音频的数据，此数据经过了优先级排队在此线程中被取出，即将进行音频合成。
                # 由于有些对接的项目自带音频播放功能，所以为保留相关机制的情况下做对接，此类型的对接源码应写于此处
                #
                # 音频分流（EDTalk功能集成计划 §4.3.2，与启动条件对齐杜绝静音组合；
                # 2026-10-22 批次：metahuman_stream 驱动退役（G2）——本地 TTS 单路径）：
                # - edtalk_realtime 且 enable：走本地 TTS 合成 → 播放环节跳过本地播放、
                #   推送 /audio/push_full 给 EDTalk 帧锁定播放（playback_manager 分流）
                # - edtalk_realtime 但 enable=false：走本地 TTS + 本地正常播放
                #   （有声音、无数字人画面）——UI 侧常驻警告提示服务未启用
                if self.config.get("visual_body") == "edtalk_realtime" and not is_edtalk_active(self.config):
                    logger.warning(
                        "数字人服务未启用（edtalk_realtime.enable=false）："
                        "本次音频将走本地声音播放，无数字人画面与口型"
                    )

                # 合成音频并插入待播放队列
                await self.my_play_voice(message)

                # E2 饥饿路径 flush：突发结束（待合成队列空且无在途合成）时，
                # 若待播放队列积压未达首次播放阈值，立即放行——规则见
                # QueueMixin.flush_starved_playback_queue 注释
                self.flush_starved_playback_queue()

                # message = Audio.message_queue.get(block=True)
                # logger.debug(message)
                # await self.my_play_voice(message)
                # Audio.message_queue.task_done()

                # 加个延时 降低点edge-tts的压力
                # await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(traceback.format_exc())


    async def so_vits_svc_api(self, audio_path=""):
        """调用so-vits-svc的api"""
        try:
            url = f"{self.config.get('so_vits_svc', 'api_ip_port')}/wav2wav"
            
            params = {
                "audio_path": audio_path,
                "tran": self.config.get("so_vits_svc", "tran"),
                "spk": self.config.get("so_vits_svc", "spk"),
                "wav_format": self.config.get("so_vits_svc", "wav_format")
            }

            # logger.info(params)

            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=params) as response:
                    if response.status == 200:
                        file_name = 'so-vits-svc_' + self.common.get_bj_time(4) + '.wav'

                        voice_tmp_path = self.common.get_new_audio_path(self.config.get("play_audio", "out_path"), file_name)
                        
                        with open(voice_tmp_path, 'wb') as file:
                            file.write(await response.read())

                        logger.debug(f"so-vits-svc转换完成，音频保存在：{voice_tmp_path}")

                        return voice_tmp_path
                    else:
                        logger.error(await response.text())

                        return None
        except Exception as e:
            logger.error(traceback.format_exc())
            return None


    def audio_synthesis(self, message):
        """音频合成（edge-tts / vits_fast等）并播放"""
        try:
            logger.debug(message)

            # TTS类型为 none 时不合成音频
            if self.config.get("audio_synthesis_type") == "none":
                return

            # 将用户名字符串中的数字转换成中文
            if self.config.get("filter", "username_convert_digits_to_chinese"):
                if message["username"] is not None:
                    message["username"] = self.common.convert_digits_to_chinese(message["username"])

            # 判断是否是点歌模式
            if message['type'] == "song":
                # 拼接json数据，存入队列
                data_json = {
                    "type": message['type'],
                    "tts_type": "none",
                    "voice_path": message['content'],
                    "content": message["content"]
                }

                # 是否开启了音频播放 
                if self.config.get("play_audio", "enable"):
                    self.data_priority_insert("等待合成消息", data_json)
                return
            # 异常报警
            elif message['type'] == "abnormal_alarm":
                # 拼接json数据，存入队列
                data_json = {
                    "type": message['type'],
                    "tts_type": "none",
                    "voice_path": message['content'],
                    "content": message["content"]
                }

                # 是否开启了音频播放 
                if self.config.get("play_audio", "enable"):
                    self.data_priority_insert("等待合成消息", data_json)
                return
            # 是否为本地问答音频
            elif message['type'] == "local_qa_audio":
                # 拼接json数据，存入队列
                data_json = {
                    "type": message['type'],
                    "tts_type": "none",
                    "voice_path": message['file_path'],
                    "content": message["content"]
                }

                # 是否开启了音频播放
                if self.config.get("play_audio", "enable"):
                    self.data_priority_insert("等待合成消息", data_json)
                return
            # 是否为助播-本地问答音频
            elif message['type'] == "assistant_anchor_audio":
                # 拼接json数据，存入队列
                data_json = {
                    "type": message['type'],
                    "tts_type": "none",
                    "voice_path": message['file_path'],
                    "content": message["content"]
                }

                # 是否开启了音频播放
                if self.config.get("play_audio", "enable"):
                    self.data_priority_insert("等待合成消息", data_json)
                return

            # 闲时任务
            elif message['type'] == "idle_time_task":
                if message['content_type'] in ["comment", "reread"]:
                    pass
                elif message['content_type'] == "local_audio":
                    # A11（2026-10-22 审查批次，义务 R2-A11/E4）：本地音频类维持 enable
                    # 门控（原分支漏检直接入队，关闭态仍会播出——审查发现 R1(7)）；
                    # 本地音频不走 EDTalk 豁免（F-7：闲时音效不应推给数字人端）。
                    if not self.config.get("play_audio", "enable"):
                        return
                    # 拼接json数据，存入队列
                    data_json = {
                        "type": message['type'],
                        "tts_type": "none",
                        "voice_path": message['file_path'],
                        "content": message["content"]
                    }
                    
                    self.data_priority_insert("等待合成消息", data_json)

                    return
            # 按键映射 本地音频
            elif message['type'] == "key_mapping" and "file_path" in message:
                # 拼接json数据，存入队列
                data_json = {
                    "type": message['type'],
                    "tts_type": "none",
                    "voice_path": message['file_path'],
                    "content": message["content"]
                }

                # 是否开启了音频播放
                if self.config.get("play_audio", "enable"):
                    self.data_priority_insert("等待合成消息", data_json)
                return

            # 是否语句切分
            if self.config.get("play_audio", "text_split_enable"):
                sentences = self.common.split_sentences(message['content'])
                for s in sentences:
                    message_copy = deepcopy(message)  # 创建 message 的副本
                    message_copy["content"] = s  # 修改副本的 content
                    logger.debug(f"s={s}")
                    if not self.common.is_all_space_and_punct(s):
                        self.data_priority_insert("等待合成消息", message_copy)  # 将副本放入队列中
            else:
                self.data_priority_insert("等待合成消息", message)
            

            # 单独开线程播放
            # threading.Thread(target=self.my_play_voice, args=(type, data, config, content,)).start()
        except Exception as e:
            logger.error(traceback.format_exc())
            return


    async def voice_change(self, voice_tmp_path):
        """音频变声 so-vits-svc

        Args:
            voice_tmp_path (str): 待变声音频路径

        Returns:
            str: 变声后的音频路径
        """
        # 转换为绝对路径
        voice_tmp_path = os.path.abspath(voice_tmp_path)

        # 是否启用so-vits-svc来变声
        if True == self.config.get("so_vits_svc", "enable"):
            voice_tmp_path = await self.so_vits_svc_api(audio_path=voice_tmp_path)
            if voice_tmp_path:
                logger.info(f"so_vits_svc合成成功，输出到={voice_tmp_path}")
            else:
                logger.error(f"so_vits_svc合成失败，请检查配置")
                self.abnormal_alarm_handle("svc")
                
                return None
        
        return voice_tmp_path
    

    def _resolve_random_synth_speed(self):
        """A2（2026-10-22 审查批次）：随机语速源迁移合成侧（义务 R2-A2/spec C-1）。

        读 audio_random_speed.normal.{enable,speed_min,speed_max}——键位保留、
        语义变更为合成侧随机语速源（旧 pydub 读侧随路径退役）；enable=false 或
        取值非法时返回 None（不变速=系统默认）。
        边界：tts_type=none 的本地音频/点歌在 audio_synthesis 短路直插播放队列、
        不经本函数，天然满足"本地音频与点歌不变速"边界声明（song 条目
        tts_type=none 已带标记，Feasibility 条件核实关闭）。
        """
        try:
            if not self.config.get("audio_random_speed", "normal", "enable"):
                return None
            _warn = lambda m: logger.warning(f"[变速] {m}")
            lo = clamp_speed_factor(self.config.get("audio_random_speed", "normal", "speed_min"), log_bad=_warn)
            hi = clamp_speed_factor(self.config.get("audio_random_speed", "normal", "speed_max"), log_bad=_warn)
            lo, hi = min(lo, hi), max(lo, hi)
            factor = random.uniform(lo, hi)
            logger.info(f"[变速] 随机语速 factor={factor:.3f}（域 [{lo}, {hi}]）")
            return factor
        except Exception:
            logger.error(traceback.format_exc())
            return None

    async def tts_handle(self, message):
        """根据本地配置，使用TTS进行音频合成，返回相关数据

        Args:
            message (dict): json数据，含tts配置，tts类型

        Returns:
            dict: json数据，含tts配置，tts类型，合成结果等信息
        """

        try:
            # A2：随机语速源合成侧解析（enable=false → None=系统默认；本地音频不经此路径）
            _random_speed = self._resolve_random_synth_speed()

            if message["tts_type"] == "gpt_sovits":
                # P25 键面重构（实测修复）：旧 data.language/api_0322/api_0706/v2_api_0821/webtts
                # 键随参数精简退役——inproc 装配走 params 白名单（my_tts._gpt_sovits_inproc），
                # api 兜底路径用 .get({}) 保旧结构不炸。语言：params.text_lang 缺省"自动识别"
                # （动态检测后回写代码值，inproc 装配透传）。
                _gdata = message["data"]
                _params = _gdata.get("params") or {}
                _lang_raw = _params.get("text_lang") or _gdata.get("language") or "自动识别"
                if _lang_raw == "自动识别":
                    # 动态检测语言（ref_free 模式配套）——2026-10-22 批次（P13 潜伏缺陷修复）：
                    # 原调用的 common.lang_check 已弃用不存在（AttributeError 雷），
                    # 改走新建轻量 lang_check（TextMixin，粤/谚/假/拉/汉字符区段判定）
                    language = self.common.lang_check(message["content"])
                    logger.debug(f'language={language}')
                    _lang_code = language if language in ("en", "zh", "ja", "ko") else "zh"
                    _params.setdefault("text_lang", _lang_code)
                else:
                    # 显示名→代码值（inproc 装配 text_lang 需代码值；params.text_lang 优先已回写）
                    _name_to_code = {"英文": "en", "中文": "zh", "日文": "ja", "韩文": "ko",
                                     "所有": "auto", "粤语": "auto_yue"}
                    _lang_code = _name_to_code.get(_lang_raw, _params.get("text_lang", "zh"))
                    language = _lang_code

                # need_lang（回复语言）映射覆盖 params 静态值（G3/spec L1 落地路径，2026-10-22）——
                # 代码值直通 data.language→my_tts._gpt_sovits_inproc merged.text_lang→引擎
                # （{none→auto, zh→zh, en→en, jp→ja, ko→ko, yue→auto_yue}）
                _need_lang = self.config.get("need_lang") or "none"
                _need_to_text_lang = {"none": "auto", "zh": "zh", "en": "en",
                                      "jp": "ja", "ko": "ko", "yue": "auto_yue"}
                if _need_lang in _need_to_text_lang:
                    _lang_code = _need_to_text_lang[_need_lang]
                    language = _lang_code

                data = {
                    "type": _gdata.get("type", "gpt_sovits"),
                    "gradio_ip_port": _gdata.get("gradio_ip_port", ""),
                    "api_ip_port": _gdata.get("api_ip_port", ""),
                    "backend": _gdata.get("backend", "inproc"),
                    "params": _params,
                    "ref_audio_path": _gdata.get("ref_audio_path", _params.get("ref_audio_path", "")),
                    "prompt_text": _gdata.get("prompt_text", _params.get("prompt_text", "")),
                    "prompt_language": _gdata.get("prompt_language", _params.get("prompt_lang", "zh")),
                    "language": _lang_code,
                    "cut": _gdata.get("cut", _params.get("text_split_method", "cut3")),
                    "api_0322": _gdata.get("api_0322", {}),
                    "api_0706": _gdata.get("api_0706", {}),
                    "v2_api_0821": _gdata.get("v2_api_0821", {}),
                    "webtts": _gdata.get("webtts", {}),
                    # P26/A2：运行时语速透传（合成参数变速；None=系统默认）
                    "speed": _random_speed,
                    "content": message["content"]
                }

                voice_tmp_path = await self.my_tts.gpt_sovits_api(data)
            elif message["tts_type"] == "edge_tts":
                # edge-tts 合成（切片 3：WebTTS 退役改用 EDGE-TTS；P26/A2 语速经 rate 映射+pitch 联动）
                voice_tmp_path = await self.my_tts.edge_tts_api({
                    "content": message["content"],
                    "speed": _random_speed,
                })
            elif message["tts_type"] == "none":
                # Audio.voice_tmp_path_queue.put(message)
                voice_tmp_path = None

            message["result"] = {
                "code": 200,
                "msg": "合成成功",
                "audio_path": voice_tmp_path
            }
            # P26/A2：合成侧已应用语速时随结果透传（播放侧据此识别；pydub 回退路径已退役）
            if voice_tmp_path and _random_speed is not None:
                try:
                    message["result"]["synth_speed"] = float(_random_speed)
                except (TypeError, ValueError):
                    pass
        except Exception as e:
            logger.error(traceback.format_exc())
            message["result"] = {
                "code": -1,
                "msg": f"合成失败，{e}",
                "audio_path": None
            }

        return message

    # A3（2026-10-22 审查批次）：send_audio_play_info_to_callback 方法随
    # play_audio.info_to_callback 参数退役删除——回路接收端（/callback 端点）本就
    # 仅打日志（死链，wait_play_audio_num 恒 0 从未写入），移除零功能损失；
    # builtin 完成回调注册一并移除（audio_core）。

    async def audio_synthesis_use_local_config(self, content, audio_synthesis_type="gpt_sovits"):
        """使用本地配置进行音频合成，返回音频路径（页面合成测试链 /tts/synthesize）

        【2026-10-22 审查批次自 processing_manager.py 迁入】——原文件随 pydub 变速
        路径（audio_speed_change）与文案合并（merge_audio_files）退役删除，本测试链
        方法为在飞消费点（web_server /tts/synthesize），迁移至 SynthesisMixin 保链。
        D-20 双代码路径纪律：本方法=页面测试链，与弹幕链（tts_handle）同键面但独立
        存在，改键面时两链同改。

        切片 3 键面重写（2026-10-20 实测修复）：与弹幕链同键面——gpt_sovits 参数装配
        收口在 _gpt_sovits_inproc（config.params 为事实来源，C-1），edge_tts 走
        edge_tts_api（voice/rate 自 edge_tts 节）。

        Args:
            content (str): 待合成的文本内容
            audio_synthesis_type (str, optional): 使用的tts类型. Defaults to "gpt_sovits".

        Returns:
            str: 合成的音频的路径（失败返回 None）
        """
        # 重载配置
        self.reload_config(self.config_path)

        voice_tmp_path = None
        if audio_synthesis_type == "gpt_sovits":
            data = {
                # P26：测试链默认语速取 params.speed_factor（与弹幕链 speed 透传同语义；
                # 测试链为静态配置语速，不走弹幕链随机语速源——语义差异登记 D-20）
                "speed": self.config.get("gpt_sovits", "params", "speed_factor"),
                "content": content
            }
            voice_tmp_path = await self.my_tts.gpt_sovits_api(data)
        elif audio_synthesis_type == "edge_tts":
            voice_tmp_path = await self.my_tts.edge_tts_api({"content": content})
        else:
            logger.error(f"不支持的合成类型：{audio_synthesis_type}（可选 gpt_sovits / edge_tts）")

        return voice_tmp_path


    async def my_play_voice(self, message):
        """合成音频并插入待播放队列

        Args:
            message (dict): 待合成内容的json串

        Returns:
            bool: 合成情况
        """
        logger.debug(message)

        try:
            # 如果是tts类型为none，暂时这类为直接播放音频，所以就丢给路径队列
            if message["tts_type"] == "none":
                self.data_priority_insert("待播放音频列表", message)
                return
        except Exception as e:
            logger.error(traceback.format_exc())
            return

        try:
            logger.debug(f"合成音频前的原始数据：{message['content']}")
            message["content"] = self.common.remove_extra_words(message["content"], message["config"]["max_len"], message["config"]["max_char_len"])
            # logger.info("裁剪后的合成文本:" + text)

            message["content"] = message["content"].replace('\n', '。')

            # 空数据就散了吧
            if message["content"] == "":
                return
        except Exception as e:
            logger.error(traceback.format_exc())
            return
        

        # 判断消息类型，再变声并封装数据发到队列 减少冗余
        async def voice_change_and_put_to_queue(message, voice_tmp_path, synth_speed=None):
            # 拼接json数据，存入队列
            data_json = {
                "type": message['type'],
                "voice_path": voice_tmp_path,
                "content": message["content"]
            }

            # P26：合成侧已应用语速时随条目透传（playback_manager 据此跳过 pydub 变速）
            if synth_speed is not None:
                data_json["synth_speed"] = synth_speed

            if "insert_index" in message:
                data_json["insert_index"] = message["insert_index"]

            # A11（2026-10-22 审查批次，义务 R2-A11/E4）：EDTalk 推送链豁免 enable——
            # 数字人服务的音频不受 play_audio.enable 影响（设计开关行为条款）；
            # 豁免仅限经 TTS→push_full 的类型（本函数即 TTS 链收口）。
            _playback_gate = (self.config.get("play_audio", "enable")
                              or is_edtalk_active(self.config))
            # 区分消息类型是否是 回复xxx 并且 关闭了变声
            if message["type"] == "reply":
                # 是否开启了音频播放，如果没开，则不会传文件路径给播放队列
                if _playback_gate:
                    self.data_priority_insert("待播放音频列表", data_json)
                    return True
            # 区分消息类型是否是 念弹幕 并且 关闭了变声
            elif message["type"] == "read_comment" and not self.config.get("read_comment", "voice_change"):
                # 是否开启了音频播放，如果没开，则不会传文件路径给播放队列
                if _playback_gate:
                    self.data_priority_insert("待播放音频列表", data_json)
                    return True

            voice_tmp_path = await self.voice_change(voice_tmp_path)

            # A12（2026-10-22 审查批次，义务 R7/E1）：变声失败（svc 返回 None）不再入队——
            # 报警已在 voice_change 内触发（abnormal_alarm_handle("svc")），旧路径 None
            # 照常入队导致播放侧二次报"无法加载音频文件"刷屏（审查发现 E1）。
            if not voice_tmp_path:
                logger.error("变声失败（svc 返回空路径），本次音频不入播放队列（报警已触发）")
                return False

            # 更新音频路径
            data_json["voice_path"] = voice_tmp_path

            # 是否开启了音频播放，如果没开，则不会传文件路径给播放队列
            if _playback_gate:
                self.data_priority_insert("待播放音频列表", data_json)

            return True


        resp_json = await self.tts_handle(message)
        if resp_json["result"]["code"] == 200:
            voice_tmp_path = resp_json["result"]["audio_path"]
        else:
            voice_tmp_path = None
        
        if voice_tmp_path is None:
            logger.error(f"{message['tts_type']}合成失败，请排查服务端是否启动、是否正常，配置、网络等问题。如果排查后都没有问题，可能是接口改动导致的兼容性问题，可以前往官方仓库提交issue，传送门：https://github.com/Ikaros-521/AI-Vtuber/issues\n如果是GSV 400错误，请确认参考音频和参考文本是否正确，或替换参考音频进行尝试")
            self.abnormal_alarm_handle("tts")
            
            return False
        
        logger.info(f"[{message['tts_type']}]合成成功，合成内容：【{message['content']}】，音频存储在 {voice_tmp_path}")

        await voice_change_and_put_to_queue(
            message, voice_tmp_path,
            synth_speed=resp_json.get("result", {}).get("synth_speed"))

        return True
