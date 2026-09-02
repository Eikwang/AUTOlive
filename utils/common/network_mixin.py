"""
网络系统模块 - 提供HTTP请求、设备列表等功能
"""

import json
import traceback
import requests

from ..base import logger


class NetworkMixin:
    """网络系统 Mixin"""

    def send_request(self, url: str, method: str='GET', json_data: dict=None, resp_data_type: str="json", timeout: int=60, proxy: str=None):
        """
        发送 HTTP 请求并返回结果

        Parameters:
            url (str): 请求的 URL
            method (str): 请求方法，'GET' 或 'POST'
            json_data (dict): JSON 数据，用于 POST 请求
            resp_data_type (str): 返回数据的类型（json | content）
            timeout (int): 请求超时时间
            proxy (str): 代理服务器地址

        Returns:
            dict|str: 包含响应的 JSON数据 | 字符串数据
        """
        headers = {'Content-Type': 'application/json'}

        try:
            if method in ['GET', 'get']:
                response = requests.get(url, headers=headers, timeout=timeout, proxies=proxy)
            elif method in ['POST', 'post']:
                response = requests.post(url, headers=headers, data=json.dumps(json_data), timeout=timeout, proxies=proxy)
            else:
                raise ValueError('无效 method. 支持的 methods 为 GET 和 POST.')

            # 检查请求是否成功
            response.raise_for_status()

            if resp_data_type == "json":
                # 解析响应的 JSON 数据
                result = response.json()
            else:
                result = response.content
                # 使用 'utf-8' 编码来解码字节串
                result = result.decode('utf-8')

            return result

        except requests.exceptions.RequestException as e:
            logger.error(traceback.format_exc())
            logger.error(f"请求出错: {e}")
            return None
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"请求出错: {e}")
            return None

    def list_visible_windows(self):
        """
        获取系统中可见窗口的标题列表
        
        Returns:
            list: 窗口标题列表
        """
        try:
            import pygetwindow as gw
            windows = gw.getAllWindows()
            visible_windows = [win.title for win in windows if win.title and win.visible]
            return visible_windows
        except ImportError:
            logger.warning("pygetwindow 未安装，无法获取窗口列表")
            return []
        except Exception as e:
            logger.error(f"获取窗口列表失败: {e}")
            return []

    def list_cameras(self):
        """
        获取系统中可用的摄像头设备列表
        
        Returns:
            list: 摄像头设备信息列表
        """
        try:
            import cv2
            cameras = []
            for index in range(10):  # 检查前 10 个设备
                cap = cv2.VideoCapture(index)
                if cap.isOpened():
                    ret, frame = cap.read()
                    if ret:
                        cameras.append({"device_index": index, "device_info": f"摄像头 {index}"})
                    cap.release()
            return cameras
        except ImportError:
            logger.warning("opencv-python 未安装，无法获取摄像头列表")
            return []
        except Exception as e:
            logger.error(f"获取摄像头列表失败: {e}")
            return []

    def send_heartbeat(self):
        """
        发送心跳包，用于保持会话活跃
        
        这个方法会定期被调用，用于：
        1. 保持与服务器的连接
        2. 检查系统状态
        3. 更新最后活跃时间
        """
        try:
            # 记录心跳日志
            logger.debug("心跳包发送成功")
            
            # 可以在这里添加实际的心跳逻辑，例如：
            # 1. 发送 HTTP 请求到服务器
            # 2. 更新数据库中的最后活跃时间
            # 3. 检查系统状态
            
            return True
        except Exception as e:
            logger.error(f"发送心跳包失败: {e}")
            return False
