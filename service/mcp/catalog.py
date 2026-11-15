# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : catalog.py
# @Software    : VSCode
# @Author      : leo liu
# @Version     : 1.0
# @Description : MCP工具目录与"注入说明预算"：工具说明进模型上下文，是有代价的

import re
from dataclasses import dataclass, field
from typing import Optional

# 注入预算（字符数）。参照 OpenClaw 对 server 指令 2000 字符的截断思路，
# 但这里更紧——NovaBot 的上下文预算本来就紧（连日期都只注入日期不注入时分秒）。
MAX_TOOL_DESC_CHARS = 300      # 单个工具的说明
MAX_SERVER_DESC_CHARS = 1200    # 单个 server 的工具说明合计
MAX_TOTAL_DESC_CHARS = 3000     # 全部 server 的工具说明合计

TRUNCATION_MARK = "…(已截断)"

_NAME_BAD = re.compile(r"[^A-Za-z0-9_-]+")


def normalize_name(*parts: str) -> str:
    """把 server名+工具名 归一成合法的工具名（同 OpenCode 的 server_tool 约定）。

    归一化后可能撞名，故调用方需自行检查唯一性。
    """
    joined = "_".join(p for p in parts if p)
    return _NAME_BAD.sub("_", joined)


@dataclass
class McpTool:
    server: str
    name: str
    description: str
    input_schema: dict = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        return normalize_name(self.server, self.name)


def truncate(text: str, limit: int) -> str:
    """截断到limit字符（含截断标记）。"""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    if limit <= len(TRUNCATION_MARK):
        return text[:limit]
    return text[:limit - len(TRUNCATION_MARK)] + TRUNCATION_MARK


def build_catalog(servers: list[tuple[str, list]], *,
                  per_tool: int = MAX_TOOL_DESC_CHARS,
                  per_server: int = MAX_SERVER_DESC_CHARS,
                  total: int = MAX_TOTAL_DESC_CHARS) -> tuple[list[McpTool], list[str]]:
    """按优先级把各 server 的工具拼成目录，并守住注入预算。

    servers: [(server名, 该server的工具原始列表)]，**按优先级排列**
    返回: (目录, 提示信息列表)

    预算用尽时的取舍：**丢弃**后面的工具，而不是塞一个没有说明的工具——
    没有说明的工具既费上下文又让模型瞎猜。丢弃情况会写进提示，让用户知道被裁掉了。
    """
    catalog: list[McpTool] = []
    notes: list[str] = []
    used_total = 0
    seen: set[str] = set()

    for server, tools in servers:
        used_server = 0
        dropped_here = 0

        for raw in tools or []:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "")
            if not name:
                continue

            qualified = normalize_name(server, name)
            if qualified in seen:
                notes.append(f"工具名归一化后与已有项重名，已跳过：{qualified}")
                continue

            desc = truncate(str(raw.get("description") or ""), per_tool)
            cost = len(desc) + 1
            if used_server + cost > per_server:
                dropped_here += 1
                continue
            if used_total + cost > total:
                notes.append(
                    f"注入说明预算已用尽（{total} 字符），{server} 之后的工具未纳入目录")
                return catalog, notes

            schema = raw.get("inputSchema")
            catalog.append(McpTool(
                server=server, name=name, description=desc,
                input_schema=schema if isinstance(schema, dict) else {},
            ))
            seen.add(qualified)
            used_server += cost
            used_total += cost

        if dropped_here:
            notes.append(
                f"{server} 有 {dropped_here} 个工具因单server说明预算（{per_server} 字符）未纳入目录")

    return catalog, notes
