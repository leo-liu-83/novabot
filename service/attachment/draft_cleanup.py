# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
"""清理超过保留期的未发送附件草稿。

已进入消息历史的附件不能删（重开会话会断链），所以按
`messages.attachments` 反查引用。清理只在启动时跑一次，失败不影响启动。
"""

import os
import time
from typing import Optional

from config import config
from core.agent import persistence
from logger import logger
from service.session.session_service import session_service


def cleanup_stale_drafts(now: Optional[float] = None,
                         ttl_days: Optional[int] = None) -> int:
    """删除超过 TTL 且未被消息引用的附件文件，返回删除数量。

    `ttl_days` 仅供测试；运行期取配置 `attachments.draft_ttl_days`。
    """
    days = config.ATTACHMENT_DRAFT_TTL_DAYS if ttl_days is None else ttl_days
    if days <= 0:
        return 0

    cutoff = (time.time() if now is None else now) - days * 86400
    removed = 0

    try:
        sessions = session_service.all_sessions()
    except Exception as exc:  # 数据库尚未就绪时不要让启动失败
        logger.warning("清理附件草稿失败（读取会话）：%s", exc)
        return 0

    for row in sessions:
        session_id = row.get("id")
        user_id = row.get("user_id")
        if not session_id or not user_id:
            continue
        try:
            root = persistence.attachment_dir(session_id)
            if not os.path.isdir(root):
                continue
            for name in os.listdir(root):
                path = os.path.join(root, name)
                if not os.path.isfile(path):
                    continue
                try:
                    if os.path.getmtime(path) > cutoff:
                        continue
                except OSError:
                    continue
                # 已进入历史消息的附件不能删，否则重开会话会断链
                if session_service.attachment_referenced(session_id, user_id, name):
                    continue
                try:
                    os.remove(path)
                    removed += 1
                except OSError as exc:
                    logger.warning("删除过期附件草稿失败 %s: %s", path, exc)
        except Exception as exc:  # 单个会话失败不影响其它会话
            logger.warning("清理会话 %s 的附件草稿失败：%s", session_id, exc)

    if removed:
        logger.info("已清理 %s 个过期未发送附件", removed)
    return removed
