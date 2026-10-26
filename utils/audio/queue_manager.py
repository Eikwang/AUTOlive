"""
队列管理模块 - 管理消息队列和音频路径队列
"""

import threading
import traceback
from ..my_log import logger


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
        """获取音频队列信息"""
        return {
            "wait_play_audio_num": len(self.voice_tmp_path_queue),
            "wait_synthesis_msg_num": len(self.message_queue),
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

        A3（2026-10-22 审查批次）：mixer_copywriting 位退役（文案模块已弃用）——
        位编码由 16 态收缩为 8 态：0 都不空 | 1 message_queue 空 | 2 voice_tmp_path_queue 空 |
        3 双队列空 | 4 mixer_normal 播放中…（原 8-15 位删除）；"全空"判定值 15→7
        （audio_processor 语音对话链调用点已同步）。

        Returns:
            int: 0 都不为空 | 1 message_queue 为空 | 2 voice_tmp_path_queue 为空 | 3 双队列空 |
                 4 mixer_normal 不在播放 | 5 message_queue 空+mixer 不播 | 6 voice_tmp_path_queue 空+mixer 不播 |
                 7 双队列空+mixer 不播（全空闲）
        """

        flag = 0

        # 判断队列是否为空
        if len(self.message_queue) == 0:
            flag += 1

        if len(self.voice_tmp_path_queue) == 0:
            flag += 2

        # TODO: 这一块仅在pygame播放下有效，但会对其他播放器模式下的功能造成影响，待优化
        if self.config.get("play_audio", "player") in ["pygame"]:
            # 检查mixer_normal是否正在播放
            if not self.mixer_normal.music.get_busy():
                flag += 4

        return flag

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
        logger.debug(f"message_queue: {self.message_queue}")
        logger.debug(f"data_json: {data_json}")

        # 定义 type 到优先级的映射，相同优先级的 type 映射到相同的值，值越大优先级越高
        priority_mapping = self.config.get("filter", "priority_mapping")
        
        def get_priority_level(data_json):
            """根据 data_json 的 'type' 键返回优先级，未定义的 type 或缺失 'type' 键将返回 None"""
            # 检查 data_json 是否包含 'type' 键且该键的值在 priority_mapping 中
            audio_type = data_json.get("type")
            return priority_mapping.get(audio_type, None)

        # 查找插入位置
        new_data_priority = get_priority_level(data_json)

        if type == "等待合成消息":
            logger.info(f"{type} 优先级: {new_data_priority} 内容：【{data_json['content']}】")

            # 如果新数据没有 'type' 键或其类型不在 priority_mapping 中，直接插入到末尾
            if new_data_priority is None:
                insert_position = len(self.message_queue)
            else:
                insert_position = 0  # 默认插入到列表开头
                # 从列表的最后一个元素开始，向前遍历列表，直到第一个元素
                for i in range(len(self.message_queue) - 1, -1, -1):
                    priority_level = get_priority_level(self.message_queue[i])
                    if priority_level is not None:
                        item_priority = int(priority_level)
                        # 确保比较时排除未定义类型的元素
                        if item_priority is not None and item_priority >= new_data_priority:
                            # 如果找到一个元素，其优先级小于或等于新数据，则将新数据插入到此元素之后
                            insert_position = i + 1
                            break
            
            logger.debug(f"insert_position={insert_position}")

            # 数据队列数据量超长判断，插入位置索引大于最大数，则说明优先级低与队列中已存在数据，丢弃数据
            if insert_position >= int(self.config.get("filter", "message_queue_max_len")):
                # 任务 12 根修：切分段丢弃升级 warning+组上下文（_split_group_note）
                logger.warning(f"message_queue 已满，数据丢弃：【{data_json['content']}】{_split_group_note(data_json)}")
                return {"code": 1, "msg": f"message_queue 已满，数据丢弃：【{data_json['content']}】"}

            # 获取线程锁，避免同时操作
            with self.message_queue_lock:
                # 在计算出的位置插入新数据
                self.message_queue.insert(insert_position, data_json)
                # 生产者通过notify()通知消费者列表中有新的消息
                self.message_queue_not_empty.notify()

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
                p = get_priority_level(item)
                return int(p) if p is not None else None

            # 999 单实例约束（R2-A1，优先于四规则）
            if new_data_priority is not None and int(new_data_priority) == 999:
                active_999 = getattr(self, "active_reread_top_999", False)
                queue_has_999 = any(_priority_of(item) == 999 for item in queue)
                if active_999 or queue_has_999:
                    logger.info(
                        f"999 单实例约束：系统中已存在 999 权重音频"
                        f"（{'播放器持有' if active_999 else '队列中'}），舍弃新 999：【{data_json.get('voice_path')}】")
                    return {"code": 1, "msg": "999 单实例约束，舍弃新 999 音频"}

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
                logger.info(f"voice_tmp_path_queue 已满，新权重 {new_data_priority} 插入并驱逐最低优组最老条目（原位置 {evict_idx}）：【{data_json['voice_path']}】")
                self._maybe_interrupt_999_for(data_json)
                return {"code": 200, "msg": f"插入位置 {insert_position}，驱逐最低优组条目（原位置 {evict_idx}）"}

            # 规则4：满且新权重更低 → 舍弃
            logger.warning(f"voice_tmp_path_queue 已满，音频丢弃：【{data_json['voice_path']}】{_split_group_note(data_json)}")
            return {"code": 1, "msg": f"voice_tmp_path_queue 已满，音频丢弃：【{data_json['voice_path']}】"}
