# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : skill_scan.py
# @Software    : VSCode
# @Datetime    : 2026/10/13 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 技能内容静态扫描：Python脚本走AST，SKILL.md走文本规则

import ast
import os
import re
from functools import lru_cache
from typing import Optional

import yaml

from logger import logger
from dataclasses import dataclass, field

SEVERITY_CRITICAL = "critical"
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"

SEVERITY_ORDER = {
    SEVERITY_CRITICAL: 0, SEVERITY_HIGH: 1, SEVERITY_MEDIUM: 2, SEVERITY_LOW: 3,
}

MAX_SCAN_BYTES = 1024 * 1024


@dataclass
class Finding:
    severity: str
    rule_id: str
    file: str
    line: int
    message: str
    snippet: str = ""
    # 威胁类别：与签名规则包的分类对齐，折叠重复命中时按它区分"同一构造"与"同一行两件事"
    category: str = ""

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "rule_id": self.rule_id,
            "file": self.file,
            "line": self.line,
            "message": self.message,
            "snippet": self.snippet,
            "category": self.category,
        }

    @staticmethod
    def from_dict(d: dict) -> "Finding":
        return Finding(
            severity=d.get("severity", SEVERITY_LOW),
            rule_id=d.get("rule_id", ""),
            file=d.get("file", ""),
            line=int(d.get("line", 0)),
            message=d.get("message", ""),
            snippet=d.get("snippet", ""),
            category=d.get("category", ""),
        )


@dataclass
class ScanResult:
    findings: list = field(default_factory=list)
    scanned_files: int = 0
    errors: list = field(default_factory=list)
    # 被规则的排除式抑制掉的命中数（误报抑制，不是漏报——要报出来，不能静默）
    suppressed: int = 0
    # 与已有发现落在同一行、被折叠的重复命中数
    duplicates: int = 0

    @property
    def worst(self) -> str:
        if not self.findings:
            return ""

        return min((f.severity for f in self.findings), key=lambda s: SEVERITY_ORDER[s])

    def by_severity(self, severity: str) -> list:
        return [f for f in self.findings if f.severity == severity]


# ── 凭据路径：出现在字符串里就值得看一眼 ──────────────────────────
_CREDENTIAL_PATTERNS = (
    (r"~/\.ssh|id_rsa|id_ed25519|authorized_keys", "SSH 私钥相关路径"),
    (r"~/\.aws|AWS_SECRET|aws_secret_access_key", "AWS 凭据相关"),
    (r"~/\.gnupg|id_ed25519\.pub", "GPG 密钥相关"),
    (r"~/\.ssh/config|~/\.docker/config|~/\.kube/config", "敏感配置路径"),
    (r"\bid_rsa\b|\.pem\b|\.p12\b|\.key\b", "密钥文件路径"),
    (r"os\.environ(?:\.get)?\(\s*['\"].*(?:KEY|TOKEN|SECRET|PASSWORD)", "从环境变量读取密钥"),
)


