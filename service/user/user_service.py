# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : user_service.py
# @Software    : VSCode
# @Datetime    : 2026/10/10 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 用户身份与认证服务（两级身份：匿名default / 登录USER；opaque token 鉴权）

import hashlib
import hmac
import json
import os
import secrets
import shutil
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from config import config
from logger import logger
from service.db_base import SqliteService
from const.messages import msg
from exception.user_exception import AuthError
from core.agent.deep_agents.exec_policy import (
    ADMIN_DEFAULT,
    ANONYMOUS_CEILING,
    ANONYMOUS_DEFAULT,
    SINGLE_USER_CEILING,
    SINGLE_USER_DEFAULT,
    USER_DEFAULT,
    ExecLevel,
)
from service.session.session_service import DEFAULT_USER_ID
from util.base import base_util

USERNAME_MAX = 32
PASSWORD_MIN = 6
TOKEN_BYTES = 32
# remember=False 时签发的 token 有效期。TUI 每次启动都新签一个、用完不吊销，
# 没有这个 TTL 就会永久堆积有效凭证（见 purge_expired_tokens）
SESSION_TOKEN_TTL_SECONDS = 12 * 3600
SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32

# 长期记忆：core.md 常驻注入（兼目录索引），其余按分类文件存放、按需读取
MEMORY_CORE_FILE = "core.md"
MEMORY_CORE_TEMPLATE = """# 核心记忆（自动维护，上限约 40 行，勿超）

## 当前状态
- 尚无记录

## 记忆索引（表格为唯一形式，改动前先读 core.md，勿改列数）
| 文件 | 存什么 | 最近更新 | 当前摘要 |
|---|---|---|---|
"""

MEMORY_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("projects.md", "项目上下文"),
    ("decisions.md", "决策与理由"),
    ("lessons.md", "经验教训"),
    ("todos.md", "进行中的事"),
    ("glossary.md", "术语表"),
    ("refs.md", "资料指针"),
)

# 旧命名 → 新命名。登录补目录时顺手迁移，新文件已有内容时不覆盖。
LEGACY_MEMORY_FILES: tuple[tuple[str, str], ...] = (
    ("project.md", "projects.md"),
    ("todo.md", "todos.md"),
)


def memory_category_template(what: str) -> str:
    return f"# {what}\n\n<!-- 记录什么：{what}。条目以 `- [YYYY-MM-DD] ` 开头，追加到文件末尾 -->\n"


def _read_memory_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as file:
            return file.read()
    except OSError:
        return ""


def _is_memory_seed(path: str, what: str) -> bool:
    """空文件或代码铺的模板骨架，视为"还没有内容"。"""
    text = _read_memory_text(path).strip()
    return not text or text == memory_category_template(what).strip()


def _rename_core_index(core_path: str, old: str, new: str) -> None:
    """只改索引表格里第一列等于旧名的行，正文里的提及不动。"""
    text = _read_memory_text(core_path)
    if not text:
        return
    changed = False
    lines = []
    for line in text.splitlines(keepends=True):
        cells = line.split("|")
        if len(cells) >= 3 and cells[1].strip() == old:
            cells[1] = cells[1].replace(old, new, 1)
            line = "|".join(cells)
            changed = True
        lines.append(line)
    if changed:
        with open(core_path, "w", encoding="utf-8") as file:
            file.write("".join(lines))


def _migrate_memory_files(memory_dir: str) -> None:
    """把旧单数分类文件迁到新名字：有内容就保留，空骨架直接丢掉。"""
    what_of = dict(MEMORY_CATEGORIES)
    for old, new in LEGACY_MEMORY_FILES:
        old_path = os.path.join(memory_dir, old)
        new_path = os.path.join(memory_dir, new)
        if not os.path.isfile(old_path):
            continue
        what = what_of.get(new, "")
        if not os.path.exists(new_path) or _is_memory_seed(new_path, what):
            # 新文件缺失或只是空骨架：旧文件（带内容）接管新名字
            os.replace(old_path, new_path)
        elif _is_memory_seed(old_path, what):
            os.remove(old_path)
        else:
            # 两边都有内容：并入新文件，避免静默丢记录
            with open(new_path, "a", encoding="utf-8") as file:
                file.write("\n\n")
                file.write(_read_memory_text(old_path))
            os.remove(old_path)
        _rename_core_index(os.path.join(memory_dir, MEMORY_CORE_FILE), old, new)


