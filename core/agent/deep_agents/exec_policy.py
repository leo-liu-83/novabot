# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : exec_policy.py
# @Software    : VSCode
# @Datetime    : 2026/10/13 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 脚本执行档位：解析、生效判定与后端选择

from enum import Enum, IntEnum
import os
import re
import threading
import shlex
from typing import TYPE_CHECKING, Optional, Union

from enums import AgentBackendTypeEnum

if TYPE_CHECKING:
    # 仅用于类型标注；运行时导入会与config形成环（base_config需要本模块的parse_level）
    from service.user.user_service import UserCtx

class ExecLevel(IntEnum):
    """脚本执行档位，语义见 TODO.md §3"""
    OFF = 0  # 关闭：execute 不出现在模型工具列表
    SKILL_ONLY = 1  # 仅技能声明的脚本
    READ_ONLY = 2  # 只读命令免确认
    CONFIRM_ALL = 3  # 任意命令逐条确认
    ALLOW_ALL = 4  # 任意命令免确认

class ExecDecision(Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"

# 当前版本真正实现了判定逻辑的最高档位。未实现的档位一律按OFF收敛，
# 避免用户配了T4却因为判定逻辑缺失而意外拿到执行能力。
# E3起抬到SKILL_ONLY：T1已具备完整判定（run_skill_script的路径/类型/越界校验）。
# E4起抬到READ_ONLY：T2的白名单判定已实现。
# E5起抬到ALLOW_ALL：T3/T4的逐条确认/免确认与硬地板均已实现。
IMPLEMENTED_MAX = ExecLevel.ALLOW_ALL

# ── 硬黑名单（§6.1）────────────────────────────────────────────────
# 学Hermes：在YOLO之下，任何档位、任何规则都盖不住，无override flag。
# 这是"地板"不是"边界"——§14.3已声明它必然可被混淆绕过（如 c=rm; $c -rf /），
# 它的作用是挡住手滑与低水平破坏，不是提供安全保证。

# 一旦执行就不可逆或直接摧毁系统的程序
_HARD_DENY_PROGRAMS = frozenset({
    "mkfs", "fdisk", "parted", "sgdisk", "shred", "wipefs",
    "shutdown", "reboot", "halt", "poweroff", "init", "kexec",
})

# 设备节点前缀：写入这些等同毁盘
_DEVICE_PREFIXES = ("/dev/sd", "/dev/hd", "/dev/vd", "/dev/nvme", "/dev/disk", "/dev/xvd")

# rm/chmod 的递归+强制参数。短选项组合与大小写不限，长选项单独识别。
_RECURSIVE_FORCE_RE = re.compile(r"^-[a-zA-Z]*(r|R)[a-zA-Z]*$|^-[a-zA-Z]*[fF][a-zA-Z]*$")
_RECURSIVE_LONG_FLAGS = frozenset({
    "--recursive", "--force", "--no-preserve-root", "--preserve-root=all",
})
_DESTRUCTIVE_TARGETS = frozenset({"/", "/*", "~", "~/", "~/*", "/etc", "/usr", "/var", "/bin",
                                 "/home", "/root", "/boot", "/lib", "/dev", "/sys", "/proc",
                                 # shlex不做变量展开，$HOME会原样成为token
                                 "$HOME", "${HOME}", "~/..", "/./"})

# fork bomb 的典型形态：:(){ :|:& };:
_FORK_BOMB_RE = re.compile(r"(\(\)\s*\{\s*:\|:&\s*\}\s*;?\s*:)")

# 必须靠原始文本识别的形态（切分后语义已丢）。这些无条件扫描。
_HARD_DENY_TEXT_ALWAYS = (
    _FORK_BOMB_RE,
    re.compile(r"\bdd\b[^|;&]*\bof\s*=\s*/dev/"),          # dd ... of=/dev/xxx
    re.compile(r">\s*/dev/(sd|hd|vd|nvme|disk|xvd)"),        # 重定向进设备
    re.compile(r"\bmkfs\w*\b"),
)

# 这条形态要按原始文本查才能看见藏进引号里的写法，但只对"会把参数当代码执行"的
# 程序扫——否则 echo 'rm -rf / carefully' 这类正当的文档写作会被误杀。
# 藏进解释器参数里的形态（sh -c 'rm -rf /'）照常命中，因为sh在_TEXT_EXECUTORS里。
_HARD_DENY_TEXT_IN_EXECUTOR = (
    re.compile(r"\brm\s+-[a-zA-Z]*(?:[rR][a-zA-Z]*[fF]?|[fF][a-zA-Z]*[rR]?)\s+"
               r"(?:-\S+\s+)*(?:\/|~|\$HOME)(?:\s|\/|\*|;|\||$)"),
)

# ── inline-eval 强制确认（§6.2）────────────────────────────────────
# python -c "任意代码" 能绕过一切字符串规则，两家（OpenClaw的strictInlineEval、
# Hermes的审批清单）都把它单列。这里一律强制ASK，白名单不能豁免。
_INLINE_EVAL_FLAGS = {
    "python": ("-c",), "python2": ("-c",), "python3": ("-c",),
    "node": ("-e", "--eval", "-p", "--print"),
    "ruby": ("-e",), "perl": ("-e",), "php": ("-r",),
    "sh": ("-c",), "bash": ("-c",), "zsh": ("-c",), "dash": ("-c",), "ksh": ("-c",),
    "eval": (),
}

# T2只读白名单。值None表示不校验子命令，否则要求首个非选项参数落在该集合内。
#
# 刻意排除（依据见TODO.md §6.3）：
#   find      —— Hermes明确点出 `-exec rm` / `-delete` 可造成破坏
#   python3/node/ruby/bash/sh —— OpenClaw规定解释器一律不入自动放行名单
_READ_ONLY_PROGRAMS: dict[str, Optional[frozenset]] = {
    "ls": None,
    "cat": None,
    "grep": None,
    "wc": None,
    "head": None,
    "tail": None,
    "pwd": None,
    "git": frozenset({"status", "diff", "log"}),
}

# git的这些全局选项能改变行为或指向任意仓库，等价于跳出白名单语义
_GIT_DENIED_FLAGS = frozenset({
    "-c", "--exec-path", "--git-dir", "--work-tree",
    "--namespace", "--super-prefix", "--config-env",
})

# 这些选项的值是文件路径（读取来源）。等号写法与分离写法都要校验，
# 否则 `grep -f /etc/passwd x`、`wc --files0-from=/etc/passwd` 会绕过路径收敛。
_FILE_VALUE_FLAGS: dict[str, frozenset] = {
    "grep": frozenset({"-f", "--file"}),
    "wc": frozenset({"--files0-from"}),
}

# 这些选项的值是模式而不是路径：`grep -e /usr/bin f` 是合法用法，不能误判成路径逃逸
_PATTERN_VALUE_FLAGS: dict[str, frozenset] = {
    "grep": frozenset({"-e", "--regexp"}),
}

# shell控制符：既是T2拒绝串联的依据，也是T3/T4切分命令段的依据
_CONTROL_TOKENS = frozenset({
    ";", ";;", "&", "&&", "|", "||", "|&",
    "<", ">", ">>", "<<", "(", ")", "<(", ">(",
})

_LEVEL_ALIASES = {
    "T0": ExecLevel.OFF,
    "OFF": ExecLevel.OFF,
    "T1": ExecLevel.SKILL_ONLY,
    "SKILL_ONLY": ExecLevel.SKILL_ONLY,
    "T2": ExecLevel.READ_ONLY,
    "READ_ONLY": ExecLevel.READ_ONLY,
    "T3": ExecLevel.CONFIRM_ALL,
    "CONFIRM_ALL": ExecLevel.CONFIRM_ALL,
    "T4": ExecLevel.ALLOW_ALL,
    "ALLOW_ALL": ExecLevel.ALLOW_ALL,
}

def parse_level(raw: Optional[Union[str, int]]) -> ExecLevel:
    """解析配置里的档位。无法识别时回落OFF——判不出来就按最严处理"""
    if isinstance(raw, ExecLevel):
        return raw

    if isinstance(raw, int):
        try:
            return ExecLevel(raw)
        except ValueError:
            return ExecLevel.OFF

    if isinstance(raw, str):
        return _LEVEL_ALIASES.get(raw.strip().upper(), ExecLevel.OFF)

    return ExecLevel.OFF

# ── 天花板与默认档位（§7）────────────────────────────────────────
# 两个值分工不同：**天花板**管"最高能到哪一档"（除匿名外都由 admin 授予控制），
# **默认档位**管"用户没自提时一开始在哪一档"。改天花板不动当前档位。
#
# 天花板：单用户模式坐在机器前的是机主本人，可达 T4（与 admin 同级）。
SINGLE_USER_CEILING = ExecLevel.ALLOW_ALL
# 多用户下的匿名者：匿名区是公共的，只给技能脚本
ANONYMOUS_CEILING = ExecLevel.SKILL_ONLY
# 非 admin 账号**未获授权**时的天花板：T1（技能脚本）。
# admin 可用 /exec-limit 逐人上浮，上限 = 服务封顶（config.exec.level）
NON_ADMIN_CEILING_DEFAULT = ExecLevel.SKILL_ONLY

# 默认档位（用户未用 /exec 自提时生效）
SINGLE_USER_DEFAULT = ExecLevel.SKILL_ONLY  # 单用户机主：默认 T1
ANONYMOUS_DEFAULT = ExecLevel.OFF           # 多用户匿名：默认 T0（匿名不自动获得执行）
ADMIN_DEFAULT = ExecLevel.CONFIRM_ALL       # admin：默认 T3（任意命令·逐条确认）
USER_DEFAULT = ExecLevel.OFF                # 非 admin 账号：默认 T0（要自己提到授权档）


def identity_ceiling(ctx: "UserCtx") -> ExecLevel:
    """身份天花板（可达上限），见 TODO.md §7。

    - admin：T4，完整能力
    - 非admin登录账号：默认 T1（E6 的核心是切断"自助注册→建私有技能→自动执行"链；
      给到 T1 后仍需 admin 用 /exec-limit 上浮才能用 shell）。
      admin 授予值即天花板；未授予时取 NON_ADMIN_CEILING_DEFAULT
    - default身份：多用户下是匿名者（T1，匿名区公共）；单用户下是机主本人（T4）。
      两者由ctx.default_ceiling带入——本模块是叶子（config反过来引它），不能自己读配置

    注意：本函数只给身份**固有**天花板；服务封顶（config.exec.level）由
    `effective_ceiling` / `resolve_level` 叠加，故这里可以返回高于配置的值。
    """
    if ctx.is_default:
        return ctx.default_ceiling

    if ctx.is_admin:
        return ExecLevel.ALLOW_ALL

    return ctx.exec_ceiling if ctx.exec_ceiling is not None else NON_ADMIN_CEILING_DEFAULT


def effective_ceiling(ctx: "UserCtx", service_cap: ExecLevel) -> ExecLevel:
    """对外可见/可授的天花板 = min(身份天花板, 服务封顶, 已实现上限)。

    服务封顶（config.exec.level）限定"整个部署最高到哪一档"：`exec.level=T3` 时
    admin 最多也只能授予 T3，T4 在弹窗里根本不出现（§5.6）。
    """
    # config.EXEC_LEVEL 正常是 ExecLevel，但测试/插件可能塞字符串；统一解析后再比较
    return min(identity_ceiling(ctx), parse_level(service_cap), IMPLEMENTED_MAX)


def effective_level(requested: ExecLevel, ctx: "UserCtx",
                    service_cap: Optional[ExecLevel] = None) -> ExecLevel:
    """生效档位 = min(请求档位, 身份天花板, 服务封顶, 已实现上限)

    档位是范围上限，规则无法越过它（TODO.md §4）。
    service_cap 为 None 时表示调用方未提供服务封顶，不参与收敛。
    """
    level = min(requested, identity_ceiling(ctx), IMPLEMENTED_MAX)
    if service_cap is not None:
        level = min(level, parse_level(service_cap))

    return level

def backend_for_level(level: ExecLevel) -> AgentBackendTypeEnum:
    """档位决定后端。

    T1用FILESYSTEM——该后端无execute方法，deepagents因此不会向模型宣告execute工具，
    T1要的正是这种结构性不可达。T2起才需要shell（ls/grep等只读命令本身就是二进制）。
    """
    if level < ExecLevel.READ_ONLY:
        return AgentBackendTypeEnum.FILESYSTEM

    return AgentBackendTypeEnum.LOCAL_SHELL

# ── 会话级覆盖（§10）────────────────────────────────────────────────
# /exec level=T3 只在本会话生效，不写配置文件。进程内保存即可：
# 覆盖本来就不该跨重启存活，写盘反而制造"当前为什么是这个档"难以审计的问题。
_SESSION_OVERRIDES: dict[tuple, ExecLevel] = {}
_SESSION_LOCK = threading.Lock()


def set_session_override(uid: str, session_id: str, level: Optional[ExecLevel]) -> None:
    """设置/清除某身份的某会话档位覆盖。level传None表示回到身份默认档位。"""
    with _SESSION_LOCK:
        if level is None:
            _SESSION_OVERRIDES.pop((uid, session_id), None)
        else:
            _SESSION_OVERRIDES[(uid, session_id)] = level


def get_session_override(uid: str, session_id: str) -> Optional[ExecLevel]:
    with _SESSION_LOCK:
        return _SESSION_OVERRIDES.get((uid, session_id))


def clear_all_session_overrides() -> None:
    with _SESSION_LOCK:
        _SESSION_OVERRIDES.clear()


def resolve_level(service_cap: ExecLevel, ctx: "UserCtx",
                  session_id: Optional[str] = None,
                  *, project_mode: bool = False) -> ExecLevel:
    """带会话覆盖的生效档位。

    - 没有会话覆盖：用该身份的**默认档位**（ctx.default_level）
    - 有会话覆盖：用覆盖值（用户 `/exec level=` 自提的当前档位）

    两条路都要过身份天花板与服务封顶的收敛——覆盖不能突破任何一道封顶。
    service_cap 传入 config.EXEC_LEVEL，即本部署的服务级封顶。
    """
    base = ctx.default_level
    if session_id:
        override = get_session_override(ctx.uid, session_id)
        if override is not None:
            base = override
    elif project_mode:
        # 项目会话的默认档位封顶到 T3：T4 只能由用户显式 /exec 提到，
        # 语义也从“任意命令免确认”收窄为“项目内免确认”（spec §10.4）
        base = min(base, ExecLevel.CONFIRM_ALL)

    return effective_level(base, ctx, service_cap)


RISK_TEXT = {
    ExecLevel.OFF: "不执行任何命令",
    ExecLevel.SKILL_ONLY: "只能运行你已安装技能中 scripts/ 目录下的 Python 脚本。脚本行为由技能作者决定",
    ExecLevel.READ_ONLY: "agent 可读取文件内容与仓库状态，无法修改任何东西",
    ExecLevel.CONFIRM_ALL: "可在你的机器上执行任意命令，但每条都需你确认",
    ExecLevel.ALLOW_ALL: "等同于把你的用户权限交给 agent，误操作不可挽回",
}

def is_shell_enabled(level: ExecLevel) -> bool:
    """execute工具是否开放（T2+）。

    T1刻意不开放：它的执行通道是无shell的run_skill_script，不是execute。
    """
    return level >= ExecLevel.READ_ONLY

def is_skill_script_enabled(level: ExecLevel) -> bool:
    """run_skill_script是否开放——恰好T1，与T2+互斥。

    这里用相等而非>=：T1与T2+是两条不相交的通道，不是渐进放开的同一能力。
    T2若也提供它，一个Python脚本就能改任何东西，与"无法修改任何东西"的风险文案相悖。
    """
    return level == ExecLevel.SKILL_ONLY

def parse_command(command: str) -> Optional[list]:
    """把命令切成token；无法安全解析时返回None。

    换行符必须在切分前拦掉：shlex把`\\n`当普通空白，
    `ls\\nrm -rf /`会被切成['ls','rm','-rf','/']而看起来像一条无害的ls。
    """
    text = command or ""
    if any(ch in text for ch in ("\n", "\r", "\x00")):
        return None

    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        # 引号不闭合等语法错误
        return None

def is_read_only_command(command: str, workspace: Optional[str] = None) -> bool:
    """T2白名单判定：单条简单只读命令、且路径落在工作区内才放行。

    规则是"fail closed"：解析不出干净结构、控制符出现、程序不在白名单、
    路径参数逃出工作区，一律False。
    不做去混淆（Hermes那套）——T2根本不接受任何链接/替换/重定向，没有可绕的面。

    路径收敛（§14.6）：T2的读范围必须与文件工具一致。`cat ~/.ssh/id_rsa`
    曾能绕过FilesystemPermission——execute跑的是命令，参数路径不经过后端根校验，
    所以在这里补上：非选项参数、以及 `-f`/`--files0-from` 这类读文件选项的值，
    解析成真实路径后必须仍在该工作区内（软链与`..`都会先被realpath消解）。
    绝对路径与`~`展开一律拒绝，不依赖执行层是否展开。
    """
    tokens = _read_only_tokens(command)
    if tokens is None:
        return False

    return not _escaped_paths(tokens, workspace)


def is_read_only_path_escape(command: str, workspace: Optional[str] = None) -> bool:
    """结构上是T2白名单命令，但有路径参数落到工作区外。

    中间件用它区分提示语：命令写对了、只是读的范围越界，不能用"只放行"的文案。
    """
    tokens = _read_only_tokens(command)
    return tokens is not None and bool(_escaped_paths(tokens, workspace))


def _read_only_tokens(command: str) -> Optional[list]:
    """白名单的结构判定（程序、控制符、git子命令），不含路径范围。"""
    tokens = parse_command(command)
    if not tokens:
        return None

    if any(tok in _CONTROL_TOKENS for tok in tokens):
        return None

    # 命令替换残留：反引号不会被punctuation_chars拆开，需按字符查
    if any("`" in tok or "$" in tok for tok in tokens):
        return None

    program = tokens[0]
    allowed_sub = _READ_ONLY_PROGRAMS.get(program, "missing")
    if allowed_sub == "missing":
        return None

    if allowed_sub is not None and not _git_subcommand_allowed(program, tokens[1:]):
        return None

    return tokens


def _escaped_paths(tokens: list, workspace: Optional[str]) -> list:
    """返回所有落到工作区外的路径参数；空列表表示全部收敛。"""
    root = os.path.realpath(os.path.abspath(workspace or "."))
    return [tok for tok in _path_arguments(tokens[0], tokens[1:])
            if not _confined_to(tok, root)]


def _confined_to(token: str, root: str) -> bool:
    # `~` 不依赖执行层是否展开：即使当前不展开，也不给"换个shell就成立"的歧义
    if token.startswith("~"):
        return False

    if os.path.isabs(token):
        return False

    try:
        real = os.path.realpath(os.path.join(root, token))
    except (OSError, ValueError):
        # realpath失败（非法字节/软链环）按不放行处理
        return False

    return real == root or real.startswith(root + os.sep)


def _path_arguments(program: str, rest: list) -> list:
    """从参数里挑出"会被当作路径读取"的token。

    - 普通程序：所有非选项参数
    - grep：首个非选项参数是模式；`-e/--regexp` 的值也是模式；`-f/--file` 的值是路径
    - 其他带文件值选项的程序（wc --files0-from）按映射处理
    """
    file_flags = _FILE_VALUE_FLAGS.get(program, frozenset())
    pattern_flags = _PATTERN_VALUE_FLAGS.get(program, frozenset())
    is_grep = program == "grep"

    paths: list = []
    pending: Optional[str] = None  # "path" / "pattern"
    pattern_seen = False

    for tok in rest:
        if pending == "path":
            paths.append(tok)
            pending = None
            continue
        if pending == "pattern":
            pending = None
            continue

        if tok.startswith("-") and tok != "-":
            name, sep, value = tok.partition("=")
            if name in file_flags:
                if sep:
                    paths.append(value)
                else:
                    pending = "path"
                if is_grep:
                    pattern_seen = True
                continue
            if name in pattern_flags:
                pattern_seen = True
                if not sep:
                    pending = "pattern"
                continue
            continue

        if is_grep and not pattern_seen:
            pattern_seen = True
            continue

        paths.append(tok)

    return paths

def _git_subcommand_allowed(program: str, rest: list) -> bool:
    """git只认status/diff/log，且拒绝能改变行为或指向任意仓库的全局选项"""
    if program != "git":
        return False

    skip_next = False
    for tok in rest:
        if skip_next:
            skip_next = False
            continue
        # --exec-path=/tmp 这类"="写法要按=前的名字比对，否则绕过
        name = tok.split("=", 1)[0]
        if name in _GIT_DENIED_FLAGS:
            return False
        if name in ("-C", "-c"):
            skip_next = True
            continue
        if tok.startswith("-"):
            continue

        return tok in _READ_ONLY_PROGRAMS["git"]

    return False

def split_segments(tokens: list) -> list:
    """按控制符把token序列切成一条条命令段。

    T3/T4允许串联，所以黑名单与inline-eval必须逐段检查——
    只看首个token会漏掉 `ls && rm -rf /`。
    """
    segments: list = []
    current: list = []

    for tok in tokens:
        if tok in _CONTROL_TOKENS:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(tok)

    if current:
        segments.append(current)

    return segments

def is_hard_denied(command: str) -> bool:
    """硬黑名单判定。任何档位都拒绝，无override（§6.1）。"""
    text = command or ""
    tokens = parse_command(text)
    segments = split_segments(tokens) if tokens else []

    for pattern in _HARD_DENY_TEXT_ALWAYS:
        if pattern.search(text):
            return True

    # 只对"会把参数当代码执行"的程序扫跨引号的rm形态
    if any(_can_execute_text(os.path.basename(seg[0])) for seg in segments if seg):
        for pattern in _HARD_DENY_TEXT_IN_EXECUTOR:
            if pattern.search(text):
                return True

    if not tokens:
        # 切不出结构说明有引号未闭合之类的畸形输入，交给下游不可能安全执行
        return bool(text.strip())

    return any(_segment_hard_denied(seg) for seg in segments)

# 只有这些程序会把参数当代码/命令再执行一遍，原始文本模式才对它们有意义
_TEXT_EXECUTORS = frozenset({
    "sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh", "fish", "busybox",
    "python", "python2", "python3", "node", "nodejs", "perl", "ruby", "php",
    "awk", "gawk", "mawk", "find", "xargs", "env", "sudo", "nohup",
    "timeout", "watch", "eval", "source", "make",
})


def _can_execute_text(program: str) -> bool:
    return program in _TEXT_EXECUTORS

def _segment_hard_denied(segment: list) -> bool:
    if not segment:
        return False

    program = os.path.basename(segment[0])
    rest = segment[1:]

    if program in _HARD_DENY_PROGRAMS:
        return True

    if program == "rm" and _destructive_target(rest):
        return True

    if program == "chmod" and _destructive_target(rest):
        return True

    return any(_touches_device(tok) for tok in segment)

def _destructive_target(args: list) -> bool:
    """rm/chmod 是否带递归或强制参数，且目标指向系统根或家目录"""
    recursive = any(_RECURSIVE_FORCE_RE.match(a) for a in args if a.startswith("-") and not a.startswith("--"))
    recursive = recursive or any(a.split("=", 1)[0] in _RECURSIVE_LONG_FLAGS for a in args)

    if not recursive:
        return False

    return any(arg in _DESTRUCTIVE_TARGETS or arg.endswith("/**") for arg in args if not arg.startswith("-"))

def _touches_device(token: str) -> bool:
    bare = token.rstrip("/*")
    return bare.startswith(_DEVICE_PREFIXES)

def is_inline_eval(command: str) -> bool:
    """inline-eval 形式（§6.2）：python -c / node -e / sh -c 这类任意代码执行。

    **当前不对最终判定生效**——T2已把解释器整体挡在白名单外，T3逐条确认，
    T4按定义不确认，三条路径都覆盖了它。保留这个函数是为了两点：
    一是记录"解释器永不自动放行"这条决策的依据（OpenClaw的strictInlineEval、
    Hermes的审批清单都单列它）；二是等规则层（allow/ask/deny，§9）落地后，
    它会成为防止`python -c`被规则误放行的守卫。
    """
    tokens = parse_command(command)
    if not tokens:
        return False

    for segment in split_segments(tokens):
        if not segment:
            continue

        program = os.path.basename(segment[0])
        flags = _INLINE_EVAL_FLAGS.get(program)
        if flags is None:
            continue
        if not flags:
            # eval 本身就是inline-eval
            return True
        if any(arg in flags for arg in segment[1:]):
            return True

    return False

def command_touches_hidden_dirs(command: str, hidden_dirs: list, cwd: str) -> bool:
    """命令的参数是否落在未放行技能的目录内。

    这是执行层的第二道门（E8.16）：文件工具已把未放行技能挡住，但shell以OS用户身份运行，
    `cat skills/<未放行>/SKILL.md` 仍能读到正文——那正是提示词注入面本身。

    判据是"参数解析后的真实路径落在某个未放行技能目录内"：
    - 相对路径按shell的cwd（工作区）解析，绝对路径直接用
    - 用realpath消解软链与`..`——软链指向未放行目录同样要挡
    - 所有命令段都查（`cat a && cat skills/xxx/SKILL.md` 里的第二段也要挡）

    **已知半成品**：`grep -r "x" .` 这类递归扫描不需要点名任何技能，路径黑名单挡不住。
    本函数保证的是"未放行技能的正文不会被直接点名读到"，不是"不会被shell读到"。
    真解是OS级隔离（TODO §15 明确不做）。
    """
    if not hidden_dirs:
        return False

    tokens = parse_command(command)
    if not tokens:
        return False

    base = os.path.abspath(cwd)
    for segment in split_segments(tokens):
        for tok in segment[1:]:
            if not tok or tok.startswith("-"):
                continue
            # 控制符已被split_segments剔除；这里只关心像路径的token
            if "\x00" in tok:
                continue
            candidate = tok if os.path.isabs(tok) else os.path.join(base, tok)
            try:
                real = os.path.realpath(candidate)
            except OSError:
                continue
            for hidden in hidden_dirs:
                if real == hidden or real.startswith(hidden + os.sep):
                    return True

    return False


def _within_project(path: str, project_root: str) -> bool:
    path, root = os.path.normcase(path), os.path.normcase(project_root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def command_escapes_project(command: str, project_root: str,
                            cwd: Optional[str] = None) -> bool:
    """命令参数是否点名了项目外的路径（T4 越界兜底）。

    只做 token 级判定：解析、切分、realpath 后看每个像路径的参数。
    `sh -c '...'`、`make deploy`、git hook 等内部写入看不出来——这是 spec §10.4
    声明的边界，不是硬隔离。解析失败一律当作越界（fail closed）。
    """
    tokens = parse_command(command)
    if not tokens:
        return True

    root = os.path.normcase(os.path.realpath(os.path.expanduser(project_root)))
    base = os.path.normcase(os.path.realpath(os.path.expanduser(cwd or project_root)))

    def outside(token: str) -> bool:
        text = os.path.expanduser(token)
        candidate = text if os.path.isabs(text) else os.path.join(base, text)
        try:
            real = os.path.normcase(os.path.realpath(candidate))
        except OSError:
            return True

        return not _within_project(real, root)

    for segment in split_segments(tokens):
        if not segment:
            continue
        for index, token in enumerate(segment):
            if not token or "\x00" in token:
                continue
            # 环境变量赋值也可能带路径：FOO=/etc/x cmd
            if index == 0 and "=" in token and not token.startswith("-"):
                _, _, value = token.partition("=")
                if value and outside(value):
                    return True
                continue
            if token.startswith("-"):
                if "=" in token:
                    value = token.split("=", 1)[1]
                    if value and outside(value):
                        return True
                continue
            if outside(token):
                return True

    return False


def decide(level: ExecLevel, command: str, hidden_dirs: Optional[list] = None,
            cwd: str = ".", project_root: Optional[str] = None) -> ExecDecision:
    """给定档位与命令，返回放行/询问/拒绝。

    判定顺序不可调换：硬黑名单在最前，T4也盖不住（§6.1）。
    """
    if not is_shell_enabled(level):
        return ExecDecision.DENY

    if is_hard_denied(command):
        return ExecDecision.DENY

    # 未放行技能的路径：任何档位都挡（E8.16）
    if command_touches_hidden_dirs(command, list(hidden_dirs or []), cwd):
        return ExecDecision.DENY

    if level == ExecLevel.READ_ONLY:
        # 白名单 + 路径收敛：T2 只能读工作区内的内容，与文件工具边界一致（§14.6）
        return ExecDecision.ALLOW if is_read_only_command(command, cwd) else ExecDecision.DENY

    if level == ExecLevel.CONFIRM_ALL:
        return ExecDecision.ASK

    # 项目会话的 T4 语义收窄为“项目内免确认”：点名项目外的命令仍要确认。
    # 非项目会话不传 project_root，行为与以前完全一致。
    if project_root and command_escapes_project(command, project_root, cwd):
        return ExecDecision.ASK

    # T4按定义不确认任何命令。若在此对inline-eval单独追问，T4的文案
    # "任意命令·免确认"就成了谎话，而interrupt_on是按工具名生效的，
    # 无法只对某类命令打断。故此处放行，硬黑名单（§6.1）仍是不可覆盖的地板。
    return ExecDecision.ALLOW