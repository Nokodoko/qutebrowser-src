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

from qutebrowser.qt.core import (QEvent, QObject, QRect, QUrl, Qt, QTimer,
                                 pyqtSignal, pyqtSlot)
from qutebrowser.qt.gui import QPainter, QPaintEvent
from qutebrowser.qt.widgets import QWidget

from qutebrowser.config import config
from qutebrowser.completion import tabpreview
from qutebrowser.utils import log

#: Number of tags; dwm uses nine as well.
NUM_TAGS = 9
#: Mask with every tag set, i.e. the "view all" tagset.
TAGMASK = (1 << NUM_TAGS) - 1

LAYOUTS = ('tile', 'monocle')


def default_name(tag: int) -> str:
    """The name zellij gives an unnamed tab."""
    return f"Tab #{tag}"


def lowest_tag(mask: int) -> int:
    """The lowest 1-based tag number set in a mask (0 for an empty mask)."""
    mask &= TAGMASK
    if not mask:
        return 0
    return (mask & -mask).bit_length()


def count_on(tag: int, all_tags: Sequence[int]) -> int:
    """How many of the given tag masks include ``tag``."""
    bit = tag_mask(tag)
    return sum(1 for t in all_tags if t & bit)


def tags_over_limit(new: int, old: int, all_tags: Sequence[int],
                    limit: int) -> list[int]:
    """Tags a tab would join that already hold ``limit`` tabs.

    Only tags the tab is not already on count; ``limit <= 0`` disables the
    cap. ``all_tags`` must not include the tab being moved.
    """
    if limit <= 0:
        return []
    joining = (new & ~old) & TAGMASK
    return [n for n in tag_numbers(joining)
            if count_on(n, all_tags) >= limit]


def next_tag_with_room(start: int, all_tags: Sequence[int],
                       limit: int) -> int:
    """The first tag from ``start`` upwards (wrapping) below the cap.

    Returns ``start`` itself when the cap is off or every tag is full.
    """
    if limit <= 0:
        return start
    for i in range(NUM_TAGS):
        tag = (start - 1 + i) % NUM_TAGS + 1
        if count_on(tag, all_tags) < limit:
            return tag
    return start


def stack_order(tabs: Sequence[tuple[object, int]]) -> list[tuple[object, int]]:
    """Flatten every tab into one stack ordered by tag, then bar order.

    This is the zellij view of the window: each tag is a zellij tab and the
    browser tabs on it are its panes, so walking the stack past the last pane
    of one tag lands on the first pane of the next. A tab on several tags
    appears once per tag.
    """
    out: list[tuple[object, int]] = []
    for tag in range(1, NUM_TAGS + 1):
        bit = 1 << (tag - 1)
        out.extend((tab, tag) for tab, mask in tabs if mask & bit)
    return out


def step_focus(tabs: Sequence[tuple[object, int]], current: object,
               seltags: int, direction: int) -> tuple[object, int] | None:
    """The (tab, tag) one step from ``current`` in the flat stack.

    ``current`` is located at its lowest viewed tag so stepping stays inside
    the viewed tag until its last pane, then wraps into the next tag. Returns
    None when there is nothing to step to.
    """
    stack = stack_order(tabs)
    if not stack:
        return None
    cur_mask = next((m for t, m in tabs if t is current), 0)
    viewed = cur_mask & seltags
    cur_tag = lowest_tag(viewed) if viewed else lowest_tag(cur_mask)
    try:
        idx = stack.index((current, cur_tag))
    except ValueError:
        idx = -1 if direction > 0 else 0
    return stack[(idx + direction) % len(stack)]


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


def is_rule_url(url: QUrl) -> bool:
    """Whether a URL is a real page a tag rule may be evaluated for.

    Only http, https and file URLs count; internal pages (qute://), empty
    pages and invalid URLs do not.
    """
    if not url.isValid():
        return False
    return url.scheme() in ('http', 'https', 'file')


def rule_tag(url: QUrl) -> int:
    """The dwm-style tag rule matching ``url``, 0 when there is none.

    Looks up the pattern-scoped ``tabs.tiling.rule_tag`` setting for the
    URL; anything that is not a real page (see ``is_rule_url``) never
    matches.
    """
    if not is_rule_url(url):
        return 0
    return config.instance.get('tabs.tiling.rule_tag', url=url)


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
    #: A tag was renamed (tag number).
    renamed = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.seltags = 1
        self._prev_seltags = 1
        #: pertag: one LayoutParams per tag, slot 0 for multi-tag views.
        self._pertag: dict[int, LayoutParams] = {}
        #: zellij tab names, by tag number; unnamed tags use default_name().
        self.names: dict[int, str] = {}
        self._tabs: list[QWidget] = []
        self._current: QWidget | None = None
        self._frames: list[tuple[QRect, QRect, bool]] = []
        self._pending = False
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        config.instance.changed.connect(self._on_config_changed)

    # ---- pertag: the layout state belongs to the viewed tag -------------

    def curtag(self) -> int:
        """dwm pertag's curtag: the viewed tag, 0 when several are viewed."""
        viewed = tag_numbers(self.seltags)
        return viewed[0] if len(viewed) == 1 else 0

    @property
    def params(self) -> LayoutParams:
        slot = self.curtag()
        if slot not in self._pertag:
            self._pertag[slot] = self._params_from_config()
        return self._pertag[slot]

    @params.setter
    def params(self, value: LayoutParams) -> None:
        self._pertag[self.curtag()] = value

    def name_of(self, tag: int) -> str:
        """The zellij tab name of a tag."""
        return self.names.get(tag) or default_name(tag)

    def rename(self, tag: int, name: str) -> None:
        """Name a tag; an empty name goes back to the default."""
        name = name.strip()
        if name:
            self.names[tag] = name
        else:
            self.names.pop(tag, None)
        self.renamed.emit(tag)

    def limit(self) -> int:
        """Max browser tabs per tag (0 = unlimited)."""
        return config.val.tabs.tiling.max_per_tag

    def tag_list(self, exclude: QWidget | None = None) -> list[int]:
        """All tag masks in bar order, the input to the pure helpers.

        ``exclude`` leaves one tab out, for the helpers that ask about the
        tabs a given tab would join (they must not count the tab itself).
        """
        return [tags_of(t) for t in self._tabs if t is not exclude]

    def count_on(self, tag: int) -> int:
        return count_on(tag, self.tag_list())

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
        if option in ('tabs.tiling.layout', 'tabs.tiling.mfact',
                      'tabs.tiling.nmaster', 'tabs.tiling.gap',
                      'tabs.tiling.border'):
            # Config is the default for every tag; runtime changes made
            # through the commands are per tag and kept.
            self._pertag.clear()
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
        # dwm drops the border on a fullscreen client. A lone tile (monocle,
        # or a single tab on the viewed tags) has nothing to be told apart
        # from, so it gets none either. Every other tile keeps it, so moving
        # focus only recolors frames and never moves content.
        border = 0 if fullscreen or len(visible) < 2 else self.params.border
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
                # Last chance to picture it: a hidden webview cannot paint.
                tabpreview.capture(tab)
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

        The focused frame is painted last so it wins where frames touch.
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