AVATAR_EDGE = 256
AVATAR_MAX_BYTES = 2 * 1024 * 1024


def normalize_avatar(data_url: str) -> str:
    """把前端裁好的图片压成 256×256 JPEG data URL。

    数据库只存最终的小图：不落原始文件，避免再维护一套头像文件服务；
    同时用 Pillow 重新解码/编码，挡掉伪装成图片的文本和超大文件。
    """
    import base64
    import io

    from PIL import Image, UnidentifiedImageError

    raw = (data_url or "").strip()
    if not raw:
        return ""
    if not raw.startswith("data:image/") or "," not in raw:
        raise ValueError("头像必须是图片")

    _, encoded = raw.split(",", 1)
    try:
        data = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("头像编码无效") from exc
    if not data or len(data) > AVATAR_MAX_BYTES:
        raise ValueError("头像文件过大")

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            width, height = image.size
            if not width or not height:
                raise ValueError("头像尺寸无效")
            side = min(width, height)
            left, top = (width - side) // 2, (height - side) // 2
            square = image.crop((left, top, left + side, top + side)).convert("RGB")
            square = square.resize((AVATAR_EDGE, AVATAR_EDGE), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            square.save(out, "JPEG", quality=86, optimize=True)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("头像不是有效图片") from exc

    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode("ascii")


def _seed_memory_files(memory_dir: str) -> None:
    """建目录时铺好记忆骨架。只补不存在的文件，绝不覆盖已写内容（重复调用安全）"""
    rows = "".join(
        f"| {name} | {what} | - | 无条目 |\n" for name, what in MEMORY_CATEGORIES
    )

    for path, content in (
        (f"{memory_dir}/{MEMORY_CORE_FILE}", MEMORY_CORE_TEMPLATE + rows),
        *((f"{memory_dir}/{name}", memory_category_template(what)) for name, what in MEMORY_CATEGORIES),
    ):
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    uid        TEXT PRIMARY KEY,
    username   TEXT NOT NULL UNIQUE,
    pass_hash  TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    is_admin   INTEGER NOT NULL DEFAULT 0,
    avatar     TEXT NOT NULL DEFAULT '',
    agent_avatar TEXT NOT NULL DEFAULT '',
    projects_enabled INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS user_exec_grants (
    uid        TEXT PRIMARY KEY,
    ceiling    INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_tokens (
    token      TEXT PRIMARY KEY,
    uid        TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_auth_tokens_uid ON auth_tokens(uid);
"""


@dataclass(frozen=True)
class UserCtx:
    """当前身份的全部路径与能力派生值；default 档的人格/记忆字段为 None，表示该能力不存在"""

    uid: str
    username: Optional[str]
    workspace_path: str
    skill_mounts: tuple[tuple[str, str], ...]
    soul_path: Optional[str]
    user_profile_path: Optional[str]
    memory_path: Optional[str]
    user_prompt_path: Optional[str]
    public_prompt_path: str
    is_admin: bool = False
    # admin为该账号的执行档位授权（即天花板）；None=未授权，此时非admin的天花板取
    # NON_ADMIN_CEILING_DEFAULT（T1）。显式字段而非build_ctx里查库：调用方漏传只会少授权，不会多授权。
    exec_ceiling: Optional[ExecLevel] = None
    # default身份（匿名/机主）的天花板：多用户下T1，单用户下T4。
    # 随ctx带入是因为exec_policy是叶子模块（config反过来引它），读不到配置。
    # 默认取T1：漏传时按更保守的一档算，不会多给能力。
    default_ceiling: ExecLevel = ANONYMOUS_CEILING
    # 默认生效档位（用户未用 /exec 自提时用哪个档）：单用户机主T1、admin T3、其他T0。
    # 同样是随ctx带入的派生值；默认取T0，漏传时按最保守的一档算。
    default_level: ExecLevel = USER_DEFAULT
    # 项目功能授权：多用户下由 admin 按账号开启；默认 False，漏传只会少授权。
    projects_enabled: bool = False

    @property
    def is_default(self) -> bool:
        return self.uid == DEFAULT_USER_ID

    @property
    def skills_sources(self) -> tuple[str, ...]:
        return tuple(mount for mount, _ in self.skill_mounts)

    @property
    def can_persona(self) -> bool:
        return self.soul_path is not None

    @property
    def memory_core_path(self) -> Optional[str]:
        return f"{self.memory_path}/{MEMORY_CORE_FILE}" if self.memory_path else None

    @property
    def can_memory(self) -> bool:
        return self.memory_path is not None

    @property
    def can_projects(self) -> bool:
        """项目功能可用性：单用户机主本就能碰本机目录，多用户下要 admin 或显式授权。"""
        if self.is_default:
            return not config.MULTI_USER

        return self.is_admin or self.projects_enabled

    def resolve_prompt_path(self) -> str:
        """两层prompt.md互斥：用户层文件存在则独占生效，否则回退公共层"""
        if self.user_prompt_path and os.path.exists(self.user_prompt_path):
            return self.user_prompt_path

        return self.public_prompt_path


def user_root_path(uid: str) -> str:
    return f"{config.APP_DATA_PATH}/users/{uid}"


def user_workspace_path(uid: str) -> str:
    return f"{user_root_path(uid)}/workspace"


def system_skills_path() -> str:
    return f"{base_util.root_path}/{config.AGENT_SYSTEM_SKILLS_PATH}"


def shared_skills_path() -> str:
    return f"{config.AGENT_WORKSPACE_PATH}/skills"


def build_ctx(uid: str, username: Optional[str], is_admin: bool = False,
              exec_ceiling: Optional[ExecLevel] = None,
              projects_enabled: bool = False) -> UserCtx:
    """按身份装配ctx；default 档挂载共享技能为可写/private层，USER 档额外挂只读共享层并开启人格与记忆

    is_admin决定默认档位与天花板（E6）：非admin账号默认天花板 T1、默认档位 T0，
    "自助注册→私有技能→自动执行"那条链要靠"默认 T0 + 需 admin 上浮"两层才通。
    exec_ceiling是admin后续授予的执行档位上限；最终仍要过服务封顶（config.exec.level）。
    """
    if uid == DEFAULT_USER_ID:
        mounts = [("/skills/", shared_skills_path())]
        if os.path.isdir(system_skills_path()):
            mounts.insert(0, ("/system_skills/", system_skills_path()))

        return UserCtx(
            uid=uid,
            username=username,
            workspace_path=config.AGENT_WORKSPACE_PATH,
            skill_mounts=tuple(mounts),
            soul_path=None,
            user_profile_path=None,
            memory_path=None,
            user_prompt_path=None,
            public_prompt_path=config.AGENT_USER_PROMPT_PATH,
            is_admin=False,
            exec_ceiling=None,
            # 同一档default身份在两种模式下能力不同：多用户=匿名者(T1天花板/T0默认)，单用户=机主(T4天花板/T1默认)
            default_ceiling=(ANONYMOUS_CEILING if config.MULTI_USER else SINGLE_USER_CEILING),
            default_level=(ANONYMOUS_DEFAULT if config.MULTI_USER else SINGLE_USER_DEFAULT),
            projects_enabled=projects_enabled,
        )

    workspace = user_workspace_path(uid)
    mounts = [("/shared_skills/", shared_skills_path()), ("/skills/", f"{workspace}/skills")]
    if os.path.isdir(system_skills_path()):
        mounts.insert(0, ("/system_skills/", system_skills_path()))

    return UserCtx(
        uid=uid,
        username=username,
        workspace_path=workspace,
        skill_mounts=tuple(mounts),
        soul_path=f"{workspace}/SOUL.md",
        user_profile_path=f"{workspace}/USER.md",
        memory_path=f"{workspace}/memory",
        user_prompt_path=f"{workspace}/prompt.md",
        public_prompt_path=config.AGENT_USER_PROMPT_PATH,
        is_admin=is_admin,
        exec_ceiling=exec_ceiling,
        # admin默认T3（任意命令·逐条确认），要免确认得自己在会话里提到T4；
        # 非admin默认T0，拿到授权后也要自己提到该档才生效
        default_level=(ADMIN_DEFAULT if is_admin else USER_DEFAULT),
        projects_enabled=projects_enabled,
    )


def default_ctx() -> UserCtx:
    # 不查授权：default档是匿名/单用户身份，授权表按uid存且该档不会有行；
    # 且本函数在模块加载期被调用，那时user_service单例尚未创建。
    return build_ctx(DEFAULT_USER_ID, None)


_current_ctx: UserCtx = default_ctx()


def get_current_ctx() -> UserCtx:
    return _current_ctx


def set_current_ctx(ctx: UserCtx) -> None:
    global _current_ctx
    _current_ctx = ctx


def memory_file(uid: str, name: str) -> Optional[str]:
    """定位某个记忆文件；name 必须是已知分类或core.md，非法名返回None（防路径穿越）"""
    allowed = {MEMORY_CORE_FILE, *(n for n, _ in MEMORY_CATEGORIES)}
    if name not in allowed:
        return None

    return f"{user_workspace_path(uid)}/memory/{name}"


def list_memory_files(uid: str) -> list[tuple[str, int]]:
    """列出记忆文件及行数；core.md 排在最前"""
    base = f"{user_workspace_path(uid)}/memory"
    names = [MEMORY_CORE_FILE, *(n for n, _ in MEMORY_CATEGORIES)]

    result = []
    for name in names:
        try:
            with open(f"{base}/{name}", "r", encoding="utf-8") as f:
                result.append((name, len(f.readlines())))
        except OSError:
            continue

    return result


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
            n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN,
        )
    except (ValueError, TypeError):
        return False

    return hmac.compare_digest(digest.hex(), digest_hex)


class UserService(SqliteService):
    """users/auth_tokens 的SQLite存储与认证纯函数；签名不依赖Textual，供将来API复用"""

    def __init__(self, db_path: Optional[str] = None, login_state_path: Optional[str] = None):
        self.login_state_path = login_state_path or f"{config.APP_DATA_PATH}/data/login.json"
        super().__init__(db_path)

    def _on_new_connection(self, conn: sqlite3.Connection) -> None:
        conn.executescript(SCHEMA)
        # CREATE TABLE IF NOT EXISTS 不会给已存在的表补列，故存量库要单独迁移。
        # 存量账号一律升最早创建的那个为admin：它最可能是机主本人，
        # 否则老安装会因"没有admin"而被强制拉进建管理员流程。
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        if "is_admin" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
        if "avatar" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN avatar TEXT NOT NULL DEFAULT ''")
        if "agent_avatar" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN agent_avatar TEXT NOT NULL DEFAULT ''")
        if "projects_enabled" not in cols:
            # 项目功能默认关闭：多用户下必须由 admin 逐个开启
            conn.execute(
                "ALTER TABLE users ADD COLUMN projects_enabled INTEGER NOT NULL DEFAULT 0")
        conn.commit()

        # 这里直接用conn，不走self._query——那会重入连接时已持有的锁
        if conn.execute("SELECT uid FROM users WHERE is_admin = 1 LIMIT 1").fetchone():
            return

        oldest = conn.execute(
            "SELECT uid FROM users ORDER BY created_at ASC, uid ASC LIMIT 1").fetchone()
        if oldest:
            conn.execute("UPDATE users SET is_admin = 1 WHERE uid = ?", (oldest["uid"],))
            conn.commit()

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            conn = self._connection()
            conn.execute(sql, params)
            conn.commit()

    def _execute_returning_count(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            conn = self._connection()
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.rowcount

    def _execute_many(self, sql: str) -> None:
        with self._lock:
            conn = self._connection()
            conn.executescript(sql)
            conn.commit()

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            conn = self._connection()
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def _validate_username(self, username: str) -> str:
        name = (username or "").strip()
        if not 1 <= len(name) <= USERNAME_MAX:
            raise AuthError(msg.MSG_AUTH_USERNAME_INVALID, code="auth.username_invalid")

        return name

    def _validate_password(self, password: str) -> None:
        if len(password or "") < PASSWORD_MIN:
            raise AuthError(msg.MSG_AUTH_PASSWORD_TOO_SHORT, code="auth.password_too_short")

    def ensure_user_dirs(self, uid: str) -> str:
        workspace = user_workspace_path(uid)
        os.makedirs(f"{workspace}/skills", exist_ok=True)
        memory = f"{workspace}/memory"
        os.makedirs(memory, exist_ok=True)
        # 先迁旧命名，再补骨架：顺序反了会先建出新的空文件，导致旧内容被当成"重复"
        _migrate_memory_files(memory)
        _seed_memory_files(memory)

        return workspace

    def get_user(self, uid: str) -> Optional[dict]:
        rows = self._query(
            "SELECT uid, username, is_admin, created_at, avatar, agent_avatar,"
            " projects_enabled FROM users WHERE uid = ?",
            (uid,))
        return rows[0] if rows else None

    def set_avatar(self, uid: str, data_url: str) -> str:
        """更新账号头像；返回入库后的 data URL。"""
        avatar = normalize_avatar(data_url)
        self._execute("UPDATE users SET avatar = ? WHERE uid = ?", (avatar, uid))
        return avatar

    def clear_avatar(self, uid: str) -> None:
        self._execute("UPDATE users SET avatar = '' WHERE uid = ?", (uid,))

    def set_agent_avatar(self, uid: str, data_url: str) -> str:
        """更新 NovaBot 头像；与账号头像同一套压缩与校验。"""
        avatar = normalize_avatar(data_url)
        self._execute("UPDATE users SET agent_avatar = ? WHERE uid = ?", (avatar, uid))
        return avatar

    def clear_agent_avatar(self, uid: str) -> None:
        self._execute("UPDATE users SET agent_avatar = '' WHERE uid = ?", (uid,))

    def get_projects_enabled(self, uid: str) -> bool:
        """项目功能授权；无行（含 default 身份）一律 False。"""
        rows = self._query("SELECT projects_enabled FROM users WHERE uid = ?", (uid,))
        return bool(rows[0]["projects_enabled"]) if rows else False

    def set_projects_enabled(self, uid: str, enabled: bool) -> bool:
        """admin 开关项目功能；账号不存在返回 False。"""
        return self._execute_returning_count(
            "UPDATE users SET projects_enabled = ? WHERE uid = ?",
            (1 if enabled else 0, uid),
        ) == 1

    def get_exec_grant(self, uid: str) -> Optional[ExecLevel]:
        """admin为该账号授予的执行档位上限；无行=未授权"""
        rows = self._query("SELECT ceiling FROM user_exec_grants WHERE uid = ?", (uid,))
        if not rows:
            return None

        try:
            return ExecLevel(int(rows[0]["ceiling"]))
        except ValueError:
            return None

    def set_exec_grant(self, uid: str, ceiling: ExecLevel) -> None:
        self._execute(
            "INSERT INTO user_exec_grants (uid, ceiling, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET ceiling=excluded.ceiling, updated_at=excluded.updated_at",
            (uid, int(ceiling), int(time.time())),
        )

    def clear_exec_grant(self, uid: str) -> None:
        self._execute("DELETE FROM user_exec_grants WHERE uid = ?", (uid,))

    def list_exec_grants(self) -> list[dict]:
        return self._query(
            "SELECT uid, ceiling, updated_at FROM user_exec_grants ORDER BY uid")

    def get_uid_by_username(self, username: str) -> Optional[str]:
        """管理命令按用户名定位账号。用户名唯一，故至多一行。"""
        rows = self._query("SELECT uid FROM users WHERE username = ?", ((username or "").strip(),))
        return rows[0]["uid"] if rows else None

    def has_admin(self) -> bool:
        return bool(self._query("SELECT uid FROM users WHERE is_admin = 1 LIMIT 1"))

    def create_admin(self, username: str, password: str, *, remember: bool=True) -> tuple[UserCtx, str]:
        """创建首个管理员账号（E6）。已存在admin时拒绝——首个admin不可被顶替。

        TUI 与 Web 首次初始化都走这里；是否允许调用由入口决定（API 侧还要求多用户模式）。
        `remember=False`（配置了不保留登录态）时签发的 token 带 TTL，不留永久有效凭证。
        """
        name = self._validate_username(username)
        self._validate_password(password)

        # 先查输入合法性再查状态：用户名已被占用比"admin已存在"更能帮用户定位问题。
        # has_admin是安全闸（防第二个admin被创建），正常路径上setup页只在无admin时出现。
        if self._query("SELECT uid FROM users WHERE username = ?", (name,)):
            raise AuthError(msg.MSG_AUTH_USERNAME_TAKEN, code="auth.username_taken")

        if self.has_admin():
            raise AuthError(msg.MSG_AUTH_ADMIN_EXISTS, code="auth.admin_exists")

        uid = str(uuid.uuid4())
        # 原子闸：has_admin 与 INSERT 之间存在竞态（两个进程/worker 可同时通过检查）。
        # 把"无管理员"写进 INSERT 的 WHERE，让数据库串行化决定谁赢，输的一方 rowcount=0。
        try:
            inserted = self._execute_returning_count(
                "INSERT INTO users (uid, username, pass_hash, created_at, is_admin) "
                "SELECT ?, ?, ?, ?, 1 "
                "WHERE NOT EXISTS (SELECT 1 FROM users WHERE is_admin = 1)",
                (uid, name, hash_password(password), int(time.time())),
            )
        except sqlite3.IntegrityError as exc:
            # 并发抢同一个用户名：唯一约束兜底，语义与先检查时一致。
            raise AuthError(msg.MSG_AUTH_USERNAME_TAKEN, code="auth.username_taken") from exc

        if inserted == 0:
            raise AuthError(msg.MSG_AUTH_ADMIN_EXISTS, code="auth.admin_exists")

        self.ensure_user_dirs(uid)

        return build_ctx(
            uid, name, True, projects_enabled=self.get_projects_enabled(uid)
        ), self.issue_token(uid, ttl_seconds=self._ttl_for(remember))

    def set_admin(self, username: str, is_admin: bool) -> bool:
        """admin为其他账号升降权。账号不存在返回False。"""
        rows = self._query("SELECT uid FROM users WHERE username = ?", (username,))
        if not rows:
            return False

        self._execute("UPDATE users SET is_admin = ? WHERE uid = ?", (1 if is_admin else 0, rows[0]["uid"]))

        return True

    def list_users(self) -> list[dict]:
        return self._query(
            "SELECT uid, username, is_admin, created_at, avatar, projects_enabled FROM users "
            "ORDER BY created_at ASC, username ASC")

    def count_admins(self) -> int:
        rows = self._query("SELECT COUNT(*) AS n FROM users WHERE is_admin = 1")
        return int(rows[0]["n"]) if rows else 0

    def create_user(self, username: str, password: str, *, is_admin: bool=False) -> str:
        """管理员建号：只建账号，不签发 token（建号不是登录）。"""
        name = self._validate_username(username)
        self._validate_password(password)

        if self._query("SELECT uid FROM users WHERE username = ?", (name,)):
            raise AuthError(msg.MSG_AUTH_USERNAME_TAKEN, code="auth.username_taken")

        uid = str(uuid.uuid4())
        self._execute(
            "INSERT INTO users (uid, username, pass_hash, created_at, is_admin) VALUES (?, ?, ?, ?, ?)",
            (uid, name, hash_password(password), int(time.time()), 1 if is_admin else 0),
        )
        self.ensure_user_dirs(uid)

        return uid

    def delete_account_by_admin(self, uid: str, purge_files: bool=False) -> bool:
        """管理员删除他人账号：不验目标密码（权限来自 admin 身份，接口层负责校验）。"""
        if not self._query("SELECT uid FROM users WHERE uid = ?", (uid,)):
            return False

        self.revoke_all(uid)
        self._execute("DELETE FROM users WHERE uid = ?", (uid,))
        if purge_files:
            shutil.rmtree(user_root_path(uid), ignore_errors=True)

        return True

    def register(self, username: str, password: str, *, remember: bool=True) -> tuple[UserCtx, str]:
        """注册并直接签发token；用户名大小写敏感（SQLite默认BINARY排序）"""
        name = self._validate_username(username)
        self._validate_password(password)

        if self._query("SELECT uid FROM users WHERE username = ?", (name,)):
            raise AuthError(msg.MSG_AUTH_USERNAME_TAKEN, code="auth.username_taken")

        uid = str(uuid.uuid4())
        self._execute(
            "INSERT INTO users (uid, username, pass_hash, created_at) VALUES (?, ?, ?, ?)",
            (uid, name, hash_password(password), int(time.time())),
        )
        self.ensure_user_dirs(uid)

        return build_ctx(
            uid, name, projects_enabled=self.get_projects_enabled(uid)
        ), self.issue_token(uid, ttl_seconds=self._ttl_for(remember))

    def login(self, username: str, password: str, *, remember: bool=True) -> tuple[UserCtx, str]:
        name = (username or "").strip()
        rows = self._query("SELECT uid, username, pass_hash, is_admin FROM users WHERE username = ?", (name,))
        # 客户端一律回"密码错误"（不暴露账号是否存在），但日志必须说清楚：
        # 连到另一个库（root 装、普通用户跑）时，表现就是"密码怎么都不对"
        matched = bool(rows) and verify_password(password, rows[0]["pass_hash"])
        if not rows:
            total = self._query("SELECT COUNT(*) AS n FROM users")[0]["n"]
            logger.warning(f"登录失败：账号不存在（{name!r}）；当前库 {self.db_path} 共 {total} 个账号")
        elif not matched:
            logger.warning(f"登录失败：密码不匹配（{name!r}）；当前库 {self.db_path}")
        if not matched:
            raise AuthError(msg.MSG_AUTH_PASSWORD_INVALID, code="auth.password_wrong")

        uid = rows[0]["uid"]
        # 老账号可能早于记忆/技能目录的引入，登录时补齐骨架（只补缺失文件，不动已有内容）
        self.ensure_user_dirs(uid)

        return build_ctx(
            uid, rows[0]["username"], bool(rows[0]["is_admin"]), self.get_exec_grant(uid),
            projects_enabled=self.get_projects_enabled(uid),
        ), self.issue_token(uid, ttl_seconds=self._ttl_for(remember))

    def issue_token(self, uid: str, expires_at: Optional[int] = None,
                    ttl_seconds: Optional[int] = None) -> str:
        """签发opaque token；expires_at为None表示永不过期（本地TUI默认）

        `ttl_seconds` 给"签发完就打算用完即弃"的调用方用（见 `remember=False`）。
        不写过期时间的 token 会作为**有效凭证**永远留在表里——签发一次就泄漏一行。
        """
        token = secrets.token_hex(TOKEN_BYTES)
        if expires_at is None and ttl_seconds is not None:
            expires_at = int(time.time()) + int(ttl_seconds)

        self._execute(
            "INSERT INTO auth_tokens (token, uid, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, uid, int(time.time()), expires_at),
        )

        return token

    @staticmethod
    def _ttl_for(remember: bool) -> Optional[int]:
        """记住登录态 → 长期 token（吊销由 logout/改密/注销负责）；
        不记住 → 带 TTL，靠启动清扫收尾。"""
        return None if remember else SESSION_TOKEN_TTL_SECONDS

    def purge_expired_tokens(self) -> int:
        """删除带过期时间且已到期的 token 行，返回删除条数。

        为什么必须有：`remember_login=false` 时 TUI 不写 login.json，那次签发的 token
        再没人吊销——只增不减。过期时间 + 启动清扫才是闭环。
        只动 `expires_at IS NOT NULL` 的行；手工签发的长期 token 不在这里清理（吊销归 logout）。
        """
        deleted = self._execute_returning_count(
            "DELETE FROM auth_tokens WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (int(time.time()),)
        )
        if deleted:
            logger.info(f"清理已到期的登录凭证：{deleted} 条")

        return deleted

    def resolve_token(self, token: Optional[str]) -> Optional[str]:
        if not token:
            return None

        rows = self._query("SELECT uid, expires_at FROM auth_tokens WHERE token = ?", (token,))
        if not rows:
            return None

        expires_at = rows[0]["expires_at"]
        if expires_at is not None and expires_at <= int(time.time()):
            self.revoke_token(token)
            return None

        return rows[0]["uid"]

    def revoke_token(self, token: str) -> None:
        self._execute("DELETE FROM auth_tokens WHERE token = ?", (token,))

    def revoke_all(self, uid: str) -> None:
        self._execute("DELETE FROM auth_tokens WHERE uid = ?", (uid,))

    def logout(self, token: Optional[str]) -> None:
        if token:
            self.revoke_token(token)

        self.clear_login_state()

    def verify_password_for(self, uid: str, password: str) -> None:
        """只校验密码不改数据：用于注销等流程在确认弹窗前先行拦截"""
        rows = self._query("SELECT pass_hash FROM users WHERE uid = ?", (uid,))
        if not rows or not verify_password(password, rows[0]["pass_hash"]):
            raise AuthError(msg.MSG_AUTH_PASSWORD_INVALID, code="auth.password_wrong")

    def change_password(self, uid: str, old_password: str, new_password: str, *, remember: bool=True) -> str:
        """改密后清空该用户全部token并为当前端签发新token，实现全端立即失效"""
        self.verify_password_for(uid, old_password)
        self._validate_password(new_password)
        self._execute("UPDATE users SET pass_hash = ? WHERE uid = ?", (hash_password(new_password), uid))
        self.revoke_all(uid)

        return self.issue_token(uid, ttl_seconds=self._ttl_for(remember))

    def reset_password_by_admin(self, uid: str, new_password: str) -> None:
        """管理员重置他人密码：不验旧密码，清掉目标账号全部 token。

        管理员不能替用户登录，重置后对方必须用新密码重新登录；返回的也不是新 token。
        """
        self._validate_password(new_password)
        self._execute(
            "UPDATE users SET pass_hash = ? WHERE uid = ?",
            (hash_password(new_password), uid),
        )
        self.revoke_all(uid)

    def delete_account(self, uid: str, password: str, purge_files: bool = False) -> None:
        """注销账号：清token、删用户行；purge_files为真时一并删除该用户目录（会话数据由调用方经session_service清理）"""
        self.verify_password_for(uid, password)
        self.revoke_all(uid)
        self._execute("DELETE FROM users WHERE uid = ?", (uid,))

        if purge_files:
            shutil.rmtree(user_root_path(uid), ignore_errors=True)

    def save_login_state(self, token: str) -> None:
        os.makedirs(os.path.dirname(self.login_state_path), exist_ok=True)
        with open(self.login_state_path, "w", encoding="utf-8") as f:
            json.dump({"token": token}, f)

    def load_login_state(self) -> Optional[str]:
        try:
            with open(self.login_state_path, "r", encoding="utf-8") as f:
                return (json.load(f) or {}).get("token")
        except (OSError, ValueError):
            return None

    def clear_login_state(self) -> None:
        try:
            os.remove(self.login_state_path)
        except OSError:
            pass


user_service = UserService()