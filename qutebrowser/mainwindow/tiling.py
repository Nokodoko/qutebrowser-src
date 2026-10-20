# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""dwm-style dynamic tiling of tabs.

Tabs play the role of dwm clients. Every tab carries a bitmask of tags
(``tab.data.tags``); the window carries a selected tagset. Tabs whose tags
intersect the selected tagset are visible and get arranged by the active
layout, the rest are hidden.

The pure parts (tag arithmetic, rectangle computation) live at module level
so they can be unit-tested without a QApplication. ``TileArea`` is the widget
that owns the tab widgets and applies the computed geometry.
"""

import dataclasses
import math
from collections.abc import Sequence

from qutebrowser.qt.core import (QEvent, QObject, QRect, Qt, QTimer, pyqtSignal,
                                 pyqtSlot)
from qutebrowser.qt.gui import QPainter, QPaintEvent
from qutebrowser.qt.widgets import QWidget

from qutebrowser.config import config
from qutebrowser.utils import log

#: Number of tags; dwm uses nine as well.
NUM_TAGS = 9
#: Mask with every tag set, i.e. the "view all" tagset.
TAGMASK = (1 << NUM_TAGS) - 1

LAYOUTS = ('tile', 'monocle')


def tag_mask(tag: int) -> int:
    """Convert a 1-based tag number to its bitmask.

    ``0`` means "all tags", like ``MODKEY+0`` in dwm.
    """
    if tag == 0:
        return TAGMASK
    if not 1 <= tag <= NUM_TAGS:
        raise ValueError(f"tag must be 0..{NUM_TAGS}, got {tag}")
    return 1 << (tag - 1)


def tag_numbers(mask: int) -> list[int]:
    """List the 1-based tag numbers set in a mask."""
    return [n for n in range(1, NUM_TAGS + 1) if mask & (1 << (n - 1))]


def is_visible(tags: int, seltags: int) -> bool:
    """Whether a client with the given tags is on the selected tagset."""
    return bool(tags & seltags & TAGMASK)


def tags_of(tab: QWidget) -> int:
    """The tag mask of a tab widget; plain widgets live on tag 1."""
    data = getattr(tab, 'data', None)
    return getattr(data, 'tags', 1)


def _is_fullscreen(tab: QWidget) -> bool:
    data = getattr(tab, 'data', None)
    return bool(getattr(data, 'fullscreen', False))


@dataclasses.dataclass
class LayoutParams:

    """The per-window layout state, mirroring dwm's Monitor fields."""

    layout: str = 'monocle'
    mfact: float = 0.55
    nmaster: int = 1
    gap: int = 0
    #: dwm's borderpx: every tile is inset by this and framed (see
    #: TileArea.paintEvent), the focused one in a distinct color.
    border: int = 0

    def __post_init__(self) -> None:
        if self.layout not in LAYOUTS:
            raise ValueError(f"unknown layout {self.layout!r}")
        self.mfact = min(max(self.mfact, 0.05), 0.95)
        self.nmaster = max(self.nmaster, 0)
        self.gap = max(self.gap, 0)
        self.border = max(self.border, 0)


