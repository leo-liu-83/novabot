# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : project_policy.py
# @Description : 项目会话的权限规则与确认范围

"""项目会话的文件边界与确认规则（spec §10–§11）。

权限规则只表达 allow/deny；确认由 `interrupt_on` 的 `when` 谓词决定。
注意：`when` 在 `after_model` 阶段执行，早于工具调用中间件对相对路径的归一，
所以这里必须自己把相对路径按项目根解析，不能假设参数已经是绝对路径。
"""

import os
from typing import Iterable, Optional

from langchain.agents.middleware import InterruptOnConfig
from deepagents.middleware.filesystem import FilesystemPermission

from config import config
from service.session import approvals
from service.project.sensitive import is_sensitive
from service.skill.skill_catalog import skill_roots_on_disk
from service.user.user_service import system_skills_path
from .exec_policy import (
    ExecLevel,
    command_escapes_project,
)

# 只读挂载：写死与 agent.py 的常量一致；单独定义避免 policy ↔ agent 环
READ_ONLY_MOUNTS = ("/system_skills/", "/shared_skills/")
ATTACHMENTS_MOUNT = "/attachments/"
INTERNAL_READ_MOUNTS = (
    "/conversation_history/",
    "/large_tool_results/",
    ATTACHMENTS_MOUNT,
)
ALL_SKILL_MOUNTS = ("/system_skills/", "/shared_skills/", "/skills/")

_DECISIONS = ["approve", "edit", "reject", "respond"]


def _key(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.expanduser(str(path or ""))))


def _within(child: str, root: str) -> bool:
    return child == root or child.startswith(root.rstrip(os.sep) + os.sep)


def _resolve(raw: Optional[str], project_root: str) -> str:
    """把工具参数解析成绝对路径；相对路径按项目根，空值就是项目根。"""
    if raw is None or str(raw) in ("", ".", "/"):
        return _key(project_root)

    text = os.path.expanduser(str(raw))
    if not os.path.isabs(text):
        text = os.path.join(project_root, text)

    return _key(text)


def _skill_roots() -> list[str]:
    return sorted({_key(path) for path in (*skill_roots_on_disk(), system_skills_path())})


def _in_skill_root(path: str, roots: Iterable[str]) -> bool:
    return any(_within(path, root) for root in roots)


def _in_read_roots(path: str, project: "object", ctx, project_root: str) -> bool:
    if _within(path, _key(project_root)):
        return True
    if ctx.can_memory and ctx.memory_path and _within(path, _key(ctx.memory_path)):
        return True

    # 内部挂载是虚拟路径，不是主机路径；"/skills" 与 "/skills/" 都要认
    for mount in (*INTERNAL_READ_MOUNTS, *ALL_SKILL_MOUNTS):
        if path == mount.rstrip("/") or path.startswith(mount):
            return True

    return False


def read_needs_ask(raw_path: Optional[str], project, ctx,
                   uid: str, session_id: str,
                   skill_roots: Optional[list[str]] = None) -> bool:
    """单文件读取是否需要确认：敏感文件或项目外路径。"""
    path = _resolve(raw_path, project.path)
    roots = skill_roots if skill_roots is not None else _skill_roots()

    if _in_skill_root(path, roots):
        # 技能真实路径由 deny 规则直接报错，不走人工确认
        return False
    if approvals.is_path_allowed(uid, session_id, path, "read"):
        return False
    if is_sensitive(path, project.path):
        return True

    return not _in_read_roots(path, project, ctx, project.path)


def bulk_needs_ask(raw_path: Optional[str], project, ctx,
                   uid: str, session_id: str,
                   skill_roots: Optional[list[str]] = None) -> bool:
    """批量扫描是否要确认：只看搜索根是否越出允许范围；敏感项靠结果过滤。"""
    path = _resolve(raw_path, project.path)
    roots = skill_roots if skill_roots is not None else _skill_roots()

    if _in_skill_root(path, roots):
        return False
    if approvals.is_path_allowed(uid, session_id, path, "read"):
        return False

    return not _in_read_roots(path, project, ctx, project.path)


def write_needs_ask(tool: str, raw_path: Optional[str], project,
                    uid: str, session_id: str) -> bool:
    path = _resolve(raw_path, project.path)
    scope = approvals.scope_for_write(path, project.path)
    return not approvals.is_tool_allowed(uid, session_id, tool, scope)