class _PyVisitor(ast.NodeVisitor):
    """按AST结构找危险调用。刻意不做控制流分析——那是误报的主要来源。

    这里只回答"代码里出现了能造成损害的构造"，不回答"它一定会被执行"。
    真正的判断留给审查时的人，这也是各家扫描器共同的定位。
    """

    def __init__(self, filename: str, lines: list):
        self.filename = filename
        self.lines = lines
        self.findings: list[Finding] = []

    def _add(self, severity, rule_id, node, message, snippet="", category=""):
        lineno = getattr(node, "lineno", 0)
        if not snippet and 0 < lineno <= len(self.lines):
            snippet = self.lines[lineno - 1].strip()[:120]

        self.findings.append(Finding(
            severity=severity,
            rule_id=rule_id,
            file=self.filename,
            line=lineno,
            message=message,
            snippet=snippet,
            category=category,
        ))

    # shell=True 的子进程：命令串里的元字符会被shell解释
    def visit_Call(self, node: ast.Call) -> None:
        name = _dotted_name(node.func)

        if name in ("subprocess.run", "subprocess.call", "subprocess.Popen",
                    "subprocess.check_call", "subprocess.check_output"):
            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    self._add(SEVERITY_HIGH, "PY001", node,
                              "subprocess 带 shell=True：命令串里的元字符会被shell解释",
                              category="command_injection")
                    break

        if name in ("os.system", "os.popen"):
            self._add(SEVERITY_HIGH, "PY002", node, f"{name} 直接交给shell执行",
                              category="command_injection")

        if name in ("eval", "exec"):
            self._add(SEVERITY_HIGH, "PY003", node, "动态代码执行",
                              category="command_injection")

        if name in ("pickle.loads", "pickle.load", "marshal.loads"):
            self._add(SEVERITY_HIGH, "PY004", node, "反序列化外部数据可导致任意代码执行",
                              category="deserialization")

        if name in ("yaml.load", "yaml.unsafe_load"):
            if not _has_safe_loader(node):
                self._add(SEVERITY_HIGH, "PY005", node, "yaml.load 未使用 SafeLoader",
                              category="deserialization")

        if name == "__import__" and node.args and not isinstance(node.args[0], ast.Constant):
            self._add(SEVERITY_MEDIUM, "PY006", node, "动态 import：模块名来自运行时",
                              category="obfuscation")

        if name in ("requests.post", "requests.put", "requests.patch",
                    "urllib.request.urlopen", "http.client.HTTPSConnection",
                    "http.client.HTTPConnection", "socket.create_connection"):
            self._add(SEVERITY_MEDIUM, "PY007", node, "出网请求：脚本能把本地数据发出去",
                              category="data_exfiltration")

        if name in ("base64.b64decode", "base64.b64encode"):
            self._add(SEVERITY_LOW, "PY008", node, "base编解码：常与混淆载荷同现",
                              category="obfuscation")

        if name in ("importlib.import_module", "runpy.run_path"):
            self._add(SEVERITY_LOW, "PY009", node, "动态加载模块",
                              category="obfuscation")

        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            for pattern, desc in _CREDENTIAL_PATTERNS:
                if re.search(pattern, node.value):
                    self._add(SEVERITY_HIGH, "PY010", node, f"涉及凭据：{desc}", snippet=node.value[:80],
                              category="hardcoded_secrets")
                    break

        self.generic_visit(node)


def _dotted_name(node) -> str:
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)

    return ".".join(reversed(parts))


def _has_safe_loader(node: ast.Call) -> bool:
    for kw in node.keywords:
        if kw.arg == "Loader":
            value = kw.value
            if isinstance(value, ast.Name) and "safe" in value.id.lower():
                return True
            if isinstance(value, ast.Attribute) and "safe" in value.attr.lower():
                return True

    return False


def _scan_python(path: str, rel: str, result: ScanResult) -> None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
    except OSError as ex:
        result.errors.append(f"{rel}: 读取失败 {ex}")
        return

    if len(source.encode("utf-8")) > MAX_SCAN_BYTES:
        result.errors.append(f"{rel}: 超过 {MAX_SCAN_BYTES} 字节，未扫描")
        return

    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError as ex:
        result.errors.append(f"{rel}:{ex.lineno}: 语法错误 {ex.msg}")
        return

    visitor = _PyVisitor(rel, source.splitlines())
    visitor.visit(tree)

    result.findings.extend(visitor.findings)
    result.scanned_files += 1


# ── SKILL.md 文本规则 ─────────────────────────────────────────────
# 零宽/双向控制字符：人眼看不见但会进模型上下文，是提示词注入的常见载体
_INVISIBLE_CHARS = "[\\u200b-\\u200f\\u202a-\\u202e\\u2060-\\u2064\\ufeff]"
# Claude Code 的渲染期shell注入：!`cmd` 会在内容呈现前执行
_SHELL_HOOK = r"!`[^`]+`"
# 生命周期hook
_HOOKS_BLOCK = r"(?m)^hooks\s*:"
# 提示词注入的常见措辞——只留签名规则包**没有覆盖**的那些：
# "ignore previous instructions"/"developer mode"/"do not tell the user" 等
# 由包里的 PROMPT_INJECTION_* 规则负责（那些规则带实测标定的误报抑制）。
# 重复覆盖同一措辞只会让同一行报两遍。
_INJECTION_PHRASES = (
    "disregard your", "you are now", "act as",
    "不要告诉用户", "忽略之前", "忽略以上",
)
_OVERBROAD_TOOLS = r"allowed-tools\s*:.*\bBash\s*\("

