# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The icarus bar: a chat with icarus over the current tab.

A tray that comes up from the bottom like the tab picker (the completion
overlay: full width, above the statusbar, completion.height tall), so the
page it drives stays in view above it. Inside are the cards of
ChatGPT.nvim's chat: a transcript card titled ``ICARUS · model``, an input
card below it whose title chip is the input's vi mode, and with
``<Ctrl+o>`` a models card on the right to swap the chat model between the
local fleet and cloud. Painted with the glass chrome of the other menus
(mainwindow/glass.py).

Each tab carries its own conversation (``tab.data.icarus``); icarus serve
keeps the matching session and drives the tab itself (laya) when a submit
asks for something to be done on the page. The bar runs in icarus mode,
which passes keys through to it; its own keys follow the nvim chat:

    <Return>        send (INSERT), <Shift+Return> a new line
    <Escape>        INSERT -> NORMAL; in NORMAL, close (q too)
    NORMAL          vim on the input (icarus/vim.py): motions, d/c/y, p,
                    u and <Ctrl+r>, i/a/I/A/o/O; a j/k/gg/G the input
                    cannot take scrolls the transcript
    <Ctrl+d>/<Ctrl+u>  scroll the transcript
    <Ctrl+c>        stop a running turn, else close
    <Ctrl+o>        the models card; <Tab> moves between the cards
    <Ctrl+n>        new session for this tab
    <Ctrl+y>        yank the last answer
"""

import html

from qutebrowser.qt.core import (QEvent, QObject, QRect, QSize, Qt, QUrl,
                                 pyqtSignal, pyqtSlot)
from qutebrowser.qt.gui import QKeyEvent, QPainter, QTextCursor
from qutebrowser.qt.widgets import (QLineEdit, QListWidget, QListWidgetItem,
                                    QPlainTextEdit, QSizePolicy, QTextBrowser,
                                    QWidget)

from qutebrowser.api import cmdutils
from qutebrowser.config import config, stylesheet
from qutebrowser.icarus import client as clientmod
from qutebrowser.icarus import conversation
from qutebrowser.icarus import vim
from qutebrowser.keyinput import modeman
from qutebrowser.mainwindow import glass
from qutebrowser.utils import message, objreg, usertypes, utils

#: The text rendering of icarus.png, the nvim chat's welcome banner.
BANNER = """\
                   ▗▄▖   ▟
             ▄▄▄▖  ▝▜▙  ▟▛
        ▄▄▄▖  ▝▜▙   ▜▙ ▟▛
   ▄▄▄▄▖  ▝▜▙  ▜▙  ▜▙▟▛
       ▝▀▜▙  ▜▙ ▜▙ ▜▛
           ▝▀▜▙▜▙▜▙▛
               ▝▀▜▛"""
WORDMARK = """\
 ██  ██████  █████  ██████  ██  ██ ██████
 ██  ██     ██   ██ ██   ██ ██  ██ ██
 ██  ██     ███████ ██████  ██  ██ ██████
 ██  ██     ██   ██ ██  ██  ██  ██     ██
 ██  ██████ ██   ██ ██   ██ ██████ ██████"""
WING = '#d9a520'

_client: clientmod.Client | None = None


def client() -> clientmod.Client:
    """The process-wide serve client."""
    global _client
    if _client is None:
        _client = clientmod.Client()
    return _client


def tab_target(tab) -> str:
    """The tab's CDP target id (QWebEnginePage.devToolsId), or ''."""
    try:
        page = tab._widget.page()  # noqa: SLF001
        return page.devToolsId()
    except (AttributeError, RuntimeError):
        return ''


def render(entries: list[conversation.Entry]) -> str:
    """The transcript as markdown, ChatGPT.nvim's ``▸ you`` / ``▸ icarus``."""
    parts = []
    for entry in entries:
        if entry.kind == 'you':
            parts.append(f'**▸ you**\n\n{entry.text}')
        elif entry.kind == 'icarus':
            parts.append(f'**▸ icarus**\n\n{entry.text}')
        elif entry.kind == 'step':
            lines = [line for line in entry.text.splitlines() if line.strip()]
            parts.append('\n'.join(f'> {line}  ' for line in lines))
        else:
            parts.append(f'> **error:** {entry.text}')
    return '\n\n'.join(parts)


