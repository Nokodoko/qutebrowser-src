# SPDX-FileCopyrightText: Daniel Schadt
# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the custom TabWidget/TabBar."""

import functools

import pytest
from unittest.mock import Mock

from qutebrowser.qt.core import QUrl
from qutebrowser.qt.gui import QIcon, QPixmap
from qutebrowser.mainwindow import tabwidget, tiling
from qutebrowser.utils import urlmatch, usertypes


class TestTabWidget:

    """Tests for TabWidget."""

    @pytest.fixture
    def widget(self, qtbot, monkeypatch, config_stub):
        w = tabwidget.TabWidget(0)
        qtbot.add_widget(w)
        monkeypatch.setattr(tabwidget.objects, 'backend',
                            usertypes.Backend.QtWebKit)
        w.show()
        return w

    def test_small_icon_doesnt_crash(self, widget, qtbot, fake_web_tab):
        """Test that setting a small icon doesn't produce a crash.

        Regression test for #1015.
        """
        # Size taken from issue report
        pixmap = QPixmap(72, 1)
        icon = QIcon(pixmap)
        tab = fake_web_tab()
        widget.addTab(tab, icon, 'foobar')

        with qtbot.wait_exposed(widget):
            widget.show()

    # Sizing tests

    def test_tab_size_same(self, widget, fake_web_tab):
        """Ensure by default, all tab sizes are the same."""
        num_tabs = 10
        for i in range(num_tabs):
            widget.addTab(fake_web_tab(), 'foobar' + str(i))

        first_size = widget.tabBar().tabSizeHint(0)
        first_size_min = widget.tabBar().minimumTabSizeHint(0)

        for i in range(num_tabs):
            assert first_size == widget.tabBar().tabSizeHint(i)
            assert first_size_min == widget.tabBar().minimumTabSizeHint(i)

    @pytest.mark.parametrize('powerline', [True, False])
    def test_powerline_chevron(self, widget, fake_web_tab, config_stub, qtbot,
                               powerline):
        """With tabs.powerline, a tab ends in a chevron over the next tab."""
        config_stub.val.tabs.powerline = powerline
        config_stub.val.tabs.indicator.width = 0
        widget.addTab(fake_web_tab(), 'one')
        widget.addTab(fake_web_tab(), 'two')
        bar = widget.tabBar()
        with qtbot.wait_exposed(widget):
            widget.show()

        this_bg = bar._tab_bg(0, bar.currentIndex())
        next_bg = bar._tab_bg(1, bar.currentIndex())
        assert this_bg != next_bg

        image = bar.grab().toImage()
        rect = bar.tabRect(0)
        chevron = tabwidget.TabBarStyle.chevron_width(rect.height())
        corner = image.pixelColor(rect.right() - 1, rect.top() + 1)
        tip = image.pixelColor(rect.right() - chevron // 2,
                               rect.top() + rect.height() // 2)
        assert tip == this_bg
        assert corner == (next_bg if powerline else this_bg)

    @pytest.fixture
    def paint_spy(self, monkeypatch):
        spy = Mock()
        monkeypatch.setattr(tabwidget, "QStylePainter", spy)
        return spy

    def test_tab_text_edlided_for_narrow_tabs(self, paint_spy, widget, fake_web_tab):
        """Make sure text gets elided for narrow tabs."""
        widget.setMaximumWidth(100)
        widget.addTab(fake_web_tab(), "one two three four")

        fake_paint_event = Mock()
        fake_paint_event.region.return_value.intersects.return_value = True
        widget.tabBar().paintEvent(fake_paint_event)

        style_opt = paint_spy.return_value.drawControl.call_args_list[0][0][1]
        assert len(style_opt.text) < len(widget.tabBar().tabText(0))
        assert style_opt.text.endswith("…")
        assert len(style_opt.text) > len("…")

    def test_tab_text_not_edlided_for_wide_tabs(self, paint_spy, widget, fake_web_tab):
        """Make sure text doesn't get elided for wide tabs."""
        widget.setMaximumWidth(200)
        widget.addTab(fake_web_tab(), "one two three four")

        fake_paint_event = Mock()
        fake_paint_event.region.return_value.intersects.return_value = True
        widget.tabBar().paintEvent(fake_paint_event)

        style_opt = paint_spy.return_value.drawControl.call_args_list[0][0][1]
        assert style_opt.text.endswith(widget.tabBar().tabText(0))

    @pytest.mark.parametrize("shrink_pinned", [True, False])
    @pytest.mark.parametrize("vertical", [True, False])
    def test_pinned_size(self, widget, fake_web_tab, config_stub,
                         shrink_pinned, vertical):
        """Ensure by default, pinned min sizes are forced to title.

        If pinned.shrink is not true, then all tabs should be the same

        If tabs are vertical, all tabs should be the same"""
        num_tabs = 10
        for i in range(num_tabs):
            widget.addTab(fake_web_tab(), 'foobar' + str(i))

        # Set pinned title format longer than unpinned
        config_stub.val.tabs.title.format_pinned = "_" * 10
        config_stub.val.tabs.title.format = "_" * 2
        config_stub.val.tabs.pinned.shrink = shrink_pinned
        if vertical:
            # Use pixel width so we don't need to mock main-window
            config_stub.val.tabs.width = 50
            config_stub.val.tabs.position = "left"

        pinned_num = [1, num_tabs - 1]
        for num in pinned_num:
            tab = widget.widget(num)
            tab.set_pinned(True)

        first_size = widget.tabBar().tabSizeHint(0)
        first_size_min = widget.tabBar().minimumTabSizeHint(0)

        for i in range(num_tabs):
            if i in pinned_num and shrink_pinned and not vertical:
                assert (first_size.width() >
                        widget.tabBar().tabSizeHint(i).width())
                assert (first_size_min.width() <
                        widget.tabBar().minimumTabSizeHint(i).width())
            else:
                assert first_size == widget.tabBar().tabSizeHint(i)
                assert first_size_min == widget.tabBar().minimumTabSizeHint(i)

    @pytest.mark.parametrize("num_tabs", [4, 10, 50, 100])
    def test_update_tab_titles_benchmark(self, benchmark, widget,
                                         qtbot, fake_web_tab, num_tabs):
        """Benchmark for update_tab_titles."""
        for i in range(num_tabs):
            widget.addTab(fake_web_tab(), 'foobar' + str(i))

        with qtbot.wait_exposed(widget):
            widget.show()

        benchmark(widget.update_tab_titles)

    def test_tab_min_width(self, widget, fake_web_tab, config_stub, qtbot):
        widget.addTab(fake_web_tab(), 'foobar')
        widget.addTab(fake_web_tab(), 'foobar1')
        min_size = widget.tabBar().tabRect(0).width() + 10
        config_stub.val.tabs.min_width = min_size
        assert widget.tabBar().tabRect(0).width() == min_size

    def test_tab_max_width(self, widget, fake_web_tab, config_stub, qtbot):
        widget.addTab(fake_web_tab(), 'foobar')
        max_size = widget.tabBar().tabRect(0).width() - 10
        config_stub.val.tabs.max_width = max_size
        assert widget.tabBar().tabRect(0).width() == max_size

    def test_tab_stays_hidden(self, widget, fake_web_tab, config_stub):
        assert widget.tabBar().isVisible()
        config_stub.val.tabs.show = "never"
        assert not widget.tabBar().isVisible()
        for i in range(12):
            widget.addTab(fake_web_tab(), 'foobar' + str(i))
        assert not widget.tabBar().isVisible()

    @pytest.mark.parametrize("num_tabs", [4, 70])
    @pytest.mark.parametrize("rev", [True, False])
    def test_add_remove_tab_benchmark(self, benchmark, widget,
                                      qtbot, fake_web_tab, num_tabs, rev):
        """Benchmark for addTab and removeTab."""
        def _run_bench():
            with qtbot.wait_exposed(widget):
                widget.show()
            for i in range(num_tabs):
                idx = i if rev else 0
                widget.insertTab(idx, fake_web_tab(), 'foobar' + str(i))

            to_del = range(num_tabs)
            if rev:
                to_del = reversed(to_del)
            for i in to_del:
                widget.removeTab(i)

        benchmark(_run_bench)

    def test_tab_pinned_benchmark(self, benchmark, widget, fake_web_tab):
        """Benchmark for _tab_pinned."""
        widget.addTab(fake_web_tab(), 'foobar')
        tab_bar = widget.tabBar()
        benchmark(functools.partial(tab_bar._tab_pinned, 0))


class TestTagRules:

    """place_new_tab/apply_tag_rule: dwm-style URL-to-tag rules."""

    @pytest.fixture
    def widget(self, qtbot, config_stub):
        """A tab widget with a sane tiling setup for the rule tests."""
        config_stub.val.tabs.tiling.max_per_tag = 0
        w = tabwidget.TabWidget(0)
        qtbot.add_widget(w)
        w.show()
        return w

    def test_rule_places_tab_on_rule_tag(self, widget, fake_web_tab,
                                         config_stub):
        config_stub.set_obj('tabs.tiling.rule_tag', 4,
                            pattern=urlmatch.UrlPattern('*.youtube.com'))
        tab = fake_web_tab()
        widget.insertTab(0, tab, '')
        widget.place_new_tab(tab, url=QUrl('https://www.youtube.com/watch'))
        assert tab.data.tags == tiling.tag_mask(4)
        # follow defaults to True: the view goes with the tab.
        assert widget.tiles.seltags == tiling.tag_mask(4)
        assert tab.data.tag_rule_done

    def test_rule_not_followed_keeps_view(self, widget, fake_web_tab,
                                          config_stub):
        config_stub.set_obj('tabs.tiling.rule_tag', 4,
                            pattern=urlmatch.UrlPattern('*.youtube.com'))
        tab = fake_web_tab()
        widget.insertTab(0, tab, '')
        widget.place_new_tab(tab, url=QUrl('https://www.youtube.com/watch'),
                             follow=False)
        assert tab.data.tags == tiling.tag_mask(4)
        assert widget.tiles.seltags == 0b1

    def test_no_rule_keeps_viewed_tag(self, widget, fake_web_tab):
        tab = fake_web_tab()
        widget.insertTab(0, tab, '')
        widget.place_new_tab(tab, url=QUrl('https://example.com/'))
        assert tab.data.tags == 0b1
        assert tab.data.tag_rule_done

    def test_full_rule_tag_spills(self, widget, fake_web_tab, config_stub):
        config_stub.val.tabs.tiling.max_per_tag = 1
        config_stub.set_obj('tabs.tiling.rule_tag', 4,
                            pattern=urlmatch.UrlPattern('*.youtube.com'))
        first = fake_web_tab()
        widget.insertTab(0, first, '')
        first.data.tags = tiling.tag_mask(4)
        widget.tiles.set_tabs([first])

        tab = fake_web_tab()
        widget.insertTab(1, tab, '')
        widget.place_new_tab(tab, url=QUrl('https://www.youtube.com/watch'))
        assert tab.data.tags == tiling.tag_mask(5)

    def test_apply_tag_rule_moves_tab(self, widget, fake_web_tab,
                                      config_stub):
        config_stub.set_obj('tabs.tiling.rule_tag', 7,
                            pattern=urlmatch.UrlPattern('*.youtube.com'))
        tab = fake_web_tab()
        widget.insertTab(0, tab, '')
        widget.tiles.set_tabs([tab])
        widget.setCurrentWidget(tab)
        assert tab.data.tags == 0b1

        widget.apply_tag_rule(tab, QUrl('https://www.youtube.com/watch'),
                              follow=False)
        assert tab.data.tags == tiling.tag_mask(7)
        assert tab.data.tag_rule_done
        # follow=False: the tab goes, the view stays.
        assert widget.tiles.seltags == 0b1

    def test_apply_tag_rule_fires_once(self, widget, fake_web_tab,
                                       config_stub):
        config_stub.set_obj('tabs.tiling.rule_tag', 7,
                            pattern=urlmatch.UrlPattern('*.youtube.com'))
        tab = fake_web_tab()
        widget.insertTab(0, tab, '')
        widget.tiles.set_tabs([tab])

        widget.apply_tag_rule(tab, QUrl('https://www.youtube.com/watch'),
                              follow=False)
        assert tab.data.tags == tiling.tag_mask(7)
        # A later navigation never moves the tab again.
        tab.data.tags = 0b1
        widget.apply_tag_rule(tab, QUrl('https://www.youtube.com/other'),
                              follow=False)
        assert tab.data.tags == 0b1

    def test_apply_tag_rule_ignores_internal_urls(self, widget, fake_web_tab,
                                                  config_stub):
        config_stub.set_obj('tabs.tiling.rule_tag', 6)
        tab = fake_web_tab()
        widget.insertTab(0, tab, '')
        widget.tiles.set_tabs([tab])

        widget.apply_tag_rule(tab, QUrl('qute://settings/'), follow=False)
        assert tab.data.tags == 0b1
        assert not tab.data.tag_rule_done
