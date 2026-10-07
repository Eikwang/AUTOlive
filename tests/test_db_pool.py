import unittest
import threading
import time
import os
import sys

# 添加项目路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db import SQLiteDB, ConnectionPool


class TestConnectionPool(unittest.TestCase):
    """测试连接池功能"""
    
    def setUp(self):
        """测试前准备"""
        self.test_db = 'data/test_unit.db'
        # 确保测试数据库存在
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
    
    def tearDown(self):
        """测试后清理"""
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
    
    def test_connection_pool_initialization(self):
        """测试连接池初始化"""
        pool = ConnectionPool(self.test_db, max_connections=3)
        
        # 验证连接池状态
        status = pool.get_pool_status()
        self.assertEqual(status['max_connections'], 3)
        self.assertEqual(status['db_file'], self.test_db)
        
        # 关闭连接池
        pool.close_all()
    
    def test_connection_reuse(self):
        """测试连接复用"""
        db = SQLiteDB(self.test_db, max_connections=2)
        
        # 创建表
        db.execute('''CREATE TABLE IF NOT EXISTS test_table (
            id INTEGER PRIMARY KEY,
            value TEXT
        )''')
        
        # 插入数据
        for i in range(5):
            db.execute("INSERT INTO test_table (value) VALUES (?)", (f'value_{i}',))
        
        # 查询数据
        result = db.fetch_all("SELECT COUNT(*) FROM test_table")
        self.assertEqual(result[0][0], 5)
        
        # 验证连接池状态
        status = db.get_pool_status()
        self.assertEqual(status['active_connections'], 0)  # 所有连接都应该被释放
        
        db.close()
    
    def test_thread_safety(self):
        """测试线程安全"""
        db = SQLiteDB(self.test_db, max_connections=3)
        
        # 创建表
        db.execute('''CREATE TABLE IF NOT EXISTS thread_test (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER,
            value TEXT
        )''')
        
        results = []
        errors = []
        
        def worker(thread_id):
            try:
                for i in range(10):
                    db.execute(
                        "INSERT INTO thread_test (thread_id, value) VALUES (?, ?)",
                        (thread_id, f'value_{thread_id}_{i}')
                    )
                results.append(thread_id)
            except Exception as e:
                errors.append((thread_id, str(e)))
        
        # 创建多个线程
        threads = []
        for i in range(5):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
        
        # 等待所有线程完成
        for t in threads:
            t.join()
        
        # 验证结果
        self.assertEqual(len(errors), 0, f"线程错误: {errors}")
        self.assertEqual(len(results), 5)
        
        # 验证数据完整性
        count = db.fetch_one("SELECT COUNT(*) FROM thread_test")
        self.assertEqual(count[0], 50)  # 5 threads * 10 inserts
        
        db.close()
    
    def test_connection_timeout(self):
        """测试连接超时"""
        db = SQLiteDB(self.test_db, max_connections=2)
        
        # 获取所有连接
        connections = []
        for _ in range(2):
            conn = db.connection_pool._get_connection()
            connections.append(conn)
        
        # 尝试获取第三个连接（应该超时）
        start_time = time.time()
        conn = db.connection_pool._get_connection()
        end_time = time.time()
        
        # 应该返回None（因为达到最大连接数）
        self.assertIsNone(conn)
        
        # 释放连接
        for conn in connections:
            db.connection_pool._release_connection(conn)
        
        db.close()
    
    def test_connection_pool_monitoring(self):
        """测试连接池监控"""
        db = SQLiteDB(self.test_db, max_connections=3)
        
        # 初始状态
        status = db.get_pool_status()
        self.assertEqual(status['pool_size'], 2)  # 初始创建2个连接
        self.assertEqual(status['active_connections'], 0)
        
        # 获取一个连接
        conn = db.connection_pool._get_connection()
        status = db.get_pool_status()
        self.assertEqual(status['active_connections'], 1)
        
        # 释放连接
        db.connection_pool._release_connection(conn)
        status = db.get_pool_status()
        self.assertEqual(status['active_connections'], 0)
        
        db.close()
    
    def test_execute_and_fetch(self):
        """测试执行和查询"""
        db = SQLiteDB(self.test_db)
        
        # 创建表
        result = db.execute('''CREATE TABLE IF NOT EXISTS operations_test (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            value INTEGER
        )''')
        self.assertTrue(result)
        
        # 插入数据
        result = db.execute(
            "INSERT INTO operations_test (name, value) VALUES (?, ?)",
            ('test', 42)
        )
        self.assertTrue(result)
        
        # 查询单条数据
        row = db.fetch_one("SELECT * FROM operations_test WHERE name = ?", ('test',))
        self.assertIsNotNone(row)
        self.assertEqual(row[1], 'test')
        self.assertEqual(row[2], 42)
        
        # 查询多条数据
        db.execute("INSERT INTO operations_test (name, value) VALUES (?, ?)", ('test2', 43))
        rows = db.fetch_all("SELECT * FROM operations_test ORDER BY id")
        self.assertEqual(len(rows), 2)
        
        db.close()
    
    def test_batch_operations(self):
        """测试批量操作"""
        db = SQLiteDB(self.test_db)
        
        # 创建表
        db.execute('''CREATE TABLE IF NOT EXISTS batch_test (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            value TEXT
        )''')
        
        # 批量插入
        data = [(f'value_{i}',) for i in range(100)]
        result = db.execute_many(
            "INSERT INTO batch_test (value) VALUES (?)",
            data
        )
        self.assertTrue(result)
        
        # 验证插入数量
        count = db.fetch_one("SELECT COUNT(*) FROM batch_test")
        self.assertEqual(count[0], 100)
        
        db.close()


class TestSQLiteDBLegacy(unittest.TestCase):
    """测试旧版本SQLiteDB类（向后兼容）"""
    
    def setUp(self):
        self.test_db = 'data/test_legacy.db'
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
    
    def tearDown(self):
        # 等待连接释放
        time.sleep(0.1)
        try:
            if os.path.exists(self.test_db):
                os.remove(self.test_db)
        except PermissionError:
            # 文件可能仍被占用，跳过清理
            pass
    
    def test_legacy_operations(self):
        """测试旧版本操作"""
        from utils.db import SQLiteDBLegacy
        
        db = SQLiteDBLegacy(self.test_db)
        
        # 创建表
        db.execute('''CREATE TABLE IF NOT EXISTS legacy_test (
            id INTEGER PRIMARY KEY,
            value TEXT
        )''')
        
        # 插入数据
        db.execute("INSERT INTO legacy_test (value) VALUES (?)", ('test',))
        
        # 查询数据
        result = db.fetch_all("SELECT * FROM legacy_test")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1], 'test')
        
        # 注意：旧版本没有close方法，连接可能不会立即释放


if __name__ == '__main__':
    unittest.main()