# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Base text widgets for statusbar."""

from qutebrowser.qt.core import Qt, QEvent, QRect, pyqtProperty
from qutebrowser.qt.widgets import QLabel, QSizePolicy
from qutebrowser.qt.gui import QPainter, QColor

from qutebrowser.utils import qtutils, utils


class TextBase(QLabel):

    """A text in the statusbar.

    Unlike QLabel, the text will get elided.

    Eliding is loosely based on
    https://gedgedev.blogspot.ch/2010/12/elided-labels-in-qt.html

    With statusbar.segments, the statusbar stylesheet sets segmentBg to the
    mode color, and the label paints it only behind its own text (padded by a
    space on each side) instead of the statusbar painting it across the whole
    bar. A transparent or invalid segmentBg paints nothing.

    Attributes:
        _elidemode: Where to elide the text.
        _elided_text: The current elided text.
        _segment_bg: The segment background color.
    """

    def __init__(self, parent=None, elidemode=Qt.TextElideMode.ElideRight):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._elidemode = elidemode
        self._elided_text = ''
        self._segment_bg = QColor()

    @pyqtProperty(QColor)
    def segmentBg(self):  # noqa: N802
        """Getter for segmentBg, so the stylesheet can set it as qproperty."""
        return self._segment_bg

    @segmentBg.setter  # type: ignore[no-redef]
    def segmentBg(self, color):  # noqa: N802
        """Setter for segmentBg; re-pads the text and repaints."""
        self._segment_bg = QColor(color)
        self._update_segment_margins()
        self.update()

    def _segment_padding(self):
        """Get the horizontal segment padding, 0 when segments are off."""
        if not self._segment_bg.isValid() or self._segment_bg.alpha() == 0:
            return 0
        return self.fontMetrics().horizontalAdvance(' ')

    def _update_segment_margins(self):
        """Reserve the segment padding on both sides of the text."""
        padding = self._segment_padding()
        self.setContentsMargins(padding, 0, padding, 0)

    def __repr__(self):
        return utils.get_repr(self, text=self.text())

    def _update_elided_text(self, width):
        """Update the elided text when necessary.

        Args:
            width: The maximal width the text should take.
        """
        if self.text():
            self._elided_text = self.fontMetrics().elidedText(
                self.text(), self._elidemode, width, Qt.TextFlag.TextShowMnemonic)
        else:
            self._elided_text = ''

    def setText(self, txt):
        """Extend QLabel::setText to update the elided text afterwards.

        Args:
            txt: The text to set (string).
        """
        super().setText(txt)
        if self._elidemode != Qt.TextElideMode.ElideNone:
            self._update_elided_text(self.contentsRect().width())

    def resizeEvent(self, e):
        """Extend QLabel::resizeEvent to update the elided text afterwards."""
        super().resizeEvent(e)
        qtutils.ensure_valid(e.size())
        self._update_elided_text(self.contentsRect().width())

    def changeEvent(self, e):
        """Extend QLabel::changeEvent to re-pad the text on a font change."""
        super().changeEvent(e)
        if e.type() == QEvent.Type.FontChange:
            self._update_segment_margins()

    def _paint_segment(self, text):
        """Fill the segment background behind the given text."""
        padding = self._segment_padding()
        text_rect = self.fontMetrics().boundingRect(
            self.contentsRect(), int(self.alignment()), text)
        segment = QRect(text_rect.left() - padding, 0,
                        text_rect.width() + 2 * padding, self.height())
        painter = QPainter(self)
        painter.fillRect(segment.intersected(self.rect()), self._segment_bg)
        painter.end()

    def paintEvent(self, e):
        """Override QLabel::paintEvent to draw elided text."""
        if self._elidemode == Qt.TextElideMode.ElideNone:
            text = self.text()
        else:
            text = self._elided_text
        if text and self._segment_padding():
            self._paint_segment(text)

        if self._elidemode == Qt.TextElideMode.ElideNone:
            super().paintEvent(e)
        else:
            e.accept()
            painter = QPainter(self)
            rect = self.contentsRect()
            qtutils.ensure_valid(rect)
            painter.drawText(rect, int(self.alignment()), self._elided_text)
