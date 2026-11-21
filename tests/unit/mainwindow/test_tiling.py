# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for dwm-style tab tiling."""

import pytest

from qutebrowser.qt.core import QRect, QUrl
from qutebrowser.qt.widgets import QWidget
from qutebrowser.utils import urlmatch
from qutebrowser.mainwindow import tiling


class TestTags:

    @pytest.mark.parametrize('tag, mask', [
        (1, 0b1), (2, 0b10), (9, 1 << 8), (0, tiling.TAGMASK),
    ])
    def test_tag_mask(self, tag, mask):
        assert tiling.tag_mask(tag) == mask

    @pytest.mark.parametrize('tag', [-1, 10])
    def test_tag_mask_invalid(self, tag):
        with pytest.raises(ValueError):
            tiling.tag_mask(tag)

    def test_tag_numbers(self):
        assert tiling.tag_numbers(0b101000001) == [1, 7, 9]

    @pytest.mark.parametrize('tags, seltags, visible', [
        (0b1, 0b1, True),
        (0b10, 0b1, False),
        (0b110, 0b100, True),
        (0, 0b111, False),
    ])
    def test_is_visible(self, tags, seltags, visible):
        assert tiling.is_visible(tags, seltags) is visible


def _covers(rects, area):
    """Rects must stay inside area and not overlap each other."""
    for i, r in enumerate(rects):
        assert area.contains(r), (r, area)
        for other in rects[i + 1:]:
            assert not r.intersects(other), (r, other)


class TestArrange:

    area = QRect(0, 0, 1000, 600)

    def test_empty(self):
        assert tiling.arrange(self.area, 0, tiling.LayoutParams()) == []

    def test_monocle(self):
        params = tiling.LayoutParams(layout='monocle')
        rects = tiling.arrange(self.area, 3, params)
        assert rects == [self.area] * 3

    def test_tile_single(self):
        params = tiling.LayoutParams(layout='tile')
        assert tiling.arrange(self.area, 1, params) == [self.area]

    def test_tile_master_and_stack(self):
        params = tiling.LayoutParams(layout='tile', mfact=0.5, nmaster=1)
        master, s1, s2 = tiling.arrange(self.area, 3, params)
        assert master == QRect(0, 0, 500, 600)
        assert s1 == QRect(500, 0, 500, 300)
        assert s2 == QRect(500, 300, 500, 300)
        _covers([master, s1, s2], self.area)

    def test_tile_two_masters(self):
        params = tiling.LayoutParams(layout='tile', mfact=0.6, nmaster=2)
        rects = tiling.arrange(self.area, 3, params)
        assert [r.x() for r in rects] == [0, 0, 600]
        assert rects[0].height() + rects[1].height() == 600
        _covers(rects, self.area)

    def test_tile_no_master(self):
        params = tiling.LayoutParams(layout='tile', nmaster=0)
        rects = tiling.arrange(self.area, 2, params)
        assert all(r.x() == 0 and r.width() == 1000 for r in rects)
        _covers(rects, self.area)

    def test_tile_gap(self):
        params = tiling.LayoutParams(layout='tile', mfact=0.5, nmaster=1, gap=10)
        master, s1, s2 = tiling.arrange(self.area, 3, params)
        assert master.topLeft() == QRect(10, 10, 1, 1).topLeft()
        assert s1.x() == master.right() + 1 + 10
        assert s2.y() == s1.bottom() + 1 + 10
        assert s2.bottom() == 600 - 10 - 1
        _covers([master, s1, s2], self.area)

    def test_rounding_fills_column(self):
        params = tiling.LayoutParams(layout='tile', nmaster=0)
        rects = tiling.arrange(QRect(0, 0, 100, 601), 7, params)
        assert sum(r.height() for r in rects) == 601
        _covers(rects, QRect(0, 0, 100, 601))

    def test_params_clamped(self):
        params = tiling.LayoutParams(mfact=2.0, nmaster=-3, gap=-1, border=-2)
        assert (params.mfact, params.nmaster, params.gap, params.border) == (
            0.95, 0, 0, 0)

    def test_inset_and_frame(self):
        outer = QRect(10, 20, 100, 50)
        inner = tiling.inset(outer, 2)
        assert inner == QRect(12, 22, 96, 46)
        strips = tiling.frame_rects(outer, inner)
        assert len(strips) == 4
        assert sum(r.width() * r.height() for r in strips) == (
            outer.width() * outer.height() - inner.width() * inner.height())
        for strip in strips:
            assert outer.contains(strip) and not strip.intersects(inner)

    def test_inset_zero_is_identity(self):
        outer = QRect(0, 0, 10, 10)
        assert tiling.inset(outer, 0) == outer
        assert tiling.frame_rects(outer, outer) == []

    def test_inset_never_collapses(self):
        tiny = QRect(0, 0, 3, 3)
        assert tiling.inset(tiny, 50).isValid()

    def test_unknown_layout(self):
        with pytest.raises(ValueError):
            tiling.LayoutParams(layout='spiral')