#: Keys NORMAL leaves to the editor (they only move the cursor).
_NAV_KEYS = {Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down,
             Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_PageUp,
             Qt.Key.Key_PageDown}


def _to_u16(text: str, pos: int) -> int:
    """A str index as a Qt (UTF-16) text position."""
    return len(text[:pos].encode('utf-16-le')) // 2


def _from_u16(text: str, pos: int) -> int:
    """A Qt (UTF-16) text position as a str index."""
    units = 0
    for i, ch in enumerate(text):
        if units >= pos:
            return i
        units += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


class IcarusBar(QWidget):

    """The bar of one window, over the current tab's conversation.

    Signals:
        update_geometry: The main window should re-place the tray (it is
                         one of its overlays, like the completion).
    """

    STYLESHEET = """
        QWidget {
            /* the tab picker's font: one setting sizes both trays */
            font: {{ conf.fonts.completion.entry }};
            color: {{ qcolor_to_qsscolor(conf.colors.glass.fg) }};
            background: transparent;
            border: none;
            selection-color: {{ qcolor_to_qsscolor(conf.colors.glass.selected.fg) }};
            selection-background-color:
                {{ qcolor_to_qsscolor(conf.colors.glass.selected.bg) }};
        }
        QListWidget::item {
            border-radius: 6px;
            padding: 2px 6px;
        }
        QListWidget::item:selected {
            color: {{ qcolor_to_qsscolor(conf.colors.glass.selected.fg) }};
            background: {{ qcolor_to_qsscolor(conf.colors.glass.selected.bg) }};
        }
        QScrollBar { width: 0px; height: 0px; }
    """

    update_geometry = pyqtSignal()

    #: Lines of text the input card shows.
    INPUT_LINES = 3

    def __init__(self, win_id: int, parent: QWidget) -> None:
        super().__init__(parent)
        self._win_id = win_id
        self._conv: conversation.Conversation | None = None
        self._tab = None
        self._mode = 'INSERT'
        self._vim = vim.Normal()
        self._models_open = False
        self._frost = glass.Frost(self)

        self._transcript = QTextBrowser(self)
        self._transcript.setOpenExternalLinks(False)
        self._transcript.setOpenLinks(False)
        self._transcript.anchorClicked.connect(self._on_link)
        self._transcript.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._input = QPlainTextEdit(self)
        self._input.setPlaceholderText('Ask icarus... (Enter to send)')
        self._input.installEventFilter(self)

        self._filter = QLineEdit(self)
        self._filter.setPlaceholderText('filter models')
        self._filter.textChanged.connect(self._fill_models)
        self._filter.installEventFilter(self)
        self._models = QListWidget(self)
        self._models.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._models.itemActivated.connect(self._pick_model)
        self._filter.hide()
        self._models.hide()

        stylesheet.set_register(self)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        modeman.instance(win_id).left.connect(self._on_mode_left)
        self.hide()

    # ---- opening and closing ---------------------------------------------

    def _tabbed_browser(self):
        return objreg.get('tabbed-browser', scope='window', window=self._win_id)

    def open(self) -> None:
        tab = self._tabbed_browser().widget.currentWidget()
        if tab is None:
            raise cmdutils.CommandError("No tab to open the icarus bar on")
        target = tab_target(tab)
        if not target:
            raise cmdutils.CommandError(
                "This tab has no DevTools id (QtWebEngine 6.6+ is needed)")
        conv = getattr(tab.data, 'icarus', None)
        if conv is None:
            conv = conversation.Conversation(client(), target, tab)
            conv.open_url.connect(self._open_url)
            tab.data.icarus = conv
            self._seed_model(conv)
            if tab.data.icarus_saved is not None:
                # Restored by a session: carry on with the same serve-side
                # session (serve re-binds it to this tab's new DevTools id).
                conv.restore(tab.data.icarus_saved)
                tab.data.icarus_saved = None
        self._attach(tab, conv)
        client().fetch_models(lambda _models: self._on_models())
        self.show()
        self._reposition()
        self.raise_()
        self._frost.refresh()
        self._set_mode('INSERT')
        modeman.enter(self._win_id, usertypes.KeyMode.icarus, 'icarus bar')
        self._input.setFocus()

    def close_bar(self) -> None:
        if not self.isVisible():
            return
        self.hide()
        self._frost.clear()
        modeman.leave(self._win_id, usertypes.KeyMode.icarus, 'icarus bar',
                      maybe=True)
        self._tabbed_browser().on_release_focus()

    def _attach(self, tab, conv: conversation.Conversation) -> None:
        if self._conv is not None:
            self._conv.changed.disconnect(self._refresh)
        self._tab, self._conv = tab, conv
        conv.changed.connect(self._refresh)
        self._refresh()

    @pyqtSlot(usertypes.KeyMode)
    def _on_mode_left(self, mode: usertypes.KeyMode) -> None:
        if mode == usertypes.KeyMode.icarus and self.isVisible():
            self.hide()
            self._frost.clear()

    # ---- the model -------------------------------------------------------

    def _seed_model(self, conv: conversation.Conversation) -> None:
        """A new conversation starts on icarus.model (q3.8-fn by default)."""
        spec = config.val.icarus.model
        conv.model = clientmod.resolve_model(spec, client().models)
        if conv.model is None and '/' in spec:
            provider, model = spec.split('/', 1)
            conv.model = clientmod.Model(provider, model)

    def _on_models(self) -> None:
        conv = self._conv
        if conv is not None and conv.model is None and not conv.entries:
            self._seed_model(conv)
            self._refresh()
        if self._models_open:
            self._fill_models()

    def _fill_models(self) -> None:
        self._models.clear()
        needle = self._filter.text().strip().lower()
        current = str(self._conv.model) if self._conv and self._conv.model else ''
        for model in client().models:
            name = str(model)
            if needle and needle not in name.lower():
                continue
            tag = 'fleet' if model.local else 'cloud'
            item = QListWidgetItem(f'{name}   · {tag}')
            item.setData(Qt.ItemDataRole.UserRole, model)
            self._models.addItem(item)
            if name == current:
                self._models.setCurrentItem(item)
        if self._models.currentRow() < 0 and self._models.count():
            self._models.setCurrentRow(0)
        self.update()

    def _pick_model(self, item: QListWidgetItem | None = None) -> None:
        item = item or self._models.currentItem()
        if item is None or self._conv is None:
            return
        self._conv.model = item.data(Qt.ItemDataRole.UserRole)
        message.info(f"icarus: this tab now talks to {self._conv.model}")
        self._toggle_models(False)
        self._refresh()

    def _toggle_models(self, show: bool | None = None) -> None:
        self._models_open = not self._models_open if show is None else show
        self._filter.setVisible(self._models_open)
        self._models.setVisible(self._models_open)
        if self._models_open:
            self._filter.clear()
            self._fill_models()
            self._filter.setFocus()
        else:
            self._input.setFocus()
        self._layout()
        self.update()

    def set_model(self, spec: str) -> None:
        if self._conv is None:
            raise cmdutils.CommandError("The icarus bar is not open")
        model = clientmod.resolve_model(spec, client().models)
        if model is None:
            if '/' not in spec:
                raise cmdutils.CommandError(f"Unknown model {spec!r}")
            provider, name = spec.split('/', 1)
            model = clientmod.Model(provider, name)
        self._conv.model = model
        self._refresh()

    # ---- the transcript --------------------------------------------------

    def _refresh(self) -> None:
        conv = self._conv
        if conv is None:
            return
        bar = self._transcript.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        if conv.entries:
            self._transcript.setMarkdown(render(conv.entries))
        else:
            self._transcript.setHtml(self._welcome())
        if at_end:
            bar.setValue(bar.maximum())
        self.update()

    def _welcome(self) -> str:
        model = self._conv.model if self._conv else None
        tag = html.escape(f'local inference · {model}' if model else 'icarus')
        return (f'<pre style="color:{WING}">{html.escape(BANNER)}</pre>'
                f'<pre><b>{html.escape(WORDMARK)}</b></pre>'
                f'<p style="opacity:0.6">&nbsp;&nbsp;{tag}</p>'
                '<p style="opacity:0.6">&nbsp;&nbsp;? search · ! do it on '
                'this page · = just answer · &lt;Ctrl+o&gt; models</p>')

    def _on_link(self, url: QUrl) -> None:
        self._open_url(url.toString())

    def _open_url(self, url: str) -> None:
        self._tabbed_browser().tabopen(QUrl(url), background=False)

    def _submit(self) -> None:
        conv, tab = self._conv, self._tab
        if conv is None or tab is None:
            return
        if conv.busy:
            message.warning("icarus is still answering (<Ctrl+c> stops it)")
            return
        if conv.submit(self._input.toPlainText(), url=tab.url().toDisplayString(),
                       title=tab.title()):
            self._input.clear()

    def _scroll(self, pages: float) -> None:
        bar = self._transcript.verticalScrollBar()
        bar.setValue(bar.value() + int(pages * bar.pageStep()))

    def _yank(self) -> None:
        reply = self._conv.last_reply() if self._conv else ''
        if not reply:
            message.info("icarus: nothing to yank yet")
            return
        utils.set_clipboard(reply)
        message.info("icarus: yanked the last answer")

    def new_session(self) -> None:
        if self._conv is not None:
            self._conv.reset()

    def stop(self) -> None:
        if self._conv is not None and self._conv.busy:
            self._conv.stop()
        else:
            self.close_bar()

    # ---- keys ------------------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        """INSERT or NORMAL; NORMAL gets vim's block cursor.

        The input stays editable in NORMAL (a read-only QPlainTextEdit hides
        its cursor); _input_key keeps typed text from reaching it.
        """
        if mode == 'NORMAL' and self._mode == 'INSERT':
            text, pos = self._buffer()
            self._move(vim.escape_pos(text, pos))
        self._mode = mode
        self._vim.reset()
        width = self._input.fontMetrics().horizontalAdvance('M')
        self._input.setCursorWidth(width if mode == 'NORMAL' else 2)
        self.update()

    # The buffer and cursor in str indices: Qt counts UTF-16 code units,
    # which differ from Python's once an emoji is in the input.

    def _buffer(self) -> tuple[str, int]:
        text = self._input.toPlainText()
        return text, _from_u16(text, self._input.textCursor().position())

    def _move(self, pos: int) -> None:
        cursor = self._input.textCursor()
        cursor.setPosition(_to_u16(self._input.toPlainText(), pos))
        self._input.setTextCursor(cursor)

    def _vim_key(self, key: str) -> bool:
        """Run one NORMAL key through the vim engine."""
        text, pos = self._buffer()
        out = self._vim.feed(key, text, pos)
        if not out.handled:
            return True  # NORMAL swallows what it does not know
        if out.history == 'undo':
            self._input.undo()
            text, pos = self._buffer()
            self._move(vim.clamp(text, pos))
        if out.edit is not None:
            start, end, new = out.edit
            cursor = self._input.textCursor()
            cursor.setPosition(_to_u16(text, start))
            cursor.setPosition(_to_u16(text, end),
                               QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText(new)
        if out.pos is not None:
            self._move(out.pos)
        if out.scroll:
            self._scroll({1: 0.1, -1: -0.1, 2: 1e6, -2: -1e6}[out.scroll])
        if out.insert:
            self._set_mode('INSERT')
        return True

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:
        if event is None:
            return False
        if event.type() != QEvent.Type.KeyPress:
            return False
        assert isinstance(event, QKeyEvent)
        if obj is self._filter:
            return self._models_key(event)
        if obj is self._input:
            return self._input_key(event)
        return False

    def _common_key(self, event: QKeyEvent) -> bool:
        ctrl = event.modifiers() == Qt.KeyboardModifier.ControlModifier
        key = event.key()
        if ctrl and key == Qt.Key.Key_C:
            self.stop()
        elif ctrl and key == Qt.Key.Key_O:
            self._toggle_models()
        elif ctrl and key == Qt.Key.Key_N:
            self.new_session()
        elif ctrl and key == Qt.Key.Key_Y:
            self._yank()
        elif ctrl and key == Qt.Key.Key_D:
            self._scroll(0.5)
        elif ctrl and key == Qt.Key.Key_U:
            self._scroll(-0.5)
        elif key == Qt.Key.Key_Tab and self._models_open:
            (self._input if self._filter.hasFocus() else self._filter).setFocus()
            self.update()
        else:
            return False
        return True

    def _input_key(self, event: QKeyEvent) -> bool:
        if self._common_key(event):
            return True
        key = event.key()
        shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        if self._mode == 'INSERT':
            if key == Qt.Key.Key_Escape:
                self._set_mode('NORMAL')
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not shift:
                self._submit()
                return True
            return False
        # NORMAL
        text = event.text()
        ctrl = event.modifiers() == Qt.KeyboardModifier.ControlModifier
        if key == Qt.Key.Key_Escape:
            if self._vim.pending:
                self._vim.reset()
            else:
                self.close_bar()
        elif ctrl and key == Qt.Key.Key_R:
            self._input.redo()
            buf, pos = self._buffer()
            self._move(vim.clamp(buf, pos))
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._submit()
        elif text == 'q' and not self._vim.pending:
            self.close_bar()
        elif len(text) == 1 and text.isprintable():
            return self._vim_key(text)
        elif key in _NAV_KEYS:
            self._vim.reset()
            self._vim.forget_column()
            return False  # arrows and friends move the cursor as usual
        return True

    def _models_key(self, event: QKeyEvent) -> bool:
        if self._common_key(event):
            return True
        key = event.key()
        ctrl = event.modifiers() == Qt.KeyboardModifier.ControlModifier
        row = self._models.currentRow()
        if key == Qt.Key.Key_Escape:
            self._toggle_models(False)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._pick_model()
        elif key == Qt.Key.Key_Down or (ctrl and key in (Qt.Key.Key_J, Qt.Key.Key_N)):
            self._models.setCurrentRow(min(row + 1, self._models.count() - 1))
        elif key == Qt.Key.Key_Up or (ctrl and key in (Qt.Key.Key_K, Qt.Key.Key_P)):
            self._models.setCurrentRow(max(row - 1, 0))
        else:
            return False
        return True

    # ---- geometry and painting -------------------------------------------

    def sizeHint(self) -> QSize:
        """As tall as the tab picker: completion.height (shrink aside)."""
        confheight = str(config.val.completion.height)
        if confheight.endswith('%'):
            window = self.window()
            assert window is not None
            height = window.height() * int(confheight.rstrip('%')) // 100
        else:
            height = int(confheight)
        return QSize(-1, height)

    def _reposition(self) -> None:
        """Have the main window place the tray above the statusbar."""
        self.update_geometry.emit()
        self._layout()
        self._frost.schedule()

    def _areas(self) -> tuple[QRect, QRect, QRect | None]:
        """The outer areas of the transcript, input and models cards."""
        rect = self.rect().adjusted(glass.PAD, glass.PAD // 2, -glass.PAD,
                                    -glass.PAD // 2)
        font = self.font()
        line = self.fontMetrics().height()
        input_h = (self.INPUT_LINES * line + 2 * glass.INNER +
                   glass.chip_height(font) + glass.PAD)
        models = None
        if self._models_open:
            width = min(max(rect.width() // 3, 260), rect.width() // 2)
            models = QRect(rect.right() - width + 1, rect.top(), width,
                           rect.height())
            rect.setRight(models.left() - 1)
        transcript = QRect(rect.left(), rect.top(), rect.width(),
                           rect.height() - input_h)
        inp = QRect(rect.left(), transcript.bottom() + 1, rect.width(), input_h)
        return transcript, inp, models

    def _layout(self) -> None:
        transcript, inp, models = self._areas()
        font = self.font()
        self._transcript.setGeometry(glass.content_rect(transcript, font))
        self._input.setGeometry(glass.content_rect(inp, font))
        if models is not None:
            content = glass.content_rect(models, font)
            line = self.fontMetrics().height() + 8
            self._filter.setGeometry(content.left(), content.top(),
                                     content.width(), line)
            self._models.setGeometry(content.adjusted(0, line + 6, 0, 0))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._layout()
        if self.isVisible():
            self._frost.schedule()

    def _title(self) -> str:
        conv = self._conv
        model = str(conv.model) if conv and conv.model else 'default model'
        title = f'ICARUS · {model}'
        if conv and conv.busy:
            title += ' · thinking…' if conv.lane != 'browser_act' else ' · laya driving'
        return title

    def paintEvent(self, e) -> None:
        painter = QPainter(self)
        colors = glass.Colors.from_config()
        font = self.font()
        glass.paint_panel(painter, self.rect(), colors, self._frost)
        transcript, inp, models = self._areas()
        session = self._conv.session[:8] if self._conv and self._conv.session else 'new'
        glass.paint_card(painter, transcript, font, colors, self._title(),
                         right=f'session {session}')
        mode = self._mode
        chip = (config.val.colors.icarus.insert.bg if mode == 'INSERT'
                else config.val.colors.icarus.normal.bg)
        glass.paint_card(painter, inp, font, colors, mode, center=True,
                         title_bg=chip)
        if models is not None:
            count = f'{self._models.count()} / {len(client().models)}'
            glass.paint_card(painter, models, font, colors, 'Models', right=count)
        painter.end()


def init(win_id: int, parent: QWidget) -> IcarusBar:
    bar = IcarusBar(win_id, parent)
    objreg.register('icarus-bar', bar, scope='window', window=win_id)
    return bar


def _bar(win_id: int) -> IcarusBar:
    return objreg.get('icarus-bar', scope='window', window=win_id)


@cmdutils.register()
@cmdutils.argument('win_id', value=cmdutils.Value.win_id)
def icarus(win_id: int, toggle: bool = False) -> None:
    """Open the icarus bar on the current tab.

    Each tab keeps its own conversation; icarus serve (icarus.url) runs it
    and drives the tab when asked to do something on the page.

    Args:
        toggle: Close the bar when it is already open.
    """
    bar = _bar(win_id)
    if toggle and bar.isVisible():
        bar.close_bar()
    else:
        bar.open()


@cmdutils.register()
@cmdutils.argument('win_id', value=cmdutils.Value.win_id)
def icarus_close(win_id: int) -> None:
    """Close the icarus bar."""
    _bar(win_id).close_bar()


@cmdutils.register()
@cmdutils.argument('win_id', value=cmdutils.Value.win_id)
def icarus_new(win_id: int) -> None:
    """Start a new icarus conversation for the current tab."""
    _bar(win_id).new_session()


@cmdutils.register()
@cmdutils.argument('win_id', value=cmdutils.Value.win_id)
def icarus_model(win_id: int, model: str | None = None) -> None:
    """Pick the model the current tab's icarus conversation runs on.

    Args:
        model: provider/model or a bare model id. Without it, the models
               card opens.
    """
    bar = _bar(win_id)
    if not bar.isVisible():
        bar.open()
    if model is None:
        bar._toggle_models(True)  # noqa: SLF001
    else:
        bar.set_model(model)
