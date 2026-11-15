# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : __init__.py
# @Software    : VSCode
# @Datetime    : 2026/09/29 10:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description :

import asyncio
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from config import config
from logger import logger
from core.agent import persistence
from service.attachment.draft_cleanup import cleanup_stale_drafts
from service.user.user_service import user_service
from exception.base_exception import ViewError, ServiceError, CoreError, PluginsError, UtilsError
from api.resp import response_code
from api.v1 import api_v1


@asynccontextmanager
async def lifespan(app: FastAPI):
    """进程级初始化：checkpoint持久化要绑定当前事件循环，只能在这里建一次。

    初始化失败只降级为内存模式（见persistence.startup），不能因此让服务起不来。
    """
    await persistence.startup()
    # 到期凭证清扫：长期运行的服务不扫就会一直堆（见 purge_expired_tokens）
    try:
        user_service.purge_expired_tokens()
    except Exception as exc:  # noqa: BLE001 - 清理失败不能拦住服务启动
        logger.warning(f"清理过期登录凭证失败（不影响使用）：{exc}")
    try:
        # 附件草稿按天保留；放到线程里，目录大时也不卡事件循环
        await asyncio.to_thread(cleanup_stale_drafts)
    except Exception as exc:  # noqa: BLE001 - 清理失败不能拦住服务启动
        logger.warning(f"清理过期附件草稿失败（不影响使用）：{exc}")
    try:
        yield
    finally:
        await persistence.shutdown()


def create_app():
    app = FastAPI(
        title=config.APP_NAME,
        description=config.APP_DESC,
        version=config.APP_VERSION,
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    app.include_router(api_v1, prefix="/api/v1")

    register_exception(app)
    register_cors(app)
    register_middleware(app)

    # Web 静态控制台：必须最后挂在根路径，前面的 /api/v1 与 /docs 路由优先匹配。
    # 所有页面路由用 hash（#/chat），刷新时仍由 index.html 承接。
    web_root = Path(__file__).resolve().parent.parent / "web"
    if web_root.is_dir():
        app.mount("/", StaticFiles(directory=str(web_root), html=True), name="web")

    return app

def register_exception(app: FastAPI):
    """
    全局异常捕获
    """

    def _handle(exc_title: str):
        async def handler(request: Request, exc: Exception):
            logger.error(f"{exc_title}\nURL:{request.url}\n{traceback.format_exc()}")
            return response_code._resp(
                status.HTTP_400_BAD_REQUEST, None,
                getattr(exc, "err_desc", str(exc)),
                error=getattr(exc, "code", None),
            )
        return handler

    app.add_exception_handler(ViewError, _handle("路由异常"))
    app.add_exception_handler(ServiceError, _handle("业务逻辑异常"))
    app.add_exception_handler(CoreError, _handle("核心逻辑异常"))
    app.add_exception_handler(PluginsError, _handle("插件逻辑异常"))
    app.add_exception_handler(UtilsError, _handle("工具逻辑异常"))

    # 鉴权/路由类HTTP异常统一走本项目响应信封，避免同一接口出现两种错误体形状。
    # 注册在Starlette基类上：未匹配路由抛的是StarletteHTTPException而非fastapi.HTTPException
    async def _handle_http(request: Request, exc: Exception):
        status_code = getattr(exc, "status_code", 500)
        detail = getattr(exc, "detail", str(exc))
        error = vars_ = None
        if isinstance(detail, dict):
            # api.resp.errors.api_error 的形态：固定提示带 code，前端按 code 翻译
            error = detail.get("error")
            vars_ = detail.get("vars") or None
            detail = detail.get("message") or str(detail)
        logger.warning(f"HTTP异常\nURL:{request.url}\nstatus:{status_code} detail:{detail}")
        return response_code._resp(status_code, data=None, message=str(detail),
                                   error=error, vars=vars_)

    app.add_exception_handler(StarletteHTTPException, _handle_http)

def register_cors(app: FastAPI):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"]
    )

def register_middleware(app: FastAPI):
    @app.middleware("http")
    async def logger_request(request: Request, call_next):
        logger.info(f"访问记录:{request.method} url:{request.url} client:{request.headers.get('user-agent')}")
        response = await call_next(request)
        # 静态控制台必须每次回源校验：否则改了 JS/CSS 后，浏览器启发式缓存会继续执行旧代码，
        # 表现为“只有手动刷新/清缓存才生效”。no-cache 仍然允许 304，不会重复传完整文件。
        if not request.url.path.startswith("/api/") and "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-cache"
        return response
