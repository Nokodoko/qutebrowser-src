# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Telescope-ivy style preview pane for tab completions.

A tab that is on screen is grabbed live. A hidden webview cannot be painted,
so the tile area hands every tab to capture() just before hiding it, and the
preview falls back to that thumbnail. Thumbnails are scaled down when they are
taken, so showing one is a cheap scale of a small pixmap.

The ivy chrome (panel, rounded cards with titles in their top border) is
painted here with QPainter, for both the result list and the preview pane.
"""

import dataclasses
import weakref

from qutebrowser.qt.core import Qt, QRect, QRectF, QSize, QPoint, QPointF
from qutebrowser.qt.gui import (QBrush, QColor, QFont, QFontMetrics,
                                QLinearGradient, QPainter, QPainterPath, QPen,
                                QPixmap)
from qutebrowser.qt.widgets import QWidget

from qutebrowser.config import config, stylesheet
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


# ---- ivy chrome -----------------------------------------------------------

#: Gap around and between the cards.
PAD = 10
#: Corner radius of the cards.
RADIUS = 10
#: Space between a card's border and its content.
INNER = 8


@dataclasses.dataclass
class Colors:

    """The colors.completion.ivy.* settings, read once per paint."""

    bg: QColor
    card: QColor
    border: QColor
    title_fg: QColor
    title_bg: QColor
    fg: QColor
    dim: QColor
    selected_bg: QColor
    selected_fg: QColor
    accent: QColor
    match: QColor

    @classmethod
    def from_config(cls) -> 'Colors':
        ivy = config.val.colors.completion.ivy
        return cls(bg=ivy.bg, card=ivy.card.bg, border=ivy.border,
                   title_fg=ivy.title.fg, title_bg=ivy.title.bg,
                   fg=ivy.fg, dim=ivy.dim.fg,
                   selected_bg=ivy.selected.bg, selected_fg=ivy.selected.fg,
                   accent=ivy.accent, match=ivy.match.fg)


def title_font(font: QFont) -> QFont:
    """The chip font: the entry font (``font``), bold and a bit smaller.

    Fonts are stylesheet strings in the config, so the widgets take the
    entry font from their stylesheet and everything derives from it.
    """
    title = QFont(font)
    title.setBold(True)
    if title.pointSizeF() > 0:
        title.setPointSizeF(title.pointSizeF() * 0.8)
    elif title.pixelSize() > 0:
        title.setPixelSize(int(title.pixelSize() * 0.8))
    return title


def chip_height(font: QFont) -> int:
    return QFontMetrics(title_font(font)).height() + 4


def card_rect(outer: QRect, font: QFont) -> QRect:
    """The card inside an area: padded, with room for the title chip."""
    return outer.adjusted(PAD, PAD + chip_height(font) // 2, -PAD, -PAD)


def content_rect(outer: QRect, font: QFont) -> QRect:
    """Where a card's content goes, below its title chip."""
    card = card_rect(outer, font)
    return card.adjusted(INNER, chip_height(font) // 2 + INNER // 2,
                         -INNER, -INNER)


