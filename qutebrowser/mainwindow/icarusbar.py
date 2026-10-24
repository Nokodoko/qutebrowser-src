# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The icarus bar: icarus on the page in the current tab.

A tray that comes up from the bottom like the tab picker (the completion
overlay: full width, above the statusbar, completion.height tall), so the
page it drives stays in view above it. It carries the icarus web UI's chat
over the page: a transcript card titled ``ICARUS · model`` (replies rendered
as the web UI renders them, the model's thinking, laya's steps, the web UI's
working strip while a turn runs), an input card whose title chip is the
input's vi mode, and on the right one of the models card (``<Ctrl+o>``), the
tools pane (``<Ctrl+t>`` / ``<Ctrl+j>``, every tool call's arguments and
result live) or the editor split (``<Ctrl+g>``: the right half of the tray
becomes a real nvim on a ``.md`` buffer, icarus serve's GET /editor page in a
QWebEngineView). Painted with the glass chrome of the other menus
(mainwindow/glass.py).

Each tab carries its own conversation (``tab.data.icarus``). icarus serve
keeps the matching session on the site's own page project, drives the tab
(the model's browser tools, multi-step actions through laya) and, on the
first open, introduces the page (icarus.greet). Keys:

    <Return>        send; while a turn runs it steers or queues
                    (icarus.mid_turn, <Ctrl+s> switches, <Ctrl+x> unqueues)
    <Shift+Return>  a new line
    <Escape>        close the / popup, else INSERT -> NORMAL; in NORMAL,
                    close (q too)
    NORMAL          vim on the input (icarus/vim.py)
    <Tab>           accept the / popup's pick, else cycle the reply's
                    → suggestions into the input
    → <End> <Ctrl+f>  accept the history ghost text at the end of the input
    <Up>/<Down>     recall earlier prompts into an empty input
    /               skills and the bar's own commands (/new /model /tools
                    /steer /queue /thinking)
    <Ctrl+g>        edit the input in nvim, split on the right half of the
                    bar (:wq hands the buffer back, :q discards, <Ctrl+g>
                    again closes); editor.command only if the split cannot load
    <Ctrl+d>/<Ctrl+u>  scroll the transcript
    <Ctrl+c>        stop a running turn and its queue, else close
    <Ctrl+o>        the models card
    <Ctrl+t>/<Ctrl+j>  the tools pane (<Ctrl+j> as in the web UI's tool pane)
    <Alt+t>         unfold (or fold) the thinking
    <Ctrl+n>        new session for this tab
    <Ctrl+y>        yank the last answer
"""

import html
import json
from urllib.parse import urlencode

from qutebrowser.qt.core import (QEvent, QObject, QRect, QSize, Qt, QUrl,
                                 pyqtSignal, pyqtSlot)
from qutebrowser.qt.gui import QColor, QKeyEvent, QPainter, QTextCursor
from qutebrowser.qt.widgets import (QLineEdit, QListWidget, QListWidgetItem,
                                    QPlainTextEdit, QSizePolicy, QTextBrowser,
                                    QWidget)

from qutebrowser.api import cmdutils
from qutebrowser.config import config, stylesheet
from qutebrowser.icarus import client as clientmod
from qutebrowser.icarus import conversation, history, render, transcript, vim
from qutebrowser.icarus import working
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

#: The bar's own / commands (handled here, never sent).
BAR_COMMANDS = [
    ('/new', 'start a new conversation for this tab'),
    ('/model', '<provider/model> switch this tab\'s model'),
    ('/tools', 'show or hide the tools pane'),
    ('/editor', 'edit the input in nvim, split on the right'),
    ('/steer', 'a submit while a turn runs steers it'),
    ('/queue', 'a submit while a turn runs waits behind it'),
    ('/thinking', 'unfold or fold the model\'s thinking'),
]
#: Rows the / popup shows.
POPUP_ROWS = 8

_client: clientmod.Client | None = None
_history = history.HistoryRing()
_skills: list[tuple[str, str]] = []


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


#: The titles the editor split's page sets when nvim is done or the split was
#: closed without a buffer (static/editorsplit.js on serve).
EDITOR_DONE = 'icarus-editor:done'
EDITOR_CANCEL = 'icarus-editor:cancel'
#: The JavaScript that reads the buffer a finished split parked.
EDITOR_RESULT_JS = 'JSON.stringify(window.__icarusEditorResult || null)'


def editor_url(base: str, seed: str, label: str = 'prompt.md') -> QUrl:
    """The editor split's page on serve: GET /editor seeded with the input."""
    url = QUrl(base.rstrip('/') + '/editor')
    url.setQuery(urlencode({'seed': seed, 'label': label}))
    return url


def editor_result(raw) -> tuple[str, bool] | None:
    """The (text, auto_send) a finished split parked, or None.

    raw is what EDITOR_RESULT_JS evaluated to: a JSON string, or already a
    dict when the web view decoded it.
    """
    if not raw:
        return None
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(data, dict) or not isinstance(data.get('text'), str):
        return None
    return data['text'], bool(data.get('autoSend'))


def popup_items(text: str, skills: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The / popup's rows for the input text (only while it is one token)."""
    if not text.startswith('/') or any(c.isspace() for c in text):
        return []
    needle = text.lower()
    return [(name, desc) for name, desc in BAR_COMMANDS + skills
            if name.lower().startswith(needle)]


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


class _Input(QPlainTextEdit):

    """The input, drawing the history ghost text after the cursor."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.ghost = ''

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if not self.ghost or not self.textCursor().atEnd():
            return
        rect = self.cursorRect()
        painter = QPainter(self.viewport())
        painter.setPen(QColor(render.DIM))
        painter.setFont(self.font())
        painter.drawText(rect.right() + 2, rect.top() + self.fontMetrics().ascent(),
                         self.ghost.split('\n')[0])
        painter.end()


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
        QListWidget#popup {
            background: {{ qcolor_to_qsscolor(conf.colors.glass.bg) }};
            border-radius: 8px;
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
        #: The right-hand card: None, 'models', 'tools' or 'editor'.
        self._side: str | None = None
        self._mid_turn = config.val.icarus.mid_turn
        self._expand_thinking = False
        #: The suggestion Tab last put into the input (-1: none).
        self._pick = -1
        #: Where ↑/↓ recall is in the history (-1: not recalling).
        self._recall = -1
        self._recalling = False
        self._editor: QObject | None = None
        #: The editor split's web view (serve's /editor page), made on first use.
        self._editor_view: QWidget | None = None
        self._frost = glass.Frost(self)

        self._transcript = QTextBrowser(self)
        self._transcript.setOpenExternalLinks(False)
        self._transcript.setOpenLinks(False)
        self._transcript.anchorClicked.connect(self._on_link)
        self._transcript.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._strip = working.WorkingStrip(self)

        self._input = _Input(self)
        self._input.setPlaceholderText('Ask icarus about this page, or tell it '
                                       'what to do here... (Enter to send)')
        self._input.installEventFilter(self)
        self._input.textChanged.connect(self._on_text_changed)
        self._input.cursorPositionChanged.connect(self._update_ghost)

        self._popup = QListWidget(self)
        self._popup.setObjectName('popup')
        self._popup.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._popup.itemActivated.connect(lambda _item: self._accept_popup())
        self._popup.hide()

        self._filter = QLineEdit(self)
        self._filter.setPlaceholderText('filter models')
        self._filter.textChanged.connect(self._fill_models)
        self._filter.installEventFilter(self)
        self._models = QListWidget(self)
        self._models.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._models.itemActivated.connect(self._pick_model)
        self._filter.hide()
        self._models.hide()

        self._tools = QTextBrowser(self)
        self._tools.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tools.hide()

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
        if not _history.seeded:
            client().fetch_history(_history.seed)
        client().fetch_skills(self._on_skills)
        self.show()
        self._reposition()
        self.raise_()
        self._frost.refresh()
        self._set_mode('INSERT')
        modeman.enter(self._win_id, usertypes.KeyMode.icarus, 'icarus bar')
        self._input.setFocus()
        if config.val.icarus.greet and not conv.greeted and not conv.entries:
            conv.greet(url=tab.url().toDisplayString(), title=tab.title())

    def close_bar(self) -> None:
        if not self.isVisible():
            return
        self._leave_editor()
        self.hide()
        self._frost.clear()
        modeman.leave(self._win_id, usertypes.KeyMode.icarus, 'icarus bar',
                      maybe=True)
        self._tabbed_browser().on_release_focus()

    def _attach(self, tab, conv: conversation.Conversation) -> None:
        if self._conv is not None:
            self._conv.changed.disconnect(self._refresh)
        self._tab, self._conv = tab, conv
        self._pick = -1
        conv.changed.connect(self._refresh)
        self._refresh()

    @pyqtSlot(usertypes.KeyMode)
    def _on_mode_left(self, mode: usertypes.KeyMode) -> None:
        if mode == usertypes.KeyMode.icarus and self.isVisible():
            self._leave_editor()
            self.hide()
            self._frost.clear()

    def _on_skills(self, skills: list[tuple[str, str]]) -> None:
        global _skills
        _skills = skills

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
        if self._side == 'models':
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
        self._toggle_side('models', False)
        self._refresh()

    def _toggle_side(self, side: str, show: bool | None = None) -> None:
        """Open or close the right-hand card (models, tools or editor)."""
        if show is None:
            show = self._side != side
        was = self._side
        self._side = side if show else (None if self._side == side else self._side)
        models, tools = self._side == 'models', self._side == 'tools'
        editor = self._side == 'editor'
        self._filter.setVisible(models)
        self._models.setVisible(models)
        self._tools.setVisible(tools)
        if self._editor_view is not None:
            self._editor_view.setVisible(editor)
            if was == 'editor' and not editor:
                # Leaving the split ends its nvim: the page goes, so the WS
                # /pty session closes and serve reaps the editor.
                self._editor_view.setUrl(QUrl('about:blank'))
        if models:
            self._filter.clear()
            self._fill_models()
            self._filter.setFocus()
        elif editor:
            assert self._editor_view is not None
            self._editor_view.setFocus()
        else:
            self._input.setFocus()
        if tools:
            self._refresh_tools()
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

    # ---- the transcript and the tools pane --------------------------------

    def _indent(self) -> int:
        return 2 * self.fontMetrics().horizontalAdvance('M')

    def _refresh(self) -> None:
        conv = self._conv
        if conv is None:
            return
        bar = self._transcript.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        if conv.entries or conv.busy:
            self._transcript.setHtml(transcript.transcript_html(
                conv, selected=self._pick, expand_thinking=self._expand_thinking,
                indent=self._indent()))
        else:
            self._transcript.setHtml(self._welcome())
        if at_end:
            bar.setValue(bar.maximum())
        was_busy = self._strip.isVisible()
        self._strip.follow(conv if self.isVisible() else None)
        if was_busy != self._strip.isVisible():
            self._layout()
        if self._side == 'tools':
            self._refresh_tools()
        self.update()

    def _refresh_tools(self) -> None:
        conv = self._conv
        bar = self._tools.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        self._tools.setHtml(transcript.tools_html(conv.tools if conv else []))
        if at_end:
            bar.setValue(bar.maximum())

    def _welcome(self) -> str:
        model = self._conv.model if self._conv else None
        tag = html.escape(f'{model}' if model else 'icarus')
        return (f'<pre style="color:{WING}">{html.escape(BANNER)}</pre>'
                f'<pre><b>{html.escape(WORDMARK)}</b></pre>'
                f'<p style="opacity:0.6">&nbsp;&nbsp;{tag} · drives this tab</p>'
                '<p style="opacity:0.6">&nbsp;&nbsp;/ skills &amp; commands · '
                'Tab suggestions · &lt;Ctrl+j&gt; tools · &lt;Ctrl+o&gt; models · '
                '&lt;Ctrl+g&gt; nvim</p>')

    def _on_link(self, url: QUrl) -> None:
        self._open_url(url.toString())

    def _open_url(self, url: str) -> None:
        self._tabbed_browser().tabopen(QUrl(url), background=False)

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
        if self._conv is not None and (self._conv.busy or self._conv.queue):
            self._conv.cancel()
        else:
            self.close_bar()

    # ---- submitting ------------------------------------------------------

    def _bar_command(self, text: str) -> bool:
        """Run one of BAR_COMMANDS; False when text is not one."""
        name, _, arg = text.strip().partition(' ')
        arg = arg.strip()
        if name == '/new':
            self.new_session()
        elif name == '/model':
            if arg:
                try:
                    self.set_model(arg)
                except cmdutils.CommandError as e:
                    message.error(str(e))
            else:
                self._toggle_side('models', True)
        elif name == '/tools':
            self._toggle_side('tools')
        elif name == '/editor':
            self.open_editor()
        elif name in ('/steer', '/queue'):
            self._mid_turn = name[1:]
            message.info(f"icarus: a submit while a turn runs now {name[1:]}s")
        elif name == '/thinking':
            self._expand_thinking = not self._expand_thinking
            self._refresh()
        else:
            return False
        return True

    def _submit(self) -> None:
        conv, tab = self._conv, self._tab
        if conv is None or tab is None:
            return
        text = self._input.toPlainText()
        if not text.strip():
            return
        if self._bar_command(text):
            self._input.clear()
            return
        if conv.submit(text, url=tab.url().toDisplayString(), title=tab.title(),
                       mid_turn=self._mid_turn):
            _history.add(text)
            self._recall = -1
            self._pick = -1
            self._input.clear()

    # ---- INSERT helpers: ghost, recall, suggestions, popup, editor -------

    def _on_text_changed(self) -> None:
        if not self._recalling:
            self._recall = -1
        self._update_popup()
        self._update_ghost()

    def _update_ghost(self) -> None:
        text = self._input.toPlainText()
        at_end = self._input.textCursor().atEnd()
        ghost = (_history.suggest(text) if self._mode == 'INSERT' and at_end
                 and not self._popup.isVisible() else '')
        if ghost != self._input.ghost:
            self._input.ghost = ghost
            vp = self._input.viewport()
            assert vp is not None
            vp.update()

    def _accept_ghost(self) -> bool:
        if not self._input.ghost or not self._input.textCursor().atEnd():
            return False
        self._input.insertPlainText(self._input.ghost)
        return True

    def _recall_step(self, step: int) -> bool:
        """↑/↓ through earlier prompts while the input is empty or recalled."""
        if self._input.toPlainText() and self._recall < 0:
            return False
        recent = _history.recent()
        index = self._recall + step
        if index < -1 or index >= len(recent):
            return True
        self._recall = index
        self._recalling = True
        self._input.setPlainText(recent[index] if index >= 0 else '')
        self._recalling = False
        self._input.moveCursor(QTextCursor.MoveOperation.End)
        return True

    def _cycle_suggestion(self) -> bool:
        conv = self._conv
        if conv is None or not conv.suggestions or conv.busy:
            return False
        self._pick = (self._pick + 1) % len(conv.suggestions)
        self._input.setPlainText(conv.suggestions[self._pick])
        self._input.moveCursor(QTextCursor.MoveOperation.End)
        self._refresh()
        return True

    def _update_popup(self) -> None:
        items = popup_items(self._input.toPlainText(), _skills) if self._mode == 'INSERT' else []
        self._popup.clear()
        for name, desc in items:
            item = QListWidgetItem(f'{name}   {desc}')
            item.setData(Qt.ItemDataRole.UserRole, name)
            self._popup.addItem(item)
        if items:
            self._popup.setCurrentRow(0)
        self._popup.setVisible(bool(items))
        self._layout()

    def _accept_popup(self) -> None:
        item = self._popup.currentItem()
        if item is None:
            return
        self._input.setPlainText(item.data(Qt.ItemDataRole.UserRole) + ' ')
        self._input.moveCursor(QTextCursor.MoveOperation.End)
        self._popup.hide()

    def _popup_key(self, event: QKeyEvent) -> bool:
        key = event.key()
        ctrl = event.modifiers() == Qt.KeyboardModifier.ControlModifier
        row, count = self._popup.currentRow(), self._popup.count()
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._accept_popup()
        elif key == Qt.Key.Key_Escape:
            self._popup.hide()
        elif key == Qt.Key.Key_Down or (ctrl and key == Qt.Key.Key_N):
            self._popup.setCurrentRow((row + 1) % count)
        elif key == Qt.Key.Key_Up or (ctrl and key == Qt.Key.Key_P):
            self._popup.setCurrentRow((row - 1) % count)
        else:
            return False
        return True

    def open_editor(self) -> None:
        """Edit the input in nvim, split on the right half of the bar.

        The split is icarus serve's GET /editor page (the web UI's editor
        flyout as a whole page: xterm over WS /pty, nvim on a ``.md``
        tempfile) in a QWebEngineView. nvim owns every key while it has
        focus; ``:wq`` hands the buffer back to the input, ``:q`` discards
        it, and <Ctrl+g> from the input closes the split. If the page
        cannot load, the external editor (editor.command) takes over.
        """
        if self._side == 'editor':
            self._toggle_side('editor', False)
            return
        view = self._editor_view or self._make_editor_view()
        if view is None:
            self._open_external_editor()
            return
        self._toggle_side('editor', True)
        view.setUrl(editor_url(config.val.icarus.url, self._input.toPlainText()))

    def _make_editor_view(self) -> QWidget | None:
        """The split's web view, over the bar's glass; None without QtWebEngine."""
        try:
            # Imported here like misc.editor: QtWebEngine must not be pulled
            # in while mainwindow is being imported.
            from qutebrowser.qt.webenginewidgets import QWebEngineView  # pylint: disable=import-outside-toplevel
        except ImportError:
            return None
        view = QWebEngineView(self)
        page = view.page()
        assert page is not None
        page.setBackgroundColor(QColor(0, 0, 0, 0))  # the glass shows through
        view.titleChanged.connect(self._on_editor_title)
        view.loadFinished.connect(self._on_editor_loaded)
        view.hide()
        self._editor_view = view
        return view

    def _leave_editor(self) -> None:
        if self._side == 'editor':
            self._toggle_side('editor', False)

    def _on_editor_title(self, title: str) -> None:
        """The split's page reports over its title (static/editorsplit.js)."""
        if self._side != 'editor' or self._editor_view is None:
            return
        if title == EDITOR_DONE:
            page = self._editor_view.page()
            assert page is not None
            page.runJavaScript(EDITOR_RESULT_JS, self._on_editor_result)
        elif title == EDITOR_CANCEL:
            self._toggle_side('editor', False)

    def _on_editor_result(self, raw) -> None:
        result = editor_result(raw)
        self._toggle_side('editor', False)
        if result is None:
            return
        text, send = result
        self._on_editor_text(text)
        if send:
            self._submit()

    def _on_editor_loaded(self, ok: bool) -> None:
        if ok or self._side != 'editor':
            return
        message.error(f"icarus: the editor split could not load "
                      f"{config.val.icarus.url}/editor; using editor.command")
        self._toggle_side('editor', False)
        self._open_external_editor()

    def _open_external_editor(self) -> None:
        """Edit the input in the external editor (editor.command)."""
        # Imported here: misc.editor -> guiprocess -> api.apitypes ->
        # browsertab is a cycle while mainwindow is being imported.
        from qutebrowser.misc import editor  # pylint: disable=import-outside-toplevel
        self._editor = editor.ExternalEditor(parent=self)
        self._editor.file_updated.connect(self._on_editor_text)
        self._editor.edit(self._input.toPlainText())

    def _on_editor_text(self, text: str) -> None:
        self._input.setPlainText(text.rstrip('\n'))
        self._input.moveCursor(QTextCursor.MoveOperation.End)
        self._input.setFocus()

    # ---- keys ------------------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        """INSERT or NORMAL; NORMAL gets vim's block cursor.

        The input stays editable in NORMAL (a read-only QPlainTextEdit hides
        its cursor); _input_key keeps typed text from reaching it.
        """
        if mode == 'NORMAL' and self._mode == 'INSERT':
            text, pos = self._buffer()
            self._move(vim.escape_pos(text, pos))
            self._popup.hide()
        self._mode = mode
        self._vim.reset()
        width = self._input.fontMetrics().horizontalAdvance('M')
        self._input.setCursorWidth(width if mode == 'NORMAL' else 2)
        self._update_ghost()
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
        mods = event.modifiers()
        ctrl = mods == Qt.KeyboardModifier.ControlModifier
        alt = mods == Qt.KeyboardModifier.AltModifier
        key = event.key()
        if ctrl and key == Qt.Key.Key_C:
            self.stop()
        elif ctrl and key == Qt.Key.Key_O:
            self._toggle_side('models')
        elif ctrl and key in (Qt.Key.Key_T, Qt.Key.Key_J):
            self._toggle_side('tools')
        elif ctrl and key == Qt.Key.Key_N:
            self.new_session()
        elif ctrl and key == Qt.Key.Key_Y:
            self._yank()
        elif ctrl and key == Qt.Key.Key_D:
            self._scroll(0.5)
        elif ctrl and key == Qt.Key.Key_U:
            self._scroll(-0.5)
        elif ctrl and key == Qt.Key.Key_G:
            self.open_editor()
        elif ctrl and key == Qt.Key.Key_S:
            self._mid_turn = 'queue' if self._mid_turn == 'steer' else 'steer'
            self.update()
        elif ctrl and key == Qt.Key.Key_X:
            dropped = self._conv.unqueue() if self._conv else ''
            message.info(f"icarus: unqueued {dropped!r}" if dropped
                         else "icarus: nothing queued")
        elif alt and key == Qt.Key.Key_T:
            self._expand_thinking = not self._expand_thinking
            self._refresh()
        elif key == Qt.Key.Key_Tab and self._side == 'models':
            (self._input if self._filter.hasFocus() else self._filter).setFocus()
            self.update()
        else:
            return False
        return True

    def _input_key(self, event: QKeyEvent) -> bool:
        if self._mode == 'INSERT' and self._popup.isVisible() and self._popup_key(event):
            return True
        if self._common_key(event):
            return True
        key = event.key()
        mods = event.modifiers()
        shift = mods & Qt.KeyboardModifier.ShiftModifier
        ctrl = mods == Qt.KeyboardModifier.ControlModifier
        if self._mode == 'INSERT':
            if key == Qt.Key.Key_Escape:
                self._set_mode('NORMAL')
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not shift:
                self._submit()
                return True
            if key == Qt.Key.Key_Tab:
                return self._cycle_suggestion() or True
            if key in (Qt.Key.Key_Right, Qt.Key.Key_End) or (ctrl and key == Qt.Key.Key_F):
                return self._accept_ghost()
            if key == Qt.Key.Key_Up and self._input.textCursor().blockNumber() == 0:
                return self._recall_step(1)
            if key == Qt.Key.Key_Down and self._recall >= 0:
                return self._recall_step(-1)
            return False
        # NORMAL
        text = event.text()
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
        key = event.key()
        ctrl = event.modifiers() == Qt.KeyboardModifier.ControlModifier
        row = self._models.currentRow()
        # The list's own <Ctrl+j>/<Ctrl+k> come before the common keys, where
        # <Ctrl+j> is the tools pane.
        if key == Qt.Key.Key_Down or (ctrl and key in (Qt.Key.Key_J, Qt.Key.Key_N)):
            self._models.setCurrentRow(min(row + 1, self._models.count() - 1))
            return True
        if key == Qt.Key.Key_Up or (ctrl and key in (Qt.Key.Key_K, Qt.Key.Key_P)):
            self._models.setCurrentRow(max(row - 1, 0))
            return True
        if self._common_key(event):
            return True
        if key == Qt.Key.Key_Escape:
            self._toggle_side('models', False)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._pick_model()
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
        """The outer areas of the transcript, input and right-hand cards."""
        rect = self.rect().adjusted(glass.PAD, glass.PAD // 2, -glass.PAD,
                                    -glass.PAD // 2)
        font = self.font()
        line = self.fontMetrics().height()
        input_h = (self.INPUT_LINES * line + 2 * glass.INNER +
                   glass.chip_height(font) + glass.PAD)
        side = None
        if self._side is not None:
            if self._side == 'editor':
                # The split: nvim gets the right half of the tray outright.
                width = rect.width() // 2
            else:
                width = min(max(rect.width() * 2 // 5, 260), rect.width() // 2)
            side = QRect(rect.right() - width + 1, rect.top(), width,
                         rect.height())
            rect.setRight(side.left() - 1)
        chat = QRect(rect.left(), rect.top(), rect.width(),
                     rect.height() - input_h)
        inp = QRect(rect.left(), chat.bottom() + 1, rect.width(), input_h)
        return chat, inp, side

    def _layout(self) -> None:
        chat, inp, side = self._areas()
        font = self.font()
        line = self.fontMetrics().height()
        content = glass.content_rect(chat, font)
        if self._strip.isVisible():
            strip = QRect(content.left(), content.bottom() - line + 1,
                          content.width(), line)
            self._strip.setGeometry(strip)
            content.setBottom(strip.top() - 4)
        self._transcript.setGeometry(content)
        self._input.setGeometry(glass.content_rect(inp, font))
        if self._popup.isVisible():
            rows = min(self._popup.count(), POPUP_ROWS)
            height = rows * (line + 6) + 8
            width = min(content.width(), 60 * self.fontMetrics().horizontalAdvance('M'))
            self._popup.setGeometry(content.left(), content.bottom() - height + 1,
                                    width, height)
            self._popup.raise_()
        if side is not None:
            inner = glass.content_rect(side, font)
            if self._side == 'models':
                row = line + 8
                self._filter.setGeometry(inner.left(), inner.top(), inner.width(), row)
                self._models.setGeometry(inner.adjusted(0, row + 6, 0, 0))
            elif self._side == 'editor':
                if self._editor_view is not None:
                    self._editor_view.setGeometry(inner)
            else:
                self._tools.setGeometry(inner)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._layout()
        if self.isVisible():
            self._frost.schedule()

    def _title(self) -> str:
        conv = self._conv
        model = str(conv.model) if conv and conv.model else 'default model'
        title = f'ICARUS · {model}'
        if conv and conv.busy and conv.lane == 'browser_act':
            title += ' · laya driving'
        return title

    def paintEvent(self, e) -> None:
        painter = QPainter(self)
        colors = glass.Colors.from_config()
        font = self.font()
        glass.paint_panel(painter, self.rect(), colors, self._frost)
        chat, inp, side = self._areas()
        conv = self._conv
        session = conv.session[:8] if conv and conv.session else 'new'
        right = f'session {session}'
        if conv and conv.tools and self._side != 'tools':
            right += f' · {len(conv.tools)} tools <Ctrl+j>'
        glass.paint_card(painter, chat, font, colors, self._title(), right=right)
        mode = self._mode
        chip = (config.val.colors.icarus.insert.bg if mode == 'INSERT'
                else config.val.colors.icarus.normal.bg)
        mid = self._mid_turn
        if conv and conv.queue:
            mid += f' · {len(conv.queue)}'
        glass.paint_card(painter, inp, font, colors, mode, center=True,
                         title_bg=chip, right=mid)
        if side is not None and self._side == 'models':
            count = f'{self._models.count()} / {len(client().models)}'
            glass.paint_card(painter, side, font, colors, 'Models', right=count)
        elif side is not None and self._side == 'editor':
            glass.paint_card(painter, side, font, colors, 'nvim',
                             right='prompt.md · :wq return · :q discard · <Ctrl+g> close')
        elif side is not None:
            calls = len(conv.tools) if conv else 0
            glass.paint_card(painter, side, font, colors, 'Tools', right=str(calls))
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
def icarus_tools(win_id: int) -> None:
    """Show or hide the icarus bar's tools pane (every tool call, live)."""
    bar = _bar(win_id)
    if not bar.isVisible():
        bar.open()
    bar._toggle_side('tools')  # noqa: SLF001


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
        bar._toggle_side('models', True)  # noqa: SLF001
    else:
        bar.set_model(model)
