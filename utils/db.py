import sqlite3
import threading
import time
from queue import Queue, Empty
from datetime import datetime
from typing import Optional, Any, List, Tuple
import logging

logger = logging.getLogger(__name__)


class ConnectionPool:
    """线程安全的SQLite连接池"""
    
    def __init__(self, db_file: str, max_connections: int = 5, 
                 connection_timeout: float = 30.0, idle_timeout: float = 300.0):
        """
        初始化连接池
        
        Args:
            db_file: 数据库文件路径
            max_connections: 最大连接数
            connection_timeout: 获取连接超时时间（秒）
            idle_timeout: 空闲连接超时时间（秒）
        """
        self.db_file = db_file
        self.max_connections = max_connections
        self.connection_timeout = connection_timeout
        self.idle_timeout = idle_timeout
        
        # 连接池队列
        self._pool = Queue(max_connections)
        # 活跃连接集合（用于跟踪和清理）
        self._active_connections = set()
        # 连接锁（用于线程安全）
        self._lock = threading.RLock()
        # 连接创建时间跟踪
        self._connection_times = {}
        
        # 初始化连接池
        self._initialize_pool()
        
        # 启动连接清理线程
        self._cleanup_thread = threading.Thread(target=self._cleanup_idle_connections, daemon=True)
        self._cleanup_thread.start()
        
        logger.info(f"连接池初始化完成: {db_file}, 最大连接数: {max_connections}")
    
    def _initialize_pool(self):
        """初始化连接池，创建初始连接"""
        for _ in range(min(2, self.max_connections)):  # 初始创建2个连接
            try:
                conn = self._create_connection()
                self._pool.put(conn)
                self._connection_times[id(conn)] = time.time()
                logger.debug(f"创建初始连接: {id(conn)}")
            except Exception as e:
                logger.error(f"创建初始连接失败: {e}")
    
    def _create_connection(self) -> sqlite3.Connection:
        """创建新的数据库连接"""
        conn = sqlite3.connect(
            self.db_file,
            check_same_thread=False,  # 允许在多线程中使用
            timeout=20.0  # 连接超时
        )
        # 启用WAL模式提高并发性能
        conn.execute("PRAGMA journal_mode=WAL")
        # 设置忙等待超时
        conn.execute("PRAGMA busy_timeout=5000")
        return conn
    
    def _get_connection(self) -> Optional[sqlite3.Connection]:
        """从连接池获取连接"""
        try:
            # 尝试从池中获取连接
            conn = self._pool.get(timeout=self.connection_timeout)
            
            # 检查连接是否有效
            if self._is_connection_valid(conn):
                with self._lock:
                    self._active_connections.add(conn)
                    self._connection_times[id(conn)] = time.time()
                return conn
            else:
                # 连接无效，创建新连接
                logger.warning(f"连接无效，创建新连接: {id(conn)}")
                self._close_connection(conn)
                return self._create_new_connection()
                
        except Empty:
            # 池中没有可用连接
            logger.warning("连接池中没有可用连接，尝试创建新连接")
            return self._create_new_connection()
    
    def _create_new_connection(self) -> Optional[sqlite3.Connection]:
        """创建新连接（如果未达到最大连接数）"""
        with self._lock:
            if len(self._active_connections) < self.max_connections:
                try:
                    conn = self._create_connection()
                    self._active_connections.add(conn)
                    self._connection_times[id(conn)] = time.time()
                    logger.debug(f"创建新连接: {id(conn)}")
                    return conn
                except Exception as e:
                    logger.error(f"创建新连接失败: {e}")
                    return None
            else:
                logger.error("已达到最大连接数，无法创建新连接")
                return None
    
    def _release_connection(self, conn: sqlite3.Connection):
        """释放连接回连接池"""
        if conn is None:
            return
            
        with self._lock:
            if conn in self._active_connections:
                self._active_connections.remove(conn)
                
                # 检查连接是否仍然有效
                if self._is_connection_valid(conn):
                    try:
                        self._pool.put_nowait(conn)
                        self._connection_times[id(conn)] = time.time()
                        logger.debug(f"连接已释放回池: {id(conn)}")
                    except Exception:
                        # 池已满，关闭连接
                        self._close_connection(conn)
                else:
                    # 连接无效，关闭它
                    self._close_connection(conn)
    
    def _is_connection_valid(self, conn: sqlite3.Connection) -> bool:
        """检查连接是否有效"""
        try:
            # 尝试执行一个简单的查询
            conn.execute("SELECT 1")
            return True
        except Exception:
            return False
    
    def _close_connection(self, conn: sqlite3.Connection):
        """安全关闭连接"""
        try:
            conn.close()
            if id(conn) in self._connection_times:
                del self._connection_times[id(conn)]
            logger.debug(f"连接已关闭: {id(conn)}")
        except Exception as e:
            logger.warning(f"关闭连接时出错: {e}")
    
    def _cleanup_idle_connections(self):
        """清理空闲连接（后台线程）"""
        while True:
            time.sleep(60)  # 每分钟检查一次
            
            with self._lock:
                current_time = time.time()
                connections_to_close = []
                
                # 检查池中的连接
                temp_connections = []
                while not self._pool.empty():
                    try:
                        conn = self._pool.get_nowait()
                        conn_id = id(conn)
                        
                        # 检查是否超时
                        if conn_id in self._connection_times:
                            idle_time = current_time - self._connection_times[conn_id]
                            if idle_time > self.idle_timeout:
                                connections_to_close.append(conn)
                            else:
                                temp_connections.append(conn)
                        else:
                            connections_to_close.append(conn)
                    except Empty:
                        break
                
                # 将有效的连接放回池中
                for conn in temp_connections:
                    try:
                        self._pool.put_nowait(conn)
                    except Exception:
                        connections_to_close.append(conn)
                
                # 关闭超时的连接
                for conn in connections_to_close:
                    self._close_connection(conn)
                    logger.debug(f"清理空闲连接: {id(conn)}")
    
    def get_pool_status(self) -> dict:
        """获取连接池状态"""
        with self._lock:
            return {
                "pool_size": self._pool.qsize(),
                "active_connections": len(self._active_connections),
                "max_connections": self.max_connections,
                "db_file": self.db_file
            }
    
    def close_all(self):
        """关闭所有连接"""
        with self._lock:
            # 关闭池中的连接
            while not self._pool.empty():
                try:
                    conn = self._pool.get_nowait()
                    self._close_connection(conn)
                except Empty:
                    break
            
            # 关闭活跃连接
            for conn in list(self._active_connections):
                self._close_connection(conn)
            
            self._active_connections.clear()
            self._connection_times.clear()
            
            logger.info("所有连接已关闭")


