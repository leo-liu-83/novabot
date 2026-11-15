#!/usr/bin/env bash
# NovaBot 源码安装脚本：准备 Python 环境、依赖，并把启动命令装进 PATH。
#
# 直接运行会进入交互式初始化；CI/自动化可用参数关闭交互。
#   ./install.sh
#   ./install.sh --non-interactive
#
# 安装完成后可在任意目录执行 `novabot`、`novabot web --port 9000`、`novabot repl`。

set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR=""
USE_VENV=1
SKIP_DEPS=0
INIT_CONFIG=0
BIN_DIR="${HOME:-}/.local/bin"
CMD_NAME="novabot"
INTERACTIVE=0
NON_INTERACTIVE=0
CONFIG_DIR=""
CONFIG_FILE=""
WRITE_CONFIG=0
MULTI_USER_CHOICE=0
PROVIDER_BASE_URL=""
PROVIDER_KEY=""
PROVIDER_MODEL=""
PROVIDER_CODE=""

# 哪些值是被用户显式指定的；交互模式只询问没指定的项
VENV_SET=0
PYTHON_SET=0
BIN_SET=0
NAME_SET=0
DEPS_SET=0
CONFIG_SET=0

usage() {
  cat <<EOF
用法: ./install.sh [选项]

直接运行会交互式询问环境、命令名和安装目录。

  --venv PATH         使用或创建指定虚拟环境
  --no-venv           不创建虚拟环境，直接使用 --python 指定的解释器
  --python PATH       Python 解释器（默认：python3，要求 3.12+）
  --bin-dir PATH      启动器安装目录（默认：~/.local/bin）
  --name NAME         启动命令名（默认：novabot）
  --skip-deps         跳过 pip 安装依赖
  --init-config       若 ~/.novabot/novabot.json 不存在，生成单用户最小配置
                      （交互模式会询问多用户与模型接口）
  --non-interactive   不提问，使用默认值/上面的参数
  -h, --help          显示帮助

示例:
  ./install.sh
  ./install.sh --non-interactive
  ./install.sh --venv ~/venvs/novabot
  ./install.sh --no-venv
  ./install.sh --bin-dir /usr/local/bin
  ./install.sh --init-config
EOF
}

die() {
  printf '%s✗ %s%s\n' "${C_RED:-}" "$*" "${C_RESET:-}" >&2
  exit 1
}

require_value() {
  [ "$#" -ge 2 ] || die "$1 需要一个参数"
}

# ── 终端样式 ─────────────────────────────────────────────────────────────────
# 只在真正对着终端时上色：重定向到文件或接管道时保持纯文本，日志和 CI 里不会有
# 转义序列。NO_COLOR 是跨工具的事实标准，TERM=dumb 一眼就知道不支持颜色。
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != "dumb" ]; then
  C_RESET=$'\033[0m'
  C_BOLD=$'\033[1m'
  C_DIM=$'\033[2m'
  C_CYAN=$'\033[36m'
  C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'
  C_RED=$'\033[31m'
else
  C_RESET=""
  C_BOLD=""
  C_DIM=""
  C_CYAN=""
  C_GREEN=""
  C_YELLOW=""
  C_RED=""
fi

ui_rule() {
  printf '%s────────────────────────────────────────────────%s\n' "$C_DIM" "$C_RESET"
}

ui_banner() {
  ui_rule
  printf ' %sNovaBot 安装向导%s\n' "$C_BOLD" "$C_RESET"
  printf ' %s项目目录%s  %s\n' "$C_DIM" "$C_RESET" "$PROJECT_DIR"
  ui_rule
}

# 分组标题：交互流程的每一步一个，避免十几行输出糊成一片
ui_step() {
  printf '\n%s◆ %s%s\n' "${C_BOLD}${C_CYAN}" "$*" "$C_RESET"
}

ui_ok() {
  printf '%s✔ %s%s\n' "$C_GREEN" "$*" "$C_RESET"
}

ui_warn() {
  printf '%s! %s%s\n' "$C_YELLOW" "$*" "$C_RESET" >&2
}

# 进度行：不带步骤感，只标记"正在做某事"
ui_progress() {
  printf '%s→%s %s\n' "$C_CYAN" "$C_RESET" "$*"
}

