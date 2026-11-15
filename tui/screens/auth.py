# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : auth.py
# @Software    : VSCode
# @Datetime    : 2026/10/10 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 身份切换编排：命令可见性、过户判定、身份应用、启动登录态恢复

from typing import Optional

from config import config
from const.messages import msg
from exception.user_exception import AuthError
from service.project.project_service import project_service
from service.session.session_service import DEFAULT_USER_ID, session_service
from service.user.user_service import (
    UserCtx,
    build_ctx,
    default_ctx,
    set_current_ctx,
    user_service,
)
from .sessions import reset_session_cache
from tui.i18n import t

LOGIN_STEPS = [("username", "用户名", False), ("password", "密码", True)]
REGISTER_STEPS = LOGIN_STEPS + [("password2", "确认密码", True)]
PASSWD_STEPS = [("old", "当前密码", True), ("new", "新密码", True), ("confirm", "确认新密码", True)]
DELETE_ACCOUNT_STEPS = [("password", "登录密码（再次验证）", True)]
# 建管理员：与注册同形，但语义不同——这是本机的"机主"声明，只有本地TUI能走这条路
ADMIN_SETUP_STEPS = LOGIN_STEPS + [("password2", "确认密码", True)]

ADMIN_SETUP_TITLE = (
    "创建管理员账号（必须完成，无法跳过）\n\n"
    "本机还没有管理员账号。多用户模式下必须有且只能有一个管理员，\n"
    "它负责为其他账号授权脚本执行能力。\n\n"
    "该操作只能在运行 NovaBot 的这台机器上完成，不经过 API。"
)


def needs_admin_setup() -> bool:
    """是否处于必须先建管理员的状态（仅多用户模式）"""
    return bool(config.MULTI_USER) and not user_service.has_admin()


def setup_admin(values: dict) -> tuple[UserCtx, str]:
    if values.get("password") != values.get("password2"):
        raise AuthError(msg.MSG_AUTH_PASSWORD_MISMATCH)

    return user_service.create_admin(values["username"], values["password"], remember=config.REMEMBER_LOGIN)


def visible_auth_commands(multi_user: bool, is_default: bool) -> list[tuple[str, str]]:
    """认证命令可见性：单用户模式全隐藏；多用户未登录显示登录注册，已登录显示登出"""
    if not multi_user:
        return []

    if is_default:
        return [("/login", t(t(t("登录已有账号")))), ("/register", t(t(t("注册新账号"))))]

    return [("/logout", t(t(t("退出登录，回到匿名状态"))))]


def visible_account_commands(is_default: bool) -> list[tuple[str, str]]:
    """账户维护命令仅登录态可见"""
    if is_default:
        return []

    return [("/passwd", t(t(t("修改密码")))), ("/delete-account", t(t(t("注销当前账号"))))]


REMEMBER_MAX = 500


def build_remember_instruction(content: str) -> Optional[str]:
    """校验并拼装 /remember 的指令文本；内容为空或超长返回None"""
    content = content.strip()
    if not content or len(content) > REMEMBER_MAX:
        return None

    return (
        # 这是发给模型的指令，不随界面语言变化；界面只翻译提示与命令描述
        "请把以下内容记入长期记忆（写入 /memory/ 下最合适的分类文件，"
        "并同步更新 core.md 索引中该文件的摘要与日期）：\n\n" + content
    )


def remember_hint(content: str) -> str:
    """build_remember_instruction 返回None时的提示语"""
    return t("用法：/remember <想记住的内容>") if not content.strip() else \
        t(t(t('内容过长（{} 字），请精简到 {} 字以内')), len(content.strip()), REMEMBER_MAX)


def visible_memory_commands(is_default: bool) -> list[tuple[str, str]]:
    """记忆相关命令仅登录态可见：记忆只存在于登录用户的workspace"""
    if is_default:
        return []

    return [("/remember", t(t(t("把指定内容记入长期记忆")))),
            ("/memory", t(t(t("查看长期记忆（不带参数看索引，带文件名看内容）"))))]


def identity_label(ctx: UserCtx) -> str:
    return f"user:{ctx.username}" if ctx.username else t(t(t("匿名")))