class TestCycle:

    """cycle(): one step through a sequence, wrapping at both ends."""

    seq = ['a', 'b', 'c']

    def test_step_forward(self):
        assert tiling.cycle(self.seq, 'a', +1) == 'b'
        assert tiling.cycle(self.seq, 'b', +1) == 'c'

    def test_step_forward_wraps(self):
        assert tiling.cycle(self.seq, 'c', +1) == 'a'

    def test_step_back_wraps(self):
        assert tiling.cycle(self.seq, 'a', -1) == 'c'
        assert tiling.cycle(self.seq, 'c', -1) == 'b'

    def test_current_missing(self):
        assert tiling.cycle(self.seq, 'z', +1) == 'a'
        assert tiling.cycle(self.seq, 'z', -1) == 'c'

    def test_single_element(self):
        assert tiling.cycle(['a'], 'a', +1) == 'a'
        assert tiling.cycle(['a'], 'a', -1) == 'a'

    def test_empty(self):
        assert tiling.cycle([], None, +1) is None
        assert tiling.cycle([], None, -1) is None


class _Tab(QWidget):

    """A widget with the .data.tags attribute the tile area reads."""

    class _Data:
        def __init__(self, tags):
            self.tags = tags
            self.fullscreen = False

    def __init__(self, tags=1, parent=None):
        super().__init__(parent)
        self.data = self._Data(tags)


class TestRules:

    """is_rule_url / rule_tag: the dwm-style URL-to-tag rule lookup."""

    @pytest.mark.parametrize('url, expected', [
        ('http://example.com/', True),
        ('https://example.com/', True),
        ('file:///home/user/index.html', True),
        ('qute://settings/', False),
        ('about:blank', False),
    ])
    def test_is_rule_url(self, url, expected):
        assert tiling.is_rule_url(QUrl(url)) is expected

    def test_is_rule_url_invalid(self):
        assert tiling.is_rule_url(QUrl()) is False

    def test_rule_tag_no_rule(self, config_stub):
        assert tiling.rule_tag(QUrl('https://example.com/')) == 0

    def test_rule_tag_matches_pattern(self, config_stub):
        pattern = urlmatch.UrlPattern('*.youtube.com')
        config_stub.set_obj('tabs.tiling.rule_tag', 3, pattern=pattern)
        assert tiling.rule_tag(QUrl('https://www.youtube.com/watch')) == 3
        assert tiling.rule_tag(QUrl('https://example.com/')) == 0

    def test_rule_tag_ignores_internal_urls(self, config_stub):
        config_stub.set_obj('tabs.tiling.rule_tag', 5)
        assert tiling.rule_tag(QUrl('qute://settings/')) == 0
        assert tiling.rule_tag(QUrl()) == 0