# 摘要的两列对齐靠调用方手工补空格（中文按 2 列宽计、补到 10 列），
# 而不是用 printf 的 %-10s——它按字符数补，中日韩字符会歪。
ui_kv() {
  printf '  %s%s%s %s\n' "$C_DIM" "$1" "$C_RESET" "$2"
}

ui_option() {
  printf '  %s%s)%s %s\n' "$C_CYAN" "$1" "$C_RESET" "$2"
}

ask() {
  local prompt="$1" default="$2" answer
  if [ "$INTERACTIVE" -eq 0 ]; then
    printf '%s' "$default"
    return 0
  fi
  if [ -n "$default" ]; then
    printf '%s?%s %s [%s]: ' "$C_CYAN" "$C_RESET" "$prompt" "$default" >&2
  else
    # 没有默认值时不要显示一对空方括号
    printf '%s?%s %s: ' "$C_CYAN" "$C_RESET" "$prompt" >&2
  fi
  IFS= read -r answer || true
  printf '%s' "${answer:-$default}"
}

ask_yes_no() {
  local prompt="$1" default="$2" answer
  if [ "$INTERACTIVE" -eq 0 ]; then
    [ "$default" = "y" ]
    return
  fi
  printf '%s?%s %s [y/n，默认 %s]: ' "$C_CYAN" "$C_RESET" "$prompt" "$default" >&2
  IFS= read -r answer || true
  answer="${answer:-$default}"
  case "$answer" in
    y|Y|yes|YES) return 0 ;;
    n|N|no|NO) return 1 ;;
    *) [ "$default" = "y" ] ;;
  esac
}

expand_path() {
  case "$1" in
    "~") printf '%s' "$HOME" ;;
    "~/"*) printf '%s/%s' "$HOME" "${1#\~/}" ;;
    *) printf '%s' "$1" ;;
  esac
}

# ── Python 探测与选择 ─────────────────────────────────────────────────────────
# 这一步在任何提问之前完成：解释器不合格就立刻停下，而不是让用户答完五六个
# 问题、甚至在磁盘上留下一个建了一半的 .venv 之后才报错。
#
# 只探测、不自动安装：调用 apt/dnf/brew/winget 需要提权与联网，安装脚本不该
# 悄悄改系统状态。找不到合格解释器时给出平台对应的安装命令，由用户自己执行。

# 打印 "3.12.9"；解释器不可用时输出空串
python_version() {
  "$1" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || true
}

python_meets_floor() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1
}

python_real_path() {
  # realpath 而不是 executable：conda/venv 里 python3 常是 python3.12 的软链接，
  # 不解析会当成两个不同的解释器重复入列
  "$1" -c 'import os, sys; print(os.path.realpath(sys.executable))' 2>/dev/null || true
}

PY_CANDIDATES=()
PY_OK_PATHS=()
PY_OK_VERS=()
PY_REJECTED_PATHS=()
PY_REJECTED_VERS=()

add_python_candidate() {
  local candidate="$1" resolved="" real="" existing=""
  [ -n "$candidate" ] || return 0
  if [ -x "$candidate" ]; then
    resolved="$candidate"
  else
    resolved="$(command -v "$candidate" 2>/dev/null)" || return 0
  fi
  [ -n "$resolved" ] || return 0
  real="$(python_real_path "$resolved")"
  [ -n "$real" ] || return 0
  [ -x "$real" ] || return 0
  # 同一个解释器可能以 PATH 名、绝对路径、软链接等多种写法出现，按真实路径去重
  for existing in "${PY_CANDIDATES[@]:-}"; do
    [ "$existing" = "$real" ] && return 0
  done
  PY_CANDIDATES+=("$real")
}

collect_python_candidates() {
  # 优先级：显式指定 > 已激活环境 > PATH 上的常见名字 > 装了但没进 PATH 的
  if [ "$PYTHON_SET" -eq 1 ]; then
    add_python_candidate "$(expand_path "$PYTHON_BIN")"
  fi
  if [ -n "$ACTIVE_ENV" ]; then
    add_python_candidate "$ACTIVE_ENV/bin/python"
  fi

  local name
  for name in python3.14 python3.13 python3.12 python3 python; do
    add_python_candidate "$name"
  done

  # pyenv / conda / Homebrew 里装了但没进 PATH 的也捡进来：用户往往不知道
  # 自己机器上其实已经有一个 3.12，只是 python3 指向了系统自带的旧版本
  local path
  for path in \
    "$HOME"/.pyenv/versions/*/bin/python3* \
    "$HOME"/miniconda3/envs/*/bin/python \
    "$HOME"/anaconda3/envs/*/bin/python \
    "$HOME"/miniforge3/envs/*/bin/python \
    /opt/homebrew/bin/python3.1* \
    /usr/local/bin/python3.1*; do
    add_python_candidate "$path"
  done
  return 0
}

