# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : project_venv.py
# @Description : 项目会话的 Python 虚拟环境：发现、按需创建、注入 PATH

"""项目里的 Python 环境。

写代码不需要虚拟环境，跑测试/启动才需要。这里把这段判断集中起来：

1. **发现**：项目根下的 `.venv` / `venv` / `env`（必须带 `pyvenv.cfg`，否则只是同名目录）；
2. **按需创建**：没有 venv、项目看起来是 Python 工程、且没有被 uv/poetry/pdm 托管时，
   用 NovaBot 所在解释器的**基础版本**建一个 `.venv`（`python -m venv`，不带任何第三方包）；
3. **注入**：把 venv 的 bin 前置进 `execute` 的 PATH 并设置 `VIRTUAL_ENV`，
   顺手剔除其它 venv 的 PATH 项，避免用户 shell 里激活的环境漏进来。

只影响 shell 命令（`execute`）；文件工具与写代码不受影响。
"""

import os
import re
import subprocess
import sys
import threading
from typing import Optional

from logger import logger

# 常见的虚拟环境目录名，顺序即优先级
VENV_DIR_NAMES = (".venv", "venv", "env")

# 判定"这是不是一个 Python 工程"的信号文件（只看项目根，避免遍历 site-packages）
PYTHON_SIGNALS = (
    "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "Pipfile", "tox.ini",
)

# 这些锁文件说明环境由外部工具托管（venv 可能在缓存目录而不是项目里）：
# 不替它建 .venv，交给 uv/poetry/pdm 自己的 runner
FOREIGN_LOCKFILES = ("uv.lock", "poetry.lock", "Pipfile.lock", "pdm.lock")

# 看起来"这条命令要用 Python"：裸 python/pytest/pip，或经 uv/poetry 等 runner
_PY_COMMAND = re.compile(
    r"(?:^|[\s;&|(`/])(?:"
    r"python[0-9.]*|py|pytest|py\.test|pip[0-9.]*|uv|poetry|pdm|pipenv|tox|nox|"
    r"ruff|black|isort|mypy|pyright|flake8|django-admin|flask|uvicorn|gunicorn"
    r")(?=$|[\s;&|)`])",
    re.IGNORECASE,
)

# "需要环境"的命令：真正要跑测试、跑脚本、装依赖。仅查询版本/执行一行代码不算，
# 否则空项目里敲一句 `python -V` 就会建出一个可能根本用不上的环境。
_ENV_COMMAND = re.compile(
    r"(?:^|[\s;&|(`])(?:"
    r"pytest|py\.test|tox|nox|coverage"
    r")(?=$|[\s;&|)`])|"
    r"(?:^|[\s;&|(`])(?:pip[0-9.]*|python[0-9.]*\s+-m\s+pip)\s+install(?=$|[\s;&|)`])|"
    r"(?:^|[\s;&|(`])python[0-9.]*\s+(?!-)(?:\S+\.py\b|-[mM]\s+\S+)|"
    r"(?:^|[\s;&|(`])(?:uv|poetry|pdm|pipenv)\s+run(?=$|[\s;&|)`])",
    re.IGNORECASE,
)

_CREATE_TIMEOUT = 180


def _runtime_env_roots() -> set:
    """NovaBot 自己所在环境的根目录：绝不能出现在项目命令的 PATH 里。

    - venv：`sys.prefix != sys.base_prefix`
    - conda：环境根下有 `conda-meta`
    """
    roots = set()
    prefix = os.path.realpath(sys.prefix)
    if sys.prefix != sys.base_prefix or os.path.isdir(os.path.join(prefix, "conda-meta")):
        roots.add(prefix)
    return roots


def _env_root_of(path_entry: str) -> Optional[str]:
    """`<env>/bin` → `<env>`；只对看起来像 Python 环境的目录返回。"""
    entry = os.path.realpath(path_entry)
    root = os.path.dirname(entry)
    if not root:
        return None
    if (os.path.isfile(os.path.join(root, "pyvenv.cfg"))
            or os.path.isdir(os.path.join(root, "conda-meta"))):
        return root
    return None
# 同一进程内并发 execute 时避免两个命令同时建同一个 .venv
_create_lock = threading.Lock()


def find_venv(project_root: str) -> Optional[str]:
    """项目根下已有的 venv 路径；没有返回 None。"""
    for name in VENV_DIR_NAMES:
        path = os.path.join(project_root, name)
        if os.path.isfile(os.path.join(path, "pyvenv.cfg")):
            return path
    return None


def looks_python(project_root: str) -> bool:
    """项目根有 Python 信号文件，或直接放着 .py 文件。"""
    if not project_root or not os.path.isdir(project_root):
        return False
    for name in PYTHON_SIGNALS:
        if os.path.isfile(os.path.join(project_root, name)):
            return True
    skip = {"node_modules", "__pycache__", *VENV_DIR_NAMES}
    try:
        entries = list(os.scandir(project_root))
    except OSError:
        return False
    if any(entry.is_file() and entry.name.endswith(".py") for entry in entries):
        return True
    # 再看一层常见源码目录（src/、app/、tests/ 或任何一级子目录），
    # 只有 src/foo.py 没有清单文件的小项目也能识别
    for entry in entries:
        if not entry.is_dir() or entry.name.startswith(".") or entry.name in skip:
            continue
        try:
            with os.scandir(entry.path) as children:
                if any(child.is_file() and child.name.endswith(".py") for child in children):
                    return True
        except OSError:
            continue
    return False


