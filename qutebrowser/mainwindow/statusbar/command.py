# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The commandline in the statusbar."""

from typing import cast

from qutebrowser.qt import machinery
from qutebrowser.qt.core import (pyqtSignal, pyqtSlot, pyqtProperty, Qt, QSize,
                                 QEvent, QRect)
from qutebrowser.qt.gui import QKeyEvent, QColor, QPainter, QPaintEvent
from qutebrowser.qt.widgets import (QSizePolicy, QWidget, QStyle,
                                    QStyleOptionFrame)

from qutebrowser.keyinput import modeman, modeparsers
from qutebrowser.api import cmdutils
from qutebrowser.misc import cmdhistory, editor
from qutebrowser.misc import miscwidgets as misc
from qutebrowser.mainwindow import glass
from qutebrowser.mainwindow.statusbar import textbase
from qutebrowser.utils import usertypes, log, objreg, message, utils
from qutebrowser.config import config


# QLineEditPrivate::horizontalMargin: the gap QLineEdit leaves between its
# contents rect (after the text margins) and the start of the text.
_LINEEDIT_HMARGIN = 2


class Command(misc.CommandLineEdit):

    """The commandline part of the statusbar.

    With statusbar.segments, the statusbar stylesheet sets segmentBg to the
    command mode color, which is painted behind the typed text only (padded by
    a space on each side), like the statusbar's TextBase labels.

    Attributes:
        _win_id: The window ID this widget is associated with.
        _segment_bg: The segment background color.
        _segment_glass: Whether the segment is painted as a glass pill.

    Signals:
        got_cmd: Emitted when a command is triggered by the user.
                 arg: The command string and also potentially the count.
        got_search: Emitted when a search should happen.
        clear_completion_selection: Emitted before the completion widget is
                                    hidden.
        hide_completion: Emitted when the completion widget should be hidden.
        update_completion: Emitted when the completion should be shown/updated.
        show_cmd: Emitted when command input should be shown.
        hide_cmd: Emitted when command input can be hidden.
    """

    got_cmd = pyqtSignal([str], [str, int])
    got_search = pyqtSignal(str, bool)  # text, reverse
    clear_completion_selection = pyqtSignal()
    hide_completion = pyqtSignal()
    update_completion = pyqtSignal()
    show_cmd = pyqtSignal()
    hide_cmd = pyqtSignal()

    def __init__(self, *, win_id: int,
                 private: bool,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._win_id = win_id
        self._segment_bg = QColor()
        self._segment_glass = False
        if not private:
            command_history = objreg.get('command-history')
            self.history.history = command_history.data
            self.history.changed.connect(command_history.changed)
        self.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Ignored)

        self.cursorPositionChanged.connect(self.update_completion)
        self.textChanged.connect(self.update_completion)
        self.textChanged.connect(self.updateGeometry)
        self.textChanged.connect(self._incremental_search)

        self.setStyleSheet(
            """
            QLineEdit {
                border: 0px;
                padding-left: 1px;
                background-color: transparent;
            }
            """
        )
        self.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)

    @pyqtProperty(QColor)
    def segmentBg(self) -> QColor:  # noqa: N802
        """Getter for segmentBg, so the stylesheet can set it as qproperty."""
        return self._segment_bg

    @segmentBg.setter  # type: ignore[no-redef]
    def segmentBg(self, color: QColor) -> None:  # noqa: N802
        """Setter for segmentBg; re-pads the text and repaints."""
        self._segment_bg = QColor(color)
        self._update_segment_margins()
        self.update()

    @pyqtProperty(bool)
    def segmentGlass(self) -> bool:  # noqa: N802
        """Getter for segmentGlass, so the stylesheet can set it."""
        return self._segment_glass

    @segmentGlass.setter  # type: ignore[no-redef]
    def segmentGlass(self, value: bool) -> None:  # noqa: N802
        """Setter for segmentGlass; re-pads the text and repaints."""
        self._segment_glass = bool(value)
        self._update_segment_margins()
        self.update()

    def _segment_padding(self) -> int:
        return textbase.segment_padding(self, self._segment_bg,
                                        self._segment_glass)

    def _update_segment_margins(self) -> None:
        """Reserve the segment padding on both sides of the text."""
        padding = self._segment_padding()
        self.setTextMargins(padding, 0, padding, 0)

    def changeEvent(self, e: QEvent | None) -> None:
        """Extend QLineEdit::changeEvent to re-pad the text on a font change."""
        super().changeEvent(e)
        assert e is not None
        if e.type() == QEvent.Type.FontChange:
            self._update_segment_margins()

    def _paint_segment(self, padding: int) -> None:
        """Fill the segment background behind the text."""
        opt = QStyleOptionFrame()
        self.initStyleOption(opt)
        style = self.style()
        assert style is not None
        contents = style.subElementRect(
            QStyle.SubElement.SE_LineEditContents, opt, self)
        text_left = (contents.left() + self.textMargins().left() +
                     _LINEEDIT_HMARGIN)
        text_width = self.fontMetrics().horizontalAdvance(self.text())
        segment = QRect(text_left - padding, 0,
                        text_width + 2 * padding, self.height())
        painter = QPainter(self)
        if self._segment_glass:
            glass.paint_pill(painter, segment.intersected(self.rect()),
                             self._segment_bg)
        else:
            painter.fillRect(segment.intersected(self.rect()),
                             self._segment_bg)
        painter.end()

    def paintEvent(self, e: QPaintEvent | None) -> None:
        """Extend QLineEdit::paintEvent to paint the segment behind the text."""
        padding = self._segment_padding()
        if padding and self.text():
            self._paint_segment(padding)
        super().paintEvent(e)

    def _handle_search(self) -> bool:
        """Check if the currently entered text is a search, and if so, run it.

        Return:
            True if a search was executed, False otherwise.
        """
        if self.prefix() == '/':
            self.got_search.emit(self.text()[1:], False)
            return True
        elif self.prefix() == '?':
            self.got_search.emit(self.text()[1:], True)
            return True
        else:
            return False

    def prefix(self) -> str:
        """Get the currently entered command prefix."""
        text = self.text()
        if not text:
            return ''
        elif text[0] in modeparsers.STARTCHARS:
            return text[0]
        else:
            return ''

    def cmd_set_text(self, text: str) -> None:
        """Preset the statusbar to some text.

        Args:
            text: The text to set as string.
        """
        self.setText(text)
        log.modes.debug("Setting command text, focusing {!r}".format(self))
        modeman.enter(self._win_id, usertypes.KeyMode.command, 'cmd focus')
        self.setFocus()
        self.show_cmd.emit()

    @cmdutils.register(instance='status-command', name='cmd-set-text',
                       scope='window', maxsplit=0, deprecated_name='set-cmd-text')
    @cmdutils.argument('count', value=cmdutils.Value.count)
    def cmd_set_text_command(self, text: str,
                             count: int | None = None,
                             space: bool = False,
                             append: bool = False,
                             run_on_count: bool = False) -> None:
        """Preset the statusbar to some text.

        //

        Wrapper for cmd_set_text to check the arguments and allow multiple
        strings which will get joined.

        Args:
            text: The commandline to set.
            count: The count if given.
            space: If given, a space is added to the end.
            append: If given, the text is appended to the current text.
            run_on_count: If given with a count, the command is run with the
                          given count rather than setting the command text.
        """
        if space:
            text += ' '
        if append:
            if not self.text():
                raise cmdutils.CommandError("No current text!")
            text = self.text() + text

        if not text or text[0] not in modeparsers.STARTCHARS:
            raise cmdutils.CommandError(
                "Invalid command text '{}'.".format(text))
        if run_on_count and count is not None:
            self.got_cmd[str, int].emit(text, count)
        else:
            self.cmd_set_text(text)

    @cmdutils.register(instance='status-command',
                       modes=[usertypes.KeyMode.command], scope='window')
    def command_history_prev(self) -> None:
        """Go back in the commandline history."""
        try:
            if not self.history.is_browsing():
                item = self.history.start(self.text().strip())
            else:
                item = self.history.previtem()
        except (cmdhistory.HistoryEmptyError,
                cmdhistory.HistoryEndReachedError):
            return
        if item:
            self.cmd_set_text(item)

    @cmdutils.register(instance='status-command',
                       modes=[usertypes.KeyMode.command], scope='window')
    def command_history_next(self) -> None:
        """Go forward in the commandline history."""
        if not self.history.is_browsing():
            return
        try:
            item = self.history.nextitem()
        except cmdhistory.HistoryEndReachedError:
            return
        if item:
            self.cmd_set_text(item)

    @cmdutils.register(instance='status-command',
                       modes=[usertypes.KeyMode.command], scope='window')
    def command_accept(self, rapid: bool = False) -> None:
        """Execute the command currently in the commandline.

        Args:
            rapid: Run the command without closing or clearing the command bar.
        """
        was_search = self._handle_search()

        text = self.text()
        if not (self.prefix() == ':' and text[1:].startswith(' ')):
            self.history.append(text)

        if not rapid:
            modeman.leave(self._win_id, usertypes.KeyMode.command,
                          'cmd accept')

        if not was_search:
            self.got_cmd[str].emit(text[1:])

    @cmdutils.register(instance='status-command', scope='window',
                       deprecated_name='edit-command')
    def cmd_edit(self, run: bool = False) -> None:
        """Open an editor to modify the current command.

        Args:
            run: Run the command if the editor exits successfully.
        """
        ed = editor.ExternalEditor(parent=self)

        def callback(text: str) -> None:
            """Set the commandline to the edited text."""
            if not text or text[0] not in modeparsers.STARTCHARS:
                message.error('command must start with one of {}'
                              .format(modeparsers.STARTCHARS))
                return
            self.cmd_set_text(text)
            if run:
                self.command_accept()

        ed.file_updated.connect(callback)
        ed.edit(self.text())

    @pyqtSlot(usertypes.KeyMode)
    def on_mode_left(self, mode: usertypes.KeyMode) -> None:
        """Clear up when command mode was left.

        - Clear the statusbar text if it's explicitly unfocused.
        - Clear completion selection
        - Hide completion

        Args:
            mode: The mode which was left.
        """
        if mode == usertypes.KeyMode.command:
            self.setText('')
            self.history.stop()
            self.hide_cmd.emit()
            self.clear_completion_selection.emit()
            self.hide_completion.emit()

    def setText(self, text: str | None) -> None:
        """Extend setText to set prefix and make sure the prompt is ok."""
        if not text:
            pass
        elif text[0] in modeparsers.STARTCHARS:
            super().set_prompt(text[0])
        else:
            raise utils.Unreachable("setText got called with invalid text "
                                    "'{}'!".format(text))
        # FIXME:mypy PyQt6 stubs issue
        if machinery.IS_QT6:
            text = cast(str, text)
        super().setText(text)

    def keyPressEvent(self, e: QKeyEvent | None) -> None:
        """Override keyPressEvent to ignore Return key presses, and add Shift-Ins.

        If this widget is focused, we are in passthrough key mode, and
        Enter/Shift+Enter/etc. will cause QLineEdit to think it's finished
        without command_accept to be called.
        """
        assert e is not None
        if machinery.IS_QT5:  # FIXME:v4 needed for Qt 5 typing
            shift = cast(Qt.KeyboardModifiers, Qt.KeyboardModifier.ShiftModifier)
        else:
            shift = Qt.KeyboardModifier.ShiftModifier

        text = self.text()
        if text in modeparsers.STARTCHARS and e.key() == Qt.Key.Key_Backspace:
            e.accept()
            modeman.leave(self._win_id, usertypes.KeyMode.command,
                          'prefix deleted')
        elif e.key() == Qt.Key.Key_Return:
            e.ignore()
        elif e.key() == Qt.Key.Key_Insert and e.modifiers() == shift:
            try:
                text = utils.get_clipboard(selection=True, fallback=True)
            except utils.ClipboardError:
                e.ignore()
            else:
                e.accept()
                self.insert(text)
        else:
            super().keyPressEvent(e)

    def sizeHint(self) -> QSize:
        """Dynamically calculate the needed size."""
        height = super().sizeHint().height()
        text = self.text()
        if not text:
            text = 'x'
        width = self.fontMetrics().boundingRect(text).width()
        return QSize(width, height)

    @pyqtSlot()
    def _incremental_search(self) -> None:
        if not config.val.search.incremental:
            return

        self._handle_search()