class TestTileArea:

    @pytest.fixture
    def area(self, qtbot, config_stub):
        config_stub.val.tabs.tiling.layout = 'tile'
        # Geometry expectations below are for bare tiles; the border tests
        # opt in to a border explicitly.
        config_stub.val.tabs.tiling.border = 0
        area = tiling.TileArea()
        qtbot.add_widget(area)
        area.resize(1000, 600)
        area.show()
        return area

    @pytest.fixture
    def tabs(self, area):
        tabs = [_Tab(0b1), _Tab(0b10), _Tab(0b11)]
        for tab in tabs:
            area.attach(tab)
        area.set_tabs(tabs)
        area.set_current(tabs[0])
        area.relayout()
        return tabs

    def test_initial_view(self, area, tabs):
        assert area.visible_tabs() == [tabs[0], tabs[2]]
        assert tabs[0].isVisible() and tabs[2].isVisible()
        assert not tabs[1].isVisible()
        assert tabs[0].geometry().width() == 550

    def test_view_and_back(self, area, tabs):
        area.view(tiling.tag_mask(2))
        area.relayout()
        assert area.visible_tabs() == [tabs[1], tabs[2]]
        area.view(0)
        assert area.seltags == 0b1

    def test_toggleview_never_empty(self, area, tabs):
        area.toggleview(0b1)
        assert area.seltags == 0b1
        area.toggleview(0b10)
        assert area.seltags == 0b11
        assert area.visible_tabs() == tabs

    def test_occupied(self, area, tabs):
        assert area.occupied() == 0b11

    def test_monocle_shows_only_current(self, area, tabs, config_stub):
        config_stub.val.tabs.tiling.layout = 'monocle'
        area.relayout()
        assert tabs[0].isVisible()
        assert not tabs[2].isVisible()
        assert tabs[0].geometry() == area.rect()

    def test_fullscreen_takes_all(self, area, tabs):
        tabs[2].data.fullscreen = True
        area.set_current(tabs[2])
        area.relayout()
        assert tabs[2].geometry() == area.rect()
        assert not tabs[0].isVisible()

    def test_border_insets_every_tile_and_marks_focus(self, area, tabs,
                                                       config_stub):
        config_stub.val.tabs.tiling.border = 3
        area.params = area._params_from_config()
        area.relayout()
        frames = area.frames()
        assert [focused for _, _, focused in frames] == [True, False]
        for outer, inner, _ in frames:
            assert inner == outer.adjusted(3, 3, -3, -3)
        assert tabs[0].geometry() == frames[0][1]
        assert tabs[2].geometry() == frames[1][1]
        # Moving focus recolors the frames but moves nothing.
        before = [tab.geometry() for tab in tabs]
        area.set_current(tabs[2])
        area.relayout()
        assert [focused for _, _, focused in area.frames()] == [False, True]
        assert [tab.geometry() for tab in tabs] == before

    def test_monocle_drops_border(self, area, tabs, config_stub):
        config_stub.val.tabs.tiling.border = 3
        config_stub.val.tabs.tiling.layout = 'monocle'
        area.params = area._params_from_config()
        area.relayout()
        assert tabs[0].geometry() == area.rect()
        assert area.frames() == [(area.rect(), area.rect(), True)]

    def test_single_tile_drops_border(self, area, tabs, config_stub):
        config_stub.val.tabs.tiling.border = 3
        area.params = area._params_from_config()
        area.view(tiling.tag_mask(2))
        area.set_tabs([tabs[1]])
        area.set_current(tabs[1])
        area.relayout()
        assert tabs[1].geometry() == area.rect()
        assert area.frames() == [(area.rect(), area.rect(), True)]

    def test_fullscreen_drops_border(self, area, tabs, config_stub):
        config_stub.val.tabs.tiling.border = 3
        area.params = area._params_from_config()
        tabs[2].data.fullscreen = True
        area.set_current(tabs[2])
        area.relayout()
        assert tabs[2].geometry() == area.rect()
        assert area.frames() == [(area.rect(), area.rect(), True)]


