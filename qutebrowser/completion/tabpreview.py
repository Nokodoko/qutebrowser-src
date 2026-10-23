# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Telescope-ivy style preview pane for tab completions.

A tab that is on screen is grabbed live. A hidden webview cannot be painted,
so the tile area hands every tab to capture() just before hiding it, and the
preview falls back to that thumbnail. Thumbnails are scaled down when they are
taken, so showing one is a cheap scale of a small pixmap.

The card around the preview is painted by the completion's backdrop with
the shared glass chrome (mainwindow/glass.py); this widget paints only its
content.
"""

import weakref

from qutebrowser.qt.core import Qt, QRect, QRectF, QSize, QPoint
from qutebrowser.qt.gui import (QFont, QFontMetrics, QPainter, QPainterPath,
                                QPen, QPixmap)
from qutebrowser.qt.widgets import QWidget

from qutebrowser.config import config, stylesheet
from qutebrowser.mainwindow import glass
from qutebrowser.utils import debug, log, utils


#: Thumbnails are scaled to fit this box when taken (about 1 MB each).
THUMB_SIZE = QSize(720, 450)

_thumbnails: 'weakref.WeakKeyDictionary[QWidget, QPixmap]' = (
    weakref.WeakKeyDictionary())


def _grab(tab: QWidget) -> QPixmap | None:
    """Grab a visible tab, scaled down to the thumbnail box."""
    if not tab.isVisible() or tab.width() <= 0 or tab.height() <= 0:
        return None
    with debug.log_time(log.misc, 'Grabbing tab preview'):
        pixmap = tab.grab()
        if pixmap.isNull():
            return None
        return pixmap.scaled(THUMB_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)


def capture(tab: QWidget) -> None:
    """Remember what a tab looks like; called before it is hidden."""
    if not config.val.completion.tab_preview:
        return
    pixmap = _grab(tab)
    if pixmap is not None:
        _thumbnails[tab] = pixmap


def snapshot(tab: QWidget) -> QPixmap | None:
    """A picture of the tab: live if it is on screen, else the last capture."""
    pixmap = _grab(tab)
    if pixmap is not None:
        _thumbnails[tab] = pixmap
        return pixmap
    return _thumbnails.get(tab)


class TabPreview(QWidget):

    """The preview card's content on the right of the tab completion.

    The widget has no background: the completion's backdrop, under it,
    paints the glass and the card, so the frosted page shows through.
    """

    STYLESHEET = """
        TabPreview {
            font: {{ conf.fonts.completion.entry }};
        }
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tab: weakref.ReferenceType[QWidget] | None = None
        self._pixmap: QPixmap | None = None
        self._scaled: QPixmap | None = None
        self._scaled_for = QSize()
        self._title = ''
        self._url = ''
        self._icon = None
        stylesheet.set_register(self)
        self.hide()

    def __repr__(self) -> str:
        return utils.get_repr(self)

    def current_tab(self) -> QWidget | None:
        return None if self._tab is None else self._tab()

    def set_tab(self, tab: QWidget | None) -> None:
        """Show the given tab (a browsertab.AbstractTab), or nothing."""
        self._scaled = None
        if tab is None:
            self._tab = None
            self._pixmap = None
            self._title = self._url = ''
            self._icon = None
        else:
            self._tab = weakref.ref(tab)
            self._url = tab.url().toDisplayString()  # type: ignore[attr-defined]
            self._title = tab.title() or self._url  # type: ignore[attr-defined]
            self._icon = tab.icon()  # type: ignore[attr-defined]
            self._pixmap = snapshot(tab)
        self.update()

    def clear(self) -> None:
        self.set_tab(None)

    def _scaled_to(self, size: QSize) -> QPixmap | None:
        """The picture fitted to ``size``, cached until the size changes."""
        if self._pixmap is None or size.width() <= 0 or size.height() <= 0:
            return None
        if self._scaled is None or self._scaled_for != size:
            self._scaled_for = size
            self._scaled = self._pixmap
            if (self._pixmap.width() > size.width() or
                    self._pixmap.height() > size.height()):
                self._scaled = self._pixmap.scaled(
                    size, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
        return self._scaled

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._scaled = None

    def outer_rect(self) -> QRect:
        """The card's area in this widget, reaching half a gap to the left."""
        return self.rect().adjusted(-glass.PAD // 2, 0, 0, 0)

    def paintEvent(self, e):
        """Paint the card: favicon and title, URL, then the page."""
        colors = glass.Colors.from_config()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        outer = self.outer_rect()
        font = self.font()
        heading = QFont(font)
        heading.setBold(True)
        if self._tab is None:
            return

        content = glass.content_rect(outer, font)
        title_metrics = QFontMetrics(heading)
        entry_metrics = QFontMetrics(font)
        line = title_metrics.height()

        x = content.left()
        if self._icon is not None and not self._icon.isNull():
            self._icon.paint(painter, QRect(x, content.top(), line, line))
            x += line + glass.INNER
        painter.setFont(heading)
        painter.setPen(colors.fg)
        painter.drawText(
            QRect(x, content.top(), content.right() - x, line),
            Qt.AlignmentFlag.AlignVCenter,
            title_metrics.elidedText(self._title, Qt.TextElideMode.ElideRight,
                                     content.right() - x))
        top = content.top() + line + 2
        painter.setFont(font)
        painter.setPen(colors.dim)
        painter.drawText(
            QRect(content.left(), top, content.width(),
                  entry_metrics.height()),
            Qt.AlignmentFlag.AlignVCenter,
            entry_metrics.elidedText(self._url, Qt.TextElideMode.ElideMiddle,
                                     content.width()))
        top += entry_metrics.height() + glass.INNER

        area = QRect(content.left(), top, content.width(),
                     content.bottom() - top)
        # Leave room for the shadow.
        pixmap = self._scaled_to(area.size() - QSize(12, 12))
        if pixmap is None:
            self._paint_placeholder(painter, area, colors)
            return

        target = QRectF(
            area.left() + (area.width() - pixmap.width()) / 2,
            area.top() + 2, pixmap.width(), pixmap.height())
        radius = 6
        glass.paint_shadow(painter, target, radius)
        clip = QPainterPath()
        clip.addRoundedRect(target, radius, radius)
        painter.save()
        painter.setClipPath(clip)
        painter.drawPixmap(target.topLeft(), pixmap)
        painter.restore()
        painter.setPen(QPen(colors.border, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(target.adjusted(0.5, 0.5, -0.5, -0.5),
                                radius, radius)

    def _paint_placeholder(self, painter: QPainter, area: QRect,
                           colors: glass.Colors) -> None:
        """A tab that was never on screen: a big faded favicon and a note."""
        size = min(64, area.height() // 3)
        center = area.center()
        if self._icon is not None and not self._icon.isNull() and size > 0:
            painter.save()
            painter.setOpacity(0.35)
            self._icon.paint(painter, QRect(center - QPoint(size // 2, size),
                                            QSize(size, size)))
            painter.restore()
        painter.setFont(self.font())
        painter.setPen(colors.dim)
        painter.drawText(
            QRect(area.left(), center.y() + glass.INNER, area.width(),
                  QFontMetrics(self.font()).height() * 2),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            "Not rendered yet\nfocus it once to capture a preview")