def blur(pixmap: QPixmap, radius: int) -> QPixmap:
    """A cheap frosted-glass blur: scale down smoothly, then back up.

    Two smooth downscales and one smooth upscale approximate a gaussian of
    about ``radius`` pixels in a few milliseconds, with no extra modules.
    """
    if radius <= 1 or pixmap.isNull():
        return pixmap
    size = pixmap.size()
    smooth = Qt.TransformationMode.SmoothTransformation
    ignore = Qt.AspectRatioMode.IgnoreAspectRatio
    half = pixmap.scaled(QSize(max(size.width() * 2 // radius, 1),
                               max(size.height() * 2 // radius, 1)),
                         ignore, smooth)
    small = half.scaled(QSize(max(size.width() // radius, 1),
                              max(size.height() // radius, 1)),
                        ignore, smooth)
    return small.scaled(size, ignore, smooth)


def paint_panel(painter: QPainter, rect: QRect, colors: Colors,
                glass: QPixmap | None = None) -> None:
    """The panel behind the cards: frosted page, tint, a lit top edge."""
    if glass is not None and not glass.isNull():
        painter.drawPixmap(rect.topLeft(), glass)
    painter.fillRect(rect, colors.bg)
    painter.fillRect(QRect(rect.left(), rect.top(), rect.width(), 1),
                     colors.border)
    painter.fillRect(QRect(rect.left(), rect.top() + 1, rect.width(), 1),
                     QColor(255, 255, 255, 22))


def paint_card(painter: QPainter, outer: QRect, font: QFont,
               colors: Colors, title: str, right: str = '') -> None:
    """A rounded card with a title chip in its top border.

    ``right`` goes into a quieter outlined chip at the right end of the
    border, telescope's match counter.
    """
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    card = QRectF(card_rect(outer, font)).adjusted(0.5, 0.5, -0.5, -0.5)
    painter.setPen(QPen(colors.border, 1))
    painter.setBrush(colors.card)
    painter.drawRoundedRect(card, RADIUS, RADIUS)
    # Glass edge: light catching the top of the card, fading downwards.
    sheen = QLinearGradient(card.topLeft(), card.topLeft() + QPointF(
        0, min(card.height() / 2, 60)))
    sheen.setColorAt(0, QColor(255, 255, 255, 40))
    sheen.setColorAt(1, QColor(255, 255, 255, 0))
    painter.setPen(QPen(QBrush(sheen), 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(card.adjusted(1, 1, -1, -1), RADIUS - 1,
                            RADIUS - 1)

    chip_font = title_font(font)
    metrics = QFontMetrics(chip_font)
    height = chip_height(font)
    top = int(card.top()) - height // 2
    painter.setFont(chip_font)
    if title:
        width = metrics.horizontalAdvance(title) + 2 * INNER + 4
        chip = QRectF(card.left() + RADIUS + 4, top, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colors.title_bg)
        painter.drawRoundedRect(chip, height / 2, height / 2)
        painter.setPen(colors.title_fg)
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, title)
    if right:
        width = metrics.horizontalAdvance(right) + 2 * INNER
        box = QRectF(card.right() - RADIUS - 4 - width, top + 0.5, width,
                     height - 1)
        painter.setPen(QPen(colors.border, 1))
        painter.setBrush(colors.card)
        painter.drawRoundedRect(box, height / 2, height / 2)
        painter.setPen(colors.dim)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, right)
    painter.restore()


def paint_shadow(painter: QPainter, rect: QRectF, radius: float) -> None:
    """A soft drop shadow: a few translucent rounded rects, growing."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    for i in range(6, 0, -1):
        painter.setBrush(QColor(0, 0, 0, 14))
        painter.drawRoundedRect(rect.adjusted(-i, -i + 3, i, i + 3),
                                radius + i, radius + i)
    painter.restore()


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
        return self.rect().adjusted(-PAD // 2, 0, 0, 0)

    def paintEvent(self, e):
        """Paint the card: favicon and title, URL, then the page."""
        colors = Colors.from_config()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        outer = self.outer_rect()
        font = self.font()
        heading = QFont(font)
        heading.setBold(True)
        if self._tab is None:
            return

        content = content_rect(outer, font)
        title_metrics = QFontMetrics(heading)
        entry_metrics = QFontMetrics(font)
        line = title_metrics.height()

        x = content.left()
        if self._icon is not None and not self._icon.isNull():
            self._icon.paint(painter, QRect(x, content.top(), line, line))
            x += line + INNER
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
        top += entry_metrics.height() + INNER

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
        paint_shadow(painter, target, radius)
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
                           colors: Colors) -> None:
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
            QRect(area.left(), center.y() + INNER, area.width(),
                  QFontMetrics(self.font()).height() * 2),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            "Not rendered yet\nfocus it once to capture a preview")
