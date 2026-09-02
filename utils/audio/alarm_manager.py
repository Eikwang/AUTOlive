"""
异常报警模块 - 管理异常报警处理
"""

import os
import random
import traceback
from ..my_log import logger


class AlarmMixin:
    """异常报警Mixin类"""
    
    def abnormal_alarm_handle(self, type):
        """异常报警

        Args:
            type (str): 报警类型

        Returns:
            bool: True/False
        """

        try:
            self.abnormal_alarm_data[type]["error_count"] += 1

            if not self.config.get("abnormal_alarm", type, "enable"):
                return True

            logger.debug(f"abnormal_alarm_handle type={type}, error_count={self.abnormal_alarm_data[type]['error_count']}")

            if self.config.get("abnormal_alarm", type, "type") == "local_audio":
                # 是否错误数大于 自动重启错误数
                if self.abnormal_alarm_data[type]["error_count"] >= self.config.get("abnormal_alarm", type, "auto_restart_error_num"):
                    logger.warning(f"【异常报警-{type}】 出错数超过自动重启错误数，即将自动重启")
                    data = {
                        "type": "restart",
                        "api_type": "api",
                        "data": {
                            "config_path": "config.json"
                        }
                    }

                    webui_ip = "127.0.0.1" if self.config.get("webui", "ip") == "0.0.0.0" else self.config.get("webui", "ip")
                    self.common.send_request(f'http://{webui_ip}:{self.config.get("webui", "port")}/sys_cmd', "POST", data)
                    
                # 是否错误数小于 开始报警错误数，是则不触发报警
                if self.abnormal_alarm_data[type]["error_count"] < self.config.get("abnormal_alarm", type, "start_alarm_error_num"):
                    return
                
                path_list = self.common.get_all_file_paths(self.config.get("abnormal_alarm", type, "local_audio_path"))

                # 随机选择列表中的一个元素
                audio_path = random.choice(path_list)

                data_json = {
                    "type": "abnormal_alarm",
                    "tts_type": self.config.get("audio_synthesis_type"),
                    "data": self.config.get(self.config.get("audio_synthesis_type")),
                    "config": self.config.get("filter"),
                    "username": "系统",
                    "content": os.path.join(self.config.get("abnormal_alarm", type, "local_audio_path"), self.common.extract_filename(audio_path, True))
                }

                logger.warning(f"【异常报警-{type}】 {self.common.extract_filename(audio_path, False)}")

                self.audio_synthesis(data_json)

        except Exception as e:
            logger.error(traceback.format_exc())

            return False

        return True
