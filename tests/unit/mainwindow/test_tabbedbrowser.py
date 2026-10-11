# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

from qutebrowser.qt.widgets import QWidget
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
