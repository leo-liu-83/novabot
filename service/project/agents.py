# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : agents.py
# @Description : 根据项目描述与根目录构建文件生成 AGENTS.md 草稿

"""项目根 AGENTS.md 草稿。

只做两件事：从项目描述提取“这个项目是什么”，从根目录可识别的构建文件
（package.json / pyproject.toml / Makefile 等）提取常用命令。没有证据的命令
宁可留空，也不要猜；草稿必须由用户预览确认后才写入，且绝不覆盖已有文件。
"""

import asyncio
import json
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Optional

from config import config
from service.project.errors import ProjectError
from service.project.paths import project_dir_state

AGENTS_FILENAME = "AGENTS.md"
# 描述太短时生成出来只有标题和占位符，价值不大；前端用同一个阈值决定是否弹预览
DESCRIPTION_MIN = 6
AGENTS_MAX_BYTES = 128 * 1024
DRAFT_MARK = "<!-- 由 NovaBot 根据项目描述生成，请核对命令与约束后再提交 -->"


@dataclass(frozen=True)
class AgentsDraft:
    eligible: bool
    reason: Optional[str]
    content: str
    sources: tuple[str, ...] = ()


def _read_text(path: str, limit: int = 256 * 1024) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as file:
            return file.read(limit)
    except OSError:
        return ""


def _js_commands(root: str, entries: set[str], commands: dict[str, str],
                 sources: list[str]) -> None:
    if "package.json" not in entries:
        return
    try:
        data = json.loads(_read_text(os.path.join(root, "package.json")) or "{}")
    except ValueError:
        return
    scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}

    if "pnpm-lock.yaml" in entries:
        pm, install = "pnpm", "pnpm install"
    elif "yarn.lock" in entries:
        pm, install = "yarn", "yarn install"
    elif "bun.lockb" in entries or "bun.lock" in entries:
        pm, install = "bun", "bun install"
    else:
        pm, install = "npm", "npm install"

    commands.setdefault("install", install)
    if scripts.get("test"):
        commands.setdefault("test", f"{pm} run test" if pm == "bun" else f"{pm} test")
    if scripts.get("lint"):
        commands.setdefault("lint", f"{pm} run lint")
    for name in ("dev", "start"):
        if scripts.get(name):
            commands.setdefault("run", f"{pm} run {name}")
            break

    sources.append("package.json")
    for lock in ("pnpm-lock.yaml", "yarn.lock", "bun.lockb", "bun.lock", "package-lock.json"):
        if lock in entries:
            sources.append(lock)
            break


def _python_commands(root: str, entries: set[str], commands: dict[str, str],
                     sources: list[str]) -> None:
    if "requirements.txt" in entries:
        commands.setdefault("install", "pip install -r requirements.txt")
        sources.append("requirements.txt")

    if "pyproject.toml" in entries:
        text = _read_text(os.path.join(root, "pyproject.toml"))
        if "[project]" in text or "[tool.poetry]" in text:
            commands.setdefault("install", "pip install -e .")
        if "pytest" in text:
            commands.setdefault("test", "pytest")
        sources.append("pyproject.toml")

    if ({"pytest.ini", "tox.ini", "setup.cfg"} & entries
            or os.path.isdir(os.path.join(root, "tests"))):
        commands.setdefault("test", "pytest")
        for name in ("pytest.ini", "tox.ini", "setup.cfg"):
            if name in entries:
                sources.append(name)
                break

    if "main.py" in entries:
        commands.setdefault("run", "python main.py")
        sources.append("main.py")


def _make_commands(root: str, entries: set[str], commands: dict[str, str],
                   sources: list[str]) -> None:
    if "Makefile" not in entries:
        return
    text = _read_text(os.path.join(root, "Makefile"))
    targets = set(re.findall(r"^([A-Za-z0-9_.-]+)\s*:", text, flags=re.MULTILINE))
    picks = (
        ("install", ("install", "setup")),
        ("test", ("test",)),
        ("run", ("dev", "run", "serve", "start")),
        ("lint", ("lint",)),
    )
    used = False
    for key, names in picks:
        for name in names:
            if name in targets:
                commands.setdefault(key, f"make {name}")
                used = True
                break
    if used:
        sources.append("Makefile")


def _scan_commands(root: str) -> tuple[dict[str, str], tuple[str, ...]]:
    """只扫项目根，不递归；识别不了的构建系统直接跳过。"""
    try:
        entries = set(os.listdir(root))
    except OSError:
        return {}, ()

    commands: dict[str, str] = {}
    sources: list[str] = []

    _js_commands(root, entries, commands, sources)
    _python_commands(root, entries, commands, sources)
    _make_commands(root, entries, commands, sources)

    if "go.mod" in entries:
        commands.setdefault("test", "go test ./...")
        commands.setdefault("run", "go run .")
        sources.append("go.mod")
    if "Cargo.toml" in entries:
        commands.setdefault("test", "cargo test")
        commands.setdefault("run", "cargo run")
        sources.append("Cargo.toml")

    return commands, tuple(dict.fromkeys(sources))


