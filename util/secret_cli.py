# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : secret_cli.py
# @Description : 主密钥与配置密钥的命令行工具（init / encrypt-config / encrypt-db / status / --rotate）

"""`python -m util.secret_cli`。

设计见 `docs/superpowers/specs/2026-11-17-密钥加密存储-design.md` §7、§10.3。
子命令：

| 子命令 | 作用 |
|---|---|
| `init` | 建立主密钥并把配置文件/数据库里的明文 key 加密（幂等） |
| `init --rotate` | 旧主密钥解出全部值 → 生成新密钥 → 重新加密写回 |
| `encrypt-config` | 只加密配置文件（含同目录的 `.bak`） |
| `encrypt-db` | 只加密数据库里自备搜索 provider 的 key |
| `status` | 打印算法、指纹、明文/密文/解不开的条数 |

主密钥从环境变量 `NOVABOT_MASTER_KEY` 读，不走命令行参数（避免 `ps` 里看见）。
"""

import argparse
import json
import os
import sqlite3
import stat
import sys
import tempfile
from typing import Optional

from util import secrets as sec
from util.paths import db_path_from

ENV_KEY = "NOVABOT_MASTER_KEY"
BACKUP_SUFFIX = ".bak"
CONFIG_KEYS = ("key", "api_key")


# ── 配置文件读写 ────────────────────────────────────────
def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)
    return data if isinstance(data, dict) else {}


def save_config(path: str, data: dict) -> None:
    """原子写 + 收紧权限：中途断电不会留下半截文件，别人也读不到明文。"""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".novabot-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    restrict_permissions(path)


def restrict_permissions(path: str) -> None:
    """POSIX 下把文件收到 0600；Windows 靠 NTFS 默认 ACL，不做额外动作。"""
    if os.name != "posix":
        return
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _config_entries(data: dict) -> list[tuple[str, dict]]:
    return sec.config_key_holders(data)


def config_values(data: dict) -> list[str]:
    return sec.config_values(data)


def encrypt_config_file(store: sec.SecretStore, path: str) -> tuple[int, list[str]]:
    """加密一个配置文件里的明文 key，返回 (加密条数, 解不开的标签)。"""
    if not os.path.exists(path):
        return 0, []

    data = load_config(path)
    changed = 0
    broken: list[str] = []
    for label, holder in _config_entries(data):
        for field in CONFIG_KEYS:
            value = holder.get(field)
            if not isinstance(value, str) or not value:
                continue
            if sec.is_ciphertext(value):
                try:
                    store.reveal(value)
                except sec.SecretError:
                    broken.append(f"{label}.{field}")
                continue
            holder[field] = store.encrypt_if_plain(value)
            if sec.is_ciphertext(holder[field]):
                changed += 1

    if changed:
        save_config(path, data)
    else:
        restrict_permissions(path)
    return changed, broken


def backup_path(path: str) -> str:
    return path + BACKUP_SUFFIX


def encrypt_both(store: sec.SecretStore, path: str) -> tuple[int, list[str]]:
    """配置文件与同目录的 `.bak` 一起加密：备份里的明文等于没有加密。"""
    total, broken = encrypt_config_file(store, path)
    backup = backup_path(path)
    if os.path.exists(backup):
        extra, broken_backup = encrypt_config_file(store, backup)
        total += extra
        broken += broken_backup
    return total, broken


# ── 数据库 ─────────────────────────────────────────────
def _db_connection(db_path: str) -> Optional[sqlite3.Connection]:
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def db_values(db_path: str) -> list[str]:
    return sec.db_values(db_path)


