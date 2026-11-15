#!/usr/bin/env bash
# NovaBot 卸载脚本：默认只删除启动器。
# 配置与数据（~/.novabot）和项目内 .venv 必须显式开启才会删除。
#
#   ./uninstall.sh
#   ./uninstall.sh --purge --remove-venv
#   ./uninstall.sh --help

set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
BIN_DIR="${HOME:-}/.local/bin"
CMD_NAME="novabot"
NON_INTERACTIVE=0
INTERACTIVE=0
PURGE=0
REMOVE_VENV=0

usage() {
  cat <<EOF
用法: ./uninstall.sh [选项]

默认只删除启动器，配置、数据和虚拟环境都会保留。

  --bin-dir PATH      启动器所在目录（默认：~/.local/bin）
  --name NAME         启动命令名（默认：novabot）
  --purge             删除 ~/.novabot，删除前备份为 ~/.novabot.bak-<时间戳>
  --remove-venv       删除项目内 .venv（不会碰安装时指定的外部环境）
  --non-interactive   不提问，只执行以上显式选项
  -h, --help          显示帮助

示例:
  ./uninstall.sh
  ./uninstall.sh --purge
  ./uninstall.sh --name novabot --bin-dir ~/.local/bin
EOF
}

die() {
  echo "错误: $*" >&2
  exit 1
}

expand_path() {
  case "$1" in
    "~") printf '%s' "$HOME" ;;
    "~/") printf '%s/' "$HOME" ;;
    "~/"*) printf '%s/%s' "$HOME" "${1#\~/}" ;;
    *) printf '%s' "$1" ;;
  esac
}

ask_yes_no() {
  local prompt="$1" default="$2" answer
  if [ "$INTERACTIVE" -eq 0 ]; then
    [ "$default" = "y" ]
    return
  fi
  printf '%s [%s]: ' "$prompt" "$default" >&2
  IFS= read -r answer || true
  answer="${answer:-$default}"
  case "$answer" in
    y|Y|yes|YES) return 0 ;;
    n|N|no|NO) return 1 ;;
    *) [ "$default" = "y" ] ;;
  esac
}

while [ $# -gt 0 ]; do
  case "$1" in
    --bin-dir)
      [ $# -ge 2 ] || die "--bin-dir 需要一个路径"
      BIN_DIR="$2"
      shift 2
      ;;
    --bin-dir=*)
      BIN_DIR="${1#*=}"
      shift
      ;;
    --name)
      [ $# -ge 2 ] || die "--name 需要一个名称"
      CMD_NAME="$2"
      shift 2
      ;;
    --name=*)
      CMD_NAME="${1#*=}"
      shift
      ;;
    --purge)
      PURGE=1
      shift
      ;;
    --remove-venv)
      REMOVE_VENV=1
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

[ -n "${HOME:-}" ] || die "HOME 未设置，无法确定默认安装目录"
case "$CMD_NAME" in
  ""|.*|*[!A-Za-z0-9._-]*) die "--name 只能包含字母、数字、点、下划线或连字符，且不能以点开头" ;;
esac

if [ "$NON_INTERACTIVE" -eq 0 ] && [ -t 0 ]; then
  INTERACTIVE=1
fi

BIN_DIR="$(expand_path "$BIN_DIR")"
DATA_DIR="$HOME/.novabot"
VENV_DIR="$PROJECT_DIR/.venv"
LAUNCHER="$BIN_DIR/$CMD_NAME"

REMOVE_LAUNCHER=1
DELETED=()
KEPT=()

if [ "$INTERACTIVE" -eq 1 ]; then
  echo "NovaBot 卸载"
  echo
  echo "将删除:"
  echo "  启动器  $LAUNCHER"
  echo "将保留:"
  echo "  配置与数据  $DATA_DIR"
  echo "  虚拟环境    $VENV_DIR"
  echo

  if ! ask_yes_no "删除启动器?" "y"; then
    REMOVE_LAUNCHER=0
  fi
  if [ "$PURGE" -eq 0 ] && ask_yes_no "同时删除 $DATA_DIR（删除前会备份）?" "n"; then
    PURGE=1
  fi
  if [ "$REMOVE_VENV" -eq 0 ] && ask_yes_no "同时删除项目内 .venv?" "n"; then
    REMOVE_VENV=1
  fi
  echo
fi

if [ "$REMOVE_LAUNCHER" -eq 1 ]; then
  if [ -e "$LAUNCHER" ]; then
    rm -f -- "$LAUNCHER" || die "无法删除启动器: $LAUNCHER"
    echo "已删除启动器: $LAUNCHER"
    DELETED+=("启动器 $LAUNCHER")
  else
    echo "启动器不存在，跳过: $LAUNCHER"
  fi
else
  echo "保留启动器: $LAUNCHER"
  KEPT+=("启动器 $LAUNCHER")
fi

if [ "$PURGE" -eq 1 ]; then
  if [ -d "$DATA_DIR" ]; then
    BACKUP="$DATA_DIR.bak-$(date +%Y%m%d-%H%M%S)"
    if [ -e "$BACKUP" ]; then
      BACKUP="$BACKUP-$$"
    fi
    if ! mv -- "$DATA_DIR" "$BACKUP"; then
      die "备份失败，未删除数据目录: $DATA_DIR"
    fi
    echo "已备份并删除数据目录: $DATA_DIR -> $BACKUP"
    DELETED+=("数据目录 $DATA_DIR（备份 $BACKUP）")
  else
    echo "数据目录不存在，跳过: $DATA_DIR"
  fi
else
  echo "保留数据目录: $DATA_DIR"
  KEPT+=("数据目录 $DATA_DIR")
fi

if [ "$REMOVE_VENV" -eq 1 ]; then
  if [ -d "$VENV_DIR" ]; then
    rm -rf -- "$VENV_DIR" || die "无法删除虚拟环境: $VENV_DIR"
    echo "已删除虚拟环境: $VENV_DIR"
    DELETED+=("虚拟环境 $VENV_DIR")
  else
    echo "项目内虚拟环境不存在，跳过: $VENV_DIR"
  fi
else
  echo "保留虚拟环境: $VENV_DIR"
  KEPT+=("虚拟环境 $VENV_DIR")
fi

echo
echo "卸载完成"
if [ "${#DELETED[@]}" -gt 0 ]; then
  echo "已删除:"
  for item in "${DELETED[@]}"; do
    echo "  $item"
  done
else
  echo "没有删除任何内容"
fi
if [ "${#KEPT[@]}" -gt 0 ]; then
  echo "已保留:"
  for item in "${KEPT[@]}"; do
    echo "  $item"
  done
fi
echo
echo "提示: 配置与数据只在 --purge 时删除；安装时指定的外部虚拟环境不受本脚本管理。"
