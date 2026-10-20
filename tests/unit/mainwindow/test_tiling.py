# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for dwm-style tab tiling."""

import pytest

from qutebrowser.qt.core import QRect
from qutebrowser.qt.widgets import QWidget
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
        params = tiling.LayoutParams(mfact=2.0, nmaster=-3, gap=-1)
        assert (params.mfact, params.nmaster, params.gap) == (0.95, 0, 0)

    def test_unknown_layout(self):
        with pytest.raises(ValueError):
            tiling.LayoutParams(layout='spiral')


class _Tab(QWidget):

    """A widget with the .data.tags attribute the tile area reads."""

    class _Data:
        def __init__(self, tags):
            self.tags = tags
            self.fullscreen = False

    def __init__(self, tags=1, parent=None):
        super().__init__(parent)
        self.data = self._Data(tags)


class TestTileArea:

    @pytest.fixture
    def area(self, qtbot, config_stub):
        config_stub.val.tabs.tiling.layout = 'tile'
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