def should_offer_handover(message_count: int) -> bool:
    """当前default会话已有内容时才需要询问过户；空会话直接开新的"""
    return message_count > 0


def authenticate(register: bool, username: str, password: str, password2: Optional[str] = None) -> tuple[UserCtx, str]:
    """按配置决定是否给这次登录签发长期 token。

    `remember_login=false` 时 TUI 不写 login.json，那张票再没人吊销——
    所以签成带 TTL 的，启动时由 `purge_expired_tokens` 收尾。
    """
    remember = bool(config.REMEMBER_LOGIN)
    if register:
        if password != password2:
            raise AuthError(msg.MSG_AUTH_PASSWORD_MISMATCH)

        return user_service.register(username, password, remember=remember)

    return user_service.login(username, password, remember=remember)


def verify_account(uid: str, password: str) -> None:
    """只校验不改数据：让密码错误在确认弹窗前就暴露"""
    user_service.verify_password_for(uid, password)


def change_password(uid: str, old_password: str, new_password: str, confirm: str) -> str:
    """改密并返回当前端的新token（旧token已被revoke_all作废，必须换写login.json）"""
    if new_password != confirm:
        raise AuthError(msg.MSG_AUTH_PASSWORD_MISMATCH)

    token = user_service.change_password(uid, old_password, new_password, remember=config.REMEMBER_LOGIN)
    persist_login(token)

    return token


def delete_account(uid: str, password: str, purge_files: bool = False) -> None:
    """同步注销辅助（测试/非异步调用）。生产 TUI 流程用 async_delete_account。"""
    user_service.verify_password_for(uid, password)
    session_service.delete_user_sessions_wait_sync(uid)
    user_service.delete_account(uid, password, purge_files)
    project_service.delete_projects_by_user(uid)
    user_service.clear_login_state()
    switch_identity(default_ctx())


async def async_delete_account(uid: str, password: str, purge_files: bool = False) -> None:
    """先封锁新会话并等待在途请求结束，再删 checkpoint/用户目录。

    `await` 轮询时事件循环仍能运行 ChatService 的 DB 租约 heartbeat，避免注销流程
    自己阻塞 heartbeat、等 TTL 到期后把仍在执行的 thread 当成已空闲来 purge。
    """
    user_service.verify_password_for(uid, password)
    await session_service.delete_user_sessions_wait(uid)
    user_service.delete_account(uid, password, purge_files)
    project_service.delete_projects_by_user(uid)
    user_service.clear_login_state()
    switch_identity(default_ctx())


def switch_identity(ctx: UserCtx) -> None:
    """切换当前身份并清空会话缓存：缓存里可能残留上一身份的会话列表"""
    set_current_ctx(ctx)
    reset_session_cache()


def logout_current() -> UserCtx:
    """吊销当前token并回落default档"""
    user_service.logout(user_service.load_login_state())
    ctx = default_ctx()
    switch_identity(ctx)

    return ctx


def persist_login(token: str) -> None:
    if config.REMEMBER_LOGIN:
        user_service.save_login_state(token)


def restore_login() -> UserCtx:
    """启动时按login.json恢复登录态；token失效/用户已注销则回落default并清理残留文件"""
    if not (config.MULTI_USER and config.REMEMBER_LOGIN):
        return default_ctx()

    token = user_service.load_login_state()
    if not token:
        return default_ctx()

    uid = user_service.resolve_token(token)
    if not uid:
        user_service.clear_login_state()
        return default_ctx()

    user = user_service.get_user(uid)
    if not user:
        user_service.revoke_all(uid)
        user_service.clear_login_state()
        return default_ctx()

    return build_ctx(uid, user["username"], bool(user["is_admin"]),
                     user_service.get_exec_grant(uid))


def transfer_current_session(session_id: Optional[str], ctx: UserCtx) -> bool:
    """把当前default档会话过户给目标身份；无会话或已归属他人时不处理"""
    if not session_id or ctx.is_default:
        return False

    return session_service.transfer_session(session_id, ctx.uid, DEFAULT_USER_ID) > 0
