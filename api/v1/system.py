"""系统信息：只读诊断快照（管理员/单用户机主）。

只返回展示需要的值；provider/MCP 的密钥与环境变量永远不进这个接口。
"""

import platform
import sys
from typing import Any

from fastapi import APIRouter, Depends

from api.resp import response_code
from api.v1.auth import deps
from api.v1.auth.deps import require_admin
from config import config
from core.agent.deep_agents.exec_policy import (
    ADMIN_DEFAULT,
    NON_ADMIN_CEILING_DEFAULT,
    SINGLE_USER_DEFAULT,
    USER_DEFAULT,
    parse_level,
)
from service.user.user_service import UserCtx
from service.web.provider_service import user_web_provider_service
from service.web.quota_service import next_reset_at
from service.web.search_service import builtin_rows

router = APIRouter(prefix="/system")


@router.get("", summary="读取系统信息")
async def system_info(ctx: UserCtx = Depends(require_admin)) -> Any:
    return response_code.resp_200({
        "version": config.APP_VERSION,
        "python": platform.python_version(),
        "platform": f"{sys.platform} / {platform.machine()}",
        "multi_user": bool(config.MULTI_USER),
        "mode_label": "多用户" if config.MULTI_USER else "单用户",
        "data_dir": config.APP_DATA_PATH,
        "db_path": config.db_file_path(),
        # 展示用户实际编辑的配置文件；仓库内置的 config.json 只是发行版默认值
        "config_file": f"{config.APP_DATA_PATH}/{config.USER_CONFIG_FILE}",
        "exec_cap": f"T{int(parse_level(config.EXEC_LEVEL))}",
        "defaults": {
            "single": f"T{int(SINGLE_USER_DEFAULT)}",
            "admin": f"T{int(ADMIN_DEFAULT)}",
            "user": f"T{int(USER_DEFAULT)}",
            "non_admin_ceiling": f"T{int(NON_ADMIN_CEILING_DEFAULT)}",
        },
        "web": {
            "builtin": [row["provider"] for row in builtin_rows()],
            "global": [
                {"provider": row["provider"], "enabled": bool(row["enabled"])}
                for row in user_web_provider_service.list_global()
            ],
            "user_provider_count": user_web_provider_service.count_user_providers(
                {row["uid"] for row in deps.user_service.list_users()}
            ),
            "reset_at": next_reset_at(),
        },
    })
