# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : messages.py
# @Software    : VSCode
# @Datetime    : 2026/04/19 20:52:36
# @Author      : leo liu
# @Version     : 1.0
# @Description :

class MessageCn:
    # 基础异常消息
    MSG_VIEW_ERROR: str = "路由异常"
    MSG_SERVICE_ERROR: str = "服务异常"
    MSG_CORE_ERROR: str = "核心逻辑异常"
    MSG_PLUGINS_ERROR: str = "插件异常"
    MSG_UTILS_ERROR: str = "公共组件异常"

    # 路由相关异常消息
    MSG_VIEW_PARAMS_ERROR: str = "入参异常"
    MSG_VIEW_TOKEN_ERROR: str = "Token异常"

    # 模型相关异常消息
    MSG_MODEL_PROP_ERROR: str = "模型属性定义异常"
    MSG_MODEL_CALL_ERROR: str = "模型请求异常"
    MSG_PROMPT_ERROR: str = "提示词定义异常"

    # 配置相关异常消息
    MSG_CONFIG_FILE_ERROR: str = "配置文件定义异常"

    # 用户认证相关异常消息
    MSG_AUTH_ERROR: str = "认证异常"
    MSG_AUTH_USERNAME_TAKEN: str = "用户名已被占用"
    MSG_AUTH_USERNAME_INVALID: str = "用户名需为1-32个字符"
    MSG_AUTH_PASSWORD_INVALID: str = "密码错误"
    MSG_AUTH_PASSWORD_TOO_SHORT: str = "密码至少6个字符"
    MSG_AUTH_PASSWORD_MISMATCH: str = "两次输入的密码不一致"
    MSG_AUTH_TOKEN_INVALID: str = "登录状态无效或已过期"
    MSG_AUTH_TOKEN_MISSING: str = "缺少登录凭证"
    MSG_AUTH_ADMIN_EXISTS: str = "管理员账号已存在，首个管理员不可顶替"
    MSG_AUTH_ADMIN_REQUIRED: str = "尚未创建管理员账号，请先在本机完成管理员初始化"

    # 脚本执行相关（T1档run_skill_script）；这些会回给模型，故写明下一步该怎么做
    MSG_EXEC_SKILL_NAME_REQUIRED: str = "未提供技能名。请传入 skill 参数，形如 my-skill。"
    MSG_EXEC_SKILL_NAME_INVALID: str = "技能名非法：只能是单段名称，不能含 / 或 \\ 。请只传技能目录名本身。"
    MSG_EXEC_SKILL_NOT_FOUND: str = "找不到该技能，或它没有 scripts/ 目录。请先用 ls 查看 /skills/ 下可用的技能。"
    MSG_EXEC_SCRIPT_PATH_REQUIRED: str = "未提供脚本路径。请传入相对 scripts/ 目录的路径。"
    MSG_EXEC_SCRIPT_SUFFIX: str = "T1档只允许运行 .py 脚本，其他类型的脚本不可执行。"
    MSG_EXEC_SCRIPT_OUTSIDE: str = "脚本路径越界：必须位于该技能的 scripts/ 目录内，不允许 .. 或符号链接指向外部。"
    MSG_EXEC_SCRIPT_NOT_FOUND: str = "该技能的 scripts/ 目录下不存在这个脚本。请先用 ls 查看实际有哪些脚本。"
    MSG_EXEC_SKILL_UNREVIEWED: str = "技能「{skill}」尚未通过安全审查，不能自动执行它的脚本。请让用户在 TUI 里用 /skill-review {skill} 审阅并批准，或改用其他方式完成任务。"
    MSG_EXEC_SKILL_STALE: str = "技能「{skill}」的内容在上次审查后发生过变化，需要重新审查。请让用户用 /skill-review {skill} 重新审阅。"

    # 会话标识（键由服务端签发，见 E10.6/E10.7）
    MSG_SESSION_NOT_OWNED: str = "会话标识无效：session_id 必须由服务端签发且属于当前账号。留空表示新建会话。"

    # checkpoint 降级（E10.6/E10.7：降级必须可见）
    MSG_PERSISTENCE_MEMORY_MODE: str = "上下文未持久化：checkpoint 不可用，本轮状态只在内存里，重启会丢失对话状态。"
    MSG_PERSISTENCE_MEMORY_SHORT: str = "上下文未持久化，重启会丢失对话状态。"

