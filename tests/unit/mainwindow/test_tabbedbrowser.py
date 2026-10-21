# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

from qutebrowser.qt.widgets import QWidget
from qutebrowser.browser import browsertab
from qutebrowser.mainwindow import tabbedbrowser


class TestTabDeque:

    @pytest.mark.parametrize('size', [-1, 5])
    def test_size_handling(self, size, config_stub):
        config_stub.val.tabs.focus_stack_size = size
        dq = tabbedbrowser.TabDeque()
        dq.update_size()


class TestFocusChanged:

    """A background tab whose widget gets the focus becomes the current tab."""

    @pytest.fixture
    def browser(self, mocker, qtbot):
        tabs = [QWidget(), QWidget()]
        children = [QWidget(tab) for tab in tabs]
        for tab in tabs:
            qtbot.add_widget(tab)
        browser = mocker.Mock(is_shutting_down=False)
        browser.widgets.return_value = tabs
        browser.widget.currentWidget.return_value = tabs[0]
        browser.tabs = tabs
        browser.children = children
        return browser

    def test_background_tab(self, browser):
        tabbedbrowser.TabbedBrowser._on_focus_changed(
            browser, None, browser.children[1])
        browser.widget.setCurrentWidget.assert_called_once_with(
            browser.tabs[1])

    def test_current_tab(self, browser):
        tabbedbrowser.TabbedBrowser._on_focus_changed(
            browser, None, browser.children[0])
        browser.widget.setCurrentWidget.assert_not_called()

    def test_unrelated_widget(self, browser, qtbot):
        other = QWidget()
        qtbot.add_widget(other)
        tabbedbrowser.TabbedBrowser._on_focus_changed(browser, None, other)
        browser.widget.setCurrentWidget.assert_not_called()

    def test_no_widget(self, browser):
        tabbedbrowser.TabbedBrowser._on_focus_changed(browser, None, None)
        browser.widget.setCurrentWidget.assert_not_called()

    def test_shutting_down(self, browser):
        browser.is_shutting_down = True
        tabbedbrowser.TabbedBrowser._on_focus_changed(
            browser, None, browser.children[1])
        browser.widget.setCurrentWidget.assert_not_called()


class TestTagRules:

    """tabopen with tags=... (session restore) never gets a rule applied."""

    @pytest.fixture
    def browser(self, mocker, qtbot, config_stub, fake_web_tab):
        mocker.patch.object(tabbedbrowser.quitter, 'instance',
                            mocker.Mock(shutting_down=mocker.Mock()))
        mocker.patch.object(tabbedbrowser.modeman, 'instance')

        def _create(**kwargs):
            tab = fake_web_tab()
            # FakeWebTab lacks the bits the tab-change plumbing touches.
            tab.search = mocker.Mock(match=browsertab.SearchMatch())
            return tab
        mocker.patch.object(tabbedbrowser.browsertab, 'create',
                            side_effect=_create)
        tb = tabbedbrowser.TabbedBrowser(win_id=0, private=False)
        # FakeWebTab does not carry every sub-object the signal wiring
        # touches; the wiring is not what these tests are about.
        mocker.patch.object(tb, '_connect_tab_signals')
        qtbot.add_widget(tb)
        return tb

    def test_restored_tags_mark_rule_done(self, browser):
        tab = browser.tabopen(tags=0b100)
        assert tab.data.tags == 0b100
        assert tab.data.tag_rule_done

    def test_plain_tabopen_not_rule_done(self, browser):
        tab = browser.tabopen()
        assert not tab.data.tag_rule_done

    def test_rule_without_follow_stays_in_background(self, browser,
                                                     config_stub):
        """A foreground open onto an unfollowed rule tag must not pull the view.

        setCurrentIndex views an off-view tab's tag, so tabopen has to
        leave such a tab in the background instead of making it current.
        """
        from qutebrowser.qt.core import QUrl
        from qutebrowser.utils import urlmatch
        config_stub.val.tabs.tiling.rule_follow = False
        config_stub.set_obj('tabs.tiling.rule_tag', 4,
                            pattern=urlmatch.UrlPattern('*.youtube.com'))
        first = browser.tabopen()
        tab = browser.tabopen(QUrl('https://www.youtube.com/'),
                              background=False)
        assert tab.data.tags == 0b1000
        assert browser.widget.tiles.seltags == 0b1
        assert browser.widget.currentWidget() is first
