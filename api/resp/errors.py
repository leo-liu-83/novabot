"""后端错误码注册表。

为什么要有 code：Web/TUI 的文案需要能本地化，但错误消息里混着两类东西——
固定提示（"会话不存在"）与原始异常文本（某个库抛出的具体原因）。
前者可以翻译，后者翻不了也不该猜。所以固定提示一律带稳定 code（前端按 code 翻译），
原始异常保持 message 原文，不带 code。

模型输出（assistant 文本、工具输出）永远不走这里，也永远不翻译。
"""

from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException

# code -> (默认 HTTP 状态, 中文兜底消息)
ERRORS: Dict[str, Tuple[int, str]] = {
    # 认证/账号
    "auth.token_missing": (401, "缺少登录凭证"),
    "auth.token_invalid": (401, "登录状态无效或已过期"),
    "auth.admin_required": (403, "需要管理员权限"),
    "auth.register_blocked": (403, "尚未创建管理员账号，请先完成首次初始化"),
    "auth.multi_user_required": (403, "当前是单用户模式，Web 控制台需要多用户模式"),
    "auth.cross_origin_blocked": (403, "请求来源与本站不一致，已拒绝创建管理员"),
    "auth.no_account": (400, "当前是匿名/单用户身份，没有可操作的账号"),
    "auth.password_wrong": (401, "密码错误"),
    "auth.username_taken": (409, "用户名已被占用"),
    "auth.username_invalid": (400, "用户名格式不正确"),
    "auth.password_too_short": (400, "密码长度不符合要求"),
    "auth.password_mismatch": (400, "两次输入的密码不一致"),
    "auth.admin_exists": (409, "管理员账号已存在，首个管理员不可顶替"),
    "auth.invalid_input": (400, "账号信息不符合要求"),
    "auth.avatar_invalid": (400, "头像必须是 2 MB 以内的有效图片"),
    "persona.unavailable": (403, "当前账号没有 SOUL.md，人格页不可用"),
    "persona.avatar_invalid": (400, "NovaBot 头像必须是 2 MB 以内的有效图片"),
    "persona.memory_not_found": (404, "记忆文件不存在或无权访问"),
    "auth.last_admin_delete": (409, "你是最后一名管理员，请先指定另一位管理员再注销"),

    # 管理端
    "admin.user_not_found": (404, "账号不存在"),
    "admin.last_admin_demote": (409, "最后一名管理员不能被降级；请先把另一个账号设为管理员"),
    "admin.last_admin_delete": (409, "最后一名管理员不能被删除；请先把另一个账号设为管理员"),
    "admin.ceiling_cap": (409, "超过服务封顶 T{cap}，不能授予 T{level}"),

    # 项目
    "project.disabled": (403, "当前账号未开通项目功能，请联系管理员"),
    "project.not_found": (404, "项目不存在或无权访问"),
    "project.name_taken": (409, "已有同名项目"),
    "project.name_invalid": (400, "项目名称不能为空且不超过 {max} 个字符"),
    "project.description_invalid": (400, "项目描述不能超过 {max} 个字符"),
    "project.path_invalid": (400, "项目路径不可用：{reason}"),
    "project.path_forbidden": (403, "不能把该目录登记为项目：{path}"),
    "project.path_nested": (409, "该目录与已有项目目录重叠"),
    "project.path_allowed_roots": (
        403, "Web 端只能登记 workspace 与配置的额外根目录下的项目：{path}"),
    "project.path_unavailable": (409, "项目目录已失效或被移动到别处：{path}"),
    "project.archived": (409, "项目已归档"),
    "project.agents_exists": (409, "项目根目录已有 AGENTS.md，不会覆盖"),
    "project.agents_invalid": (400, "AGENTS.md 内容为空或超过 {max} KB"),
    "project.agents_too_large": (413, "AGENTS.md 超过 {max} KB，请在外部编辑"),
    "project.agents_read_failed": (500, "读取 AGENTS.md 失败，请检查目录权限或编码"),
    "project.agents_write_failed": (500, "写入 AGENTS.md 失败，请检查目录权限"),
    "project.agents_delete_failed": (500, "删除 AGENTS.md 失败，请检查目录权限"),
    "project.agents_generate_failed": (502, "模型生成 AGENTS.md 失败，请稍后重试或手工编写"),

    # 会话与附件
    "session.not_found": (404, "会话不存在或无权访问"),
    "session.invalid_level": (400, "无法识别的执行档位"),
    "session.deleting": (404, "会话不存在、正在删除或无权访问"),
    "session.project_transfer_forbidden": (409, "只有尚未归属项目的会话能转移进项目"),
    "session.project_transfer_busy": (409, "会话正在执行，等这一轮结束后再转移"),
    "session.access_denied": (400, "会话标识无效：session_id 必须由服务端签发且属于当前账号。留空表示新建会话。"),
    "session.create_blocked": (409, "该账号正在注销或已注销，不能再创建会话"),
    "attachment.upload_limit": (413, "单个附件上限 {mb}MB"),
    "attachment.too_large": (413, "文件过大：{size}，上限 {limit}"),
    "attachment.too_many": (400, "一条消息最多 {limit} 个附件"),
    "attachment.type_denied": (415, "不支持该附件类型"),
    "attachment.empty": (415, "空文件不能作为附件"),
    "attachment.mime_mismatch": (415, "上传 MIME 与文件扩展名不匹配：{declared} / {expected}"),
    "attachment.id_invalid": (400, "附件 ID 无效"),
    "attachment.not_found": (404, "附件不存在或不属于当前会话"),
    "attachment.signature_mismatch": (415, "文件内容与扩展名不匹配：{name}"),
    "attachment.text_binary": (415, "文件看起来是二进制内容，不作为文本附件接收：{name}"),
    "attachment.text_encoding": (415, "文本附件必须是 UTF-8 编码：{name}"),
    "attachment.invalid": (400, "附件未通过校验"),
    "attachment.missing": (404, "附件不存在"),
    "attachment.referenced": (409, "该附件已被会话消息引用，不能作为未发送附件删除"),

    # MCP
    "mcp.server_not_found": (404, "MCP server 不存在"),
    "mcp.account_not_found": (404, "账号不存在"),

    # 搜索 provider
    "provider.not_found": (404, "provider 不存在"),

    # 模型目录 / 默认模型
    "model.not_found": (404, "当前模型目录里没有这个模型：{model}"),
    "model.role_not_found": (404, "没有这个子代理角色：{role}"),
    "model.config_invalid": (409, "用户配置文件无法解析，请先修好 novabot.json 再设置默认模型"),
    "model.write_failed": (500, "写入 novabot.json 失败"),

    # 技能审查
    "skill.not_found": (404, "技能不存在或无权查看"),
    "skill.scan_forbidden": (403, "当前身份不能扫描这个技能"),
    "skill.release_forbidden": (403, "当前身份不能放行这个技能"),
    "skill.reject_forbidden": (403, "当前身份不能拒绝这个技能"),
    "skill.dangerous": (409, "该技能扫描出危险项，只有管理员能放行"),
    "skill.upload_forbidden": (403, "当前身份不能上传到该技能目录"),
    "skill.package_invalid": (400, "技能包格式不正确"),
    "skill.package_too_large": (413, "技能包过大或解压后文件过多"),
    "skill.exists": (409, "同名技能已存在"),
    "skill.file_not_found": (404, "技能文件不存在或无权读取"),

    # 聊天事件（系统提示，不是模型输出）
    "chat.deleting": (409, "会话正在删除或已不存在，请刷新会话列表。"),
    "chat.compact_unavailable": (400, "当前 agent 未启用摘要中间件，无法手动压缩"),
    "chat.compact_failed": (400, "上下文压缩失败"),
    "chat.gate_rejected": (400, "发送前校验未通过，请拆分或缩短内容"),
}


def api_error(code: str, *, status_code: Optional[int] = None,
              message: Optional[str] = None, **vars: Any) -> HTTPException:
    """构造带 code 的 HTTP 异常。

    message 显式传入时优先（例如把底层异常文本作为补充），否则用注册表里的中文兜底。
    vars 会一起下发，前端用同一组变量翻译。
    """
    default_status, text = ERRORS.get(code, (400, code))
    if message is not None:
        rendered = message
    else:
        rendered = text.format(**vars) if vars else text
    return HTTPException(
        status_code=status_code or default_status,
        detail={"error": code, "message": rendered, "vars": vars},
    )
