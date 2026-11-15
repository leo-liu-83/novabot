# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : sensitive.py
# @Description : 项目内敏感文件判定（读取需确认、批量结果过滤）

"""项目内敏感路径。

口径见 spec §10.2：默认清单相对项目根匹配；v1 不支持项目自定义。
`read_file` 命中时确认；`ls`/`glob`/`grep` 直接过滤结果，不逐条弹卡。
"""

import os

from wcmatch import glob

SENSITIVE_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "**/*.pem",
    "**/*.key",
    "**/*.p12",
    "**/*.pfx",
    "**/id_rsa*",
    "**/id_ed25519*",
    "**/id_ecdsa*",
    "**/.ssh/**",
    "**/.aws/credentials",
    "**/.config/gcloud/**",
    "**/.git-credentials",
    "**/.netrc",
    "**/secrets.*",
    "**/credentials.*",
    "**/.git/config",
)

_FLAGS = glob.GLOBSTAR | glob.DOTMATCH


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.expanduser(str(path or ""))))


def _posix(path: str) -> str:
    return path.replace(os.sep, "/")


def _within(child: str, root: str) -> bool:
    return child == root or child.startswith(root.rstrip(os.sep) + os.sep)


def _matches(text: str, pattern: str) -> bool:
    return glob.globmatch(_posix(text), pattern, flags=_FLAGS)


def is_sensitive(path: str, project_root: str) -> bool:
    """该路径是否命中项目敏感清单。

    项目内按相对路径匹配；项目外按“任意层级”匹配（例如 `/etc/x/.env` 也算），
    避免批准一次外部目录后把里面的密钥当作普通文件直接展示。
    """
    target, root = _norm(path), _norm(project_root)
    if not target or not root:
        return False

    # 模板文件（.env.example 等）用于示例，不含真实凭据
    if os.path.basename(target).endswith(".example"):
        return False

    if _within(target, root):
        rel = os.path.relpath(target, root)
        return any(_matches(rel, pattern) for pattern in SENSITIVE_PATTERNS)

    absolute = _posix(target)
    for pattern in SENSITIVE_PATTERNS:
        if pattern.startswith("**/"):
            if _matches(absolute, pattern):
                return True
        elif _matches(absolute, f"**/{pattern}"):
            return True

    return False