class MessageEn:
    # 基础异常消息
    MSG_VIEW_ERROR: str = "Router Error"
    MSG_SERVICE_ERROR: str = "Service Error"
    MSG_CORE_ERROR: str = "Core Logic Error"
    MSG_PLUGINS_ERROR: str = "Plugin Logic Error"
    MSG_UTILS_ERROR: str = "Utils Logic Error"

    # 路由相关异常消息
    MSG_VIEW_PARAMS_ERROR: str = "Params Error"
    MSG_VIEW_TOKEN_ERROR: str = "Token Error"

    # 模型相关异常消息
    MSG_MODEL_PROP_ERROR: str = "Model Properties Error"
    MSG_MODEL_CALL_ERROR: str = "Model Request Error"
    MSG_PROMPT_ERROR: str = "Prompt Define Error"

    # 配置相关异常消息
    MSG_CONFIG_FILE_ERROR: str = "Config Files Error"

    # 脚本执行相关（T1档run_skill_script）；这些会回给模型，故写明下一步该怎么做
    MSG_EXEC_SKILL_NAME_REQUIRED: str = "No skill name given. Pass the skill argument, e.g. skill=my-skill."
    MSG_EXEC_SKILL_NAME_INVALID: str = "Invalid skill name: it must be a single path segment with no / or \\ . Pass just the skill directory name."
    MSG_EXEC_SKILL_NOT_FOUND: str = "That skill was not found, or it has no scripts/ directory. Use ls to list the skills under /skills/."
    MSG_EXEC_SCRIPT_PATH_REQUIRED: str = "No script path given. Pass a path relative to the scripts/ directory."
    MSG_EXEC_SCRIPT_SUFFIX: str = "Tier T1 only runs .py scripts; other script types cannot be executed."
    MSG_EXEC_SCRIPT_OUTSIDE: str = "Script path escapes its skill: it must stay inside that skill's scripts/ directory, with no .. or symlinks pointing outside."
    MSG_EXEC_SCRIPT_NOT_FOUND: str = "That script does not exist in the skill's scripts/ directory. Use ls to see the actual scripts."
    MSG_EXEC_SKILL_UNREVIEWED: str = "Skill {skill} has not passed security review, so its scripts cannot be auto-executed. Ask the user to review and approve it in the TUI with /skill-review {skill}."
    MSG_EXEC_SKILL_STALE: str = "Skill {skill} has changed since it was last reviewed and must be reviewed again. Ask the user to run /skill-review {skill}."

    # 用户认证相关异常消息
    MSG_AUTH_ERROR: str = "Auth Error"
    MSG_AUTH_USERNAME_TAKEN: str = "Username Already Taken"
    MSG_AUTH_USERNAME_INVALID: str = "Username Must Be 1-32 Characters"
    MSG_AUTH_PASSWORD_INVALID: str = "Wrong Password"
    MSG_AUTH_PASSWORD_TOO_SHORT: str = "Password Needs At Least 6 Characters"
    MSG_AUTH_PASSWORD_MISMATCH: str = "Passwords Do Not Match"
    MSG_AUTH_TOKEN_INVALID: str = "Invalid or Expired Token"
    MSG_AUTH_TOKEN_MISSING: str = "Missing Credentials"
    MSG_AUTH_ADMIN_EXISTS: str = "Admin Already Exists"
    MSG_AUTH_ADMIN_REQUIRED: str = "No Admin Account Yet"

    # 会话标识（键由服务端签发，见 E10.6/E10.7）
    MSG_SESSION_NOT_OWNED: str = "Invalid session_id: it must be one this server issued to the current account. Leave it empty to start a new session."

    # checkpoint 降级（E10.6/E10.7：降级必须可见）
    MSG_PERSISTENCE_MEMORY_MODE: str = "Context is not persisted: the checkpoint store is unavailable, so this turn lives only in memory and a restart loses it."
    MSG_PERSISTENCE_MEMORY_SHORT: str = "Context is not persisted; a restart will lose the conversation state."

msg = MessageCn()
