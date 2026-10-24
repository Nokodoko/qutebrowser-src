# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Frosted-glass chrome shared by qutebrowser's menus.

The completion, the keyhint popup, prompts and context menus are painted as
rounded translucent cards over a blurred copy of the page they cover. There
is no compositor blur to lean on, so the page is grabbed when a menu opens
(or resizes) and blurred by smooth down- and upscaling, a few milliseconds.

Overlays are child widgets of the main window and grab the tab widget, which
they cover but are not part of, so a menu never shows up in its own glass.
Context menus are popup windows; they get a translucent window background
for their rounded corners and frost the main window area under them.
"""

import dataclasses
from collections.abc import Callable

from qutebrowser.qt.core import (QEvent, QObject, QPoint, QPointF, QRect,
                                 QRectF, QSize, QTimer, Qt)
from qutebrowser.qt.gui import (QBrush, QColor, QFont, QFontMetrics,
                                QLinearGradient, QPainter, QPainterPath, QPen,
                                QPixmap, QTransform)
from qutebrowser.qt.widgets import QApplication, QMenu, QWidget

from qutebrowser.config import config
from qutebrowser.utils import debug, log, objreg


#: Gap around and between cards.
PAD = 10
#: Corner radius of cards and menus.
RADIUS = 10
#: Space between a card's border and its content.
INNER = 8


def enabled() -> bool:
    return config.val.glass.enabled


@dataclasses.dataclass
class Colors:

    """The colors.glass.* settings, read once per paint."""

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
        glass = config.val.colors.glass
        return cls(bg=glass.bg, card=glass.card.bg, border=glass.border,
                   title_fg=glass.title.fg, title_bg=glass.title.bg,
                   fg=glass.fg, dim=glass.dim.fg,
                   selected_bg=glass.selected.bg,
                   selected_fg=glass.selected.fg,
                   accent=glass.accent, match=glass.match.fg)


# ---- geometry ---------------------------------------------------------------

def title_font(font: QFont) -> QFont:
    """The chip font: the entry font (``font``), bold and a bit smaller.

    Fonts are stylesheet strings in the config, so the widgets take their
    font from their stylesheet and everything derives from it.
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


# ---- the frosted page -------------------------------------------------------

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


def _page_widget(widget: QWidget) -> QWidget | None:
    """The tab widget of the main window ``widget`` belongs to."""
    window = widget.window()
    if isinstance(widget, QMenu) and widget.parentWidget() is not None:
        window = widget.parentWidget().window()
    win_id = getattr(window, 'win_id', None)
    if win_id is None:
        active = QApplication.activeWindow()
        win_id = getattr(active, 'win_id', None)
    if win_id is None:
        return None
    try:
        tabbed_browser = objreg.get('tabbed-browser', scope='window',
                                    window=win_id)
    except (KeyError, objreg.RegistryUnavailableError):
        return None
    page = tabbed_browser.widget
    return page if isinstance(page, QWidget) else None


class Frost:

    """A sharp and a frosted copy of the page under a widget.

    Refreshed when the widget shows or resizes. Grabbing lays the window
    out, which can resize the widget again, so refreshes are guarded and
    the ones caused by a resize are deferred to the next event-loop turn.

    With ``adjacent`` a widget outside the page (the statusbar, above or
    below it) takes the strip of page nearest to it, mirrored, so the glass
    reads as the page edge continuing under the bar.
    """

    def __init__(self, widget: QWidget,
                 on_change: Callable[[], None] | None = None, *,
                 adjacent: bool = False) -> None:
        self._widget = widget
        self._on_change = on_change or widget.update
        self._adjacent = adjacent
        self.sharp: QPixmap | None = None
        self.frosted: QPixmap | None = None
        self._busy = False
        self._scheduled = False

    def clear(self) -> None:
        self.sharp = self.frosted = None

    def schedule(self) -> None:
        """Refresh soon; for resize events."""
        if self._busy or self._scheduled:
            return
        self._scheduled = True
        QTimer.singleShot(0, self._run_scheduled)

    def _run_scheduled(self) -> None:
        self._scheduled = False
        self.refresh()

    def refresh(self) -> None:
        if self._busy:
            return
        self.clear()
        if not enabled() or not self._widget.isVisible():
            return
        self._busy = True
        try:
            self._grab()
        finally:
            self._busy = False
        self._on_change()

    def _grab(self) -> None:
        page = _page_widget(self._widget)
        if page is None:
            return
        topleft = page.mapFromGlobal(self._widget.mapToGlobal(QPoint(0, 0)))
        size = self._widget.size()
        area = QRect(topleft, size).intersected(page.rect())
        mirror = False
        if area.isEmpty() and self._adjacent:
            area = QRect(topleft.x(), 0, size.width(),
                         min(size.height(), page.height()))
            if topleft.y() >= page.height():
                area.moveBottom(page.height() - 1)
            area = area.intersected(page.rect())
            mirror = True
        if area.isEmpty():
            return
        with debug.log_time(log.misc, 'Frosting glass for {!r}'.format(
                type(self._widget).__name__)):
            grabbed = page.grab(area)
            if mirror:
                grabbed = grabbed.transformed(QTransform().scale(1, -1))
                offset = QPoint(area.left() - topleft.x(), 0)
            else:
                offset = area.topLeft() - topleft
            sharp = QPixmap(size)
            sharp.fill(Qt.GlobalColor.black)
            painter = QPainter(sharp)
            painter.drawPixmap(offset, grabbed)
            painter.end()
            self.sharp = sharp
            self.frosted = blur(sharp, config.val.glass.blur)


