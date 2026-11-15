# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : skill_script.py
# @Software    : VSCode
# @Datetime    : 2026/10/13 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : T1档技能脚本执行：按技能名解析 scripts/ 下的 .py，无shell调用

import os
import subprocess
import sys
from typing import Optional

from langchain.tools import tool

from const.messages import msg
from service.skill.skill_review_service import skill_review_service
from service.user.user_service import UserCtx

SCRIPTS_DIRNAME = "scripts"
SCRIPT_SUFFIX = ".py"
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_OUTPUT_BYTES = 100_000

# 传给子进程的环境变量白名单：只给PATH与临时目录，其余（含模型密钥）一律不传。
# inherit_env=False是LocalShellBackend的默认取向，这里保持一致。
_ENV_PASSTHROUGH = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP")


class SkillScriptError(Exception):
    """技能脚本不可执行：技能不存在、路径越界、类型不符、未通过审查等。消息直接回给模型"""


def _skill_roots(ctx: UserCtx) -> list[str]:
    """该身份可见的技能目录，按覆盖优先级排列（私有>共享>系统）。

    build_ctx给出的挂载顺序是 system→shared→private（优先级低到高），
    故这里反向遍历，与技能加载的同名覆盖规则保持一致。
    """
    roots: list[str] = []

    for _mount, path in reversed(ctx.skill_mounts):
        if path not in roots:
            roots.append(path)

    return roots


def find_skill_scripts_dir(ctx: UserCtx, skill: str) -> str:
    """按技能名解析出scripts目录。

    只认单段技能名，不接受路径分隔符——否则技能名就成了路径穿越的入口。
    同名技能按覆盖优先级取第一个命中（私有>共享>系统，与技能加载一致）。
    """
    name = (skill or "").strip()
    if not name:
        raise SkillScriptError(msg.MSG_EXEC_SKILL_NAME_REQUIRED)

    if name != os.path.basename(name) or name in (".", "..") or "/" in name or "\\" in name:
        raise SkillScriptError(msg.MSG_EXEC_SKILL_NAME_INVALID)

    for root in _skill_roots(ctx):
        scripts_dir = os.path.join(root, name, SCRIPTS_DIRNAME)
        if os.path.isdir(scripts_dir):
            return scripts_dir

    raise SkillScriptError(msg.MSG_EXEC_SKILL_NOT_FOUND)


def resolve_script_path(ctx: UserCtx, skill: str, script: str) -> str:
    """把模型给的（技能名, 相对脚本路径）解析成绝对路径，并做三重校验。

    返回的路径已realpath，可直接交给subprocess。
    """
    scripts_dir = find_skill_scripts_dir(ctx, skill)
    rel = (script or "").strip()

    if not rel:
        raise SkillScriptError(msg.MSG_EXEC_SCRIPT_PATH_REQUIRED)

    if not rel.endswith(SCRIPT_SUFFIX):
        raise SkillScriptError(msg.MSG_EXEC_SCRIPT_SUFFIX)

    # realpath先解符号链接，再校验是否仍落在该技能自己的scripts目录内。
    # 顺序不能反：先校验后realpath会被 ../ 或符号链接绕过。
    resolved = os.path.realpath(os.path.join(scripts_dir, rel))
    scripts_real = os.path.realpath(scripts_dir)

    if os.path.commonpath([resolved, scripts_real]) != scripts_real:
        raise SkillScriptError(msg.MSG_EXEC_SCRIPT_OUTSIDE)

    if not os.path.isfile(resolved):
        raise SkillScriptError(msg.MSG_EXEC_SCRIPT_NOT_FOUND)

    return resolved


def _ensure_reviewed(skill_dir: str, skill: str) -> None:
    """T1的审查闸门（E7）：技能必须已审查且内容未变更才可执行。

    技能来源可能是公网下载，也可能只是agent自己写的，两种都可能是恶意代码。
    审查是人工判断，扫描器只负责把危险构造摆到人眼前（见skill_scan的模块说明）。
    """
    state = skill_review_service.get_state(skill_dir)

    if state.runnable:
        return

    if not state.approved:
        raise SkillScriptError(msg.MSG_EXEC_SKILL_UNREVIEWED.format(skill=skill))

    if state.stale:
        raise SkillScriptError(msg.MSG_EXEC_SKILL_STALE.format(skill=skill))


def _child_env() -> dict:
    return {k: v for k, v in os.environ.items() if k in _ENV_PASSTHROUGH and v}


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def run_skill_script_impl(ctx: UserCtx, skill: str, script: str,
                          arguments: Optional[list] = None,
                          timeout: int = DEFAULT_TIMEOUT_SECONDS,
                          max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES) -> str:
    """执行技能脚本并返回其输出。

    用sys.executable + 参数列表调用，全程不经shell——没有元字符解析，
    也就没有注入面。解释器钉死为跑NovaBot的这个Python，不用PATH里的python3。
    """
    resolved = resolve_script_path(ctx, skill, script)
    # 技能目录从find拿，不能从脚本路径反推：嵌套脚本如 scripts/lib/x.py 反推会得到 scripts
    _ensure_reviewed(os.path.dirname(find_skill_scripts_dir(ctx, skill)), skill)

    argv = [sys.executable, resolved] + [str(a) for a in (arguments or [])]

    try:
        proc = subprocess.run(
            argv,
            cwd=ctx.workspace_path,
            env=_child_env(),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"[技能脚本] 执行超时（{timeout}秒）：{skill}/{script}"
    except OSError as ex:
        return f"[技能脚本] 启动失败：{ex}"

    out = _decode(proc.stdout)
    err = _decode(proc.stderr)
    parts = []

    if out:
        parts.append(out)
    if err:
        parts.append(f"[stderr]\n{err}")

    body = "\n".join(parts)
    if len(body.encode("utf-8")) > max_output_bytes:
        body = body.encode("utf-8")[:max_output_bytes].decode("utf-8", errors="ignore")
        body += f"\n[输出超过 {max_output_bytes} 字节，已截断]"

    return f"[exit {proc.returncode}]\n{body}" if body else f"[exit {proc.returncode}]"


def build_run_skill_script_tool(ctx: UserCtx, timeout: int = DEFAULT_TIMEOUT_SECONDS,
                                max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES):
    """按当前身份构造run_skill_script工具。

    闭包捕获ctx而非让模型传身份：模型能指定跑哪个技能的脚本，但不能指定以谁的身份跑。
    """

    @tool("run_skill_script")
    def run_skill_script(skill: str, script: str, arguments: Optional[list] = None) -> str:
        """运行某个技能 scripts/ 目录下的 Python 脚本。仅T1档可用。

        Args:
            skill: 技能名（单段名称，不含路径分隔符）
            script: 相对 scripts/ 目录的脚本路径，必须以 .py 结尾
            arguments: 传给脚本的位置参数列表，逐个原样传递
        """
        try:
            return run_skill_script_impl(ctx, skill, script, arguments, timeout, max_output_bytes)
        except SkillScriptError as ex:
            return str(ex)

    return run_skill_script