class SQLiteDB:
    """优化的SQLite数据库类，使用连接池"""
    
    def __init__(self, db_file: str, max_connections: int = 5):
        """
        初始化数据库连接
        
        Args:
            db_file: 数据库文件路径
            max_connections: 最大连接数
        """
        self.db_file = db_file
        self.connection_pool = ConnectionPool(db_file, max_connections)
        logger.info(f"SQLiteDB初始化完成: {db_file}")
    
    def execute(self, query: str, args: Optional[Tuple] = None) -> bool:
        """
        执行SQL语句
        
        Args:
            query: SQL查询语句
            args: 查询参数
            
        Returns:
            bool: 执行是否成功
        """
        conn = None
        try:
            conn = self.connection_pool._get_connection()
            if conn is None:
                logger.error("无法获取数据库连接")
                return False
                
            cursor = conn.cursor()
            
            if args:
                cursor.execute(query, args)
            else:
                cursor.execute(query)
            
            conn.commit()
            return True
            
        except Exception as e:
            logger.error(f"执行SQL失败: {e}")
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return False
            
        finally:
            if conn:
                self.connection_pool._release_connection(conn)
    
    def fetch_all(self, query: str, args: Optional[Tuple] = None) -> List[Tuple]:
        """
        执行查询并返回所有结果
        
        Args:
            query: SQL查询语句
            args: 查询参数
            
        Returns:
            List[Tuple]: 查询结果列表
        """
        conn = None
        try:
            conn = self.connection_pool._get_connection()
            if conn is None:
                logger.error("无法获取数据库连接")
                return []
                
            cursor = conn.cursor()
            
            if args:
                cursor.execute(query, args)
            else:
                cursor.execute(query)
            
            return cursor.fetchall()
            
        except Exception as e:
            logger.error(f"查询失败: {e}")
            return []
            
        finally:
            if conn:
                self.connection_pool._release_connection(conn)
    
    def fetch_one(self, query: str, args: Optional[Tuple] = None) -> Optional[Tuple]:
        """
        执行查询并返回单条结果
        
        Args:
            query: SQL查询语句
            args: 查询参数
            
        Returns:
            Optional[Tuple]: 查询结果
        """
        conn = None
        try:
            conn = self.connection_pool._get_connection()
            if conn is None:
                logger.error("无法获取数据库连接")
                return None
                
            cursor = conn.cursor()
            
            if args:
                cursor.execute(query, args)
            else:
                cursor.execute(query)
            
            return cursor.fetchone()
            
        except Exception as e:
            logger.error(f"查询失败: {e}")
            return None
            
        finally:
            if conn:
                self.connection_pool._release_connection(conn)
    
    def execute_many(self, query: str, args_list: List[Tuple]) -> bool:
        """
        批量执行SQL语句
        
        Args:
            query: SQL查询语句
            args_list: 参数列表
            
        Returns:
            bool: 执行是否成功
        """
        conn = None
        try:
            conn = self.connection_pool._get_connection()
            if conn is None:
                logger.error("无法获取数据库连接")
                return False
                
            cursor = conn.cursor()
            
            for args in args_list:
                cursor.execute(query, args)
            
            conn.commit()
            return True
            
        except Exception as e:
            logger.error(f"批量执行失败: {e}")
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return False
            
        finally:
            if conn:
                self.connection_pool._release_connection(conn)
    
    def get_pool_status(self) -> dict:
        """获取连接池状态"""
        return self.connection_pool.get_pool_status()
    
    def close(self):
        """关闭数据库连接"""
        self.connection_pool.close_all()
        logger.info(f"数据库连接已关闭: {self.db_file}")


