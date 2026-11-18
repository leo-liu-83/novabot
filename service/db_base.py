# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : db_base.py
# @Description : SQLite服务的连接基类：库路径实时解析，路径变了就重连并重建表

import os
import sqlite3
import stat
import threading
from typing import Optional

from config import config


class SqliteService:
    """共用一个SQLite库的服务的连接基类。

    **为什么路径不能定死在构造时**：库路径早先是import时算好的类属性，
    之后改 `APP_DATA_PATH` 不会让它跟着变。单例服务若在构造时把路径钉死，
    就永远指向那一个库——测试会写进真库（审查记录里混进/tmp路径的行就是这么来的），
    多用户隔离也会指到该去的库之外。

    所以这里**每次连接都重新解析路径**，路径变了就重连并重建表结构。
    """

    def __init__(self, db_path: Optional[str] = None):
        # 显式传入的路径优先（测试用）；None表示跟随config实时解析
        self._fixed_db_path = db_path
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._conn_path: Optional[str] = None
        self._connection()  # 建表与迁移在连上时做

    @property
    def db_path(self) -> str:
        """当前生效的库路径。"""
        if self._fixed_db_path:
            return self._fixed_db_path

        path = config.db_file_path()
        directory = os.path.dirname(path) or "."
        if not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
            # 库里有主密钥与密文：目录不给其他用户遍历
            if os.name == "posix":
                try:
                    os.chmod(directory, stat.S_IRWXU)
                except OSError:
                    pass
        return path

    def _connection(self) -> sqlite3.Connection:
        path = self.db_path
        if self._conn is not None and self._conn_path == path:
            return self._conn

        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

        conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
        if os.name == "posix":
            # 主密钥就在这个库里：文件权限是最后一道边界。
            # 这里对存量安装也生效（目录可能是旧版本用默认权限建的）
            try:
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
                os.chmod(os.path.dirname(path) or ".", stat.S_IRWXU)
            except OSError:
                pass
        conn.row_factory = sqlite3.Row
        self._conn = conn
        self._conn_path = path
        self._on_new_connection(conn)

        return conn

    def _on_new_connection(self, conn: sqlite3.Connection) -> None:
        """连上新库时的建表/迁移——由各服务实现（拿到conn，不走self._query以免重入锁）。"""