# ---- painting -----------------------------------------------------------

def _sheen(painter: QPainter, rect: QRectF, radius: float) -> None:
    """Glass edge: light catching the top of a card, fading downwards."""
    gradient = QLinearGradient(rect.topLeft(), rect.topLeft() + QPointF(
        0, min(rect.height() / 2, 60)))
    gradient.setColorAt(0, QColor(255, 255, 255, 40))
    gradient.setColorAt(1, QColor(255, 255, 255, 0))
    painter.setPen(QPen(QBrush(gradient), 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius - 1,
                            radius - 1)


def paint_panel(painter: QPainter, rect: QRect, colors: Colors,
                frost: Frost | None = None, *,
                lit_edge: str = 'top') -> None:
    """A full-width panel: frosted page, tint, a lit edge.

    The lit edge is the one facing the page: the top, or the bottom for a
    panel above the page (``lit_edge='bottom'``).
    """
    if frost is not None and frost.frosted is not None:
        painter.drawPixmap(rect.topLeft(), frost.frosted)
    painter.fillRect(rect, colors.bg)
    if lit_edge == 'bottom':
        border, light = rect.bottom(), rect.bottom() - 1
    else:
        border, light = rect.top(), rect.top() + 1
    painter.fillRect(QRect(rect.left(), border, rect.width(), 1),
                     colors.border)
    painter.fillRect(QRect(rect.left(), light, rect.width(), 1),
                     QColor(255, 255, 255, 22))


def paint_pill(painter: QPainter, rect: QRect, color: QColor) -> None:
    """A statusbar segment as a glass pill.

    The mode color, slightly translucent over the panel, with the lit edge
    of a card. Self-contained (no config access) so the statusbar widgets
    can paint it wherever they are used.
    """
    if rect.isEmpty():
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pill = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    radius = pill.height() / 2
    fill = QColor(color)
    fill.setAlpha(min(fill.alpha(), 225))
    painter.setPen(QPen(QColor(255, 255, 255, 45), 1))
    painter.setBrush(fill)
    painter.drawRoundedRect(pill, radius, radius)
    _sheen(painter, pill, radius)
    painter.restore()


def paint_surface(painter: QPainter, rect: QRect, colors: Colors,
                  frost: Frost | None = None, *,
                  radius: float = RADIUS, corners: bool = True) -> None:
    """A free-standing rounded glass card filling ``rect``.

    With ``corners``, the page is drawn sharp outside the rounded corners,
    so they look cut out even though the widget itself is rectangular.
    A translucent popup window has real transparency and does without.
    """
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if corners and frost is not None and frost.sharp is not None:
        painter.drawPixmap(0, 0, frost.sharp)
    card = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    path = QPainterPath()
    path.addRoundedRect(card, radius, radius)
    painter.save()
    painter.setClipPath(path)
    if frost is not None and frost.frosted is not None:
        painter.drawPixmap(0, 0, frost.frosted)
    painter.fillRect(card, colors.bg)
    painter.fillRect(card, colors.card)
    painter.restore()
    painter.setPen(QPen(colors.border, 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(card, radius, radius)
    _sheen(painter, card, radius)
    painter.restore()


def paint_card(painter: QPainter, outer: QRect, font: QFont,
               colors: Colors, title: str, right: str = '') -> None:
    """A rounded card on a panel, with a title chip in its top border.

    ``right`` goes into a quieter outlined chip at the right end of the
    border, telescope's match counter.
    """
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    card = QRectF(card_rect(outer, font)).adjusted(0.5, 0.5, -0.5, -0.5)
    painter.setPen(QPen(colors.border, 1))
    painter.setBrush(colors.card)
    painter.drawRoundedRect(card, RADIUS, RADIUS)
    _sheen(painter, card, RADIUS)

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


# ---- context menus ----------------------------------------------------------

class MenuGlass(QObject):

    """Application-wide event filter giving every QMenu the glass look.

    The item styling is the QMenu part of the main window's stylesheet; this
    makes the popup window translucent (for its rounded corners, before the
    window is created, on Polish) and paints the frosted card under the
    menu's own painting.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._frosts: dict[int, Frost] = {}

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:
        if (not isinstance(obj, QMenu) or event is None or
                not enabled()):
            return False
        etype = event.type()
        if etype == QEvent.Type.Polish:
            obj.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            obj.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint)
        elif etype == QEvent.Type.Show:
            self._frost_for(obj).refresh()
        elif etype == QEvent.Type.Hide:
            self._frosts.pop(id(obj), None)
        elif etype == QEvent.Type.Paint:
            painter = QPainter(obj)
            paint_surface(painter, obj.rect(), Colors.from_config(),
                          self._frosts.get(id(obj)), corners=False)
            painter.end()
        return False

    def _frost_for(self, menu: QMenu) -> Frost:
        frost = self._frosts.get(id(menu))
        if frost is None:
            frost = Frost(menu)
            self._frosts[id(menu)] = frost
        return frost


_menu_glass: MenuGlass | None = None


def init() -> None:
    """Install the context menu glass on the application."""
    global _menu_glass
    app = QApplication.instance()
    if app is None or _menu_glass is not None:
        return
    _menu_glass = MenuGlass(app)
    app.installEventFilter(_menu_glass)
