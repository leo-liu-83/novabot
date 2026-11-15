# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : paths.py
# @Description : 项目路径规范化与边界校验

import os
from typing import Sequence

from config import config
from service.project.errors import ProjectError


def _norm(raw: str) -> str:
    """展开 ~、解析符号链接、统一大小写；所有比较都在这条规范形上做。"""
    return os.path.normcase(os.path.realpath(os.path.expanduser(raw)))


def is_within(path: str, root: str) -> bool:
    """path 是否等于 root 或位于 root 之下（按路径段，不做前缀字符串匹配）。"""
    path, root = _norm(path), _norm(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


_STATE_TEXT = {
    "missing": "目录不存在",
    "not_dir": "不是目录",
    "not_writable": "目录不可写",
}


def project_dir_state(path: str) -> str:
    """目录可用性：ok / missing / not_dir / not_writable。"""
    if not os.path.exists(path):
        return "missing"
    if not os.path.isdir(path):
        return "not_dir"
    if not os.access(path, os.W_OK):
        return "not_writable"
    return "ok"


def _user_paths():
    """延迟导入：user_service 在模块级会拉会话层，本项目包不希望在 import 时就付这笔钱。"""
    from service.user.user_service import (
        DEFAULT_USER_ID, shared_skills_path, system_skills_path, user_workspace_path,
    )
    return DEFAULT_USER_ID, shared_skills_path, system_skills_path, user_workspace_path


def validate_project_path(raw: str, *, uid: str,
                          existing_paths: Sequence[str] = ()) -> str:
    """校验并返回规范化后的项目路径；不合法直接抛 ProjectError。

    拒绝口径（spec §7.1）：不是目录 / 不可写 / 根与 home / 应用数据及其祖先 /
    应用数据内但不在当前用户 workspace 下（含别人的账号目录与 workspace 根本身）/
    技能根 / 与已有活动项目相同或互相嵌套。
    """
    path = _norm(raw)
    state = project_dir_state(path)
    if state != "ok":
        raise ProjectError(
            "project.path_invalid", path=path, reason=_STATE_TEXT.get(state, state))

    default_uid, shared_skills_path, system_skills_path, user_workspace_path = _user_paths()

    if path == os.sep or path == _norm("~"):
        raise ProjectError("project.path_forbidden", path=path)

    app_data = _norm(config.APP_DATA_PATH)
    # 项目包住应用数据：放行项目写入等于放行写数据库与配置
    if is_within(app_data, path):
        raise ProjectError("project.path_forbidden", path=path)

    if is_within(path, app_data):
        workspace = _norm(
            config.AGENT_WORKSPACE_PATH if uid == default_uid else user_workspace_path(uid)
        )
        # 只允许 workspace 的子目录：workspace 根本身、别人的账号目录都在这里被挡掉
        if path == workspace or not is_within(path, workspace):
            raise ProjectError("project.path_forbidden", path=path)

    for root in (system_skills_path(), shared_skills_path(), f"{user_workspace_path(uid)}/skills"):
        if is_within(path, root):
            raise ProjectError("project.path_forbidden", path=path)

    for other in existing_paths:
        if is_within(path, other) or is_within(other, path):
            raise ProjectError("project.path_nested", path=path)

    return path


def browse_root(uid: str) -> str:
    """目录浏览的根：单用户机主可看整个文件系统，其他账号最多到自己家目录。"""
    default_uid, _, _, _ = _user_paths()
    return os.sep if uid == default_uid else _norm("~")


def browse_dirs(raw: str, *, uid: str) -> dict:
    """返回可选的子目录列表；只列目录，不读任何文件内容。

    应用数据目录（数据库、登录态、各账号 workspace）一律不可浏览，
    与 `validate_project_path` 拒绝把应用数据登记为项目的口径保持一致。
    """
    root = browse_root(uid)
    default_uid, _, _, _ = _user_paths()
    start = raw.strip() if raw else ("~" if uid == default_uid else root)
    path = _norm(start)

    if not is_within(path, root):
        raise ProjectError("project.path_forbidden", path=path)
    if is_within(path, _norm(config.APP_DATA_PATH)):
        raise ProjectError("project.path_forbidden", path=path)

    if not os.path.exists(path):
        raise ProjectError("project.path_invalid", path=path, reason=_STATE_TEXT["missing"])
    if not os.path.isdir(path):
        raise ProjectError("project.path_invalid", path=path, reason=_STATE_TEXT["not_dir"])

    try:
        entries = [entry for entry in os.scandir(path) if entry.is_dir()]
    except OSError:
        raise ProjectError("project.path_invalid", path=path, reason="目录不可读") from None

    dirs = sorted(
        ({"name": entry.name, "path": os.path.join(path, entry.name)} for entry in entries),
        key=lambda item: item["name"].casefold(),
    )

    parent = None
    if path != root:
        candidate = os.path.dirname(path)
        if is_within(candidate, root):
            parent = candidate

    return {"path": path, "parent": parent, "root": root, "dirs": dirs}
