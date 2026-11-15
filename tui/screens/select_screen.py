# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : select_screen.py
# @Software    : VSCode
# @Datetime    : 2026/09/29 17:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 通用居中选择弹窗（ModalScreen），返回选中value，Esc返回None

from typing import Callable, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option
from tui.i18n import t

DEFAULT_HINT = "↑↓ 选择    Enter 确认    Esc 取消"


class SelectScreen(ModalScreen[Optional[str]]):
    """窗体居中的滚动选择弹窗：↑↓选择，Enter确认，Esc取消；on_delete支持Ctrl+D删除；filterable带搜索过滤"""

    CSS = """
    #select-center {
        width: 100%;
        height: 100%;
        align: center middle;
    }

    #select-panel {
        width: 74;
        min-width: 50;
        max-width: 90%;
        height: auto;
        max-height: 100%;
        padding: 1 2;
        background: $surface;
        border: round $primary;
    }

    #select-title {
        width: 100%;
        height: auto;
        margin-bottom: 1;
        text-style: bold;
        color: $foreground;
    }

    #select-search {
        width: 100%;
        height: auto;
        margin-bottom: 1;
        padding: 0 1;
        background: $surface;
        color: $foreground;
        border: none;
    }

    #select-search:focus {
        border: none;
    }

    #select-search--placeholder {
        color: $foreground-muted;
    }

    #select-options {
        width: 100%;
        height: 9;
        background: $surface;
        border: none;
        padding: 0;
    }

    #select-options:focus {
        border: none;
    }

    #select-options > .option-list--option {
        padding: 0 3;
    }

    #select-options > .option-list--option-highlighted {
        background: $accent;
        color: $background;
        text-style: bold;
    }

    #select-options > .option-list--option-disabled {
        color: $foreground-muted;
    }

    #select-hint {
        width: 100%;
        height: auto;
        margin-top: 1;
        color: $foreground-muted;
        content-align: right middle;
    }

    #boost-probe {
        height: 0;
        display: none;
        background: $boost;
    }
    """

    # priority=True：按键优先走弹窗绑定，不被聚焦widget的按键处理抢占
    BINDINGS = [
        Binding("up", "cursor_up", "Up", priority=True),
        Binding("down", "cursor_down", "Down", priority=True),
        Binding("enter", "choose", "Choose", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("ctrl+d", "delete", "Delete", priority=True),
    ]

    def __init__(self, title: str, items: list, hint: str=DEFAULT_HINT, on_delete: Optional[Callable[[str], None]]=None, filterable: bool=False, items_factory: Optional[Callable[[dict], list]]=None, content_highlight: bool=True, selected: Optional[str]=None, empty_text: str="", **kwargs):
        super().__init__(**kwargs)
        self._title = t(title)
        self._hint = t(hint)
        self._on_delete = on_delete
        self._filterable = filterable
        self._items_factory = items_factory
        # 过滤后没有任何命中时显示的占位项；空字符串表示不显示
        self._empty_text = t(empty_text) if empty_text else ""
        # 内容层自绘高亮背景（需随光标重建，适合少量条目）；
        # False=高亮交给OptionList的widget层CSS，光标移动零重建，适合大列表
        self._content_highlight = content_highlight
        self._highlight_cache: Optional[int] = None
        # selected: 初始光标落在哪个value上（如"当前语言/当前模型"），找不到时退回首个可用项
        self._hl_id: Optional[str] = str(selected) if selected else None
        self._rendered_hl_id: Optional[str] = None
        self._term: str = ""
        # items支持 (value, Text[, disabled[, search_text]]) 元组 或 Option对象（分组标题等）
        self._items = items

    def compose(self) -> ComposeResult:
        with Container(id="select-center"):
            with Container(id="select-panel"):
                yield Label(self._title, id="select-title")
                if self._filterable:
                    yield Input(placeholder=t(t("搜索")), id="select-search")
                yield OptionList(id="select-options")
                yield Label(f"[dim]{self._hint}[/dim]", id="select-hint")
                yield Label(id="boost-probe")

    # 主题色经CSS变量解析（display:none探针widget），挂载后可取
    # boost是带alpha的叠加色，rich不认8位hex需与底色合成；斑马带用中性微亮色（$panel带蓝调不适合做间隔色）
    def _theme_ctx(self) -> dict:
        from textual.color import Color

        options = self.query_one("#select-options")
        surface = options.styles.background or Color.parse("#1e1e1e")

        try:
            boost = self.query_one("#boost-probe").styles.background
            boost_hex = surface.blend(boost, boost.a).hex6 if boost and boost.a < 1.0 else (boost.hex6 if boost else "#303030")
        except Exception:
            boost_hex = "#303030"

        tint = Color(255, 255, 255, 1) if self.app.current_theme.dark else Color(0, 0, 0, 1)
        # current(当前会话底)与zebra(斑马带)拉开明显差距，避免当前行与普通行难以区分
        # accent/highlighted_id供内容层渲染光标行背景：rich内容样式会盖过组件样式背景，
        # 有自定义底色的行必须由内容层自己画highlight背景，否则选中效果被遮挡
        return {
            "current": surface.blend(tint, 0.12).hex6,
            "boost": boost_hex,
            "zebra": surface.blend(tint, 0.03).hex6,
            "accent": (self.app.current_theme.accent or "#ffa62b").lower(),
            "highlighted_id": self._hl_id,
            # 选项内容可用宽度（扣除.option-list--option左右padding 0 3），供两端对齐填充
            "avail": max(options.size.width - 6, 20),
        }

    # 按搜索词过滤并重建列表；search_text缺省用value，大小写不敏感子串匹配
    def _rebuild(self, term: str="") -> None:
        # on_mount 的 call_after_refresh 可能晚于弹窗关闭：DOM 已拆时直接放弃这次重建
        options = self.query("#select-options")
        if not options:
            return
        options = options.first()

        term = (term or "").strip().lower()
        self._term = term
        items = self._items_factory(self._theme_ctx()) if self._items_factory else self._items

        # 先按“分组标题 → 内容项”切块：标题（disabled 且无 id 的 Option）只属于
        # 它后面那一组。搜索后某组没有命中项时，标题和它前面的空行一起丢掉。
        blocks: list[tuple[list, list]] = []
        for item in items:
            if isinstance(item, Option) and item.id is None:
                if blocks and blocks[-1][1] and not blocks[-1][0]:
                    blocks[-1][0].append(item)
                else:
                    blocks.append(([item], []))
                continue
            if not blocks:
                blocks.append(([], []))
            blocks[-1][1].append(item)

        opts: list = []
        for headers, entries in blocks:
            matched: list = []
            for item in entries:
                if isinstance(item, Option):
                    # 可操作项（如“添加”）不参与过滤，搜索时也要能点到
                    matched.append(item)
                    continue

                search_text = str(item[3]).lower() if len(item) > 3 and item[3] else str(item[0]).lower()
                if term and term not in search_text:
                    continue
                matched.append(item)

            if not matched:
                continue
            opts.extend(headers)
            for item in matched:
                if isinstance(item, Option):
                    opts.append(item)
                else:
                    opts.append(Option(item[1], id=item[0], disabled=len(item) > 2 and item[2]))

        if not opts and term and self._empty_text:
            opts.append(Option(self._empty_text, disabled=True))

        options.clear_options()
        options.add_options(opts)

        # 优先恢复原光标位置（过滤或重建后），否则落到首个可用项
        keep = next((o for o in opts if o.id == self._hl_id and not o.disabled), None)
        target = keep or next((o for o in opts if not o.disabled), None)
        self._hl_id = str(target.id) if target and target.id else None
        # 记录本次渲染实际画accent背景的行；与_hl_id不一致说明还需重建
        rendered_before = self._rendered_hl_id
        self._rendered_hl_id = self._hl_id
        options.highlighted = opts.index(target) if target else None

        if self._content_highlight and rendered_before != self._rendered_hl_id and self._items_factory:
            # 首帧/过滤后光标位置变化：重建一次让内容层画上accent背景
            self._rebuild(self._term)

    # 光标移动：内容层工厂列表需重建渲染highlight背景；同时清除错误提示
    # 外部数据变化后原地重建列表（保持当前搜索词与光标位置）
    def refresh_items(self) -> None:
        self._rebuild(self._term)

    def on_option_list_option_highlighted(self, event) -> None:
        self.query_one("#select-hint", Label).update(f"[dim]{self._hint}[/dim]")

        if event.option_id is None:
            return
        new_id = str(event.option_id)
        self._hl_id = new_id
        # 仅内容层自绘高亮需要随光标重建；widget层CSS高亮则零重建直接移动
        if self._content_highlight and self._items_factory and new_id != self._rendered_hl_id:
            self._rebuild(self._term)

    def on_mount(self) -> None:
        self._rebuild()
        # 首帧mount时size未定（avail按保底值填充），布局完成后重建一次校正两端对齐
        self.call_after_refresh(self._rebuild, self._term)

        options = self.query_one("#select-options", OptionList)
        # OptionList不取焦点：避免其自带down绑定绕过本屏action导致提示无法复位
        options.can_focus = False

        if self._filterable:
            self.query_one("#select-search", Input).focus()
        else:
            self.set_focus(self)

    def on_resize(self, event) -> None:
        # 宽度变化重排两端对齐；仅factory模式列表需要
        if self._items_factory:
            self._rebuild(self._term)

    def on_input_changed(self, event) -> None:
        if event.input.id == "select-search":
            self._rebuild(event.value)

    def on_show(self) -> None:
        self.set_timer(0.1, self._watch_highlight)

    # 轮询光标位置：移动后清除错误提示恢复默认hint
    def _watch_highlight(self) -> None:
        hint = self.query_one("#select-hint", Label)
        if self._highlight_cache != self._current_highlight():
            self._highlight_cache = self._current_highlight()
            hint.update(f"[dim]{self._hint}[/dim]")
        self.set_timer(0.1, self._watch_highlight)

    def _current_highlight(self) -> Optional[int]:
        return self.query_one("#select-options", OptionList).highlighted

    def _current_value(self) -> Optional[str]:
        selected = self.query_one("#select-options", OptionList).highlighted_option
        if selected is not None and selected.id and not selected.disabled:
            return str(selected.id)
        return None

    def action_cursor_up(self) -> None:
        self.query_one("#select-options", OptionList).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#select-options", OptionList).action_cursor_down()

    def action_choose(self) -> None:
        value = self._current_value()
        self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_delete(self) -> None:
        if self._on_delete is None:
            return
        value = self._current_value()
        if value:
            self._on_delete(value)

    def set_hint_error(self, text: str) -> None:
        self.query_one("#select-hint", Label).update(f"[red]{text}[/red]")

    # OptionList自带enter绑定且点击会发OptionSelected，统一走dismiss
    def on_option_list_option_selected(self, event) -> None:
        event.stop()
        if event.option_id and not event.option.disabled:
            self.dismiss(str(event.option_id))


class ConfirmScreen(ModalScreen[bool]):
    """居中确认弹窗：Enter确认返回True，Esc取消返回False"""

    CSS = """
    #confirm-center {
        width: 100%;
        height: 100%;
        align: center middle;
    }

    #confirm-panel {
        width: auto;
        min-width: 40;
        max-width: 80%;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: round $warning;
    }

    #confirm-message {
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }

    #confirm-hint {
        width: 100%;
        height: auto;
        color: $foreground-muted;
        content-align: right middle;
    }
    """

    BINDINGS = [
        Binding("enter", "yes", "Yes", priority=True),
        Binding("escape", "no", "No", priority=True),
    ]

    def __init__(self, message: str, **kwargs):
        super().__init__(**kwargs)
        self._message = message

    def compose(self) -> ComposeResult:
        with Container(id="confirm-center"):
            with Container(id="confirm-panel"):
                yield Label(self._message, id="confirm-message")
                yield Label(t("[dim]Enter 确认    Esc 取消[/dim]"), id="confirm-hint")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)