class TestPick:

    """The transient pick view: an arbitrary set of tabs, no tag changes."""

    @pytest.fixture
    def area(self, qtbot, config_stub):
        config_stub.val.tabs.tiling.layout = 'tile'
        config_stub.val.tabs.tiling.border = 0
        area = tiling.TileArea()
        qtbot.add_widget(area)
        area.resize(1000, 600)
        area.show()
        return area

    @pytest.fixture
    def tabs(self, area):
        tabs = [_Tab(0b1), _Tab(0b10), _Tab(0b11)]
        for tab in tabs:
            area.attach(tab)
        area.set_tabs(tabs)
        area.set_current(tabs[0])
        area.relayout()
        return tabs

    def test_pick_shows_picked_tabs_only(self, area, tabs):
        area.set_pick([tabs[1]])
        area.relayout()
        assert area.visible_tabs() == [tabs[1]]
        assert tabs[1].isVisible()
        assert not tabs[0].isVisible()
        # No tab's tags were touched.
        assert [tiling.tags_of(t) for t in tabs] == [0b1, 0b10, 0b11]

    def test_pick_keeps_stack_order(self, area, tabs):
        area.set_pick([tabs[2], tabs[0]])
        assert area.visible_tabs() == [tabs[0], tabs[2]]

    def test_pick_emits_pick_changed(self, area, tabs):
        seen = []
        area.pick_changed.connect(seen.append)
        area.set_pick([tabs[0], tabs[1]])
        area.clear_pick()
        assert seen == [2, 0]

    def test_pick_pertag_slot_is_isolated(self, area, tabs, config_stub):
        """Layout changes inside a pick do not leak into a tag slot."""
        assert area.curtag() == 1
        area.set_mfact(0.3)
        area.set_layout('monocle')
        tag_params = area.params

        area.set_pick([tabs[0], tabs[1]])
        assert area.curtag() == tiling.PICK_SLOT
        # The pick starts out tiled whatever the tag slot holds.
        assert area.params.layout == 'tile'
        assert area.params.mfact != 0.3
        area.set_layout('tile')
        area.set_mfact(0.7)
        area.set_nmaster(2)

        area.clear_pick()
        assert area.params is tag_params
        assert area.params.layout == 'monocle'
        assert area.params.mfact == 0.3
        assert area.params.nmaster == tag_params.nmaster

    def test_view_zero_restores_pre_pick_tagset(self, area, tabs):
        area.view(0b11)
        area.set_pick([tabs[0]])
        assert area.pick == [tabs[0]]
        area.view(0)
        assert area.pick is None
        assert area.seltags == 0b11

    def test_clear_pick_restores_pre_pick_tagset(self, area, tabs):
        area.view(0b10)
        area.set_pick([tabs[2]])
        area.clear_pick()
        assert area.pick is None
        assert area.seltags == 0b10

    def test_view_mask_clears_pick(self, area, tabs):
        area.set_pick([tabs[0]])
        area.view(tiling.tag_mask(2))
        assert area.pick is None
        assert area.seltags == 0b10
        assert area.visible_tabs() == [tabs[1], tabs[2]]

    def test_toggleview_clears_pick(self, area, tabs):
        area.set_pick([tabs[0]])
        area.toggleview(tiling.tag_mask(2))
        assert area.pick is None
        assert area.seltags == 0b11

    def test_detach_shrinks_pick(self, area, tabs):
        area.set_pick([tabs[0], tabs[1]])
        area.detach(tabs[1])
        assert area.pick == [tabs[0]]
        assert area.visible_tabs() == [tabs[0]]

    def test_detach_last_clears_pick(self, area, tabs):
        area.view(0b10)
        area.set_pick([tabs[1]])
        area.detach(tabs[1])
        assert area.pick is None
        assert area.seltags == 0b10

    def test_empty_set_pick_clears(self, area, tabs):
        area.view(0b10)
        area.set_pick([tabs[1]])
        area.set_pick([])
        assert area.pick is None
        assert area.seltags == 0b10

    def test_clear_pick_without_pick(self, area, tabs):
        area.clear_pick()
        assert area.pick is None
        assert area.seltags == 0b1

    def test_view_zero_without_pick_is_dwm_prev(self, area, tabs):
        """With no pick, view(0) still swaps to the previous tagset."""
        area.view(0b10)
        area.view(0b100)
        area.view(0)
        assert area.pick is None
        assert area.seltags == 0b10
