# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : user_config_store.py
# @Description : 用户配置文件（novabot.json）的写入口

"""用户配置文件（novabot.json）的写入口。

目前只写一个值：`agent.model`。写之前先读**原始** JSON 再改，绝不把合并后的
`CONFIG_DATA` 整份倒进用户文件——那会把内置 provider（含空 key）与默认值一起
固化进用户配置，之后升级内置配置也不会生效。
"""

import json
import os
import tempfile
from typing import Optional

from config import config
from service.config.dtos import AgentDto


class UserConfigError(Exception):
    """用户配置文件不可读/不可写。

    code 直接对应错误码注册表里的条目，接口层原样转成响应，不再自己猜状态码。
    """

    def __init__(self, message: str, *, code: str = "model.write_failed"):
        super().__init__(message)
        self.code = code


def user_config_path() -> str:
    """实时读取：测试与 --data-dir 都会改 APP_DATA_PATH，不能在导入时缓存。"""
    return os.path.join(config.APP_DATA_PATH, config.USER_CONFIG_FILE)


def _read_existing(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        # utf-8-sig：Windows 上用记事本保存过的文件会带 BOM
        with open(path, encoding="utf-8-sig") as handle:
            text = handle.read().strip()
    except OSError as exc:
        raise UserConfigError(f"读取 {path} 失败：{exc}", code="model.config_invalid") from exc
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UserConfigError(
            f"{path} 不是合法 JSON：{exc.msg}（第 {exc.lineno} 行）",
            code="model.config_invalid",
        ) from exc
    if not isinstance(data, dict):
        raise UserConfigError(f"{path} 顶层必须是 JSON 对象", code="model.config_invalid")
    return data


def _write_atomic(path: str, data: dict) -> None:
    """同目录临时文件 + os.replace：中途失败不会留下半份配置。"""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    mode: Optional[int] = None
    if os.path.exists(path):
        mode = os.stat(path).st_mode & 0o777
    handle_fd, tmp = tempfile.mkstemp(dir=directory, prefix=".novabot-", suffix=".json")
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _apply_in_memory(model: str) -> None:
    data = config.CONFIG_DATA
    if data is None:
        return
    if data.agent is None:
        data.agent = AgentDto(model=model)
    else:
        data.agent.model = model


def set_agent_model(model: str) -> str:
    """把 agent 默认模型写进用户配置，并同步内存里的 CONFIG_DATA。"""
    path = user_config_path()
    data = _read_existing(path)
    agent = data.get("agent")
    if not isinstance(agent, dict):
        agent = {}
    agent["model"] = model
    data["agent"] = agent
    try:
        _write_atomic(path, data)
    except OSError as exc:
        raise UserConfigError(f"写入 {path} 失败：{exc}", code="model.write_failed") from exc
    _apply_in_memory(model)
    return model