def inset(rect: QRect, border: int) -> QRect:
    """The client rectangle inside a tile's border (dwm: wa minus 2*bw)."""
    if border <= 0:
        return QRect(rect)
    # Never collapse to nothing on a tile narrower than its own border.
    bw = min(border, max(rect.width() // 2 - 1, 0))
    bh = min(border, max(rect.height() // 2 - 1, 0))
    return rect.adjusted(bw, bh, -bw, -bh)


def frame_rects(outer: QRect, inner: QRect) -> list[QRect]:
    """The four strips between a tile and its client rectangle."""
    if outer == inner:
        return []
    return [
        QRect(outer.left(), outer.top(), outer.width(), inner.top() - outer.top()),
        QRect(outer.left(), inner.bottom() + 1, outer.width(),
              outer.bottom() - inner.bottom()),
        QRect(outer.left(), inner.top(), inner.left() - outer.left(), inner.height()),
        QRect(inner.right() + 1, inner.top(), outer.right() - inner.right(),
              inner.height()),
    ]


def arrange(area: QRect, count: int, params: LayoutParams) -> list[QRect]:
    """Compute one rectangle per visible client.

    Args:
        area: The rectangle to tile into.
        count: Number of visible clients, in stack order (master first).
        params: The layout parameters.

    Return:
        A list of ``count`` rectangles. For ``monocle`` every rectangle is the
        whole area, the caller is expected to raise the focused one.
    """
    if count <= 0:
        return []
    gap = params.gap
    inner = area.adjusted(gap, gap, -gap, -gap)
    if params.layout == 'monocle':
        return [QRect(inner) for _ in range(count)]
    return _tile(inner, count, params)


def _tile(area: QRect, count: int, params: LayoutParams) -> list[QRect]:
    """dwm's tile(): nmaster clients on the left, the rest stacked right."""
    gap = params.gap
    nmaster = params.nmaster
    if count > nmaster and nmaster > 0:
        mw = int(area.width() * params.mfact)
    elif nmaster > 0:
        mw = area.width()
    else:
        mw = 0

    rects: list[QRect] = []
    master_rects = _column(
        QRect(area.x(), area.y(), mw, area.height()),
        min(count, nmaster), gap)
    stack_x = area.x() + mw + (gap if mw and count > nmaster else 0)
    stack_w = area.right() + 1 - stack_x
    stack_rects = _column(
        QRect(stack_x, area.y(), stack_w, area.height()),
        max(count - nmaster, 0), gap)
    rects.extend(master_rects)
    rects.extend(stack_rects)
    return rects


def _column(area: QRect, count: int, gap: int) -> list[QRect]:
    """Split an area into ``count`` rows separated by ``gap`` pixels."""
    if count <= 0:
        return []
    total_gap = gap * (count - 1)
    usable = max(area.height() - total_gap, 0)
    rects = []
    y = area.y()
    for i in range(count):
        # Distribute the rounding remainder over the first rows.
        h = math.ceil(usable * (i + 1) / count) - math.ceil(usable * i / count)
        rects.append(QRect(area.x(), y, area.width(), h))
        y += h + gap
    return rects


class TileArea(QWidget):

    """The widget holding every tab and positioning the visible ones.

    It covers the page area of the tab widget and replaces the hiding done by
    QStackedWidget with dwm-style arrangement.

    Attributes:
        seltags: The selected tagset of this window.
        params: The active layout parameters.
        _tabs: All tabs in tab-bar order (set by the owner).
        _current: The focused tab, raised in monocle and used as fallback.
        _frames: (tile rect, client rect, focused) per shown tab, painted as
                 borders by paintEvent. Children cover the client rect, so
                 only the strips between the two are ever visible.

    Signals:
        arranged: Emitted after a relayout, so a status widget can update.
        view_changed: Emitted when the selected tagset changed.
    """

    arranged = pyqtSignal()
    view_changed = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.seltags = 1
        self._prev_seltags = 1
        self.params = self._params_from_config()
        self._tabs: list[QWidget] = []
        self._current: QWidget | None = None
        self._frames: list[tuple[QRect, QRect, bool]] = []
        self._pending = False
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        config.instance.changed.connect(self._on_config_changed)

    @staticmethod
    def _params_from_config() -> LayoutParams:
        return LayoutParams(
            layout=config.val.tabs.tiling.layout,
            mfact=config.val.tabs.tiling.mfact,
            nmaster=config.val.tabs.tiling.nmaster,
            gap=config.val.tabs.tiling.gap,
            border=config.val.tabs.tiling.border,
        )

    @pyqtSlot(str)
    def _on_config_changed(self, option: str) -> None:
        if option.startswith('tabs.tiling.'):
            self.params = self._params_from_config()
            self.schedule()
        elif option.startswith('colors.tabs.tiling.'):
            self.update()

    # ---- bookkeeping driven by the tab widget --------------------------

    def set_tabs(self, tabs: Sequence[QWidget]) -> None:
        """Replace the ordered tab list and relayout."""
        self._tabs = list(tabs)
        self.schedule()

    def set_current(self, tab: QWidget | None) -> None:
        """Set the focused tab and relayout."""
        self._current = tab
        self.schedule()

    def attach(self, tab: QWidget) -> None:
        """Reparent a tab into this area without showing it yet."""
        if tab.parent() is not self:
            tab.setParent(self)
        tab.hide()

    def detach(self, tab: QWidget) -> None:
        """Forget a tab that is being removed; the owner deletes it."""
        tab.hide()
        if tab in self._tabs:
            self._tabs.remove(tab)
        if tab is self._current:
            self._current = None
        self.schedule()

    # ---- tag operations (names follow dwm) ----------------------------

    def view(self, mask: int) -> None:
        """Show the given tagset. Viewing the current one again goes back."""
        mask &= TAGMASK
        if mask == 0 or mask == self.seltags:
            # dwm: view(0) swaps to the previous tagset, as does re-viewing.
            mask = self._prev_seltags
        if mask == self.seltags:
            return
        self._prev_seltags = self.seltags
        self.seltags = mask
        self.view_changed.emit(self.seltags)
        self.schedule()

    def toggleview(self, mask: int) -> None:
        """Add or remove tags from the view, never leaving it empty."""
        new = self.seltags ^ (mask & TAGMASK)
        if new:
            self._prev_seltags = self.seltags
            self.seltags = new
            self.view_changed.emit(self.seltags)
            self.schedule()

    # ---- layout parameters (runtime, per window) -----------------------

    def frames(self) -> list[tuple[QRect, QRect, bool]]:
        """The (tile, client, focused) rectangles of the last relayout."""
        return list(self._frames)

    def set_layout(self, layout: str) -> None:
        """Switch layout; ``toggle`` flips between tile and monocle."""
        if layout == 'toggle':
            layout = 'monocle' if self.params.layout == 'tile' else 'tile'
        self.params = dataclasses.replace(self.params, layout=layout)
        self.schedule()

    def set_mfact(self, mfact: float) -> None:
        self.params = dataclasses.replace(self.params, mfact=mfact)
        self.schedule()

    def set_nmaster(self, nmaster: int) -> None:
        self.params = dataclasses.replace(self.params, nmaster=nmaster)
        self.schedule()

    def visible_tabs(self) -> list[QWidget]:
        """The tabs on the selected tagset, in stack order."""
        return [t for t in self._tabs
                if is_visible(tags_of(t), self.seltags)]

    def occupied(self) -> int:
        """Bitmask of tags that have at least one tab."""
        mask = 0
        for t in self._tabs:
            mask |= tags_of(t)
        return mask & TAGMASK

    # ---- layout --------------------------------------------------------

    def schedule(self) -> None:
        """Coalesce relayouts into one per event-loop turn."""
        if self._pending:
            return
        self._pending = True
        QTimer.singleShot(0, self.relayout)

    def resizeEvent(self, e: QEvent) -> None:  # noqa: N802
        super().resizeEvent(e)
        self.relayout()

    @pyqtSlot()
    def relayout(self) -> None:
        """Apply the layout to the visible tabs and hide the others."""
        self._pending = False
        visible = self.visible_tabs()
        current = self._current
        fullscreen = (current is not None and current in visible and
                      _is_fullscreen(current))
        if fullscreen:
            visible = [current]
        if self.params.layout == 'monocle' and current in visible:
            # Only the focused client is shown; the others would just be
            # painted over, and hidden webviews are cheaper.
            visible = [current]
        rects = arrange(self.rect(), len(visible), self.params)
        # dwm drops the border on a fullscreen client; every other tile keeps
        # it so that moving focus only recolors frames, never moves content.
        border = 0 if fullscreen else self.params.border
        shown = set()
        frames = []
        for tab, rect in zip(visible, rects):
            shown.add(tab)
            client = inset(rect, border)
            frames.append((rect, client, tab is current))
            if tab.geometry() != client:
                tab.setGeometry(client)
            if not tab.isVisible():
                tab.show()
        for tab in self._tabs:
            if tab not in shown and tab.isVisible():
                tab.hide()
        if current in shown:
            current.raise_()
        if frames != self._frames:
            self._frames = frames
            self.update()
        log.webview.vdebug(  # type: ignore[attr-defined]
            f"tiled {len(shown)}/{len(self._tabs)} tabs, "
            f"seltags={self.seltags:#b}, layout={self.params.layout}")
        self.arranged.emit()


    def paintEvent(self, e: QPaintEvent) -> None:  # noqa: N802
        """Paint the tile borders; the tabs themselves cover the client rects.

        In monocle the frames of the hidden tiles coincide with the shown
        one, so the focused frame is painted last and wins.
        """
        if not self._frames:
            return
        focused_color = config.val.colors.tabs.tiling.border.focused
        unfocused_color = config.val.colors.tabs.tiling.border.unfocused
        painter = QPainter(self)
        for focused in (False, True):
            color = focused_color if focused else unfocused_color
            for outer, inner, is_focused in self._frames:
                if is_focused != focused:
                    continue
                for strip in frame_rects(outer, inner):
                    painter.fillRect(strip, color)
        painter.end()


class _StackWatcher(QObject):

    """Keep a TileArea the same size as the QStackedWidget it covers."""

    def __init__(self, stack: QWidget, tiles: TileArea) -> None:
        super().__init__(stack)
        self._stack = stack
        self._tiles = tiles
        stack.installEventFilter(self)
        tiles.setGeometry(stack.rect())

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self._stack and event.type() in (
                QEvent.Type.Resize, QEvent.Type.Show):
            self._tiles.setGeometry(self._stack.rect())
            self._tiles.raise_()
        return False


def cover(stack: QWidget, tiles: TileArea) -> _StackWatcher:
    """Make ``tiles`` track ``stack`` and keep it on top."""
    return _StackWatcher(stack, tiles)