def encrypt_db(store: sec.SecretStore, db_path: str) -> tuple[int, list[str]]:
    """把数据库里明文的自备搜索 key 加密；解不开的报出来但不改。"""
    conn = _db_connection(db_path)
    if conn is None:
        return 0, []
    changed = 0
    broken: list[str] = []
    try:
        try:
            rows = conn.execute(
                "SELECT uid, provider, api_key FROM user_web_providers").fetchall()
        except sqlite3.Error:
            # 库还没建表（全新安装）：没有可加密的行
            return 0, []
        for row in rows:
            value = row["api_key"]
            if not value:
                continue
            if sec.is_ciphertext(value):
                try:
                    store.reveal(value)
                except sec.SecretError:
                    broken.append(f"{row['provider']}（{row['uid']}）")
                continue
            sealed = store.encrypt_if_plain(value)
            if sec.is_ciphertext(sealed):
                conn.execute(
                    "UPDATE user_web_providers SET api_key = ? WHERE uid = ? AND provider = ?",
                    (sealed, row["uid"], row["provider"]),
                )
                changed += 1
        conn.commit()
    finally:
        conn.close()
    return changed, broken


# ── 统计与输出 ─────────────────────────────────────────
def collect_values(config_path: str, db_path: str) -> list[str]:
    values = sec.config_file_values(config_path)
    values += sec.config_file_values(backup_path(config_path))
    values += sec.db_values(db_path)
    return values


def print_master(material: str) -> None:
    print("已生成主密钥（SM2 私钥），请立即保存到密码管理器：")
    print(f"  {material}")
    print("丢失后配置里的密文将无法解开，只能重新填写各 provider 的 key。")


def _print_result(action: str, changed: int, broken: list[str]) -> None:
    print(f"{action}：本次加密 {changed} 条明文 key")
    if broken:
        print("以下条目解不开（主密钥与配置不匹配），需要重新填写：")
        for label in broken:
            print(f"  - {label}")


# ── 子命令 ─────────────────────────────────────────────
def cmd_init(args) -> int:
    config_path = os.path.abspath(os.path.expanduser(args.config))
    db_path = db_path_from(os.path.abspath(os.path.expanduser(args.data_dir)))
    store = sec.SecretStore(db_path)

    existing = store.master()
    provided_text = os.environ.get(ENV_KEY, "").strip()
    try:
        provided = sec.normalize_master_key(provided_text) if provided_text else None
    except sec.SecretError as exc:
        print(f"主密钥格式不对：{exc}", file=sys.stderr)
        return 1
    has_ciphertext = any(sec.is_ciphertext(v) for v in collect_values(config_path, db_path))

    if args.rotate:
        return _rotate(store, config_path, db_path, provided,
                       skip_broken=getattr(args, "skip_broken", False))

    if existing is not None:
        if provided is not None and provided[1] != existing[1]:
            print("库里已有一把不同的主密钥。要更换请用：python -m util.secret_cli init --rotate",
                  file=sys.stderr)
            return 1
        if provided is not None:
            print("已存在同一把主密钥，未修改")
        action = "使用库中主密钥"
        master = existing
    else:
        if provided is None:
            if has_ciphertext and not args.force:
                print("配置里已有密文，但库里没有主密钥：自动生成只会让旧密文永久解不开。\n"
                      "请用原主密钥运行（NOVABOT_MASTER_KEY=...），或加 --force 承认丢失并重填。\n"
                      "如果那条只是恰好以 enc:v1:sm4: 开头的普通文案，删掉它即可。",
                      file=sys.stderr)
                return 1
            provided = ("sm2", sec.generate_master_key())
        elif has_ciphertext and not args.force:
            # 用提供的密钥先试开一遍：不匹配就退出，别把它落库（落库后旧密文永远解不开）
            for value in collect_values(config_path, db_path):
                if not sec.is_ciphertext(value):
                    continue
                try:
                    sec.unseal(value, provided[1])
                except sec.SecretError as exc:
                    print(f"密钥与密文不匹配（{exc}）。没有写入任何文件。", file=sys.stderr)
                    return 1
        action = "已保存主密钥"
        master = provided
        # 先按新主密钥加密文件，最后再落库：写文件失败时库里还没有新密钥，旧密文仍可解
        store.use_master(provided[0], provided[1])

    changed, broken = encrypt_both(store, config_path)
    db_changed, db_broken = encrypt_db(store, db_path)
    if existing is None:
        store.save_master(master[0], master[1])
        if provided_text == "":
            print_master(master[1])
    print(f"{action}（{master[0]}，指纹 {sec.fingerprint(master[1])}）")
    _print_result("加密完成", changed + db_changed, broken + db_broken)
    return 0


