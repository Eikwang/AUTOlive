"""
配置热更新功能测试
"""

import unittest
import json
import os
import time
import tempfile
import threading


class TestConfigHotReload(unittest.TestCase):
    """测试 Config 热更新功能"""

    def setUp(self):
        """测试前准备"""
        self.tmp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmp_dir, "test_config.json")
        self.initial_config = {
            "test_key": "initial_value",
            "nested": {"key": "value"}
        }
        self._write_config(self.initial_config)
        
        # 重置 Config 类状态
        from utils.config import Config
        Config.config = None
        Config._config_file = None
        Config._callbacks = []
        self.Config = Config

    def tearDown(self):
        """测试后清理"""
        if os.path.exists(self.config_path):
            os.remove(self.config_path)
        if os.path.exists(self.tmp_dir):
            os.rmdir(self.tmp_dir)

    def _write_config(self, config_dict):
        """写入配置文件"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(config_dict, f, ensure_ascii=False)

    def test_config_load(self):
        """测试配置加载"""
        config = self.Config(self.config_path)
        self.assertEqual(config.get("test_key"), "initial_value")
        self.assertEqual(config.get("nested", "key"), "value")

    def test_config_reload(self):
        """测试配置重新加载"""
        config = self.Config(self.config_path)
        self.assertEqual(config.get("test_key"), "initial_value")

        # 修改配置
        new_config = {"test_key": "updated_value", "nested": {"key": "new_value"}}
        self._write_config(new_config)

        # 重新加载
        result = self.Config.reload()
        self.assertTrue(result)
        self.assertEqual(config.get("test_key"), "updated_value")
        self.assertEqual(config.get("nested", "key"), "new_value")

    def test_config_reload_callback(self):
        """测试配置变化回调"""
        config = self.Config(self.config_path)
        
        callback_called = threading.Event()
        callback_config = [None]

        def on_change(new_config):
            callback_config[0] = new_config
            callback_called.set()

        self.Config.on_config_change(on_change)

        # 修改配置并重新加载
        new_config = {"test_key": "callback_test"}
        self._write_config(new_config)
        self.Config.reload()

        # 验证回调被调用
        self.assertTrue(callback_called.wait(timeout=2))
        self.assertIsNotNone(callback_config[0])
        self.assertEqual(callback_config[0]["test_key"], "callback_test")

    def test_config_reload_invalid_json(self):
        """测试无效JSON配置文件"""
        config = self.Config(self.config_path)
        
        # 写入无效JSON
        with open(self.config_path, 'w') as f:
            f.write("not valid json")

        # 重新加载应该失败
        result = self.Config.reload()
        self.assertFalse(result)
        
        # 原配置应保持不变
        self.assertEqual(config.get("test_key"), "initial_value")

    def test_config_reload_file_not_found(self):
        """测试配置文件不存在"""
        config = self.Config(self.config_path)
        
        # 删除配置文件
        os.remove(self.config_path)

        # 重新加载应该失败
        result = self.Config.reload()
        self.assertFalse(result)

    def test_config_get_nested(self):
        """测试嵌套配置获取"""
        config = self.Config(self.config_path)
        self.assertEqual(config.get("nested", "key"), "value")
        self.assertIsNone(config.get("nonexistent"))
        self.assertIsNone(config.get("nested", "nonexistent"))

    def test_config_remove_callback(self):
        """测试移除回调"""
        callback_count = [0]

        def on_change(new_config):
            callback_count[0] += 1

        self.Config(self.config_path)
        self.Config.on_config_change(on_change)

        # 第一次reload
        self._write_config({"test_key": "v1"})
        self.Config.reload()
        self.assertEqual(callback_count[0], 1)

        # 移除回调
        self.Config.remove_callback(on_change)

        # 第二次reload
        self._write_config({"test_key": "v2"})
        self.Config.reload()
        self.assertEqual(callback_count[0], 1)  # 不应增加


class TestConfigWatcher(unittest.TestCase):
    """测试 ConfigWatcher 文件监听"""

    def setUp(self):
        """测试前准备"""
        self.tmp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmp_dir, "watcher_test.json")
        self.initial_config = {"watcher_key": "initial"}
        self._write_config(self.initial_config)

    def tearDown(self):
        """测试后清理"""
        if os.path.exists(self.config_path):
            os.remove(self.config_path)
        if os.path.exists(self.tmp_dir):
            os.rmdir(self.tmp_dir)

    def _write_config(self, config_dict):
        """写入配置文件"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(config_dict, f, ensure_ascii=False)

    def test_watcher_detects_change(self):
        """测试监视器检测文件变化"""
        from utils.config_watcher import ConfigWatcher

        change_detected = threading.Event()
        detected_config = [None]

        def on_change(new_config):
            detected_config[0] = new_config
            change_detected.set()

        watcher = ConfigWatcher(self.config_path, callback=on_change, debounce_seconds=0.5)
        watcher.start()

        try:
            # 等待监视器启动
            time.sleep(0.5)

            # 修改配置文件
            new_config = {"watcher_key": "updated"}
            self._write_config(new_config)

            # 等待检测到变化
            self.assertTrue(change_detected.wait(timeout=5))
            self.assertIsNotNone(detected_config[0])
            self.assertEqual(detected_config[0]["watcher_key"], "updated")
        finally:
            watcher.stop()

    def test_watcher_debounce(self):
        """测试防抖机制"""
        from utils.config_watcher import ConfigWatcher

        change_count = [0]

        def on_change(new_config):
            change_count[0] += 1

        watcher = ConfigWatcher(self.config_path, callback=on_change, debounce_seconds=1.0)
        watcher.start()

        try:
            time.sleep(0.5)

            # 快速连续修改多次（间隔0.15秒，防抖1秒内只触发1次）
            for i in range(5):
                self._write_config({"count": i})
                time.sleep(0.15)

            # 等待足够时间让防抖生效 + 事件传播
            time.sleep(3)

            # 防抖后应只触发有限次（最多2次，取决于OS事件合并）
            self.assertLessEqual(change_count[0], 2)
            self.assertGreaterEqual(change_count[0], 1)
        finally:
            watcher.stop()

    def test_watcher_start_stop(self):
        """测试监视器启停"""
        from utils.config_watcher import ConfigWatcher

        watcher = ConfigWatcher(self.config_path, callback=lambda x: None)
        
        self.assertFalse(watcher.is_running())
        
        watcher.start()
        self.assertTrue(watcher.is_running())
        
        watcher.stop()
        self.assertFalse(watcher.is_running())


if __name__ == '__main__':
    unittest.main()
