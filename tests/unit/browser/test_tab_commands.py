# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Command-level tests for :tab-pick and :tab-select --monocle.

The tabbed browsers are stubs whose widget is a real TabWidget, so the pick
lands in a real TileArea; `N` indices resolve against a fake active window.
"""

import pytest

from qutebrowser.browser import commands
from qutebrowser.commands import runners
from qutebrowser.mainwindow import mainwindow, tabwidget, tiling
from qutebrowser.misc import objects
from qutebrowser.utils import objreg, usertypes


@pytest.fixture
def dispatchers(mocker, qtbot, config_stub, fake_web_tab, win_registry,
                tab_registry):
    """Two stub tabbed browsers (win 0/1) with real tab widgets."""
    config_stub.val.tabs.tiling.layout = 'tile'
    mocker.patch.object(usertypes, 'Backend', usertypes.Backend)
    mocker.patch.object(commands.message, 'info')
    # _resolve_tab_index asserts the active window is a MainWindow; a
    # subclass instance that skips __init__ satisfies it.
    fake_win = mainwindow.MainWindow.__new__(mainwindow.MainWindow)
    fake_win.win_id = 0
    mocker.patch.object(commands.QApplication, 'activeWindow',
                        return_value=fake_win)
    # tab_select raises the window it focused a tab in, which needs an app.
    mocker.patch.object(objects, 'qapp', mocker.Mock())

    result = {}
    for win_id in (0, 1):
        if win_id != 0:
            win_registry.add_window(win_id)
        tb = mocker.Mock()
        tb.is_private = False
        tb.widget = mocker.Mock(spec=[])
        real = tabwidget.TabWidget(win_id)
        qtbot.add_widget(real)
        tb.widget = real
        for idx in range(3):
            tab = fake_web_tab()
            real.addTab(tab, f'tab {win_id}/{idx}')
        objreg.register('tabbed-browser', tb, scope='window', window=win_id)
        dispatcher = commands.CommandDispatcher(win_id, tb)
        objreg.register('command-dispatcher', dispatcher, scope='window',
                        window=win_id)
        result[win_id] = (tb, dispatcher)
    yield result
    for win_id in (0, 1):
        objreg.delete('command-dispatcher', scope='window', window=win_id)
        objreg.delete('tabbed-browser', scope='window', window=win_id)


class TestTabPick:

    def test_command_line_splits_each_index(self, dispatchers, mocker):
        """`:tab-pick 1 3` through the parser arrives as two indices.

        Regression: the command was registered with maxsplit=0, which makes
        the whole argument string one argument, so every multi-index pick
        failed to parse.
        """
        tb, _ = dispatchers[0]
        # The macro recorder is a global set up by the app, not by unit tests.
        mocker.patch.object(runners.macros, 'macro_recorder',
                            mocker.Mock(recording=False))
        runners.CommandRunner(0).run('tab-pick 1 3', safely=True)
        assert tb.widget.tiles.pick == [tb.widget.widget(0),
                                        tb.widget.widget(2)]

    def test_command_line_reports_no_indices(self, dispatchers, mocker):
        """A bare `:tab-pick` says so instead of running with no arguments."""
        mocker.patch.object(runners.macros, 'macro_recorder',
                            mocker.Mock(recording=False))
        error = mocker.patch.object(runners.message, 'error')
        runners.CommandRunner(0).run('tab-pick', safely=True)
        assert error.called
        assert 'Usage: :tab-pick' in error.call_args[0][0]

    def test_pick_shows_the_named_tabs(self, dispatchers):
        tb, dispatcher = dispatchers[0]
        dispatcher.tab_pick('1', '3')
        assert tb.widget.tiles.pick == [tb.widget.widget(0),
                                        tb.widget.widget(2)]
        assert tb.widget.currentWidget() is tb.widget.widget(0)

    def test_single_index_makes_a_one_tab_pick(self, dispatchers):
        tb, dispatcher = dispatchers[0]
        dispatcher.tab_pick('2')
        assert tb.widget.tiles.pick == [tb.widget.widget(1)]

    def test_other_window_tabs_are_skipped(self, dispatchers, mocker):
        warning = mocker.patch.object(commands.message, 'warning')
        tb, dispatcher = dispatchers[0]
        dispatcher.tab_pick('1', '1/2', '3')
        assert tb.widget.tiles.pick == [tb.widget.widget(0),
                                        tb.widget.widget(2)]
        assert warning.call_args_list == [
            mocker.call('Tab 1/2 is in another window')]

    def test_only_other_window_tabs_raises(self, dispatchers, mocker):
        mocker.patch.object(commands.message, 'warning')
        tb, dispatcher = dispatchers[0]
        with pytest.raises(commands.cmdutils.CommandError,
                           match='No tab to pick in this window'):
            dispatcher.tab_pick('1/1')
        assert tb.widget.tiles.pick is None

    def test_no_index_raises(self, dispatchers):
        _, dispatcher = dispatchers[0]
        with pytest.raises(commands.cmdutils.CommandError,
                           match='Usage: :tab-pick'):
            dispatcher.tab_pick()


class TestTileFocus:

    """`:tile-focus next/prev` inside a pick, and in the plain tag stack."""

    @pytest.fixture
    def picked(self, dispatchers):
        """A pick of tabs 1 and 3 of window 0, with tab 1 focused."""
        tb, dispatcher = dispatchers[0]
        dispatcher.tab_pick('1', '3')
        return tb, dispatcher

    def test_pick_cycles_picked_tabs_only(self, picked):
        tb, dispatcher = picked
        tiles = tb.widget.tiles
        a, c = tiles.pick
        assert tb.widget.currentWidget() is a
        dispatcher.tile_focus('next')
        assert tb.widget.currentWidget() is c
        dispatcher.tile_focus('next')
        assert tb.widget.currentWidget() is a

    def test_pick_cycles_backwards(self, picked):
        tb, dispatcher = picked
        tiles = tb.widget.tiles
        a, c = tiles.pick
        dispatcher.tile_focus('prev')
        assert tb.widget.currentWidget() is c
        dispatcher.tile_focus('prev')
        assert tb.widget.currentWidget() is a

    def test_pick_survives_stepping(self, picked, mocker):
        """Stepping inside a pick never leaves it nor changes the view."""
        tb, dispatcher = picked
        tiles = tb.widget.tiles
        view = mocker.spy(tiles, 'view')
        pick = tiles.pick
        seltags = tiles.seltags
        for _ in range(3):
            dispatcher.tile_focus('next')
        assert tiles.pick == pick
        assert tiles.seltags == seltags
        assert not view.called

    def test_one_tab_pick_is_a_no_op(self, dispatchers):
        tb, dispatcher = dispatchers[0]
        dispatcher.tab_pick('2')
        tiles = tb.widget.tiles
        only = tiles.pick[0]
        for direction in ('next', 'prev'):
            dispatcher.tile_focus(direction)
            assert tb.widget.currentWidget() is only
            assert tiles.pick == [only]

    def test_current_outside_the_pick_focuses_the_first(self, picked, mocker):
        """A current tab that is not picked steps onto the first picked one.

        Focusing an off-pick tab ends the pick (TabWidget follows the focus),
        so the state is forced on the tile area instead.
        """
        tb, dispatcher = picked
        tiles = tb.widget.tiles
        first = tiles.pick[0]
        mocker.patch.object(dispatcher, '_current_widget',
                            return_value=tb.widget.widget(1))
        dispatcher.tile_focus('next')
        assert tb.widget.currentWidget() is first

    def test_no_pick_steps_the_flat_stack(self, dispatchers):
        """Without a pick, stepping past the tag's last pane views the next."""
        tb, dispatcher = dispatchers[0]
        tiles = tb.widget.tiles
        a, b, c = (tb.widget.widget(i) for i in range(3))
        for tab in (a, c):
            tab.data.tags = tiling.tag_mask(1)
        b.data.tags = tiling.tag_mask(2)
        tiles.view(tiling.tag_mask(1))
        tb.widget.setCurrentWidget(a)
        dispatcher.tile_focus('next')
        assert tb.widget.currentWidget() is c
        assert tiles.seltags == tiling.tag_mask(1)
        dispatcher.tile_focus('next')
        assert tb.widget.currentWidget() is b
        assert tiles.seltags == tiling.tag_mask(2)