def _rotate(store: sec.SecretStore, config_path: str, db_path: str,
            provided: Optional[tuple[str, str]], *, skip_broken: bool = False) -> int:
    """旧密钥解出全部值，再用新密钥整体重写；任何一条解不开就整体中止。

    文件先写、主密钥最后落库：中途写盘失败时库里还是旧密钥，不会留下一堆解不开的密文。
    """
    existing = store.master()
    if existing is None:
        print("库里没有主密钥，无法轮换；请先运行 init", file=sys.stderr)
        return 1

    # 第一步：内存里全部解密（不落盘），默认任何一条失败就退出；
    # --skip-broken 用于恰好以 enc:v1:sm4: 开头的普通文案：跳过并保持原样
    skipped: list[str] = []

    def on_broken(label: str, exc: Exception) -> bool:
        if not skip_broken:
            print(f"轮换中止：{label} 解不开（{exc}）。没有写入任何文件。", file=sys.stderr)
            return False
        print(f"轮换跳过：{label} 解不开（{exc}），保持原样。", file=sys.stderr)
        skipped.append(label)
        return True

    data = load_config(config_path) if os.path.exists(config_path) else {}
    backup = backup_path(config_path)
    backup_data = load_config(backup) if os.path.exists(backup) else None
    conn = _db_connection(db_path)
    rows = []
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT uid, provider, api_key FROM user_web_providers").fetchall()
        except sqlite3.Error:
            rows = []

    holders = [h for _l, h in _config_entries(data)]
    holders += [h for _l, h in _config_entries(backup_data or {})]
    for holder in holders:
        for field in CONFIG_KEYS:
            value = holder.get(field)
            if isinstance(value, str) and sec.is_ciphertext(value):
                try:
                    holder[field] = store.reveal(value)
                except sec.SecretError as exc:
                    if not on_broken(f"{field}", exc):
                        return 1
    slots = sec.ciphertext_slots(data)
    if backup_data is not None:
        slots += sec.ciphertext_slots(backup_data)
    for container, key, path in slots:
        try:
            container[key] = store.reveal(container[key])
        except sec.SecretError as exc:
            if not on_broken(path, exc):
                return 1
    db_plain = []
    for row in rows:
        value = row["api_key"]
        if not value:
            continue
        if sec.is_ciphertext(value):
            try:
                value = store.reveal(value)
            except sec.SecretError as exc:
                if on_broken(f"{row['provider']} 的 key", exc):
                    continue
                return 1
        db_plain.append((row["uid"], row["provider"], value))

    # 第二步：用新主密钥整体重写；落库放到最后，写文件失败时旧密钥仍能解开现有密文
    new_master = provided or ("sm2", sec.generate_master_key())
    new_store = sec.SecretStore(db_path)
    new_store.use_master(new_master[0], new_master[1])

    def reseal(container: dict) -> None:
        for holder in [h for _l, h in _config_entries(container)]:
            for field in CONFIG_KEYS:
                value = holder.get(field)
                if isinstance(value, str) and value:
                    holder[field] = new_store.encrypt_if_plain(value)

    reseal(data)
    for container, key, _path in slots:
        container[key] = new_store.encrypt_if_plain(container[key])
    if config_path and data:
        save_config(config_path, data)
    if backup_data is not None:
        reseal(backup_data)
        save_config(backup, backup_data)

    db_changed = 0
    if conn is not None:
        try:
            for uid, provider, value in db_plain:
                conn.execute(
                    "UPDATE user_web_providers SET api_key = ? WHERE uid = ? AND provider = ?",
                    (new_store.encrypt_if_plain(value), uid, provider),
                )
                db_changed += 1
            conn.commit()
        finally:
            conn.close()

    new_store.save_master(new_master[0], new_master[1], replace=True)
    if provided is None:
        print_master(new_master[1])
    print(f"主密钥已轮换（{new_master[0]}，指纹 {sec.fingerprint(new_master[1])}）")
    _print_result("重新加密完成", len(holders) + db_changed, [])
    if skipped:
        print(f"有 {len(skipped)} 处带密文前缀但解不开的值保持原样：", file=sys.stderr)
        for label in skipped:
            print(f"  - {label}", file=sys.stderr)
    return 0


