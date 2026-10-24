# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The working indicator: the icarus web UI's crush-style scramble strip.

A fixed-width row of cells, each re-rolling through the same glyph pool as
app.css's working-reroll keyframes, under a gradient that shimmers along the
strip, then the phase label (working… / responding… / running <tool>…) and
the turn's elapsed time. Purple; amber while a tool runs. Painted on a timer
only while a turn is in flight.
"""

import random
import time

from qutebrowser.qt.core import QRectF, Qt, QTimer
from qutebrowser.qt.gui import QColor, QPainter
from qutebrowser.qt.widgets import QWidget

from qutebrowser.icarus import conversation, render

#: app.css working-reroll's glyphs.
GLYPHS = '0123456789af~!@#'
#: WORKING_SCRAMBLE_CELLS in app.js.
CELLS = 10
#: Repaint interval (the CSS re-roll is .5s per 16 steps).
TICK_MS = 60


def label(phase: str, tool: str) -> str:
    """working.js workingLabel."""
    if phase == conversation.TOOL:
        return f'running {tool}…' if tool else 'running tool…'
    if phase == conversation.RESPONDING:
        return 'responding…'
    if phase == conversation.WORKING:
        return 'working…'
    return ''


def elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f'{seconds}s' if seconds < 60 else f'{seconds // 60}m{seconds % 60:02d}s'


class WorkingStrip(QWidget):

    """The strip, for one conversation's running turn."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._conv: conversation.Conversation | None = None
        self._cells = [random.choice(GLYPHS) for _ in range(CELLS)]
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def follow(self, conv: conversation.Conversation | None) -> None:
        """Show for conv while it is busy, hide otherwise."""
        self._conv = conv
        if conv is not None and conv.busy:
            if not self._timer.isActive():
                self._timer.start()
            self.show()
            self.raise_()
        else:
            self._timer.stop()
            self.hide()
        self.update()

    def _tick(self) -> None:
        for i in range(CELLS):
            if random.random() < 0.35:
                self._cells[i] = random.choice(GLYPHS)
        self._phase = (self._phase + TICK_MS / 1000) % 1.0
        self.update()

    def paintEvent(self, e) -> None:
        conv = self._conv
        if conv is None:
            return
        tool = conv.phase == conversation.TOOL
        base = QColor(render.AMBER if tool else render.PURPLE)
        dim = QColor(base)
        dim.setAlpha(90)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        metrics = self.fontMetrics()
        cell = metrics.horizontalAdvance('M')
        x, h = 0.0, float(self.height())
        for i, glyph in enumerate(self._cells):
            # The shimmer: a bright band sweeping left to right.
            pos = (i / CELLS - self._phase) % 1.0
            painter.setPen(base if pos < 0.3 else dim)
            painter.drawText(QRectF(x, 0, cell, h), Qt.AlignmentFlag.AlignVCenter, glyph)
            x += cell
        text = label(conv.phase, conv.tool)
        if conv.started:
            text += f'  {elapsed(time.monotonic() - conv.started)}'
        if conv.queue:
            text += f'  · {len(conv.queue)} queued'
        painter.setPen(base)
        painter.drawText(QRectF(x + cell, 0, self.width() - x - cell, h),
                         Qt.AlignmentFlag.AlignVCenter, text)
        painter.end()