# 向后兼容的旧版本SQLiteDB类
class SQLiteDBLegacy:
    """旧版本的SQLiteDB类，用于向后兼容"""
    
    def __init__(self, db_file: str, max_connections: int = 5):
        self.db_file = db_file
        self.connection_pool = self._create_connection_pool(max_connections)
    
    def _create_connection_pool(self, max_connections: int) -> Queue:
        connections = Queue(max_connections)
        for _ in range(max_connections):
            conn = sqlite3.connect(self.db_file)
            connections.put(conn)
        return connections
    
    def _get_connection(self) -> sqlite3.Connection:
        return self.connection_pool.get()
    
    def _release_connection(self, conn: sqlite3.Connection):
        self.connection_pool.put(conn)
    
    def execute(self, query: str, args: Optional[Tuple] = None) -> bool:
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        try:
            if args:
                cursor.execute(query, args)
            else:
                cursor.execute(query)
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"执行SQL失败: {e}")
            return False
        finally:
            conn.close()
    
    def fetch_all(self, query: str, args: Optional[Tuple] = None) -> List[Tuple]:
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        try:
            if args:
                cursor.execute(query, args)
            else:
                cursor.execute(query)
            return cursor.fetchall()
        except Exception as e:
            logger.error(f"查询失败: {e}")
            return []
        finally:
            conn.close()


if __name__ == "__main__":
    # 测试新的连接池
    db = SQLiteDB('data/test.db')
    
    # 创建表
    create_table_sql = '''
    CREATE TABLE IF NOT EXISTS danmu (
        username TEXT,
        content TEXT,
        ts DATETIME
    )
    '''
    db.execute(create_table_sql)
    
    # 插入数据
    insert_data_sql = '''
    INSERT INTO danmu (username, content, ts) VALUES (?, ?, ?)
    '''
    db.execute(insert_data_sql, ('user1', 'test1', datetime.now()))
    
    # 查询数据
    select_data_sql = '''
    SELECT * FROM danmu
    '''
    data = db.fetch_all(select_data_sql)
    print(f"查询结果: {data}")
    
    # 获取连接池状态
    status = db.get_pool_status()
    print(f"连接池状态: {status}")
    
    # 关闭连接
    db.close()