classify_python_candidates() {
  local path="" version=""
  for path in "${PY_CANDIDATES[@]:-}"; do
    [ -n "$path" ] || continue
    version="$(python_version "$path")"
    if python_meets_floor "$path"; then
      PY_OK_PATHS+=("$path")
      PY_OK_VERS+=("$version")
    else
      PY_REJECTED_PATHS+=("$path")
      PY_REJECTED_VERS+=("${version:-未知}")
    fi
  done
  return 0
}

print_python_install_hint() {
  local kernel="" ids=""
  kernel="$(uname -s 2>/dev/null || echo unknown)"
  if [ "$kernel" = "Darwin" ]; then
    echo "  brew install python@3.12"
    echo "  # 或从 https://www.python.org/downloads/macos/ 下载安装包"
    return 0
  fi

  if [ -r /etc/os-release ]; then
    ids="$(set +u; . /etc/os-release >/dev/null 2>&1; printf '%s %s' "${ID:-}" "${ID_LIKE:-}")"
  fi
  case " $ids " in
    *debian*|*ubuntu*)
      echo "  sudo apt update"
      echo "  sudo apt install python3.12 python3.12-venv"
      echo "  # Ubuntu 22.04 及更早的官方源没有 3.12，需先加 deadsnakes："
      echo "  #   sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update"
      ;;
    *fedora*|*rhel*|*centos*)
      echo "  sudo dnf install python3.12 python3.12-pip"
      ;;
    *arch*)
      echo "  sudo pacman -S python"
      ;;
    *alpine*)
      echo "  sudo apk add python3 py3-pip"
      ;;
    *)
      echo "  # 用系统包管理器安装 Python 3.12，或改用 pyenv / conda / uv"
      echo "  # 下载地址 https://www.python.org/downloads/"
      ;;
  esac
}

report_no_python() {
  local reason="$1" index=0
  {
    echo
    printf '%s✗ %s%s\n' "$C_RED" "${reason:-没有找到可用的 Python}" "$C_RESET"
    printf '  %sNovaBot 需要 Python 3.12 或更高版本，当前环境不满足。%s\n' "$C_DIM" "$C_RESET"
    echo
    if [ "${#PY_CANDIDATES[@]}" -eq 0 ]; then
      echo "没有检测到任何 Python 解释器。"
    else
      if [ "${#PY_REJECTED_PATHS[@]}" -gt 0 ]; then
        echo "已检测到但版本不足："
        while [ "$index" -lt "${#PY_REJECTED_PATHS[@]}" ]; do
          printf '  %-9s %s\n' "${PY_REJECTED_VERS[$index]}" "${PY_REJECTED_PATHS[$index]}"
          index=$((index + 1))
        done
      fi
      if [ "${#PY_OK_PATHS[@]}" -gt 0 ]; then
        index=0
        echo "本机可用的 3.12+ 解释器："
        while [ "$index" -lt "${#PY_OK_PATHS[@]}" ]; do
          printf '  %-9s %s\n' "${PY_OK_VERS[$index]}" "${PY_OK_PATHS[$index]}"
          index=$((index + 1))
        done
      fi
    fi
    echo
    printf '%s请先安装 Python 3.12：%s\n' "$C_BOLD" "$C_RESET"
    print_python_install_hint
    echo
    printf '%s装好后重跑本脚本，或直接指定解释器：%s\n' "$C_BOLD" "$C_RESET"
    echo "  ./install.sh --python /path/to/python3.12"
    echo
    printf '%s已经用 pyenv / conda / uv 管理 Python 的话：%s\n' "$C_DIM" "$C_RESET"
    echo "  pyenv install 3.12 && ./install.sh --python \"\$(pyenv root)/versions/3.12.9/bin/python3.12\""
    echo "  conda create -n novabot python=3.12"
    echo "  uv python install 3.12 && ./install.sh --python \"\$(uv python find 3.12)\""
  } >&2
}