class TestTabSelectMonocle:

    def test_monocle_sets_layout_after_following(self, dispatchers):
        tb, dispatcher = dispatchers[0]
        tiles = tb.widget.tiles
        tb.widget.widget(2).data.tags = tiling.tag_mask(2)
        dispatcher.tab_select(index='3', monocle=True)
        assert tiles.seltags == tiling.tag_mask(2)
        assert tiles.params.layout == 'monocle'

    def test_monocle_applies_to_the_window_the_tab_is_in(self, dispatchers):
        """Focusing a tab in another window monocles that window, not this one.

        Regression: the monocle was applied to the tiles of the window the
        command ran in, not the window the tab ended up being focused in.
        """
        tb0, dispatcher = dispatchers[0]
        tb1, _ = dispatchers[1]
        dispatcher.tab_select(index='1/2', monocle=True)
        assert tb1.widget.tiles.params.layout == 'monocle'
        assert tb0.widget.tiles.params.layout != 'monocle'

    def test_without_flag_layout_unchanged(self, dispatchers):
        tb, dispatcher = dispatchers[0]
        dispatcher.tab_select(index='2', monocle=False)
        assert tb.widget.tiles.params.layout != 'monocle'

    def test_monocle_does_nothing_for_tabs_page(self, dispatchers, mocker):
        """With no index, :tab-select opens qute://tabs and monocle is moot."""
        tb, dispatcher = dispatchers[0]
        openurl = mocker.patch.object(dispatcher, 'openurl')
        dispatcher.tab_select(monocle=True)
        openurl.assert_called_once_with('qute://tabs/', tab=True)
        assert tb.widget.tiles.params.layout != 'monocle'
