# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : config_service.py
# @Software    : VSCode
# @Datetime    : 2026/04/19 22:33:27
# @Author      : leo liu
# @Version     : 1.0
# @Description :

import os
import sys
import json
from decimal import Decimal
from typing import Optional

from util.base import base_util
from exception.config_exception import ConfigFilesError
from .dtos import *

def _str_list(value) -> Optional[list]:
    """配置里的字符串列表；不是列表就当没配（None）"""
    if not isinstance(value, list):
        return None
    return [str(x) for x in value]


def _positive_int(value) -> Optional[int]:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _headers(value) -> Optional[dict]:
    """配置里的请求头：只接受 dict，键值统一转字符串，空键忽略。

    httpx 要求 header 值是 str/bytes；配置里写成数字会让请求在发出前就崩，
    这里提前收敛，避免把配置错误拖到运行时。
    """
    if not isinstance(value, dict):
        return None
    headers = {}
    for key, val in value.items():
        name = str(key).strip()
        if not name or val is None:
            continue
        headers[name] = str(val)
    return headers or None


class ConfigService:
    def __init__(self, user_config_path: str):
        self.base_config_path = f"{base_util.root_path}/config/config.json"
        self.user_config_path = user_config_path

    def load(self):
        config = None

        try:
            # 内置配置文件必须存在
            if not self.base_config_path or not os.path.exists(self.base_config_path):
                raise ConfigFilesError("未找到内置配置文件")

            config = self._json_to_dto(self.base_config_path)
            if not config:
                raise ConfigFilesError("内置配置文件加载失败")

            # 用户配置文件可选，存在则加载
            if self.user_config_path and os.path.exists(self.user_config_path):
                user_config = self._json_to_dto(self.user_config_path)

                # 如果有用户配置文件，添加用户配置的供应商
                # llm加载模型时取同code的最后一个供应商，用户配置天然覆盖内置配置
                if user_config and user_config.models:
                    config.models.extend(user_config.models)

                # 多用户开关：仅当用户配置显式给出时才覆盖内置值（None表示未配置）
                if user_config and user_config.multi_user is not None:
                    config.multi_user = user_config.multi_user
                if user_config and user_config.remember_login is not None:
                    config.remember_login = user_config.remember_login

                # 脚本执行档位同理：用户显式给出才覆盖内置值
                if user_config and user_config.exec and user_config.exec.level is not None:
                    config.exec = user_config.exec

                # LLM默认参数：用户显式给出才覆盖内置值
                if user_config and user_config.llm and user_config.llm.max_input_tokens is not None:
                    config.llm = user_config.llm

                # 附件限制逐字段合并：用户只改一个值时，其余继续用内置默认
                if user_config and user_config.attachments:
                    base_att = config.attachments
                    user_att = user_config.attachments
                    config.attachments = AttachmentDto(
                        max_per_message=(user_att.max_per_message
                                         if user_att.max_per_message is not None
                                         else (base_att.max_per_message if base_att else None)),
                        max_file_mb=(user_att.max_file_mb
                                     if user_att.max_file_mb is not None
                                     else (base_att.max_file_mb if base_att else None)),
                        admin_unlimited=(user_att.admin_unlimited
                                         if user_att.admin_unlimited is not None
                                         else (base_att.admin_unlimited if base_att else None)),
                        draft_ttl_days=(user_att.draft_ttl_days
                                        if user_att.draft_ttl_days is not None
                                        else (base_att.draft_ttl_days if base_att else None)),
                    )

                # agent段逐字段合并：用户只改model时，内置的max_steps不能被整段替换掉
                if user_config and user_config.agent:
                    base_agent = config.agent
                    base_steps = base_agent.max_steps if base_agent else None
                    user_steps = user_config.agent.max_steps
                    config.agent = AgentDto(
                        model=user_config.agent.model or (base_agent.model if base_agent else ""),
                        max_steps=user_steps if user_steps is not None else base_steps,
                    )

                # 网络能力：逐字段合并。search.providers是**系统内置档**（只在config.json里定义），
                # 用户的web段不能把它整段替换掉——否则用户改个fetch权限就把系统内置搜索弄没了
                if user_config and user_config.web:
                    base_web = config.web
                    base_search = base_web.search if base_web else None
                    base_fetch = base_web.fetch if base_web else None
                    user_search = user_config.web.search
                    user_fetch = user_config.web.fetch

                    search = WebSearchDto(
                        enabled=(user_search.enabled
                                 if user_search and user_search.enabled is not None
                                 else (base_search.enabled if base_search else None)),
                        providers=base_search.providers if base_search else None,
                        user_providers=(user_search.providers
                                        if user_search else None),
                    )
                    fetch = WebFetchDto(
                        permission=(user_fetch.permission
                                    if user_fetch and user_fetch.permission is not None
                                    else (base_fetch.permission if base_fetch else None)),
                        url_allowlist=(user_fetch.url_allowlist
                                       if user_fetch and user_fetch.url_allowlist is not None
                                       else (base_fetch.url_allowlist if base_fetch else None)),
                    )
                    config.web = WebDto(search=search, fetch=fetch)
        except Exception as ex:
            # 配置加载发生在日志系统就绪之前，此处直接输出到stderr
            print(f"[CONFIG] 配置文件加载失败: {str(ex)}", file=sys.stderr)
            config = None

        return config

    # json文件转dto
    def _json_to_dto(self, config_file: str) -> Optional[ConfigDto]:
        if not config_file or not os.path.exists(config_file):
            return None

        config = ConfigDto(
            models=[],
            agent=None
        )

        with open(config_file, "r", encoding="utf-8") as f:
            jdata = json.load(f)
            if jdata:
                # provider解析
                j_providers = jdata.get("models", None)
                if j_providers:
                    for pk, pv in j_providers.items():
                        provider = ProviderDto(
                            code=pk,
                            name=pv.get("name", ""),
                            base_url=pv.get("base_url", ""),
                            api=pv.get("api", ""),
                            api_key=pv.get("key", "") or pv.get("api_key", ""),
                            host=pv.get("host", ""),
                            headers=_headers(pv.get("headers", None)),
                            dynamic_models=pv.get("dynamic_models", False) is True,
                            models_url=str(pv.get("models_url", "") or "").strip(),
                            models=[]
                        )

                        config.models.append(provider)

                        j_models = pv.get("models", None)
                        if j_models:
                            for jm in j_models:
                                model = ModelDto(
                                    code=jm.get("code", ""),
                                    name=jm.get("name", ""),
                                    stream=jm.get("stream", True),
                                    enable_thinking=jm.get("enable_thinking", False),
                                    num_ctx=jm.get("num_ctx", 0),
                                    max_output_tokens=jm.get("max_output_tokens", None),
                                    cost=self._json_to_cost_dto(jm.get("cost", None)),
                                    thinking_style=jm.get("thinking_style", None),
                                    headers=_headers(jm.get("headers", None))
                                )

                                provider.models.append(model)

                # LLM默认参数（模型未配置num_ctx时的回退值）
                j_llm = jdata.get("llm", None)
                if isinstance(j_llm, dict):
                    raw_max_input = j_llm.get("max_input_tokens", None)
                    config.llm = LlmDto(
                        max_input_tokens=(
                            raw_max_input
                            if isinstance(raw_max_input, int)
                            and not isinstance(raw_max_input, bool)
                            and raw_max_input > 0
                            else None
                        )
                    )

                # 附件限制：缺省留None，交由base_config决定默认值
                j_attachments = jdata.get("attachments", None)
                if isinstance(j_attachments, dict):
                    config.attachments = AttachmentDto(
                        max_per_message=_positive_int(j_attachments.get("max_per_message", None)),
                        max_file_mb=_positive_int(j_attachments.get("max_file_mb", None)),
                        admin_unlimited=(j_attachments.get("admin_unlimited")
                                         if isinstance(j_attachments.get("admin_unlimited"), bool) else None),
                        draft_ttl_days=_positive_int(j_attachments.get("draft_ttl_days", None)),
                    )

                # agent解析（max_steps：单次运行的模型调用轮数上限，缺省不限）
                j_agent = jdata.get("agent", None)
                if j_agent:
                    raw_max = j_agent.get("max_steps", None)
                    agent = AgentDto(
                        model=j_agent.get("model", ""),
                        max_steps=int(raw_max) if isinstance(raw_max, int) and raw_max > 0 else None,
                    )

                    config.agent = agent

                # 脚本执行档位：缺省留None，交由base_config决定默认值
                j_exec = jdata.get("exec", None)
                if isinstance(j_exec, dict):
                    config.exec = ExecDto(level=j_exec.get("level", None))

                # 网络能力：缺省留None，交由base_config决定默认值
                j_web = jdata.get("web", None)
                if isinstance(j_web, dict):
                    j_search = j_web.get("search", None)
                    j_fetch = j_web.get("fetch", None)
                    config.web = WebDto(
                        search=WebSearchDto(
                            enabled=j_search.get("enabled"),
                            providers=[WebSearchProviderDto(
                                provider=p.get("provider", ""),
                                key=p.get("key") or p.get("api_key"),
                                endpoint=p.get("endpoint"),
                            ) for p in (j_search or {}).get("providers", []) if isinstance(p, dict)],
                        ) if isinstance(j_search, dict) else None,
                        fetch=WebFetchDto(
                            permission=j_fetch.get("permission"),
                            url_allowlist=j_fetch.get("url_allowlist"),
                        ) if isinstance(j_fetch, dict) else None,
                    )

                # MCP段：系统层server列表（用户层在DB里，不在配置文件）
                j_mcp = jdata.get("mcp", None)
                if isinstance(j_mcp, dict):
                    config.mcp = McpDto(
                        servers=[McpServerDto(
                            name=str(s.get("name") or ""),
                            url=str(s.get("url") or ""),
                            enabled=s.get("enabled"),
                            headers=s.get("headers") if isinstance(s.get("headers"), dict) else None,
                        ) for s in (j_mcp.get("servers") or []) if isinstance(s, dict)],
                        max_user_servers=j_mcp.get("max_user_servers"),
                    )

                # 多用户开关：缺省留None，交由base_config决定默认值
                config.multi_user = jdata.get("multi_user", None)
                config.remember_login = jdata.get("remember_login", None)

        return config

    # json对象转CostDto，非字典或空值返回None
    def _json_to_cost_dto(self, j_cost) -> Optional[CostDto]:
        if not isinstance(j_cost, dict):
            return None

        return CostDto(
            input=Decimal(str(j_cost.get("input", 0))),
            output=Decimal(str(j_cost.get("output", 0))),
            cache_read=Decimal(str(j_cost.get("cache_read", 0))),
            cache_write=Decimal(str(j_cost.get("cache_write", 0)))
        )