# 密码/密钥不回显；没终端时不读（非交互路径不会调用）
ask_secret() {
  local prompt="$1" answer
  if [ "$INTERACTIVE" -eq 0 ]; then
    return 0
  fi
  printf '%s?%s %s: ' "$C_CYAN" "$C_RESET" "$prompt" >&2
  IFS= read -r -s answer || true
  printf '\n' >&2
  printf '%s' "$answer"
}

# 从接口地址推导供应商 code：api.deepseek.com → deepseek，IP/localhost → local。
# 与内置 provider 同名是允许的：用户配置按 code 覆盖内置那份。
provider_code_from_url() {
  local host labels label picked=""
  host="$(printf '%s' "$1" | sed -E 's#^[A-Za-z][A-Za-z0-9+.-]*://##; s#[/?#].*$##; s#^.*@##')"
  host="${host%%:*}"
  host="$(printf '%s' "$host" | tr '[:upper:]' '[:lower:]')"
  case "$host" in
    ""|localhost|127.*|0.0.0.0|"[::1]"|::1) printf '%s' "local"; return ;;
  esac
  IFS='.' read -r -a labels <<< "$host"
  for label in "${labels[@]}"; do
    case "$label" in
      api|www|openai|com|net|org|io|ai|cn|co|dev) continue ;;
    esac
    picked="$label"
    break
  done
  [ -n "$picked" ] || picked="provider"
  printf '%s' "$picked" | tr -c 'a-z0-9_-' '-' | sed -E 's#^-+|-+$##g'
}

# 生成最小用户配置：只写差异，其余默认值来自内置 config/config.json。
# 值走环境变量传进 Python，避免引号/换行把 JSON 或 shell 弄坏。
write_config_file() {
  local path="$1"
  NOVABOT_CFG_MULTI_USER="$MULTI_USER_CHOICE" \
  NOVABOT_CFG_BASE_URL="$PROVIDER_BASE_URL" \
  NOVABOT_CFG_KEY="$PROVIDER_KEY" \
  NOVABOT_CFG_MODEL="$PROVIDER_MODEL" \
  NOVABOT_CFG_PROVIDER="$PROVIDER_CODE" \
  "$PY" - "$path" <<'PY'
import json
import os
import sys

path = sys.argv[1]
base_url = (os.environ.get("NOVABOT_CFG_BASE_URL") or "").strip()
model = (os.environ.get("NOVABOT_CFG_MODEL") or "").strip()
provider = (os.environ.get("NOVABOT_CFG_PROVIDER") or "").strip() or "provider"

data = {"multi_user": os.environ.get("NOVABOT_CFG_MULTI_USER") == "1"}
if base_url:
    entry = {
        "name": provider,
        "base_url": base_url,
        "api": "openai-completions",
        "key": os.environ.get("NOVABOT_CFG_KEY") or "",
    }
    if model:
        entry["models"] = [{"code": model, "name": model, "stream": True}]
        data["agent"] = {"model": f"{provider}/{model}"}
    else:
        # 没有模型 code 时交给 /models 自动发现，首次启动用 /model 选
        entry["dynamic_models"] = True
    data["models"] = {provider: entry}

tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=4)
    handle.write("\n")
os.replace(tmp, path)
PY
}

# 当前已激活的虚拟环境 / Conda 环境（不自动选 base）
ACTIVE_ENV=""
ACTIVE_KIND=""
if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
  ACTIVE_ENV="$VIRTUAL_ENV"
  ACTIVE_KIND="virtualenv"
elif [ -n "${CONDA_PREFIX:-}" ] && [ "${CONDA_DEFAULT_ENV:-}" != "base" ] \
     && [ -x "$CONDA_PREFIX/bin/python" ]; then
  ACTIVE_ENV="$CONDA_PREFIX"
  ACTIVE_KIND="Conda"
fi