def managed_elsewhere(project_root: str) -> bool:
    """有 uv/poetry/pdm 的锁文件：环境由它们管，不自动建 .venv。"""
    return any(os.path.isfile(os.path.join(project_root, name))
               for name in FOREIGN_LOCKFILES)


def command_needs_python(command: str) -> bool:
    """命令会不会用到 Python（决定是否注入已有 venv 的 PATH）。"""
    return bool(_PY_COMMAND.search(str(command or "")))


_VENV_BOOTSTRAP = re.compile(r"python[0-9.]*\s+-m\s+venv\b", re.IGNORECASE)


def is_venv_bootstrap(command: str) -> bool:
    """`python -m venv …`：用户/agent 在显式创建环境，不应该被 PATH 清洗挡死。"""
    return bool(_VENV_BOOTSTRAP.search(str(command or "")))


def command_needs_env(command: str) -> bool:
    """命令是否真的需要项目环境（决定要不要为此新建 venv）。

    只认"跑测试/跑脚本/装依赖"这类动作；`python -V`、`python -c ...` 这种查询不算。
    """
    return bool(_ENV_COMMAND.search(str(command or "")))


def _listdir(root: str) -> list[str]:
    try:
        return os.listdir(root)
    except OSError:
        return []


_PIP_INSTALL = re.compile(
    r"(?:^|[\s;&|(`])pip[0-9.]*\s+install(?:\s|$)|"
    r"(?:^|[\s;&|(`])python[0-9.]*\s+-m\s+pip\s+install(?:\s|$)",
    re.IGNORECASE,
)


def installs_packages(command: str) -> bool:
    """命令是否会往"当前 PATH 上的 pip"装包（可能污染 NovaBot/系统环境）。"""
    return bool(_PIP_INSTALL.search(str(command or "")))


def venv_bin(venv: str) -> str:
    return os.path.join(venv, "Scripts" if os.name == "nt" else "bin")


def create_venv(project_root: str) -> Optional[str]:
    """在项目根建 `.venv`；失败返回 None（命令照常跑，只是没有环境隔离）。

    用 `sys.executable -m venv`：运行在 NovaBot 的 venv 里时会以基础解释器为准，
    新环境是干净的，不会继承 NovaBot 的依赖。
    """
    target = os.path.join(project_root, ".venv")
    with _create_lock:
        if os.path.isfile(os.path.join(target, "pyvenv.cfg")):
            return target
        try:
            result = subprocess.run(
                [sys.executable, "-m", "venv", target],
                capture_output=True, text=True, timeout=_CREATE_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning(f"创建项目虚拟环境失败（{project_root}）：{exc}")
            return None
        if result.returncode != 0:
            logger.warning(f"创建项目虚拟环境失败（{project_root}）：{result.stderr.strip()[:200]}")
            return None
    ok = os.path.isfile(os.path.join(target, "pyvenv.cfg"))
    if ok:
        logger.info(f"已为项目创建虚拟环境：{target}")
    return target if ok else None


def ensure_venv(project_root: str, *, command: str = "") -> tuple[Optional[str], bool]:
    """返回 (venv, 是否本次创建)。

    只有"命令看起来要用 Python + 项目像 Python 工程 + 没有托管锁文件"时才创建；
    其余情况只报告已有的 venv。
    """
    existing = find_venv(project_root)
    if existing:
        return existing, False
    # 只有"真的要用环境"的命令才建：空项目/查询命令都不建，
    # 等写了 Python 代码、真正跑测试或脚本时再建
    if not command_needs_env(command):
        return None, False
    if not looks_python(project_root) or managed_elsewhere(project_root):
        return None, False
    created = create_venv(project_root)
    return created, bool(created)


def clean_path(base: Optional[str]) -> str:
    """剔除所有 Python 环境（venv / conda / NovaBot 自己）的 PATH 项。

    否则在项目没有 venv 时，裸 `python` / `pip` 会命中 NovaBot 的环境，
    `pip install` 就会污染它；系统 pip 同理。
    """
    runtime = _runtime_env_roots()
    kept = []
    for item in str(base or "").split(os.pathsep):
        if not item:
            continue
        env_root = _env_root_of(item)
        if env_root is not None or os.path.realpath(item) in runtime:
            continue
        kept.append(item)
    return os.pathsep.join(kept) or os.defpath


def build_env(venv: Optional[str], *, base_path: Optional[str] = None) -> dict:
    """execute 用的环境：PATH（venv 在最前）+ VIRTUAL_ENV + HOME。

    不继承其它环境变量（NovaBot 的模型密钥不该出现在子进程里）。
    """
    env: dict = {}
    path = clean_path(base_path if base_path is not None else os.environ.get("PATH"))
    if venv:
        env["VIRTUAL_ENV"] = venv
        path = venv_bin(venv) + os.pathsep + path
    env["PATH"] = path
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    if home:
        env["HOME"] = home
        if os.name == "nt":
            env["USERPROFILE"] = home
    return env
