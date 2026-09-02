"""
队列管理模块 - 管理消息队列和音频路径队列
"""

import threading
import traceback
from ..my_log import logger


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

    def is_audio_queue_empty(self):
        """判断等待合成和已经合成的队列是否为空

        Returns:
            int: 0 都不为空 | 1 message_queue 为空 | 2 voice_tmp_path_queue 为空 | 3 message_queue和voice_tmp_path_queue 为空 |
                 4 mixer_normal 不在播放 | 5 message_queue 为空、mixer_normal 不在播放 | 6 voice_tmp_path_queue 为空、mixer_normal 不在播放 |
                 7 message_queue和voice_tmp_path_queue 为空、mixer_normal 不在播放 | 8 mixer_copywriting 不在播放 | 9 message_queue 为空、mixer_copywriting 不在播放 |
                 10 voice_tmp_path_queue 为空、mixer_copywriting 不在播放 | 11 message_queue和voice_tmp_path_queue 为空、mixer_copywriting 不在播放 |
                 12 message_queue 为空、voice_tmp_path_queue 为空、mixer_normal 不在播放 | 13 message_queue 为空、voice_tmp_path_queue 为空、mixer_copywriting 不在播放 |
                 14 voice_tmp_path_queue为空、mixer_normal 不在播放、mixer_copywriting 不在播放 | 15 message_queue和voice_tmp_path_queue 为空、mixer_normal 不在播放、mixer_copywriting 不在播放 |
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

            # 检查mixer_copywriting是否正在播放
            if not self.mixer_copywriting.music.get_busy():
                flag += 8

        return flag

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
            abnormal_alarm 异常报警
            image_recognition_schedule 图像识别定时任务
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
                logger.info(f"message_queue 已满，数据丢弃：【{data_json['content']}】")
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

            # 如果新数据没有 'type' 键或其类型不在 priority_mapping 中，直接插入到末尾
            if new_data_priority is None:
                insert_position = len(self.voice_tmp_path_queue)
            else:
                insert_position = 0  # 默认插入到列表开头
                # 从列表的最后一个元素开始，向前遍历列表，直到第一个元素
                for i in range(len(self.voice_tmp_path_queue) - 1, -1, -1):
                    priority_level = get_priority_level(self.voice_tmp_path_queue[i])
                    if priority_level is not None:
                        item_priority = int(priority_level)
                        # 确保比较时排除未定义类型的元素
                        if item_priority is not None and item_priority >= new_data_priority:
                            # 如果找到一个元素，其优先级小于或等于新数据，则将新数据插入到此元素之后
                            insert_position = i + 1
                            break
            
            logger.debug(f"insert_position={insert_position}")

            # 数据队列数据量超长判断，插入位置索引大于最大数，则说明优先级低与队列中已存在数据，丢弃数据
            if insert_position >= int(self.config.get("filter", "voice_tmp_path_queue_max_len")):
                logger.info(f"voice_tmp_path_queue 已满，音频丢弃：【{data_json['voice_path']}】")
                return {"code": 1, "msg": f"voice_tmp_path_queue 已满，音频丢弃：【{data_json['voice_path']}】"}

            # 获取线程锁，避免同时操作
            with self.voice_tmp_path_queue_lock:
                # 在计算出的位置插入新数据
                self.voice_tmp_path_queue.insert(insert_position, data_json)

                # 待播放音频数量大于首次播放阈值 且 处于首次播放情况下：
                if len(self.voice_tmp_path_queue) >= int(self.config.get("filter", "voice_tmp_path_queue_min_start_play")) and                     self.voice_tmp_path_queue_not_empty_flag is False:
                    self.voice_tmp_path_queue_not_empty_flag = True
                    # 生产者通过notify()通知消费者列表中有新的消息
                    self.voice_tmp_path_queue_not_empty.notify()
                # 非首次触发情况下，有数据就触发消费者播放
                elif self.voice_tmp_path_queue_not_empty_flag:
                    # 生产者通过notify()通知消费者列表中有新的消息
                    self.voice_tmp_path_queue_not_empty.notify()

            return {"code": 200, "msg": f"音频已插入到位置 {insert_position}"}