while [ "$#" -gt 0 ]; do
  case "$1" in
    --venv)
      require_value "$@"
      VENV_DIR="$2"
      USE_VENV=1
      VENV_SET=1
      shift 2
      ;;
    --no-venv)
      USE_VENV=0
      VENV_SET=1
      shift
      ;;
    --python)
      require_value "$@"
      PYTHON_BIN="$2"
      PYTHON_SET=1
      shift 2
      ;;
    --bin-dir)
      require_value "$@"
      BIN_DIR="$2"
      BIN_SET=1
      shift 2
      ;;
    --name)
      require_value "$@"
      CMD_NAME="$2"
      NAME_SET=1
      shift 2
      ;;
    --skip-deps)
      SKIP_DEPS=1
      DEPS_SET=1
      shift
      ;;
    --init-config)
      INIT_CONFIG=1
      CONFIG_SET=1
      shift
      ;;
    --non-interactive)
      NON_INTERACTIVE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "未知参数: $1（用 --help 查看用法）"
      ;;
  esac
done

[ -f "$PROJECT_DIR/main.py" ] || die "找不到 $PROJECT_DIR/main.py，请在源码目录内运行"
[ -f "$PROJECT_DIR/requirements.txt" ] || die "找不到 requirements.txt"
[ -n "${HOME:-}" ] || die "HOME 未设置，无法确定默认安装目录"

if [ "$NON_INTERACTIVE" -eq 0 ] && [ -t 0 ]; then
  INTERACTIVE=1
fi

# 先探明解释器再问任何问题：这一段的失败必须早于所有交互和磁盘写入
collect_python_candidates
classify_python_candidates

PY_CHOSEN=""
PY_CHOSEN_VERSION=""

if [ "$PYTHON_SET" -eq 1 ]; then
  # 显式指定的必须真的可用：静默换成另一个解释器，用户会以为自己用的是指定的那个
  explicit="$(expand_path "$PYTHON_BIN")"
  explicit_version="$(python_version "$explicit")"
  if [ -z "$explicit_version" ] || ! python_meets_floor "$explicit"; then
    report_no_python "指定的解释器不可用：$PYTHON_BIN${explicit_version:+（版本 $explicit_version）}"
    exit 2
  fi
  PY_CHOSEN="$(python_real_path "$explicit")"
  PY_CHOSEN_VERSION="$explicit_version"
elif [ "${#PY_OK_PATHS[@]}" -eq 0 ]; then
  report_no_python ""
  exit 2
elif [ "$INTERACTIVE" -eq 0 ]; then
  PY_CHOSEN="${PY_OK_PATHS[0]}"
  PY_CHOSEN_VERSION="${PY_OK_VERS[0]}"
fi

# venv 是单独的包（Debian/Ubuntu 上是 python3.12-venv）：缺它时 `-m venv` 只会
# 抛 ensurepip 错误，最后被误读成"虚拟环境不可用"。这里先探明，选到用 venv 时
# 给出准确提示，而不是让用户自己猜。
if [ -n "$PY_CHOSEN" ]; then
  PYTHON_BIN="$PY_CHOSEN"
  VENV_MODULE_OK=1
  "$PYTHON_BIN" -c 'import venv' >/dev/null 2>&1 || VENV_MODULE_OK=0
fi

# 非交互默认：优先复用当前环境，否则建项目 .venv
if [ "$VENV_SET" -eq 0 ]; then
  if [ -n "$ACTIVE_ENV" ]; then
    VENV_DIR="$ACTIVE_ENV"
  else
    VENV_DIR="$PROJECT_DIR/.venv"
  fi
fi

