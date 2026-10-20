# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A zellij-style tab bar drawn over the browser tab bar.

Every tag is a zellij "tab" (its browser tabs are the panes). The bar is a
row of ribbons, one per occupied or viewed tag, right-aligned over the
ordinary tab bar so it costs no vertical space. It follows the tab bar's
geometry and visibility and is mouse-transparent except for a click on a
ribbon, which views that tag.

Colors default to the vesper theme's ribbon_selected / ribbon_unselected
(the user's zellij theme) and live under colors.tabs.tiling.ribbon.*.
"""

from qutebrowser.qt.core import QEvent, QObject, QPoint, QRect, QSize, Qt
from qutebrowser.qt.gui import (QColor, QFontMetrics, QMouseEvent, QPainter,
                                QPaintEvent, QPolygon)
from qutebrowser.qt.widgets import QWidget

from qutebrowser.config import config
from qutebrowser.mainwindow import tiling
from qutebrowser.utils import log

#: Horizontal padding inside a ribbon, in pixels.
_PAD = 8
#: Gap between the last ribbon and the right edge of the bar.
_MARGIN = 4


class ZellijBar(QWidget):

    """The ribbon strip; owned by the TabWidget, parented to its tab bar."""

    def __init__(self, tiles: tiling.TileArea, bar: QWidget) -> None:
        super().__init__(bar)
        self._tiles = tiles
        self._bar = bar
        self._ribbons: list[tuple[QRect, int]] = []
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        bar.installEventFilter(self)
        tiles.arranged.connect(self.refresh)
        tiles.view_changed.connect(self.refresh)
        tiles.renamed.connect(self.refresh)
        config.instance.changed.connect(self._on_config_changed)
        self.refresh()

    def _on_config_changed(self, option: str) -> None:
        if (option.startswith('colors.tabs.tiling.ribbon')
                or option.startswith('fonts.tabs')
                or option == 'tabs.tiling.zellij_bar'):
            self.refresh()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self._bar and event.type() in (
                QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Hide):
            self.refresh()
        return False

    # ---- content -------------------------------------------------------

    def _entries(self) -> list[tuple[int, str, bool, int]]:
        """(tag, label, selected, count) for every tag worth a ribbon."""
        occupied = self._tiles.occupied()
        shown = occupied | (self._tiles.seltags & tiling.TAGMASK)
        limit = self._tiles.limit()
        out = []
        for tag in tiling.tag_numbers(shown):
            count = self._tiles.count_on(tag)
            label = f"{tag} {self._tiles.name_of(tag)}"
            if limit and count >= limit:
                label += " ●"  # full: zellij has no cap, so mark it
            selected = bool(self._tiles.seltags & tiling.tag_mask(tag))
            out.append((tag, label, selected, count))
        return out

    def refresh(self) -> None:
        """Recompute geometry from the tab bar and repaint."""
        if not config.val.tabs.tiling.zellij_bar or not self._bar.isVisible():
            self.hide()
            return
        fm = QFontMetrics(self.font())
        height = self._bar.height()
        arrow = height // 2
        width = _MARGIN
        for _tag, label, _sel, _count in self._entries():
            width += fm.horizontalAdvance(label) + 2 * _PAD + arrow
        width += arrow
        bar_w = self._bar.width()
        width = min(width, bar_w)
        self.setGeometry(QRect(bar_w - width, 0, width, height))
        self.raise_()
        self.show()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.size()

    # ---- painting ------------------------------------------------------

    def paintEvent(self, e: QPaintEvent) -> None:  # noqa: N802
        """Draw the ribbons right to left, zellij style: arrow-capped blocks."""
        entries = self._entries()
        self._ribbons = []
        if not entries:
            return
        fm = QFontMetrics(self.font())
        h = self.height()
        arrow = h // 2
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        sel_bg = QColor(config.val.colors.tabs.tiling.ribbon.selected.bg)
        sel_fg = QColor(config.val.colors.tabs.tiling.ribbon.selected.fg)
        un_bg = QColor(config.val.colors.tabs.tiling.ribbon.unselected.bg)
        un_fg = QColor(config.val.colors.tabs.tiling.ribbon.unselected.fg)
        x = self.width() - _MARGIN
        # Lay out from the right so the newest tag sits at the edge.
        for tag, label, selected, _count in reversed(entries):
            text_w = fm.horizontalAdvance(label) + 2 * _PAD
            right = x
            left = right - text_w
            bg = sel_bg if selected else un_bg
            fg = sel_fg if selected else un_fg
            # body
            body = QRect(left, 0, text_w, h)
            painter.fillRect(body, bg)
            # right-pointing arrow cap on the right, notch on the left
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawPolygon(QPolygon([
                QPoint(right, 0), QPoint(right + arrow, h // 2),
                QPoint(right, h)]))
            painter.drawPolygon(QPolygon([
                QPoint(left - arrow, 0), QPoint(left, 0), QPoint(left, h),
                QPoint(left - arrow, h), QPoint(left, h // 2)]))
            painter.setPen(fg)
            painter.drawText(body, Qt.AlignmentFlag.AlignCenter, label)
            self._ribbons.append((QRect(left - arrow, 0, text_w + 2 * arrow, h), tag))
            x = left - arrow - 1
        painter.end()

    # ---- mouse ---------------------------------------------------------

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        pos = e.position().toPoint()
        for rect, tag in self._ribbons:
            if rect.contains(pos):
                log.webview.debug(f"zellij bar: view tag {tag}")
                self._tiles.view(tiling.tag_mask(tag))
                e.accept()
                return
        e.ignore()