def _load_store(args) -> Optional[sec.SecretStore]:
    db_path = db_path_from(os.path.abspath(os.path.expanduser(args.data_dir)))
    store = sec.SecretStore(db_path)
    if not store.has_master():
        print("没有主密钥：请先运行 python -m util.secret_cli init", file=sys.stderr)
        return None
    return store


def cmd_encrypt_config(args) -> int:
    store = _load_store(args)
    if store is None:
        return 1
    changed, broken = encrypt_both(store, os.path.abspath(os.path.expanduser(args.config)))
    _print_result("配置文件加密完成", changed, broken)
    return 0


def cmd_encrypt_db(args) -> int:
    store = _load_store(args)
    if store is None:
        return 1
    db_path = db_path_from(os.path.abspath(os.path.expanduser(args.data_dir)))
    changed, broken = encrypt_db(store, db_path)
    _print_result("数据库加密完成", changed, broken)
    return 0


def cmd_status(args) -> int:
    config_path = os.path.abspath(os.path.expanduser(args.config))
    db_path = db_path_from(os.path.abspath(os.path.expanduser(args.data_dir)))
    store = sec.SecretStore(db_path)
    status = store.status(collect_values(config_path, db_path))

    if not status["enabled"]:
        print("密钥加密：未启用（本机没有主密钥）")
    else:
        print(f"密钥加密：已启用（算法 {status['algo']}，指纹 {status['fingerprint']}）")
    print(f"明文 key：{status['plaintext']} 条")
    print(f"密文 key：{status['ciphertext']} 条")
    if status["undecryptable"]:
        print(f"解不开的 key：{status['undecryptable']} 条（主密钥缺失或不匹配，需要重新填写）")
    else:
        print("解不开的 key：0 条")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m util.secret_cli",
        description="NovaBot 配置密钥加密工具（主密钥与 API key）")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(target: argparse.ArgumentParser) -> None:
        target.add_argument("--config", default=os.path.join("~", ".novabot", "novabot.json"),
                            help="novabot.json 路径")
        target.add_argument("--data-dir", default=os.path.join("~", ".novabot", "data"),
                            help="数据目录（存放 novabot.db）")

    init = sub.add_parser("init", help="建立主密钥并加密明文 key")
    common(init)
    init.add_argument("--force", action="store_true",
                      help="有密文但无主密钥时仍生成新密钥（旧密文将无法解开）")
    init.add_argument("--rotate", action="store_true", help="用新主密钥重加密全部值")
    init.add_argument("--skip-broken", action="store_true",
                      help="轮换时跳过解不开的值（恰好带密文前缀的普通文案），保持原样")
    init.set_defaults(func=cmd_init)

    encrypt_config = sub.add_parser("encrypt-config", help="只加密配置文件（含 .bak）")
    common(encrypt_config)
    encrypt_config.set_defaults(func=cmd_encrypt_config)

    encrypt_db = sub.add_parser("encrypt-db", help="只加密数据库里的搜索 provider key")
    common(encrypt_db)
    encrypt_db.set_defaults(func=cmd_encrypt_db)

    status = sub.add_parser("status", help="查看加密状态")
    common(status)
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
