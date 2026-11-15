"""TUI 国际化：中文原文作 key，英文词典放在 i18n.en.json。

为什么不用 gettext：TUI 文案少、需要运行时切换，且中文原文本身就是最可读的 key；
缺 key 直接回退中文，不会出现空白界面。语言选择持久化在 APP_DATA/ui.json。
"""

import json
import os
from pathlib import Path
from typing import Any

from config import config

_DICT_PATH = Path(__file__).with_name("i18n.en.json")
_EN: dict[str, str] = json.loads(_DICT_PATH.read_text(encoding="utf-8"))

_LANGUAGE: str | None = None
LANGUAGES = ("zh", "en")
LANGUAGE_NAMES = {"zh": "中文", "en": "English"}


def _ui_config_path() -> Path:
    return Path(config.APP_DATA_PATH) / "ui.json"


def _read_ui_config() -> dict:
    try:
        return json.loads(_ui_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _persist(language: str) -> None:
    path = _ui_config_path()
    data = _read_ui_config()
    data["language"] = language
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def get_language() -> str:
    """显式设置 > NOVABOT_LANG > ui.json > 中文。"""
    global _LANGUAGE
    if _LANGUAGE is not None:
        return _LANGUAGE

    env = os.environ.get("NOVABOT_LANG", "").strip().lower()
    if env.startswith("en"):
        _LANGUAGE = "en"
    elif env.startswith("zh"):
        _LANGUAGE = "zh"
    else:
        saved = str(_read_ui_config().get("language", "")).strip().lower()
        _LANGUAGE = "en" if saved.startswith("en") else "zh"

    return _LANGUAGE


def set_language(language: str, *, save: bool = True) -> str:
    global _LANGUAGE
    _LANGUAGE = "en" if str(language).strip().lower().startswith("en") else "zh"
    if save:
        _persist(_LANGUAGE)
    return _LANGUAGE


def reset_cache() -> None:
    """测试用：让下次 get_language() 重新读环境与 ui.json。"""
    global _LANGUAGE
    _LANGUAGE = None


def t(text: str, *args: Any, **kwargs: Any) -> str:
    """翻译一条文案。

    中文模式返回原文；英文找不到时回退原文。带占位符的模板由调用方传参，
    与 Python str.format 语义一致（`{!r}`、`{:.1f}` 等格式说明原样保留）。
    """
    out = text if get_language() == "zh" else _EN.get(text, text)
    if args or kwargs:
        try:
            out = out.format(*args, **kwargs)
        except (IndexError, KeyError, ValueError):
            # 模板与参数不匹配时宁可显示原文，也不要让界面因翻译崩掉
            return text
    return out