def _command_line(label: str, value: Optional[str]) -> str:
    return f"- {label}：" + (value or "")


def _render(description: str, commands: dict[str, str], sources: tuple[str, ...]) -> str:
    lines = [
        DRAFT_MARK,
        "# 项目约定",
        "",
        "## 这个项目是什么",
        description,
        "",
        "## 常用命令",
        _command_line("安装依赖", commands.get("install")),
        _command_line("运行测试", commands.get("test")),
        _command_line("本地启动", commands.get("run")),
    ]
    if commands.get("lint"):
        lines.append(_command_line("代码检查", commands["lint"]))
    lines += [
        "",
        "## 约束",
        "- 修改前先读现有实现与测试。",
        "- 不要提交密钥、构建产物与本地数据。",
    ]
    if sources:
        lines += ["", "> 命令来自：" + "、".join(sources) + "；请核对后再提交。"]
    else:
        lines += ["", "> 未在项目根识别到构建文件，命令需要手工补充。"]
    return "\n".join(lines).rstrip() + "\n"


def build_agents_draft(project: dict) -> AgentsDraft:
    """生成草稿但不落盘；已有 AGENTS.md 或描述太短时明确返回原因。"""
    root = str(project.get("path") or "")
    target = os.path.join(root, AGENTS_FILENAME)
    if os.path.exists(target):
        return AgentsDraft(False, "agents_exists", "")
    description = str(project.get("description") or "").strip()
    if len(description) < DESCRIPTION_MIN:
        return AgentsDraft(False, "description_short", "")

    commands, sources = _scan_commands(root)
    return AgentsDraft(True, None, _render(description, commands, sources), sources)


def build_agents_from_scan(project: dict) -> str:
    """TUI 创建项目时用：不依赖描述，只按目录现状生成一版项目约定。"""
    root = str(project.get("path") or "")
    name = str(project.get("name") or "").strip() or os.path.basename(root.rstrip(os.sep))
    description = str(project.get("description") or "").strip()
    if not description:
        description = f"{name} 项目，目录：{root}"
    commands, sources = _scan_commands(root)
    return _render(description, commands, sources)


def _target(project: dict) -> str:
    return os.path.join(str(project.get("path") or ""), AGENTS_FILENAME)


