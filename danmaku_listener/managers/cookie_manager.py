"""Cookie 管理器

负责 Cookie 的持久化存储和加载，支持多平台隔离。
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger


class CookieManager:
    """Cookie 持久化管理器

    每个直播间使用独立的 Cookie 文件，避免状态污染。
    """

    def __init__(self, cookie_dir: str = "./cookie"):
        """初始化 Cookie 管理器

        Args:
            cookie_dir: Cookie 存储目录
        """
        self.cookie_dir = Path(cookie_dir)
        self.cookie_dir.mkdir(parents=True, exist_ok=True)

    def save_cookies(self, room_id: str, cookies: Dict[str, Any]) -> bool:
        """保存 Cookie 到文件

        Args:
            room_id: 房间 ID
            cookies: Cookie 数据（Playwright storage_state 格式）

        Returns:
            True 如果成功
        """
        try:
            file_path = self.cookie_dir / f"{room_id}.json"
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            logger.debug(f"Saved cookies for room: {room_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to save cookies: {e}")
            return False

    def load_cookies(self, room_id: str) -> Optional[Dict[str, Any]]:
        """从文件加载 Cookie

        Args:
            room_id: 房间 ID

        Returns:
            Cookie 数据字典，如果文件不存在返回 None
        """
        try:
            file_path = self.cookie_dir / f"{room_id}.json"
            if not file_path.exists():
                return None

            with open(file_path, "r", encoding="utf-8") as f:
                cookies = json.load(f)

            logger.debug(f"Loaded cookies for room: {room_id}")
            return cookies

        except Exception as e:
            logger.error(f"Failed to load cookies: {e}")
            return None

    def delete_cookies(self, room_id: str) -> bool:
        """删除指定房间的 Cookie 文件

        Args:
            room_id: 房间 ID

        Returns:
            True 如果成功
        """
        try:
            file_path = self.cookie_dir / f"{room_id}.json"
            if file_path.exists():
                file_path.unlink()
                logger.debug(f"Deleted cookies for room: {room_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete cookies: {e}")
            return False

    def list_rooms(self) -> list[str]:
        """列出所有有 Cookie 的房间

        Returns:
            房间 ID 列表
        """
        rooms = []
        for file in self.cookie_dir.glob("*.json"):
            rooms.append(file.stem)
        return rooms

    def cleanup_old_files(self, max_age_days: int = 30) -> int:
        """清理过期的 Cookie 文件

        Args:
            max_age_days: 最大保留天数

        Returns:
            删除的文件数量
        """
        import time

        deleted_count = 0
        current_time = time.time()
        max_age_seconds = max_age_days * 24 * 60 * 60

        for file in self.cookie_dir.glob("*.json"):
            if current_time - file.stat().st_mtime > max_age_seconds:
                file.unlink()
                deleted_count += 1
                logger.debug(f"Deleted old cookie file: {file.name}")

        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} old cookie files")

        return deleted_count