# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : secrets.py
# @Description : 主密钥与配置密钥的加解密（SM3-KDF 派生 + SM4-CBC + SM3 完整性）

"""API Key 的加密存储。

背景与取舍见 `docs/superpowers/specs/2026-11-17-密钥加密存储-design.md`。要点：

- 主密钥（用户给的 SM2 私钥或 SM4 密钥）存本机库的 `app_secrets` 表，启动自动读出；
- 数据密钥由主密钥经 SM3-KDF 确定性派生，换机器时「配置文件 + 主密钥」就能解开；
- 具体值用 SM4-CBC 加密，SM3 做完整性标签（gmssl 的 SM4 没有 GCM）；
- 密文格式 `enc:v1:sm4:<base64url(iv||ciphertext||tag)>`；
- 值不以该前缀开头就是明文，原样使用（兼容手改配置）；
- 有前缀却解不开时抛 `SecretError`，绝不把密文当 key 发出去。

挡不住本机同权限进程：主密钥与密文在同一台机器、同一个库里。它只防配置文件/数据库被
单独拷走、截图、误传。
"""

import base64
import hmac
import os
import secrets
import json
import sqlite3
import time
from typing import Iterable, Optional

from gmssl import sm3 as gm_sm3
from gmssl import sm4 as gm_sm4

PREFIX = "enc:v1:sm4:"
TABLE = "app_secrets"