def _validated_content(content: str) -> bytes:
    if not isinstance(content, str) or not content.strip():
        raise ProjectError("project.agents_invalid", max=AGENTS_MAX_BYTES // 1024)
    data = (content if content.endswith("\n") else content + "\n").encode("utf-8")
    if len(data) > AGENTS_MAX_BYTES:
        raise ProjectError("project.agents_invalid", max=AGENTS_MAX_BYTES // 1024)
    return data


def read_agents_file(project: dict) -> dict:
    """读取项目根 AGENTS.md；文件不存在时返回 exists=False，供前端切换创建/编辑。"""
    root = str(project.get("path") or "")
    target = _target(project)
    if not os.path.exists(target):
        return {"exists": False, "content": "", "path": target, "bytes": 0}
    try:
        with open(target, "rb") as file:
            data = file.read(AGENTS_MAX_BYTES + 1)
    except OSError as exc:
        raise ProjectError("project.agents_read_failed") from exc
    if len(data) > AGENTS_MAX_BYTES:
        raise ProjectError("project.agents_too_large", max=AGENTS_MAX_BYTES // 1024)
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProjectError("project.agents_read_failed") from exc
    return {"exists": True, "content": content, "path": target, "bytes": len(data)}


def write_agents_draft(project: dict, content: str, *, overwrite: bool = False) -> dict:
    """写入项目根 AGENTS.md。

    默认 O_EXCL：只在文件不存在时创建，绝不覆盖。编辑/删除走显式 overwrite=True，
    用临时文件 + os.replace，避免写一半留下残缺文件。
    """
    data = _validated_content(content)
    root = str(project.get("path") or "")
    if project_dir_state(root) != "ok":
        raise ProjectError("project.path_unavailable", path=root)
    target = _target(project)

    if overwrite:
        tmp_path = ""
        try:
            fd, tmp_path = tempfile.mkstemp(
                prefix=".AGENTS.", suffix=".tmp", dir=root)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(data.decode("utf-8"))
            os.replace(tmp_path, target)
        except OSError as exc:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            raise ProjectError("project.agents_write_failed") from exc
        return {"path": target, "bytes": len(data)}

    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError as exc:
        raise ProjectError("project.agents_exists") from exc
    except OSError as exc:
        raise ProjectError("project.agents_write_failed") from exc

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(data.decode("utf-8"))
    except OSError as exc:
        # 写一半失败时不要留下空/残缺文件
        try:
            os.unlink(target)
        except OSError:
            pass
        raise ProjectError("project.agents_write_failed") from exc

    return {"path": target, "bytes": len(data)}


def delete_agents_file(project: dict) -> dict:
    """删除项目根 AGENTS.md；文件本来不存在时按成功处理（幂等）。"""
    root = str(project.get("path") or "")
    if project_dir_state(root) != "ok":
        raise ProjectError("project.path_unavailable", path=root)
    target = _target(project)
    try:
        os.remove(target)
        return {"deleted": True, "path": target}
    except FileNotFoundError:
        return {"deleted": False, "path": target}
    except OSError as exc:
        raise ProjectError("project.agents_delete_failed") from exc


_AI_SYSTEM_PROMPT = """你是资深软件工程师，正在为项目撰写 AGENTS.md —— 给 AI 编码代理看的常驻项目说明。

只输出 Markdown 正文，不要代码块围栏，不要解释，不要重复本段要求。
要求：
1. 使用中文；
2. 结构固定为：# 项目约定 / ## 这个项目是什么 / ## 常用命令 / ## 目录结构 / ## 约束；
3. 只写有证据的内容；无法确认的命令留空，绝对不要编造；
4. 不超过 120 行；
5. 不要写入密钥、密码、token 或个人信息。"""

_AI_KEY_FILES = (
    "README.md", "package.json", "pyproject.toml", "Makefile",
    "go.mod", "Cargo.toml", "requirements.txt",
)


def _tree_summary(root: str, limit: int = 120) -> str:
    """给模型看的轻量目录结构：根目录 + 一层子目录，跳过依赖/产物目录。"""
    skip = {".git", "node_modules", "__pycache__", ".venv", "venv",
            ".novabot", "dist", "build", ".next", "target"}
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return "（目录不可读）"
    lines: list[str] = []
    for name in names:
        if len(lines) >= limit or name in skip:
            continue
        full = os.path.join(root, name)
        if os.path.isdir(full):
            lines.append(name + "/")
            try:
                children = sorted(os.listdir(full))
            except OSError:
                continue
            for child in children[:12]:
                if child in skip:
                    continue
                lines.append("  " + child + ("/" if os.path.isdir(os.path.join(full, child)) else ""))
                if len(lines) >= limit:
                    break
        else:
            lines.append(name)
    return "\n".join(lines) or "（空目录）"


def _build_generation_prompt(project: dict) -> tuple[str, str]:
    root = str(project.get("path") or "")
    description = str(project.get("description") or "").strip() or "（未填写）"
    commands, sources = _scan_commands(root)
    command_text = "\n".join(f"- {key}: {value}" for key, value in commands.items()) or "（未识别到）"

    snippets: list[str] = []
    for name in _AI_KEY_FILES:
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        text = _read_text(path, 4000).strip()
        if text:
            snippets.append(f"--- {name} ---\n{text}")
    snippets_text = "\n\n".join(snippets) or "（没有可读取的关键文件）"

    user = (
        f"项目名称：{project.get('name') or ''}\n"
        f"项目路径：{root}\n"
        f"项目描述：{description}\n\n"
        f"根目录结构：\n{_tree_summary(root)}\n\n"
        f"已识别的命令候选（来自 {('、'.join(sources) or '无')}）：\n{command_text}\n\n"
        f"关键文件片段：\n{snippets_text}"
    )
    return _AI_SYSTEM_PROMPT, user


def _clean_generated(text: str) -> str:
    value = (text or "").strip()
    if value.startswith("```"):
        lines = value.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    if not value:
        raise ProjectError("project.agents_generate_failed")
    if not value.startswith("#"):
        value = "# 项目约定\n\n" + value
    if not value.startswith(DRAFT_MARK):
        value = DRAFT_MARK + "\n" + value
    data = value.encode("utf-8")
    if len(data) + 1 > AGENTS_MAX_BYTES:
        value = data[:AGENTS_MAX_BYTES - 1].decode("utf-8", "ignore").rstrip()
    return value + "\n"


async def _invoke_model(system: str, user: str) -> str:
    """调用配置里的默认 agent 模型；单独成函数便于测试替换。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    from core.manager.llm_manager import llm_manager

    meta = getattr(config, "CONFIG_DATA", None)
    agent = getattr(meta, "agent", None)
    model = getattr(agent, "model", "") or ""
    if not model:
        raise ProjectError("project.agents_generate_failed")

    llm = llm_manager.get_llm(model)
    result = await asyncio.wait_for(
        llm.ainvoke([SystemMessage(content=system), HumanMessage(content=user)]),
        timeout=60,
    )
    content = getattr(result, "content", "")
    if isinstance(content, list):
        content = "".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content or "")


async def generate_agents_with_model(project: dict) -> dict:
    """让模型根据项目现状生成一版完整 AGENTS.md；只返回内容，不落盘。"""
    if os.path.exists(_target(project)):
        raise ProjectError("project.agents_exists")
    system, user = _build_generation_prompt(project)
    try:
        raw = await _invoke_model(system, user)
    except ProjectError:
        raise
    except Exception as exc:
        raise ProjectError("project.agents_generate_failed") from exc
    _, sources = _scan_commands(str(project.get("path") or ""))
    return {"content": _clean_generated(raw), "sources": list(sources)}