if [ "$INTERACTIVE" -eq 1 ]; then
  ui_banner

  # 多个合格解释器时让用户选；只有一个就不打扰，直接在摘要里显示版本
  if [ -z "$PY_CHOSEN" ]; then
    py_count="${#PY_OK_PATHS[@]}"
    py_index=1
    # 标"当前环境"要按真实路径比：激活环境本身是 3.10 时，列表第一项其实是 PATH
    # 上的另一个解释器，照位置贴标签会指错
    py_active_real=""
    if [ -n "$ACTIVE_ENV" ] && [ -x "$ACTIVE_ENV/bin/python" ]; then
      py_active_real="$(python_real_path "$ACTIVE_ENV/bin/python")"
    fi
    ui_step "选择 Python 解释器"
    while [ "$py_index" -le "$py_count" ]; do
      py_hint=""
      if [ -n "$py_active_real" ] && [ "${PY_OK_PATHS[$((py_index - 1))]}" = "$py_active_real" ]; then
        py_hint="（当前环境）"
      fi
      printf '  %s%d)%s %-9s %s%s\n' "$C_CYAN" "$py_index" "$C_RESET" \
        "${PY_OK_VERS[$((py_index - 1))]}" "${PY_OK_PATHS[$((py_index - 1))]}" "$py_hint"
      py_index=$((py_index + 1))
    done
    printf '  %s%d)%s 手动输入路径\n' "$C_CYAN" "$((py_count + 1))" "$C_RESET"
    py_pick="$(ask "请选择" "1")"
    case "$py_pick" in
      ""|*[!0-9]*) die "无效选项: $py_pick" ;;
    esac
    if [ "$py_pick" -ge 1 ] && [ "$py_pick" -le "$py_count" ]; then
      PY_CHOSEN="${PY_OK_PATHS[$((py_pick - 1))]}"
      PY_CHOSEN_VERSION="${PY_OK_VERS[$((py_pick - 1))]}"
    elif [ "$py_pick" -eq "$((py_count + 1))" ]; then
      py_manual="$(expand_path "$(ask "解释器路径" "$PYTHON_BIN")")"
      py_manual_version="$(python_version "$py_manual")"
      if [ -z "$py_manual_version" ] || ! python_meets_floor "$py_manual"; then
        report_no_python "指定的解释器不可用：$py_manual${py_manual_version:+（版本 $py_manual_version）}"
        exit 2
      fi
      PY_CHOSEN="$(python_real_path "$py_manual")"
      PY_CHOSEN_VERSION="$py_manual_version"
    else
      die "无效选项: $py_pick"
    fi
    PYTHON_BIN="$PY_CHOSEN"
    VENV_MODULE_OK=1
    "$PYTHON_BIN" -c 'import venv' >/dev/null 2>&1 || VENV_MODULE_OK=0
  fi

  if [ "$VENV_SET" -eq 0 ]; then
    ui_step "选择 Python 环境"
    if [ "$VENV_MODULE_OK" -eq 0 ]; then
      printf '  %s! %s 缺少 venv 模块，无法创建虚拟环境；Debian/Ubuntu 可安装 python3.12-venv%s\n' \
        "$C_YELLOW" "$PYTHON_BIN" "$C_RESET"
      echo
    fi
    ui_option 1 "创建/使用项目 .venv"
    if [ -n "$ACTIVE_ENV" ]; then
      ui_option 2 "使用当前 $ACTIVE_KIND 环境  $ACTIVE_ENV"
    fi
    ui_option 3 "使用已有虚拟环境路径"
    ui_option 4 "不使用虚拟环境，直接用当前 Python"
    choice="$(ask "请选择" "1")"
    case "$choice" in
      1)
        VENV_DIR="$PROJECT_DIR/.venv"
        USE_VENV=1
        ;;
      2)
        [ -n "$ACTIVE_ENV" ] || die "当前没有检测到可复用的虚拟环境"
        VENV_DIR="$ACTIVE_ENV"
        USE_VENV=1
        ;;
      3)
        VENV_DIR="$(ask "虚拟环境路径" "$HOME/venvs/novabot")"
        USE_VENV=1
        ;;
      4)
        USE_VENV=0
        ;;
      *)
        die "无效选项: $choice"
        ;;
    esac
  fi

  # 三个问题全被命令行参数固定时不留空标题
  if [ "$DEPS_SET" -eq 0 ] || [ "$NAME_SET" -eq 0 ] || [ "$BIN_SET" -eq 0 ]; then
    ui_step "安装选项"
  fi
  if [ "$DEPS_SET" -eq 0 ]; then
    if ! ask_yes_no "安装 Python 依赖" "y"; then
      SKIP_DEPS=1
    fi
  fi

  if [ "$NAME_SET" -eq 0 ]; then
    CMD_NAME="$(ask "启动命令名" "$CMD_NAME")"
  fi

  if [ "$BIN_SET" -eq 0 ]; then
    BIN_DIR="$(ask "启动器安装目录" "$BIN_DIR")"
  fi

  CONFIG_DIR="$HOME/.novabot"
  CONFIG_FILE="$CONFIG_DIR/novabot.json"

  ui_step "配置文件"
  ui_kv "路径      " "$CONFIG_FILE"

  # 配置不存在就直接进入问答；已存在先问是否覆盖（覆盖前会备份）
  if [ "$CONFIG_SET" -eq 1 ]; then
    [ -f "$CONFIG_FILE" ] || WRITE_CONFIG=1
  elif [ ! -f "$CONFIG_FILE" ]; then
    WRITE_CONFIG=1
  elif ask_yes_no "已存在，是否重新生成（原文件会备份为 novabot.json.bak）" "n"; then
    WRITE_CONFIG=1
  fi

  if [ "$WRITE_CONFIG" -eq 1 ]; then
    ui_step "运行模式"
    ui_kv "单用户    " "本机一个身份，TUI 直接使用，不需要登录（默认）"
    ui_kv "多用户    " "账号注册/登录，数据按账号隔离；Web 控制台必须多用户"
    echo
    if ask_yes_no "启用多用户模式" "n"; then
      MULTI_USER_CHOICE=1
    fi

    echo
    if ask_yes_no "现在配置模型接口（OpenAI 兼容）" "y"; then
      PROVIDER_BASE_URL="$(ask "接口地址（如 https://api.deepseek.com，回车跳过）" "")"
      if [ -n "$PROVIDER_BASE_URL" ]; then
        PROVIDER_KEY="$(ask_secret "API Key（输入不回显，可留空）")"
        PROVIDER_MODEL="$(ask "模型 code（如 deepseek-flash；留空则首次启动用 /model 选择）" "")"
        PROVIDER_CODE="$(provider_code_from_url "$PROVIDER_BASE_URL")"
      fi
    fi
  fi

  ui_step "安装摘要"
  ui_kv "解释器    " "$PYTHON_BIN"
  ui_kv "版本      " "$PY_CHOSEN_VERSION"
  if [ "$USE_VENV" -eq 1 ]; then
    ui_kv "虚拟环境  " "$VENV_DIR"
  else
    ui_kv "虚拟环境  " "不使用"
  fi
  ui_kv "依赖      " "$([ "$SKIP_DEPS" -eq 1 ] && echo 跳过 || echo 安装)"
  ui_kv "命令      " "$BIN_DIR/$CMD_NAME"
  if [ "$WRITE_CONFIG" -eq 1 ]; then
    ui_kv "配置文件  " "生成 $CONFIG_FILE（$([ "$MULTI_USER_CHOICE" -eq 1 ] && echo 多用户 || echo 单用户)）"
    if [ -n "$PROVIDER_BASE_URL" ]; then
      ui_kv "模型接口  " "$PROVIDER_BASE_URL（$PROVIDER_CODE${PROVIDER_MODEL:+/$PROVIDER_MODEL}）"
    else
      ui_kv "模型接口  " "暂不配置"
    fi
  else
    ui_kv "配置文件  " "保留现有 $CONFIG_FILE"
  fi
  echo
  ask_yes_no "开始安装" "y" || exit 0