# SM2 推荐曲线阶 n：自动生成私钥时用它做拒绝采样，保证打印出去的是一把可用的 SM2 私钥。
# 常量内置，避免 import gmssl.sm2（那个模块会拉 pycryptodomex 的 ASN.1）。
SM2_ORDER = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFF7203DF6B21C6052B53BBF40939D54123

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_secrets (
    name        TEXT PRIMARY KEY,
    algo        TEXT NOT NULL,
    material    TEXT NOT NULL,
    fingerprint TEXT NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL
);
"""


class SecretError(Exception):
    """密钥不可用：格式不对、主密钥缺失、密文损坏或与主密钥不匹配。"""


def _sm3_hex(data: bytes) -> str:
    return gm_sm3.sm3_hash(list(data))


def is_ciphertext(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def fingerprint(material: str) -> str:
    """SM3(material) 前 8 位。不用 material 自身前缀——那会把密钥字节印在界面与工单里。"""
    return _sm3_hex(bytes.fromhex(material))[:8]


def normalize_master_key(text: str) -> tuple[str, str]:
    """把用户输入归一成 (algo, 64/32 位十六进制)。

    判定顺序固定：前缀优先；无前缀按长度（64 位 → SM2，32 位 → SM4）；
    其余拒绝并给出两种正确写法。PEM/口令/base64 一律不做"聪明猜测"。
    """
    raw = "".join(str(text or "").split()).lower()
    if not raw:
        raise SecretError(
            "主密钥为空：请填 SM2 私钥（64 位十六进制）或 SM4 密钥（32 位十六进制）")

    algo = ""
    if raw.startswith("sm2:"):
        algo, raw = "sm2", raw[4:]
    elif raw.startswith("sm4:"):
        algo, raw = "sm4", raw[4:]

    if not raw or any(ch not in "0123456789abcdef" for ch in raw):
        raise SecretError(
            "主密钥格式不对：只接受 64 位十六进制（SM2 私钥）或 32 位十六进制（SM4 密钥）；"
            "PEM 请先导出成 64 位十六进制")
    if len(raw) == 64:
        algo = algo or "sm2"
        if algo != "sm2":
            raise SecretError("前缀写的是 sm4，但值是 64 位十六进制（应为 32 位）")
        return algo, raw
    if len(raw) == 32:
        algo = algo or "sm4"
        if algo != "sm4":
            raise SecretError("前缀写的是 sm2，但值是 32 位十六进制（应为 64 位）")
        return algo, raw

    raise SecretError(
        f"主密钥长度不对（{len(raw)} 位十六进制）：SM2 私钥是 64 位，SM4 密钥是 32 位")


def generate_master_key() -> str:
    """生成一把 SM2 私钥（64 位十六进制），落在 [1, n-2] 区间内。"""
    while True:
        candidate = int.from_bytes(secrets.token_bytes(32), "big")
        if 1 <= candidate <= SM2_ORDER - 2:
            return f"{candidate:064x}"


def _derive(material: str) -> tuple[bytes, bytes]:
    """SM3-KDF：加密与校验用两把独立派生的密钥，互不复用。"""
    seed = bytes.fromhex(material)
    k_enc = bytes.fromhex(_sm3_hex(b"novabot-dek-enc-v1" + seed))[:16]
    k_mac = bytes.fromhex(_sm3_hex(b"novabot-dek-mac-v1" + seed))
    return k_enc, k_mac


def _sm4_cbc(key: bytes, iv: bytes, data: bytes, *, encrypt: bool) -> bytes:
    mode = gm_sm4.SM4_ENCRYPT if encrypt else gm_sm4.SM4_DECRYPT
    cipher = gm_sm4.CryptSM4()
    cipher.set_key(key, mode)
    return bytes(cipher.crypt_cbc(iv, data))


def seal(plaintext: str, material: str) -> str:
    """用主密钥加密单个值；空串与已是密文的值原样返回（幂等）。"""
    if not isinstance(plaintext, str) or not plaintext:
        return plaintext or ""
    if is_ciphertext(plaintext):
        return plaintext

    k_enc, k_mac = _derive(material)
    iv = secrets.token_bytes(16)
    # gmssl 的 crypt_cbc 自己带 PKCS7 padding；完整性由下面的 SM3 tag 保证
    ciphertext = _sm4_cbc(k_enc, iv, plaintext.encode("utf-8"), encrypt=True)
    tag = bytes.fromhex(_sm3_hex(k_mac + iv + ciphertext))
    blob = base64.urlsafe_b64encode(iv + ciphertext + tag).rstrip(b"=")
    return PREFIX + blob.decode("ascii")


def unseal(value: str, material: str) -> str:
    """解开一个密文值；格式/完整性/密码任一环节失败都抛 SecretError，不返回部分结果。"""
    if not is_ciphertext(value):
        raise SecretError("不是密文")

    payload = value[len(PREFIX):]
    try:
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    except Exception as exc:  # noqa: BLE001 - base64 的异常类型不统一
        raise SecretError("密文不是合法的 base64url") from exc

    if len(raw) < 16 + 16 + 32:
        raise SecretError("密文长度不足（可能被截断）")

    iv, ciphertext, tag = raw[:16], raw[16:-32], raw[-32:]
    k_enc, k_mac = _derive(material)
    if not hmac.compare_digest(bytes.fromhex(_sm3_hex(k_mac + iv + ciphertext)), tag):
        raise SecretError("密文完整性校验失败：可能被改动，或主密钥与配置不匹配")

    try:
        return _sm4_cbc(k_enc, iv, ciphertext, encrypt=False).decode("utf-8")
    except SecretError:
        raise
    except Exception as exc:  # noqa: BLE001 - 解密/解码失败统一归为"解不开"
        raise SecretError(f"密文无法解密：{exc}") from exc


class SecretStore:
    """主密钥的读写与对外的 reveal / encrypt 门面。

    库路径按需打开（每次操作一条短连接）：配置在 import 期就会调用它，
    那时数据库可能还不存在；长连接还会把线程与句柄绑死在模块单例上。
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path
        self._cache: Optional[tuple[str, str]] = None

    # ── 库与主密钥 ──────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
        if not self.db_path:
            raise SecretError("未配置数据库路径，无法读写主密钥")
        directory = os.path.dirname(self.db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        return conn

    def master(self) -> Optional[tuple[str, str]]:
        """返回 (algo, material)；库不存在或没有主密钥时返回 None（不算错误）。"""
        if self._cache is not None:
            return self._cache
        if not self.db_path or not os.path.exists(self.db_path):
            return None
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT algo, material FROM app_secrets WHERE name = 'master'").fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        self._cache = (row["algo"], row["material"])
        return self._cache

    def has_master(self) -> bool:
        return self.master() is not None

    def save_master(self, algo: str, material: str, *, replace: bool = False) -> bool:
        """写入主密钥；已有且不是 replace 时返回 False（不覆盖）。"""
        conn = self._connect()
        try:
            if not replace:
                exists = conn.execute(
                    "SELECT 1 FROM app_secrets WHERE name = 'master'").fetchone()
                if exists:
                    return False
            conn.execute(
                "INSERT OR REPLACE INTO app_secrets (name, algo, material, fingerprint, created_at)"
                " VALUES ('master', ?, ?, ?, ?)",
                (algo, material, fingerprint(material), int(time.time())),
            )
            conn.commit()
        finally:
            conn.close()
        self._cache = (algo, material)
        return True

    # ── 对外的值处理 ────────────────────────────────
    def use_master(self, algo: str, material: str) -> None:
        """让 store 先按给定主密钥工作，但不写库。

        首次 init 与 --rotate 都要"先写文件、最后落库"：中途写文件失败时库里还是旧密钥，
        用户的密文不会变成解不开的状态。
        """
        self._cache = (algo, material)

    def reveal(self, value: Optional[str]) -> Optional[str]:
        """明文原样返回；密文解开；解不开抛 SecretError。"""
        if not is_ciphertext(value):
            return value
        master = self.master()
        if master is None:
            raise SecretError("配置里是密文，但本机没有主密钥：请补回原主密钥后重启")
        return unseal(str(value), master[1])

    def encrypt_if_plain(self, value: Optional[str]) -> Optional[str]:
        """明文 → 密文；空值、已是密文的原样返回；没有主密钥时保持明文。"""
        if not value or is_ciphertext(value):
            return value
        master = self.master()
        if master is None:
            return value
        return seal(str(value), master[1])

    def probe(self, value: Optional[str]) -> str:
        """`plain` / `cipher` / `broken`，供 status 与系统信息页统计。"""
        if not is_ciphertext(value):
            return "plain" if value else "empty"
        try:
            self.reveal(value)
            return "cipher"
        except SecretError:
            return "broken"

    def status(self, values: Iterable[Optional[str]] = ()) -> dict:
        master = self.master()
        plaintext = broken = cipher = 0
        for value in values:
            kind = self.probe(value) if master else ("broken" if is_ciphertext(value) else
                                                     ("plain" if value else "empty"))
            if kind == "plain":
                plaintext += 1
            elif kind == "cipher":
                cipher += 1
            elif kind == "broken":
                broken += 1
        return {
            "enabled": master is not None,
            "algo": master[0] if master else "",
            "fingerprint": fingerprint(master[1]) if master else "",
            "plaintext": plaintext,
            "ciphertext": cipher,
            "undecryptable": broken,
        }


# ── 配置/库里的 key 收集（CLI 与系统信息页共用）─────────
CONFIG_KEY_FIELDS = ("key", "api_key")


def _secret_slots(node, path=""):
    """深度优先产出 (容器, 键, 路径)；只认 str 值且以 PREFIX 开头。"""
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if isinstance(value, str):
                if is_ciphertext(value):
                    yield node, key, child
            else:
                yield from _secret_slots(value, child)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            child = f"{path}[{index}]"
            if isinstance(value, str):
                if is_ciphertext(value):
                    yield node, index, child
            else:
                yield from _secret_slots(value, child)


def ciphertext_slots(document: object) -> list[tuple[object, object, str]]:
    """文档里每个密文值的位置：容器可以是 dict 或 list，路径只用于报错。"""
    return list(_secret_slots(document))


def ciphertext_values(document: object) -> list[str]:
    return [container[key] for container, key, _path in ciphertext_slots(document)]


def reveal_tree(document: object, store: SecretStore) -> list[str]:
    """原地解开所有密文，返回解不开的路径。不改动明文与其它类型。"""
    broken = []
    for container, key, path in ciphertext_slots(document):
        try:
            container[key] = store.reveal(container[key])
        except SecretError:
            broken.append(path)
    return broken


def config_key_holders(document: dict) -> list[tuple[str, dict]]:
    """配置文件里所有带 key 的槽位：(标签, 所属 dict)。

    覆盖 `models.<provider>.key/api_key` 与 `web.search.providers[].key/api_key`；
    与 `ConfigService._json_to_dto` 读取的两处一一对应。
    """
    holders: list[tuple[str, dict]] = []

    models = document.get("models")
    if isinstance(models, dict):
        for code, provider in models.items():
            if isinstance(provider, dict):
                holders.append((f"models.{code}", provider))

    web = document.get("web")
    search = web.get("search") if isinstance(web, dict) else None
    providers = search.get("providers") if isinstance(search, dict) else None
    if isinstance(providers, list):
        for index, item in enumerate(providers):
            if isinstance(item, dict):
                holders.append((f"web.search.providers[{item.get('provider') or index}]", item))

    return holders


def config_values(document: dict) -> list[str]:
    """status / 轮换用：明文 key 字段 + 任意位置的密文。

    明文只认 key/api_key，避免把 base_url、模型名算进「还剩几条明文」；
    密文整树收，header 里手贴的密文也要计入。key 字段的密文只出现一次。
    """
    values = []
    for _label, holder in config_key_holders(document):
        for field in CONFIG_KEY_FIELDS:
            value = holder.get(field)
            if isinstance(value, str) and value and not is_ciphertext(value):
                values.append(value)
    values.extend(ciphertext_values(document))
    return values


def config_file_values(path: str) -> list[str]:
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8") as file:
            document = json.load(file)
    except (OSError, ValueError):
        return []
    return config_values(document if isinstance(document, dict) else {})


def db_values(db_path: str) -> list[str]:
    """库里自备搜索 provider 的 key（明文或密文都原样返回）。"""
    if not db_path:
        return []
    import os

    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
        try:
            rows = conn.execute("SELECT api_key FROM user_web_providers").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    return [row[0] for row in rows if row[0]]


def default_store(db_path: Optional[str] = None) -> SecretStore:
    """按默认数据目录建一个 store；调用方一般传自己的库路径。"""
    if db_path:
        return SecretStore(db_path)
    from util.paths import db_path_from

    return SecretStore(db_path_from())