# ── 通用文本规则：scripts/ 下非 .py 的文件也走这套 ──────────────────
# 这些文件同样能跑（agent在T2+可以用execute调它们，T1技能脚本通道只认.py），
# 但没有AST可依，只能用文本模式匹配——精度不如AST，宁可少报也别乱报。
# 判据取自真实脚本里危害最大的几类构造，不是"所有可执行文件"的通用审计。
_SHELL_INTERPRETERS = r"(?:ba|z|k|da|fi)?sh|python3?|perl|node|ruby|php"

# 下载即执行：curl|sh 这类是供应链最常见的远程代码执行手法
_GEN_DOWNLOAD_EXEC = rf"\b(?:curl|wget)\b[\s\S]{{0,200}}?\|\s*(?:sudo\s+)?{_SHELL_INTERPRETERS}\b"
# 混淆执行：base64解码后直接喂给解释器
_GEN_OBFUSCATED_EXEC = rf"\bbase64\b[^\n|]{{0,200}}(?:-d|--decode)\b[^\n|]{{0,200}}\|\s*(?:sudo\s+)?{_SHELL_INTERPRETERS}\b"
# 破坏性删除：只盯根目录与家目录，不误报 rm -rf /tmp/xxx
_GEN_ROOT_DELETE = r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+(?:-\S+\s+)*(?:/\s*\*?|~(?:\s*/)?|\$HOME(?:\s*/?)?)\s*(?:$|[\"';&|])"
# 提权
_GEN_PRIV_ESCALATION = r"(?:^|[\s;&|(])sudo\s|\bsu\s+-\s|\bsu\s+root\b"
# 放宽权限：只认777与a+rwx，755这类正常权限不管
_GEN_WORLD_WRITABLE = r"\bchmod\s+(?:-R\s+)?(?:0?777|a\+rwx)\b"
# 持久化：写定时任务（crontab -l 只是查看，不算）
_GEN_PERSISTENCE = r"(?:^|[\s;&|(])crontab\s+(?!-l\b|--list\b)"

# 文件类型：对应签名规则包里的 file_types 口径
FILE_TYPE_PYTHON = "python"
FILE_TYPE_BASH = "bash"
FILE_TYPE_JS = "javascript"
FILE_TYPE_TS = "typescript"
FILE_TYPE_MARKDOWN = "markdown"
FILE_TYPE_BINARY = "binary"
FILE_TYPE_OTHER = "other"

_FILE_TYPES = {
    ".py": FILE_TYPE_PYTHON,
    ".sh": FILE_TYPE_BASH, ".bash": FILE_TYPE_BASH, ".zsh": FILE_TYPE_BASH,
    ".fish": FILE_TYPE_BASH, ".ksh": FILE_TYPE_BASH,
    ".js": FILE_TYPE_JS, ".mjs": FILE_TYPE_JS, ".cjs": FILE_TYPE_JS,
    ".ts": FILE_TYPE_TS, ".tsx": FILE_TYPE_TS,
    ".md": FILE_TYPE_MARKDOWN, ".markdown": FILE_TYPE_MARKDOWN,
}


@dataclass(frozen=True)
class TextRule:
    """一条文本规则：正则命中 + 误报抑制 + 适用范围。

    exclude_patterns 是给规则加"误报抑制"的地方：命中行若同时命中任一排除式，
    这条发现就不算（但会计数，不静默消失）。签名规则包的排除式是上游拿 188 万
    真实技能实测标定出来的，比我们自己拍脑袋的抑制可靠。
    """
    rule_id: str
    severity: str
    category: str
    patterns: tuple[str, ...]
    exclude_patterns: tuple[str, ...] = ()
    file_types: tuple[str, ...] = ()   # 空=不限
    description: str = ""
    remediation: str = ""


# 我们自己维护的规则（扫描面内）
_OUR_TEXT_RULES: tuple[TextRule, ...] = (
    TextRule("MD001", SEVERITY_HIGH, "prompt_injection", (_INVISIBLE_CHARS,),
             file_types=(FILE_TYPE_MARKDOWN,),
             description="含不可见字符（零宽/双向控制）：人眼看不见但会进模型上下文"),
    TextRule("MD002", SEVERITY_HIGH, "command_injection", (_SHELL_HOOK,),
             file_types=(FILE_TYPE_MARKDOWN,),
             description="含 !`...` 渲染期shell注入语法"),
    TextRule("MD003", SEVERITY_HIGH, "unauthorized_tool_use", (_HOOKS_BLOCK,),
             file_types=(FILE_TYPE_MARKDOWN,),
             description="声明了hooks块：技能可在生命周期事件上运行命令"),
    TextRule("MD004", SEVERITY_MEDIUM, "unauthorized_tool_use", (_OVERBROAD_TOOLS,),
             file_types=(FILE_TYPE_MARKDOWN,),
             description="allowed-tools 授予了宽泛的Bash权限（是授权而非限制）"),
    TextRule("MD005", SEVERITY_MEDIUM, "prompt_injection",
             tuple(f"(?i){re.escape(p)}" for p in _INJECTION_PHRASES),
             file_types=(FILE_TYPE_MARKDOWN,),
             description="疑似提示词注入措辞"),
    TextRule("GEN001", SEVERITY_HIGH, "prompt_injection", (_INVISIBLE_CHARS,),
             file_types=(FILE_TYPE_BASH, FILE_TYPE_JS, FILE_TYPE_TS, FILE_TYPE_OTHER),
             description="含不可见字符（零宽/双向控制）：人眼看不见但会进模型上下文"),
    TextRule("GEN003", SEVERITY_HIGH, "command_injection", (_GEN_DOWNLOAD_EXEC,),
             file_types=(FILE_TYPE_BASH, FILE_TYPE_JS, FILE_TYPE_TS, FILE_TYPE_OTHER),
             description="下载即执行：curl/wget取到的内容直接管道给解释器"),
    TextRule("GEN004", SEVERITY_HIGH, "command_injection", (_GEN_ROOT_DELETE,),
             file_types=(FILE_TYPE_BASH, FILE_TYPE_JS, FILE_TYPE_TS, FILE_TYPE_OTHER),
             description="破坏性删除：递归删除根目录或家目录"),
    TextRule("GEN005", SEVERITY_HIGH, "obfuscation", (_GEN_OBFUSCATED_EXEC,),
             file_types=(FILE_TYPE_BASH, FILE_TYPE_JS, FILE_TYPE_TS, FILE_TYPE_OTHER),
             description="混淆执行：base64解码后直接管道给解释器"),
    TextRule("GEN006", SEVERITY_MEDIUM, "unauthorized_tool_use", (_GEN_PRIV_ESCALATION,),
             file_types=(FILE_TYPE_BASH, FILE_TYPE_JS, FILE_TYPE_TS, FILE_TYPE_OTHER),
             description="提权：脚本要求sudo/su root"),
    TextRule("GEN007", SEVERITY_MEDIUM, "unauthorized_tool_use", (_GEN_WORLD_WRITABLE,),
             file_types=(FILE_TYPE_BASH, FILE_TYPE_JS, FILE_TYPE_TS, FILE_TYPE_OTHER),
             description="放宽权限：chmod 777/a+rwx"),
    TextRule("GEN008", SEVERITY_MEDIUM, "persistence", (_GEN_PERSISTENCE,),
             file_types=(FILE_TYPE_BASH, FILE_TYPE_JS, FILE_TYPE_TS, FILE_TYPE_OTHER),
             description="持久化：写入定时任务（crontab）"),
    TextRule("GEN002", SEVERITY_HIGH, "hardcoded_secrets",
             tuple(p for p, _desc in _CREDENTIAL_PATTERNS),
             file_types=(FILE_TYPE_BASH, FILE_TYPE_JS, FILE_TYPE_TS, FILE_TYPE_OTHER),
             description="涉及凭据路径或密钥"),
)


# ── 通用文本规则：scripts/ 下非 .py 的文件也走这套 ──────────────────
# 这些文件同样能跑（agent在T2+可以用execute调它们，T1技能脚本通道只认.py），
# 但没有AST可依，只能用文本模式匹配——精度不如AST，宁可少报也别乱报。
# 判据取自真实脚本里危害最大的几类构造，不是"所有可执行文件"的通用审计。
_SHELL_INTERPRETERS = r"(?:ba|z|k|da|fi)?sh|python3?|perl|node|ruby|php"

# 下载即执行：curl|sh 这类是供应链最常见的远程代码执行手法
_GEN_DOWNLOAD_EXEC = re.compile(
    rf"\b(?:curl|wget)\b[\s\S]{{0,200}}?\|\s*(?:sudo\s+)?{_SHELL_INTERPRETERS}\b", re.I)
# 混淆执行：base64解码后直接喂给解释器
_GEN_OBFUSCATED_EXEC = re.compile(
    rf"\bbase64\b[^\n|]{{0,200}}(?:-d|--decode)\b[^\n|]{{0,200}}\|\s*(?:sudo\s+)?{_SHELL_INTERPRETERS}\b", re.I)
# 破坏性删除：只盯根目录与家目录，不误报 rm -rf /tmp/xxx
_GEN_ROOT_DELETE = re.compile(
    r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+(?:-\S+\s+)*(?:/\s*\*?|~(?:\s*/)?|\$HOME(?:\s*/?)?)\s*(?:$|[\"';&|])")
# 提权
_GEN_PRIV_ESCALATION = re.compile(r"(?:^|[\s;&|(])sudo\s|\bsu\s+-\s|\bsu\s+root\b")
# 放宽权限：只认777与a+rwx，755这类正常权限不管
_GEN_WORLD_WRITABLE = re.compile(r"\bchmod\s+(?:-R\s+)?(?:0?777|a\+rwx)\b")
# 持久化：写定时任务（crontab -l 只是查看，不算）
_GEN_PERSISTENCE = re.compile(r"(?:^|[\s;&|(])crontab\s+(?!-l\b|--list\b)")

_GEN_TEXT_RULES = (
    (SEVERITY_HIGH, "GEN001", _INVISIBLE_CHARS, "含不可见字符（零宽/双向控制）：人眼看不见但会进模型上下文"),
    (SEVERITY_HIGH, "GEN003", _GEN_DOWNLOAD_EXEC, "下载即执行：curl/wget取到的内容直接管道给解释器"),
    (SEVERITY_HIGH, "GEN004", _GEN_ROOT_DELETE, "破坏性删除：递归删除根目录或家目录"),
    (SEVERITY_HIGH, "GEN005", _GEN_OBFUSCATED_EXEC, "混淆执行：base64解码后直接管道给解释器"),
    (SEVERITY_MEDIUM, "GEN006", _GEN_PRIV_ESCALATION, "提权：脚本要求sudo/su root"),
    (SEVERITY_MEDIUM, "GEN007", _GEN_WORLD_WRITABLE, "放宽权限：chmod 777/a+rwx"),
    (SEVERITY_MEDIUM, "GEN008", _GEN_PERSISTENCE, "持久化：写入定时任务（crontab）"),
)

def _file_type(path: str) -> str:
    """文件类型——对应签名规则包里的 file_types 口径。"""
    suffix = os.path.splitext(path)[1].lower()
    if suffix in _FILE_TYPES:
        return _FILE_TYPES[suffix]
    if suffix in BINARY_SUFFIXES:
        return FILE_TYPE_BINARY
    return FILE_TYPE_OTHER


# 随包分发的签名规则（Apache-2.0，来自 cisco-ai-defense/skill-scanner 2.3.1）
SIGNATURE_RULES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules", "signatures")


@lru_cache(maxsize=1)
def _all_signature_rules() -> tuple:
    """加载签名规则包。规则文本原样保留，只做字段映射。"""
    rules: list[TextRule] = []
    if not os.path.isdir(SIGNATURE_RULES_DIR):
        return ()

    for name in sorted(os.listdir(SIGNATURE_RULES_DIR)):
        if not name.endswith(".yaml"):
            continue
        try:
            with open(os.path.join(SIGNATURE_RULES_DIR, name), "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or []
        except (OSError, yaml.YAMLError) as ex:
            logger.warning(f"[skill-scan] 规则包 {name} 读取失败: {ex}")
            continue

        for item in raw:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            rules.append(TextRule(
                rule_id=str(item["id"]),
                severity=str(item.get("severity", SEVERITY_LOW)).lower(),
                category=str(item.get("category", "unknown")),
                patterns=tuple(str(p) for p in (item.get("patterns") or [])),
                exclude_patterns=tuple(str(p) for p in (item.get("exclude_patterns") or [])),
                file_types=tuple(str(t) for t in (item.get("file_types") or [])),
                description=str(item.get("description", "")),
                remediation=str(item.get("remediation", "")),
            ))

    return tuple(rules)


@lru_cache(maxsize=8)
def _signature_rules(file_type: str) -> list:
    """某个文件类型适用的签名规则。"""
    return [r for r in _all_signature_rules() if not r.file_types or file_type in r.file_types]


# 明显是二进制产物/构建缓存的目录与扩展名：不扫，也不算异常
SKIP_DIRS = frozenset({"__pycache__", ".git", ".venv", "venv", "node_modules",
                        ".mypy_cache", ".pytest_cache", ".ruff_cache"})
BINARY_SUFFIXES = frozenset({
    ".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe", ".bin", ".class", ".jar", ".whl",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf", ".zip", ".tar",
    ".gz", ".tgz", ".bz2", ".xz", ".7z", ".mp3", ".mp4", ".mov", ".ttf", ".otf", ".woff",
})

# 可执行扩展名：这些文件能被解释器或系统直接执行，算"可执行面"。
# 不只.py——.sh/.ps1/.js这些同样能跑，漏掉它们等于漏审一整类脚本。
# 没有扩展名的文件按脚本算（scripts/build这类常见）。
EXECUTABLE_SUFFIXES = frozenset({
    ".py", ".sh", ".bash", ".zsh", ".fish", ".ksh", ".ps1", ".bat", ".cmd",
    ".pl", ".rb", ".php", ".lua", ".js", ".mjs", ".cjs", ".ts", ".awk", ".sed", ".exp",
})

# 压缩包：里面的内容扫描器看不见，等于一块审查盲区
ARCHIVE_SUFFIXES = frozenset({
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".whl", ".jar",
    ".iso", ".dmg", ".rar", ".cab", ".deb", ".rpm",
})

# 文档类二进制：常见于模板，但同样是ZIP容器（.docx=.docx是ZIP），
# Trail of Bits正是用"让agent去解.docx取真指令"绕过了基于模式匹配的扫描
DOCUMENT_BINARY_SUFFIXES = frozenset({
    ".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pptm", ".odt", ".ods", ".odp", ".epub",
})


def is_executable_file(path: str) -> bool:
    """这个文件算不算"可执行面"。

    扫描面、审批面、哈希面共用这一判据：三处口径不一致就会出现
    "扫过但没审批"或"审批了但没扫到"的缝。
    """
    suffix = os.path.splitext(path)[1].lower()
    if not suffix:
        return True

    return suffix in EXECUTABLE_SUFFIXES


def _source_for_pyc(path: str, name: str) -> Optional[str]:
    """字节码对应的源码路径；没有对应源码就是sourceless（可直接被import加载）。"""
    stem = name[:-len(".pyc")].split(".")[0]  # 去掉 .pyc 与 cpython-312 这样的标签
    src = os.path.join(os.path.dirname(path), stem + ".py")

    return src if os.path.isfile(src) else None


def _scan_package_binaries(skill_dir: str, result: ScanResult) -> None:
    """整个技能目录里的"不可审内容"：预编译字节码与压缩包。

    要扫**整个技能目录**而不只是 scripts/：Trail of Bits的.docx间接层就放在技能根下，
    攻击者的直觉也是往最显眼的地方放。
    """
    for root, dirs, files in os.walk(skill_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

        for name in sorted(files):
            if name.startswith("."):
                continue

            full = os.path.join(root, name)
            rel = os.path.relpath(full, skill_dir)

            if name.endswith(".pyc"):
                # __pycache__ 下的是编译缓存，正常产物，不报
                if os.path.basename(root) == "__pycache__":
                    continue
                if _source_for_pyc(full, name) is None:
                    result.findings.append(Finding(
                        severity=SEVERITY_HIGH, rule_id="GEN010", file=rel, line=0,
                        message="无源字节码(.pyc)：可被直接import执行，内容从未被审查",
                    ))
                else:
                    result.findings.append(Finding(
                        severity=SEVERITY_HIGH, rule_id="GEN009", file=rel, line=0,
                        message="技能包带预编译字节码：审的是源码，执行的可能是字节码",
                    ))
                continue

            suffix = os.path.splitext(name)[1].lower()
            if suffix in ARCHIVE_SUFFIXES:
                result.findings.append(Finding(
                    severity=SEVERITY_HIGH, rule_id="GEN011", file=rel, line=0,
                    message="技能包里有压缩包：里面的内容没被审查",
                ))
            elif suffix in DOCUMENT_BINARY_SUFFIXES:
                result.findings.append(Finding(
                    severity=SEVERITY_MEDIUM, rule_id="GEN012", file=rel, line=0,
                    message="文档二进制文件：里面是ZIP容器，内容没被审查",
                ))


def _read_text_file(path: str, rel: str, result: ScanResult) -> Optional[str]:
    """读文本内容；读不了就说出来（"没扫到"不能装作扫过了）。"""
    try:
        size = os.path.getsize(path)
    except OSError as ex:
        result.errors.append(f"{rel}: 读取失败 {ex}")
        return None

    if size > MAX_SCAN_BYTES:
        result.errors.append(f"{rel}: 超过 {MAX_SCAN_BYTES} 字节，未扫描")
        return None

    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as ex:
        result.errors.append(f"{rel}: 读取失败 {ex}")
        return None

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        result.errors.append(f"{rel}: 非UTF-8文本（二进制？），未扫描")
        return None


def _apply_text_rules(rules: list, content: str, rel: str, file_type: str,
                       result: ScanResult) -> None:
    """按规则集扫一份文本。

    模式对**整份内容**匹配（管道可能跨行），排除式对**命中行**匹配——
    排除式是按行写的（上游的抑制式大量用 ^ 锚定行首）。
    """
    lines = content.splitlines()

    def _line_of(pos: int) -> int:
        return content.count("\n", 0, pos) + 1

    def _snippet(pos: int) -> str:
        idx = _line_of(pos) - 1
        return lines[idx].strip()[:120] if 0 <= idx < len(lines) else ""

    for rule in rules:
        if rule.file_types and file_type not in rule.file_types:
            continue

        for pattern in rule.patterns:
            try:
                compiled = re.compile(pattern)
            except re.error:
                # 规则写坏了不能拖垮整个扫描
                result.errors.append(f"{rel}: 规则 {rule.rule_id} 的正则无效，已跳过")
                continue

            for match in compiled.finditer(content):
                line_no = _line_of(match.start())
                line_text = lines[line_no - 1] if 0 < line_no <= len(lines) else ""

                if any(re.search(ex, line_text) for ex in rule.exclude_patterns):
                    result.suppressed += 1
                    continue

                result.findings.append(Finding(
                    severity=rule.severity, rule_id=rule.rule_id, file=rel,
                    line=line_no, message=rule.description or rule.rule_id,
                    snippet=_snippet(match.start()), category=rule.category,
                ))


def _scan_text(path: str, rel: str, result: ScanResult) -> None:
    """SKILL.md：我们的规则 + 签名规则包。

    包目前**只用于 markdown**：它的 prompt_injection / secrets 规则的排除式
    就是按 markdown 的"引号内提及"标定的（上游拿188万真实技能测出来的），而它
    在代码类文件上的等价能力，上游是靠AST与数据流的Python规则做的——我们已经有
    自己的AST层。实测证据：包原样铺到.py上，把一处带退出条件的`while True:`
    报成死循环、把自测块里的硬编码路径报成路径穿越。
    """
    content = _read_text_file(path, rel, result)
    if content is None:
        return

    _apply_text_rules(list(_OUR_TEXT_RULES) + _signature_rules(FILE_TYPE_MARKDOWN),
                      content, rel, FILE_TYPE_MARKDOWN, result)
    result.scanned_files += 1


def _scan_generic_text(path: str, rel: str, result: ScanResult) -> None:
    """扫 scripts/ 下非 .py 的文件：只有文本模式可用。

    这里只用自己的GEN规则（按低误报写的），不套签名包——缺真实样本测误报。
    """
    content = _read_text_file(path, rel, result)
    if content is None:
        return

    file_type = _file_type(path)
    _apply_text_rules(list(_OUR_TEXT_RULES), content, rel, file_type, result)
    result.scanned_files += 1


def scan_skill(skill_dir: str) -> ScanResult:
    """扫描技能目录：scripts/下的.py走AST，其余文件走通用文本规则；SKILL.md走文本规则。

    扫描面 = 审批面 = 哈希面：scripts/下的一切都算可执行面（skill_catalog.has_scripts
    与 skill_review_service.content_hash 用同一口径），否则"没扫到"的东西照样能加载。
    """
    result = ScanResult()

    skill_md = os.path.join(skill_dir, "SKILL.md")
    if os.path.isfile(skill_md):
        _scan_text(skill_md, "SKILL.md", result)

    scripts_dir = os.path.join(skill_dir, "scripts")
    if not os.path.isdir(scripts_dir):
        return result

    for root, dirs, files in os.walk(scripts_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

        for name in sorted(files):
            if name.startswith("."):
                continue

            full = os.path.join(root, name)
            rel = os.path.relpath(full, skill_dir)
            if os.path.islink(full):
                result.findings.append(Finding(
                    severity=SEVERITY_HIGH, rule_id="PY000", file=rel, line=0,
                    message="scripts/ 下存在符号链接：审查内容与实际执行内容可能不一致",
                ))
                continue

            if not is_executable_file(full):
                continue

            if name.endswith(".py"):
                _scan_python(full, rel, result)
                continue

            _scan_generic_text(full, rel, result)

    # 整个技能目录都要看：压缩包与字节码放在哪都能藏（ToB的.docx就在技能根下）
    _scan_package_binaries(skill_dir, result)

    _fold_duplicates(result)

    return result


def _fold_duplicates(result: ScanResult) -> None:
    """同一构造被多条规则命中时只留最严重的一条，其余计为重复。

    我们的规则与签名规则包会重叠（都看shell=True、eval、凭据路径……），
    同一构造报两遍只是噪音——留最严重的，重复的条数记下来，不静默丢。
    按 (文件, 行, 类别) 折叠：同一行的**不同**构造（eval + base64）要各报各的。
    """
    worst: dict[tuple[str, int, str], Finding] = {}
    order: list[tuple[str, int, str]] = []

    for finding in result.findings:
        key = (finding.file, finding.line, finding.category)
        current = worst.get(key)
        if current is None:
            worst[key] = finding
            order.append(key)
            continue

        result.duplicates += 1
        if SEVERITY_ORDER.get(finding.severity, 9) < SEVERITY_ORDER.get(current.severity, 9):
            worst[key] = finding

    result.findings = [worst[key] for key in order]