fi

if [ "$INTERACTIVE" -eq 0 ] && [ "$INIT_CONFIG" -eq 1 ]; then
  CONFIG_DIR="$HOME/.novabot"
  CONFIG_FILE="$CONFIG_DIR/novabot.json"
  if [ ! -f "$CONFIG_FILE" ]; then
    WRITE_CONFIG=1
  fi
fi

case "$CMD_NAME" in
  ""|.*|*[!A-Za-z0-9._-]*) die "--name 只能包含字母、数字、点、下划线或连字符，且不能以点开头" ;;
esac

VENV_DIR="$(expand_path "$VENV_DIR")"
BIN_DIR="$(expand_path "$BIN_DIR")"
PYTHON_BIN="$PY_CHOSEN"

# 探测阶段已经确认过版本，这里只补 venv 模块的最终判断：选了用 venv 却没有
# 这个模块时，`-m venv` 的报错会指向 ensurepip，与真实原因相隔一层。
if [ "$USE_VENV" -eq 1 ] && [ "$VENV_MODULE_OK" -eq 0 ]; then
  die "$PYTHON_BIN 缺少 venv 模块，无法创建虚拟环境。请安装对应的 venv 包（Debian/Ubuntu: sudo apt install python3.12-venv），或改用 --no-venv"
fi

if [ "$USE_VENV" -eq 1 ]; then
  if [ ! -d "$VENV_DIR" ]; then
    ui_progress "创建虚拟环境 $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  fi
  [ -x "$VENV_DIR/bin/python" ] || die "$VENV_DIR 不是可用的 POSIX 虚拟环境（缺少 bin/python）"
  VENV_DIR="$(cd "$VENV_DIR" && pwd -P)"
  PY="$VENV_DIR/bin/python"
