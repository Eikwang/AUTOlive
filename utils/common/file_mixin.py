"""
文件操作模块 - 提供文件读写、路径处理等功能
"""

import os
import glob
import shutil
import traceback
import json

from ..base import logger


class FileMixin:
    """文件操作 Mixin"""

    # 读取指定文件中所有文本内容并返回 如果文件不存在则创建
    def read_file_return_content(self, file_path):
        try:
            if not os.path.exists(file_path):
                logger.warning(f"文件不存在，将创建新文件: {file_path}")
                # 创建文件
                with open(file_path, 'w', encoding='utf-8') as file:
                    content = ""
                return content
        
            with open(file_path, 'r', encoding='utf-8') as file:
                content = file.read()
            return content
        except IOError as e:
            logger.error(f"无法写入文件:{file_path}\n{e}")
            return None


    # 将一个文件路径的字符串切分成路径和文件名
    def split_path_and_filename(self, file_path):
        folder_path, file_name = os.path.split(file_path)
        # 检查路径末尾是否已经包含了'/'，如果没有，则添加
        if not folder_path.endswith('/'):
            folder_path += '/'
        
        return folder_path, file_name

    # 从文件路径中提取出带有扩展名的文件名
    def extract_filename(self, file_path, with_extension=False):
        """从文件路径中提取出带有扩展名的文件名

        Args:
            file_path (str): 文件路径
            with_extension (bool, optional): 是否包含扩展名. Defaults to False.

        Returns:
            str: 文件名
        """
        filename = os.path.basename(file_path)
        if not with_extension:
            filename = os.path.splitext(filename)[0]
        return filename

    # 返回指定文件夹内所有文件夹的名字列表
    def get_folder_names(self, path):
        return [name for name in os.listdir(path) if os.path.isdir(os.path.join(path, name))]

    # 返回指定文件夹内所有文件的文件绝对路径（包括文件扩展名）
    def get_all_file_paths(self, folder_path):
        """返回指定文件夹内所有文件的文件绝对路径（包括文件扩展名）

        Args:
            folder_path (str): 文件夹路径

        Returns:
            list: 文件路径列表
        """
        file_paths = []
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                file_paths.append(os.path.join(root, file))
        return file_paths

    # 获取指定文件夹内指定扩展名的文件名列表
    def get_specify_extension_names_in_folder(self, path: str, extension: str):
        """获取指定文件夹内指定扩展名的文件名列表

        Args:
            path (str): 文件夹路径
            extension (str): 扩展名

        Returns:
            list: 文件名列表
        """
        file_names = []
        for file in os.listdir(path):
            if file.endswith(extension):
                file_names.append(file)
        return file_names

    # 从文件名列表中去除扩展名
    def remove_extension_from_list(self, file_name_list):
        return [os.path.splitext(file_name)[0] for file_name in file_name_list]

    # 判断文件是否是音频文件
    def is_audio_file(self, file_path):
        """判断文件是否是音频文件

        Args:
            file_path (str): 文件路径

        Returns:
            bool: 是否是音频文件
        """
        audio_extensions = ['.wav', '.mp3', '.ogg', '.flac', '.aac', '.wma', '.m4a']
        _, ext = os.path.splitext(file_path)
        return ext.lower() in audio_extensions

    # 搜索指定文件夹内所有的音频文件，并随机返回一个音频文件路径
    def random_search_a_audio_file(self, root_dir):
        """搜索指定文件夹内所有的音频文件，并随机返回一个音频文件路径

        Args:
            root_dir (str): 根目录

        Returns:
            str: 音频文件路径
        """
        import random
        audio_files = []
        for root, dirs, files in os.walk(root_dir):
            for file in files:
                if self.is_audio_file(file):
                    audio_files.append(os.path.join(root, file))
        if audio_files:
            return random.choice(audio_files)
        return None

    # 获取live2d模型名称
    def get_live2d_model_name(self, path):
        """获取live2d模型名称

        Args:
            path (str): 模型文件夹路径

        Returns:
            str: 模型名称
        """
        try:
            for file in os.listdir(path):
                if file.endswith('.model3.json'):
                    return os.path.splitext(file)[0]
            return None
        except Exception as e:
            logger.error(f"获取live2d模型名称失败: {e}")
            return None

    # 读取文件内容 它接受文件路径和返回类型参数，并根据参数返回文件内容作为字典或纯文本。如果读取文件过程中出现异常，则返回 None。
    def read_file(self, file_path: str, return_type: str):
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                content = file.read()
            
            if return_type == 'dict':
                return json.loads(content)
            else:
                return content
        except Exception as e:
            logger.error(f"读取文件失败: {e}")
            return None
        
    def ensure_directory_exists(self, path):
        # 检查路径是否存在
        if not os.path.exists(path):
            # 如果路径不存在，创建它
            os.makedirs(path)
            logger.info(f"路径已创建：{path}")

    # 写入内容到指定文件中 返回T/F
    def write_content_to_file(self, file_path, content, write_log=True):
        try:
            with open(file_path, 'w', encoding='utf-8') as file:
                file.write(content)

            if write_log:
                logger.info(f"写入文件:{file_path}，内容：【{content}】")

            return True
        except IOError as e:
            logger.error(f"无法写入 【{content}】 到文件:{file_path}\n{e}")
            return False
        except Exception as e:
            logger.error(traceback.format_exc())
            return False

    # 移动文件到指定路径 src dest
    def move_file(self, source_path, destination_path, rename=None, format="wav"):
        """移动文件到指定路径

        Args:
            source_path (str): 文件路径含文件名
            destination_path (_type_): 目标文件夹
            rename (str, optional): 文件名. Defaults to None.
            format (str, optional): 文件格式（实际上只是个假拓展名）. Defaults to "wav".

        Returns:
            str: 输出到的完整路径含文件名
        """
        logger.debug(f"source_path={source_path},destination_path={destination_path},rename={rename}")

        destination_directory = os.path.dirname(destination_path)
        logger.debug(f"destination_directory={destination_directory}")
        destination_filename = os.path.basename(source_path)

        if rename is not None:
            destination_filename = rename + "." + format
        
        destination_path = os.path.join(destination_directory, destination_filename)
        
        if os.path.exists(destination_path):
            # 如果目标位置已存在同名文件，则先删除
            os.remove(destination_path)

        shutil.move(source_path, destination_path)
        logger.info(f"文件移动成功：{source_path} -> {destination_path}")

        return destination_path


    # 删除文件
    def del_file(self, file_path) -> bool:
        """
        删除文件

        Args:
            file_path (str): 文件路径

        Returns:
            bool：True/False
        """
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"文件删除成功：{file_path}")

                return True
            
            logger.error(f"文件不存在：{file_path}")
            return False
        except Exception as e:
            logger.error(traceback.format_exc())
            return False

    # 从给定的文件路径中提取文件名及其扩展名
    def get_filename_from_path(self, file_path):
        """
        从给定的文件路径中提取文件名及其扩展名。
        
        参数:
        file_path (str): 文件的绝对路径或相对路径。

        返回:
        dict: 包含状态码和数据的字典。成功时返回文件名，失败时返回错误信息。
        """
        response = {
            'code': 200,  # 默认成功状态码
            'data': None,
            'error': None
        }
        
        try:
            # 验证输入路径是否为空
            if not file_path:
                response['code'] = 400  # 客户端错误状态码
                response['error'] = '路径不能为空'
                raise ValueError(response['error'])

            # 验证文件是否存在
            if not os.path.exists(file_path):
                response['code'] = 404  # 文件未找到状态码
                response['error'] = f'文件 {file_path} 不存在'
                raise FileNotFoundError(response['error'])

            # 提取文件名及其扩展名
            filename = os.path.basename(file_path)
            response['data'] = filename

        except ValueError as ve:
            logger.error(ve)
        except FileNotFoundError as fnf:
            logger.error(fnf)
        except Exception as e:
            response['code'] = 500  # 服务器错误状态码
            response['error'] = '发生未知错误'
            logger.error(e)
        
        return response