def build_project_permissions(ctx, project, uid: str,
                              session_id: str) -> list[FilesystemPermission]:
    """按 spec §10.1 的顺序组装权限规则；首个匹配生效。"""
    rules: list[FilesystemPermission] = []

    # 1) 本会话 always 批准的路径：必须最先匹配
    for path, ops in approvals.path_allow_rules(uid, session_id):
        rules.append(FilesystemPermission(operations=list(ops), paths=[path], mode="allow"))

    # 2) 技能真实路径：挡绝对路径绕过审核门（虚拟挂载仍可读）
    denials = [f"{root}/**" for root in _skill_roots()]
    if denials:
        rules.append(FilesystemPermission(operations=["read", "write"],
                                          paths=denials, mode="deny"))

    # 3) 只读挂载的写：附件是用户输入，技能挂载是别人的资产
    rules.append(FilesystemPermission(
        operations=["write"],
        paths=[f"{mount}**" for mount in (*READ_ONLY_MOUNTS, ATTACHMENTS_MOUNT)],
        mode="deny",
    ))

    # 4) 项目目录：读写都放行到权限层；写操作仍由工具级确认把关
    rules.append(FilesystemPermission(
        operations=["read", "write"],
        paths=[project.path, f"{project.path}/**"],
        mode="allow",
    ))

    # 5) 全局记忆目录白名单
    if ctx.can_memory and ctx.memory_path:
        rules.append(FilesystemPermission(
            operations=["read", "write"],
            paths=[ctx.memory_path, f"{ctx.memory_path}/**"],
            mode="allow",
        ))

    # 6) 内部挂载只读
    rules.append(FilesystemPermission(
        operations=["read"],
        paths=[f"{mount}**" for mount in INTERNAL_READ_MOUNTS],
        mode="allow",
    ))

    # 7) 其余一律人工确认
    rules.append(FilesystemPermission(operations=["read", "write"],
                                      paths=["/**"], mode="interrupt"))

    return rules


def _arg(args: dict, name: str) -> Optional[str]:
    value = (args or {}).get(name)
    return value if isinstance(value, str) else None


def build_project_interrupt_on(exec_level: ExecLevel, ctx, session_id: str,
                               project) -> dict[str, InterruptOnConfig]:
    """项目会话的确认规则；所有文件工具都由这里接管，不再用权限层合成。"""
    uid = ctx.uid
    roots = _skill_roots()

    read_tools = ("read_file",)
    bulk_tools = ("ls", "glob", "grep")
    write_tools = ("write_file", "edit_file", "delete")

    result: dict[str, InterruptOnConfig] = {}

    for tool in read_tools:
        result[tool] = InterruptOnConfig(
            allowed_decisions=list(_DECISIONS),
            when=(lambda req, t=tool: read_needs_ask(
                _arg(req.tool_call.get("args") or {}, "file_path"), project,
                ctx, uid, session_id, roots)),
        )

    for tool in bulk_tools:
        result[tool] = InterruptOnConfig(
            allowed_decisions=list(_DECISIONS),
            when=(lambda req, t=tool: bulk_needs_ask(
                _arg(req.tool_call.get("args") or {}, "path"), project,
                ctx, uid, session_id, roots)),
        )

    for tool in write_tools:
        result[tool] = InterruptOnConfig(
            allowed_decisions=list(_DECISIONS),
            when=(lambda req, t=tool: write_needs_ask(
                t, _arg(req.tool_call.get("args") or {}, "file_path"),
                project, uid, session_id)),
        )

    command_when = None
    if exec_level == ExecLevel.CONFIRM_ALL:
        command_when = lambda req: not approvals.is_command_allowed(
            uid, session_id, str((req.tool_call.get("args") or {}).get("command") or ""))
    elif exec_level == ExecLevel.ALLOW_ALL:
        command_when = lambda req: command_escapes_project(
            str((req.tool_call.get("args") or {}).get("command") or ""),
            project.path, project.path)

    if command_when is not None:
        result["execute"] = InterruptOnConfig(allowed_decisions=list(_DECISIONS),
                                              when=command_when)

    # 保留配置里其他需要确认的工具（当前默认只有写工具与 execute，已被上面覆盖）
    for tool, enabled in config.AGENT_INTERRUPT_TOOLS.items():
        if enabled and tool not in result:
            result[tool] = InterruptOnConfig(allowed_decisions=list(_DECISIONS))

    return result
