"""
队列管理模块 - 管理消息队列和音频路径队列
"""

import threading
import time
import traceback
from ..my_log import logger

# G2（2026-10-28 消费回传批次）：消费回执环形窗容量
_CONSUMED_RECEIPT_MAX = 50


def _split_group_note(data_json):
    """切分段组上下文附注（任务 12 根修，TODOS 2026-10-22 音频批次节）：长回复
    切分段带 split_group 组绑定（synthesis_manager 切分循环注入、put_to_queue
    透传）——队列取代/丢弃点的 log 附组上下文（第几段/共几段），失效模式恢复
    可定位可见面；非切分段返回空串。"""
    sg = data_json.get("split_group") if isinstance(data_json, dict) else None
    if not isinstance(sg, dict):
        return ""
    seq = sg.get("seq")
    return (f"〔长回复切分段：第 {seq + 1 if isinstance(seq, int) else '?'}/"
            f"{sg.get('total', '?')} 段〕")


class QueueMixin:
    """队列管理Mixin类"""
    
    def clear_queue(self, type: str="message_queue"):
        """清空 待合成消息队列|待播放音频队列

        Args:
            type (str, optional): 队列类型. Defaults to "message_queue".

        Returns:
            bool: 清空结果
        """
        try:
            if type == "voice_tmp_path_queue":
                if len(self.voice_tmp_path_queue) == 0:
                    return True
                with self.voice_tmp_path_queue_lock:
                    self.voice_tmp_path_queue.clear()
                return True
            elif type == "message_queue":
                if len(self.message_queue) == 0:
                    return True
                with self.message_queue_lock:
                    self.message_queue.clear()
                return True
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"清空{type}队列失败：{e}")
            return False

    def cancel_choreo_audio(self, step_id: str):
        """O8（2026-10-28 消费批次）：编排打断撤旧音轨——从两队列移除匹配
        choreo_step_id 的未消费条目（[:] 原地替换保对象身份，与锁外读者兼容）。

        已被消费者取出（合成中/播放中）的条目无法撤销：其完成回调因编排
        inflight 已重置被静默忽略（无错配副作用）。

        Returns:
            int: 撤销条数（两队列合计）
        """
        if not step_id:
            return 0
        removed = 0
        try:
            with self.message_queue_lock:
                keep = [d for d in self.message_queue
                        if not (isinstance(d, dict) and d.get("choreo_step_id") == step_id)]
                removed += len(self.message_queue) - len(keep)
                self.message_queue[:] = keep
            with self.voice_tmp_path_queue_lock:
                keep = [d for d in self.voice_tmp_path_queue
                        if not (isinstance(d, dict) and d.get("choreo_step_id") == step_id)]
                removed += len(self.voice_tmp_path_queue) - len(keep)
                self.voice_tmp_path_queue[:] = keep
            if removed:
                logger.info(f"编排打断撤销未消费音轨 {removed} 条（step_id={step_id}）")
        except Exception:
            logger.error(f"撤销编排音轨异常（忽略）：{traceback.format_exc()}")
        return removed

    def is_queue_less_or_greater_than(self, type: str="message_queue", less: int=None, greater: int=None):
        """判断 等待合成消息队列|待播放音频队列 数是否小于或大于某个值，就返回True"""
        if less:
            if type == "voice_tmp_path_queue":
                if len(self.voice_tmp_path_queue) < less:
                    return True
                return False
            elif type == "message_queue":
                if len(self.message_queue) < less:
                    return True
                return False
        
        if greater:
            if type == "voice_tmp_path_queue":
                if len(self.voice_tmp_path_queue) > greater:
                    return True
                return False
            elif type == "message_queue":
                if len(self.message_queue) > greater:
                    return True
                return False
        
        return False
    
    def get_audio_info(self):
        """获取音频队列信息

        TODOS 2026-10-28 消费逻辑审查 #4：builtin 播放器的 play() 是提前移交
        （满载背压上限 queue_max 条进 audio_json_list），已移交条目脱离
        voice_tmp_path_queue——wait_play_audio_num 叠加其内部队列深度，防观测
        面低估积压（仅统计语义，不影响 is_queue_less_or_greater_than 等门控）。
        """
        info = {
            "wait_play_audio_num": len(self.voice_tmp_path_queue),
            "wait_synthesis_msg_num": len(self.message_queue),
        }
        player = getattr(self, "audio_player", None)
        if player is not None and hasattr(player, "internal_queue_len"):
            try:
                info["wait_play_audio_num"] += player.internal_queue_len()
            except Exception:
                pass  # 观测叠加失败不阻塞统计（快照尽力而为）
        # T1 观测面补齐包：丢弃分桶/消费延迟/合成吞吐摘要（观测面数据源单点；
        # D-1 常驻状态条 UI 消费留后续批次）
        try:
            from utils.obs_stats import snapshot as _obs_snapshot
            info["obs"] = _obs_snapshot()
        except Exception:
            pass
        return info

    # ------------------------------------------------------------------
    # G2（2026-10-28 消费回传批次）：音频条目消费完成回执（Sort 侧显式 API——
    # 图 v2.6 Frame→lineout→|回传|Sort 边的 Sort 侧落点）。类属性沿
    # message_queue 模式（Mixin 无 __init__，宿主继承共享）。
    # ------------------------------------------------------------------
    _consumed_receipts = []       # 环形窗（最近 _CONSUMED_RECEIPT_MAX 条回执）
    _consumed_total = 0           # 累计消费回执数
    _consumed_by_type = {}        # 累计分类型计数
    _consumed_lock = threading.Lock()

    def report_consumed(self, qtype: str = "", content: str = ""):
        """音频条目真实消费完成回执。

        接入点：playback_manager EDTalk push_full 返回处 + audio_core builtin
        完成回调转发处（两路消费互斥不双报，G1）。维护环形回执窗+累计计数，
        供排序层/观测面查询（get_consumption_stats）；当前无排序决策依赖，
        仅观测透出（TODOS 2026-10-27 批 #1）。异常吞掉——回执不得影响消费链。"""
        try:
            receipt = {"ts": round(time.time(), 3), "type": str(qtype or ""),
                       "content": str(content or "")[:80]}
            with QueueMixin._consumed_lock:
                QueueMixin._consumed_total += 1
                t = receipt["type"]
                QueueMixin._consumed_by_type[t] = QueueMixin._consumed_by_type.get(t, 0) + 1
                QueueMixin._consumed_receipts.append(receipt)
                if len(QueueMixin._consumed_receipts) > _CONSUMED_RECEIPT_MAX:
                    QueueMixin._consumed_receipts.pop(0)
        except Exception:
            logger.debug(f"消费回执记录异常（忽略）：{traceback.format_exc()}")

    def get_consumption_stats(self):
        """消费回执统计快照（累计计数+分类型计数+最近 10 条回执）。"""
        with QueueMixin._consumed_lock:
            return {
                "total_consumed": QueueMixin._consumed_total,
                "by_type": dict(QueueMixin._consumed_by_type),
                "recent": list(QueueMixin._consumed_receipts[-10:]),
            }


    def flush_starved_playback_queue(self):
        """E2 饥饿路径 flush：首次播放阈值门的保底放行

        规则（D-20 矩阵"音频播放队列首次触发阈值"行的 flush 规则）：
        待合成消息队列已空（本次突发全部合成完毕；待播放队列的唯一生产者
        就是合成线程自身，故此时亦无在途合成）且待播放队列仍有音频、
        首次播放触发标志 voice_tmp_path_queue_not_empty_flag 尚未置位
        （本次突发总量未达阈值 filter.voice_tmp_path_queue_min_start_play=N）
        时，立即置位标志并唤醒消费者放行积压音频。

        否则首次突发仅产出 N-1 条时，生产者永不 notify（生产者只在队列
        达到 N 或标志已置位时通知），消费者无限期 wait——积压的 N-1 条
        音频将永远不播，直到下一次突发把队列重新顶过阈值。

        只应在合成线程消费完一条消息（含合成失败）后调用；调用点在
        SynthesisMixin.message_queue_thread 循环尾。

        Returns:
            bool: 是否执行了放行（置位标志+notify）
        """
        # 快路径无锁初筛：待合成队列非空（突发未结束）/待播放队列空/
        # 标志已置位（阈值门已开，生产者自行 notify）均无需放行
        if self.message_queue or not self.voice_tmp_path_queue or self.voice_tmp_path_queue_not_empty_flag:
            return False
        with self.voice_tmp_path_queue_lock:
            # 锁内复核：标志与队列的写入方（data_priority_insert/本方法）
            # 均持同一把锁，避免与生产者置位竞争
            if self.voice_tmp_path_queue_not_empty_flag or not self.voice_tmp_path_queue:
                return False
            self.voice_tmp_path_queue_not_empty_flag = True
            # 生产者通过notify()通知消费者列表中有新的消息
            self.voice_tmp_path_queue_not_empty.notify()
        logger.info(f"E2 flush：待合成队列已空，放行未达首次播放阈值的积压音频 {len(self.voice_tmp_path_queue)} 条")
        return True

    def is_audio_queue_empty(self):
        """判断等待合成和已经合成的队列是否为空

        A3（2026-10-22 审查批次）：mixer_copywriting 位退役（文案模块已弃用）。
        2026-10-30 审查批次：「音频播放器」配置退役——mixer_normal 位4随之移除
        （pygame.mixer 播放态探测不再存在，本地播放统一 builtin 播放器），位编码
        收缩为 4 态：0 都不空 | 1 message_queue 空 | 2 voice_tmp_path_queue 空 |
        3 双队列空（全空闲）；"全空"判定值 7→3（audio_processor 语音对话链调用点
        已同步）。

        Returns:
            int: 0 都不为空 | 1 message_queue 为空 | 2 voice_tmp_path_queue 为空 |
                 3 双队列空（全空闲）
        """

        flag = 0

        # 判断队列是否为空
        if len(self.message_queue) == 0:
            flag += 1

        if len(self.voice_tmp_path_queue) == 0:
            flag += 2

        return flag

    def purge_talk_inputs(self):
        """清除待合成队列中 talk 类型条目（连续对话最新优先，D-22 输入侧）。

        连续对话开启时新输入到来前调用——舍弃未回复/未合成的旧对话消息，
        处理最新的信息；只清 talk，不影响点歌/助播等其它类型。

        Returns:
            int: 清除条数
        """
        try:
            with self.message_queue_lock:
                keep = [m for m in self.message_queue
                        if not (isinstance(m, dict) and m.get("type") == "talk")]
                removed = len(self.message_queue) - len(keep)
                if removed:
                    self.message_queue[:] = keep
            return removed
        except Exception as e:
            logger.warning(f"purge_talk_inputs 异常（忽略）：{e}")
            return 0

    def _continuous_talk_discard_notify(self, count: int):
        """D-10：待播音频被舍弃的聊天区灰字反馈（webui /callback system 源，
        经后台队列投递——沿 utils_handler 聊天记录回调同款通道）。"""
        try:
            from ..common import Common
            from ..background_tasks import submit
            webui_ip = self.config.get("webui", "ip")
            webui_ip = "127.0.0.1" if webui_ip == "0.0.0.0" else webui_ip
            payload = {"type": "llm", "data": {
                "type": "系统", "chat_source": "system", "username": "系统",
                "content_type": "answer",
                "content": f"已舍弃 {count} 条待播音频（连续对话，仅播放最新一条）",
                "timestamp": Common().get_bj_time(0)}}
            submit(Common().send_request,
                   f'http://{webui_ip}:{self.config.get("webui", "port")}/callback',
                   "POST", payload, timeout=10)
        except Exception as e:
            logger.debug(f"舍弃反馈发送失败（忽略）：{e}")

    def _maybe_discard_pending_for_talk(self, data_json):
        """D-22（连续对话最新优先，2026-10-30 聊天审查批次）：talk 整段音频
        入待播放队列时若待播放 ≥2（=2 与 >2 同规则，消除边界缺口），舍弃
        等待播放音频只播最新一条，判断条件为待播放列表数量。

        豁免：切分段（split_group）与流式回复分句（talk_stream）——同一次
        回复的分段/分句必须完整排队，不受新输入淘汰影响。
        """
        try:
            if not (isinstance(data_json, dict) and data_json.get("type") == "talk"):
                return
            if data_json.get("split_group") or data_json.get("talk_stream"):
                return
            if not bool(self.config.get("talk", "continuous_talk")):
                return
            with self.voice_tmp_path_queue_lock:
                pending = len(self.voice_tmp_path_queue)
                if pending >= 2:
                    self.voice_tmp_path_queue.clear()
            if pending >= 2:
                try:
                    from utils.obs_stats import record_drop
                    record_drop("talk_discard")
                except Exception:
                    pass
                logger.info(f"[连续对话] 待播放 {pending} 条 ≥2，舍弃等待播放音频只播最新一条")
                self._continuous_talk_discard_notify(pending)
        except Exception as e:
            logger.warning(f"连续对话待播舍弃判断异常（忽略）：{e}")

    def _maybe_interrupt_999_for(self, data_json):
        """A7（design 情况1）：非 999 新音频入队成功后，若 999 正在播放则请求让位暂停。

        仅 builtin 播放器具备暂停-续播机制（UC1a 终门裁决：保留 builtin 为推荐
        播放器；pygame 层不实现该机制——999 按普通最高优先级排队）；已挂起时
        播放器侧自行忽略（情况 c 排队不打断，R2-A7c）。
        """
        try:
            if getattr(self, "active_reread_top_999", False):
                player = getattr(self, "audio_player", None)
                if player is not None and hasattr(player, "request_999_interrupt"):
                    player.request_999_interrupt()
        except Exception:
            pass  # 让位触发失败不阻塞入队主流程（999 播完自然轮到插队音频）

    def data_priority_insert(self, type:str="等待合成消息", data_json:dict=None):
        # T1 观测面补齐包：入队时间戳（消费延迟=播放完成-_enqueued_at；_ 前缀=内部观测键，
        # 消费链无读取点、随条目透传不影响既有键面）
        try:
            if isinstance(data_json, dict) and "_enqueued_at" not in data_json:
                import time as _t
                data_json["_enqueued_at"] = _t.time()
        except Exception:
            pass  # 观测埋点不阻塞入队
        """
        数据根据优先级排队插入待合成音频队列

        type目前有
            reread_top_priority 最高优先级-复读
            talk 聊天（语音输入）
            comment 弹幕
            local_qa_audio 本地问答音频
            song 歌曲
            reread 复读
            key_mapping 按键映射
            integral 积分
            read_comment 念弹幕
            gift 礼物
            entrance 用户入场
            follow 用户关注
            schedule 定时任务
            idle_time_task 闲时任务
            trends_copywriting 动态文案
            assistant_anchor_text 助播-文本
            assistant_anchor_audio 助播-音频
        """
        # 定义 type 到优先级的映射，相同优先级的 type 映射到相同的值，值越大优先级越高
        priority_mapping = self.config.get("filter", "priority_mapping")
        
        def get_priority_level(data_json):
            """根据 data_json 的 'type' 键返回优先级，未定义的 type 或缺失 'type' 键将返回 None"""
            # 检查 data_json 是否包含 'type' 键且该键的值在 priority_mapping 中
            audio_type = data_json.get("type")
            return priority_mapping.get(audio_type, None)

        def _entry_priority(item):
            """条目有效权重（O7，2026-10-28 消费批次）：条目自带 priority_override
            （编排插队音轨，choreographer 注入固定 500）优先于类型映射表——
            既有条目权重比较、同权重组定位、最低优驱逐都必须按插队权重判定，
            否则 500 插队条目会被误当映射权重参与排序/驱逐。"""
            ov = item.get("priority_override") if isinstance(item, dict) else None
            if ov is not None:
                try:
                    return int(ov)
                except (TypeError, ValueError):
                    pass
            return get_priority_level(item)

        # 查找插入位置
        new_data_priority = get_priority_level(data_json)

        # O7：新条目显式插队权重优先于类型映射表（固定 500，高于常规语音域
        # 10~30、低于 999 复读专线，不触发 999 单实例约束）
        _override = data_json.get("priority_override") if isinstance(data_json, dict) else None
        if _override is not None:
            try:
                new_data_priority = int(_override)
            except (TypeError, ValueError):
                pass

        if type == "等待合成消息":
            logger.info(f"{type} 优先级: {new_data_priority} 内容：【{data_json['content']}】")

            # M1/M2（2026-10-28 消费批次）：扫描/满载判定/插入全部锁内完成——
            # 消除"锁外算位、锁内插入"的多生产者竞态超长窗口；满载时高优先级
            # 插入改为"插队+驱逐队尾"（净长度不变，队尾=最低优组 FIFO 尾），
            # 根治满载高优插入无界超长
            with self.message_queue_lock:
                # 如果新数据没有 'type' 键或其类型不在 priority_mapping 中，直接插入到末尾
                if new_data_priority is None:
                    insert_position = len(self.message_queue)
                else:
                    insert_position = 0  # 默认插入到列表开头
                    # 从列表的最后一个元素开始，向前遍历列表，直到第一个元素
                    for i in range(len(self.message_queue) - 1, -1, -1):
                        priority_level = _entry_priority(self.message_queue[i])
                        if priority_level is not None:
                            item_priority = int(priority_level)
                            # 确保比较时排除未定义类型的元素
                            if item_priority is not None and item_priority >= new_data_priority:
                                # 如果找到一个元素，其优先级小于或等于新数据，则将新数据插入到此元素之后
                                insert_position = i + 1
                                break

                logger.debug(f"insert_position={insert_position}")
                max_len = int(self.config.get("filter", "message_queue_max_len"))

                # 数据队列数据量超长判断：队满且插入位次在末尾（低优/未识别类型）→ 丢弃
                if len(self.message_queue) >= max_len and insert_position >= len(self.message_queue):
                    # 任务 12 根修：切分段丢弃升级 warning+组上下文（_split_group_note）
                    logger.warning(f"message_queue 已满，数据丢弃：【{data_json['content']}】{_split_group_note(data_json)}")
                    try:
                        from utils.obs_stats import record_drop
                        record_drop("message_queue_full")
                    except Exception:
                        pass
                    return {"code": 1, "msg": f"message_queue 已满，数据丢弃：【{data_json['content']}】"}

                # 队满且插队位次有效（新数据优先级高于队内既有条目）→ 插队并驱逐队尾
                evicted = None
                if len(self.message_queue) >= max_len and insert_position < len(self.message_queue):
                    evicted = self.message_queue.pop()

                # 在计算出的位置插入新数据
                self.message_queue.insert(insert_position, data_json)
                # 生产者通过notify()通知消费者列表中有新的消息
                self.message_queue_not_empty.notify()

            if evicted is not None:
                try:
                    from utils.obs_stats import record_drop
                    record_drop("message_queue_evict")
                except Exception:
                    pass
                logger.warning(
                    f"message_queue 已满，高优数据插队并驱逐队尾（最低优）："
                    f"被驱逐【{evicted.get('content', '')}】{_split_group_note(evicted)}，"
                    f"新数据【{data_json['content']}】{_split_group_note(data_json)}")
            return {"code": 200, "msg": f"数据已插入到位置 {insert_position}"}
        else:
            logger.info(f"{type} 优先级: {new_data_priority} 音频={data_json['voice_path']}")

            # A1（2026-10-22 审查批次，义务 E1）：溢出语义对齐设计示例——实现前经四示例+主
            # 叙述逐行复推精化的权威规则（比义务块字面"取代最靠后同权重"更强，后者在示例4
            # 双 150 连发下残留旧条目无法复现设计终态）：
            #   规则1 未满 → 按权重序插入（同权重接组尾，组内 FIFO）
            #   规则2 满 且同权重组存在 → 取代该组组首（最老）条目，新条目接组尾（净长度不变）
            #   规则3 满 且无同权重 且新权重 > 最低优组 → 插入后驱逐最低优组组首（最老）条目
            #   规则4 其余（满且新权重更低）→ 舍弃新音频
            # 999 单实例约束优先于全部规则：系统中已存在 999（队列中或播放器持有 active 标记）
            # 时新 999 一律舍弃（R2-A1）。
            # 注意：等待合成消息分支维持既有 drop-only 语义（设计仅规定待播放队列，不越权改动）。
            queue = self.voice_tmp_path_queue
            max_len = int(self.config.get("filter", "voice_tmp_path_queue_max_len"))

            def _priority_of(item):
                p = _entry_priority(item)
                return int(p) if p is not None else None

            # 999 单实例约束（R2-A1，优先于四规则）
            if new_data_priority is not None and int(new_data_priority) == 999:
                active_999 = getattr(self, "active_reread_top_999", False)
                queue_has_999 = any(_priority_of(item) == 999 for item in queue)
                if active_999 or queue_has_999:
                    logger.info(
                        f"999 单实例约束：系统中已存在 999 权重音频"
                        f"（{'播放器持有' if active_999 else '队列中'}），舍弃新 999：【{data_json.get('voice_path')}】")
                    try:
                        from utils.obs_stats import record_drop
                        record_drop("audio_999_rejected")
                    except Exception:
                        pass
                    return {"code": 1, "msg": "999 单实例约束，舍弃新 999 音频"}

            # D-22（连续对话最新优先）：talk 整段音频入待播放队列前，待播放 ≥2
            # 即舍弃等待播放音频只播最新一条（=2 与 >2 同规则）
            self._maybe_discard_pending_for_talk(data_json)

            def _insert_and_notify(pos, item):
                """锁内插入并执行首次播放阈值/唤醒逻辑（原语义保真）"""
                self.voice_tmp_path_queue.insert(pos, item)
                # 待播放音频数量大于首次播放阈值 且 处于首次播放情况下：
                if len(self.voice_tmp_path_queue) >= int(self.config.get("filter", "voice_tmp_path_queue_min_start_play")) and self.voice_tmp_path_queue_not_empty_flag is False:
                    self.voice_tmp_path_queue_not_empty_flag = True
                    # 生产者通过notify()通知消费者列表中有新的消息
                    self.voice_tmp_path_queue_not_empty.notify()
                # 非首次触发情况下，有数据就触发消费者播放
                elif self.voice_tmp_path_queue_not_empty_flag:
                    # 生产者通过notify()通知消费者列表中有新的消息
                    self.voice_tmp_path_queue_not_empty.notify()

            # 未识别类型：维持既有语义——插队尾，满则丢弃
            if new_data_priority is None:
                if len(queue) >= max_len:
                    logger.warning(f"voice_tmp_path_queue 已满，音频丢弃（未识别类型）：【{data_json['voice_path']}】{_split_group_note(data_json)}")
                    try:
                        from utils.obs_stats import record_drop
                        record_drop("unknown_type_dropped")
                    except Exception:
                        pass
                    return {"code": 1, "msg": f"voice_tmp_path_queue 已满，音频丢弃：【{data_json['voice_path']}】"}
                with self.voice_tmp_path_queue_lock:
                    _insert_and_notify(len(queue), data_json)
                self._maybe_interrupt_999_for(data_json)
                return {"code": 200, "msg": f"音频已插入到位置 {len(queue) - 1}"}

            same_idx_list = [i for i, item in enumerate(queue) if _priority_of(item) == int(new_data_priority)]

            if len(queue) < max_len:
                # 规则1：未满 → 权重序插入（从尾向头找第一个 ≥ 新权重的条目，插其后=接组尾）
                insert_position = 0
                for i in range(len(queue) - 1, -1, -1):
                    p = _priority_of(queue[i])
                    if p is not None and p >= int(new_data_priority):
                        insert_position = i + 1
                        break
                with self.voice_tmp_path_queue_lock:
                    _insert_and_notify(insert_position, data_json)
                logger.debug(f"insert_position={insert_position}")
                self._maybe_interrupt_999_for(data_json)
                return {"code": 200, "msg": f"音频已插入到位置 {insert_position}"}

            # 队满路径（规则2/3/4）
            if same_idx_list:
                # 规则2：取代同权重组组首（最老）条目，新条目接组尾（净长度不变，组内 FIFO）
                front_idx = same_idx_list[0]
                last_idx = same_idx_list[-1]
                with self.voice_tmp_path_queue_lock:
                    self.voice_tmp_path_queue.pop(front_idx)
                    self.voice_tmp_path_queue.insert(last_idx, data_json)  # pop 后组尾左移1，插回原组尾位
                    if self.voice_tmp_path_queue_not_empty_flag:
                        self.voice_tmp_path_queue_not_empty.notify()
                try:
                    from utils.obs_stats import record_drop
                    record_drop("audio_same_tier_evict")
                except Exception:
                    pass
                logger.info(f"voice_tmp_path_queue 已满，同权重取代组首最老条目（原位置 {front_idx}）：【{data_json['voice_path']}】{_split_group_note(data_json)}")
                self._maybe_interrupt_999_for(data_json)
                return {"code": 200, "msg": f"同权重取代组首条目（原位置 {front_idx}）"}

            non_none_priorities = [p for p in (_priority_of(it) for it in queue) if p is not None]
            min_priority = min(non_none_priorities) if non_none_priorities else None
            if min_priority is not None and int(new_data_priority) > min_priority:
                # 规则3：插入后驱逐最低优组组首（最老）条目——主叙述 10old/10new 双存要求组首驱逐
                evict_idx = next(i for i, it in enumerate(queue) if _priority_of(it) == min_priority)
                insert_position = 0
                for i in range(len(queue) - 1, -1, -1):
                    p = _priority_of(queue[i])
                    if p is not None and p >= int(new_data_priority):
                        insert_position = i + 1
                        break
                with self.voice_tmp_path_queue_lock:
                    self.voice_tmp_path_queue.pop(evict_idx)
                    if insert_position > evict_idx:
                        insert_position -= 1
                    _insert_and_notify(insert_position, data_json)
                try:
                    from utils.obs_stats import record_drop
                    record_drop("audio_same_tier_evict")
                except Exception:
                    pass
                logger.info(f"voice_tmp_path_queue 已满，新权重 {new_data_priority} 插入并驱逐最低优组最老条目（原位置 {evict_idx}）：【{data_json['voice_path']}】")
                self._maybe_interrupt_999_for(data_json)
                return {"code": 200, "msg": f"插入位置 {insert_position}，驱逐最低优组条目（原位置 {evict_idx}）"}

            # 规则4：满且新权重更低 → 舍弃
            logger.warning(f"voice_tmp_path_queue 已满，音频丢弃：【{data_json['voice_path']}】{_split_group_note(data_json)}")
            try:
                from utils.obs_stats import record_drop
                record_drop("audio_rule4_dropped")
            except Exception:
                pass
            return {"code": 1, "msg": f"voice_tmp_path_queue 已满，音频丢弃：【{data_json['voice_path']}】"}