else
  PY="$PYTHON_BIN"
fi

"$PY" - <<'PY' || die "$PY 的版本低于 3.12，无法运行 NovaBot"
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY

if ! "$PY" -m pip --version >/dev/null 2>&1; then
  ui_progress "虚拟环境缺少 pip，改用 ensurepip 安装"
  "$PY" -m ensurepip --upgrade >/dev/null
fi

if [ "$SKIP_DEPS" -eq 0 ]; then
  ui_progress "安装依赖 $PROJECT_DIR/requirements.txt"
  "$PY" -m pip install --upgrade pip
  "$PY" -m pip install -r "$PROJECT_DIR/requirements.txt"
else
  ui_progress "跳过依赖安装（--skip-deps）"
fi

mkdir -p "$BIN_DIR"
BIN_DIR="$(cd "$BIN_DIR" && pwd -P)"
LAUNCHER="$BIN_DIR/$CMD_NAME"

tmp_launcher="$LAUNCHER.tmp.$$"
{
  echo '#!/usr/bin/env bash'
  printf 'exec %q %q "$@"\n' "$PY" "$PROJECT_DIR/main.py"
} > "$tmp_launcher"
chmod 755 "$tmp_launcher"
mv "$tmp_launcher" "$LAUNCHER"

if [ "$WRITE_CONFIG" -eq 1 ]; then
  mkdir -p "$CONFIG_DIR"
  if [ -f "$CONFIG_FILE" ]; then
    cp -p "$CONFIG_FILE" "$CONFIG_FILE.bak"
    ui_progress "已备份原配置 $CONFIG_FILE.bak"
  fi
  write_config_file "$CONFIG_FILE"
  ui_progress "已生成配置文件 $CONFIG_FILE"
fi

echo
ui_ok "安装完成"
ui_kv "命令      " "$LAUNCHER"
ui_kv "项目      " "$PROJECT_DIR"
ui_kv "解释器    " "$PY（$PY_CHOSEN_VERSION）"
if [ "$USE_VENV" -eq 1 ]; then
  ui_kv "虚拟环境  " "$VENV_DIR"
fi
if [ "$WRITE_CONFIG" -eq 1 ]; then
  ui_kv "配置文件  " "$CONFIG_FILE（$([ "$MULTI_USER_CHOICE" -eq 1 ] && echo 多用户 || echo 单用户)）"
  if [ -z "$PROVIDER_BASE_URL" ]; then
    ui_warn "未配置模型接口：编辑 $CONFIG_FILE 的 models 段与 agent.model，否则无法开始对话"
    printf '  %s说明见 docs/model-config.md%s\n' "$C_DIM" "$C_RESET" >&2
  elif [ -z "$PROVIDER_MODEL" ]; then
    ui_kv "模型      " "首次启动后在 TUI 里用 /model 选择（已开启 /models 自动发现）"
  else
    ui_kv "模型      " "$PROVIDER_CODE/$PROVIDER_MODEL"
    if [ -z "$PROVIDER_KEY" ]; then
      ui_warn "API Key 为空：编辑 $CONFIG_FILE 补上 models.$PROVIDER_CODE.key"
    fi
  fi
  if [ "$MULTI_USER_CHOICE" -eq 1 ]; then
    ui_kv "多用户    " "首次启动先用本机 TUI 创建管理员，再开 Web 控制台"
  fi
elif [ -n "$CONFIG_FILE" ]; then
  ui_kv "配置文件  " "$CONFIG_FILE（保留现有内容，未修改）"
  printf '  %s需要改多用户或模型接口时直接编辑它；说明见 docs/model-config.md%s\n' \
    "$C_DIM" "$C_RESET"
fi

printf '\n  %s试运行%s\n' "$C_DIM" "$C_RESET"
printf '    %s\n' "$CMD_NAME"
printf '    %s\n' "$CMD_NAME web --host 127.0.0.1 --port 9000"

case ":${PATH:-}:" in
  *":$BIN_DIR:"*) ;;
  *)
    echo
    ui_warn "$BIN_DIR 不在 PATH 中，把它加入 shell 配置："
    printf '  export PATH="%s:$PATH"\n' "$BIN_DIR"
    ;;
esac
