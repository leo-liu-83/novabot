# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : hitl.py
# @Software    : VSCode
# @Author      : leo liu
# @Version     : 1.0
# @Description : 人工确认（HITL）判定收敛在一处：确认流统一走 action_requests/decisions

from typing import Any


def is_approved(resume_value: Any) -> bool:
    """恢复值里第一条决策是不是"放行"。

    edit 也算放行：确认栏允许用户改参数后再执行，改完仍是用户点过头的。
    多动作确认时，调用方要么一条一条恢复（各自拿到只含一条的 decisions），
    要么由 langchain 的 HITL 中间件先把决策数补齐——这里只看当前这条。
    """
    decisions = (resume_value or {}).get("decisions") or [{}]

    return str(decisions[0].get("type", "")) in ("approve", "edit")
