# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the ivy-style tab preview of the completion."""

import pytest
from qutebrowser.qt.core import QUrl, Qt
from qutebrowser.qt.gui import QColor, QPainter, QPixmap

from qutebrowser.completion import completionwidget, tabpreview
from qutebrowser.completion.models import completionmodel, listcategory


@pytest.fixture
def completionview(qtbot, status_command_stub, config_stub, win_registry,
                   mocker):
    mocker.patch('qutebrowser.completion.completer.Completer', autospec=True)
    mocker.patch(
        'qutebrowser.completion.completiondelegate.CompletionItemDelegate',
        return_value=None)
    view = completionwidget.CompletionView(cmd=status_command_stub, win_id=0)
    qtbot.add_widget(view)
    view.resize(1000, 300)
    return view


@pytest.fixture
def tabs(qtbot, fake_web_tab, tabbed_browser_stubs):
    """Two tabs in window 0 and one in window 1."""
    made = []
    for win, url in [(0, 'https://a.example/'), (0, 'https://b.example/'),
                     (1, 'https://c.example/')]:
        tab = fake_web_tab(QUrl(url), url[8], len(made))
        qtbot.add_widget(tab)
        tabbed_browser_stubs[win].widget.tabs.append(tab)
        made.append(tab)
    return made


def _tab_model(entries, *, tab_preview=True):
    model = completionmodel.CompletionModel(column_widths=(8, 36, 56, 0),
                                            tab_preview=tab_preview)
    model.add_category(listcategory.ListCategory('0', entries, sort=False))
    return model


def test_capture_and_snapshot(qtbot, fake_web_tab, config_stub):
    tab = fake_web_tab()
    qtbot.add_widget(tab)
    tab.resize(1600, 1000)
    tab.show()
    qtbot.wait_exposed(tab)

    live = tabpreview.snapshot(tab)
    assert live is not None
    assert live.width() <= tabpreview.THUMB_SIZE.width()
    assert live.height() <= tabpreview.THUMB_SIZE.height()

    tabpreview.capture(tab)
    tab.hide()
    cached = tabpreview.snapshot(tab)
    assert cached is not None
    assert cached.size() == live.size()


def test_capture_respects_setting(qtbot, fake_web_tab, config_stub):
    config_stub.val.completion.tab_preview = False
    tab = fake_web_tab()
    qtbot.add_widget(tab)
    tab.show()
    qtbot.wait_exposed(tab)
    tabpreview.capture(tab)
    tab.hide()
    assert tabpreview.snapshot(tab) is None


def test_blur_keeps_size_and_softens(qapp):
    pixmap = QPixmap(200, 100)
    pixmap.fill(Qt.GlobalColor.black)
    painter = QPainter(pixmap)
    painter.fillRect(0, 0, 100, 100, Qt.GlobalColor.white)
    painter.end()
    frosted = tabpreview.blur(pixmap, 24)
    assert frosted.size() == pixmap.size()
    edge = QColor(frosted.toImage().pixel(100, 50))
    assert 0 < edge.value() < 255
    assert tabpreview.blur(pixmap, 0) is pixmap


def test_hidden_tab_without_capture(fake_web_tab, qtbot):
    tab = fake_web_tab()
    qtbot.add_widget(tab)
    assert tabpreview.snapshot(tab) is None


@pytest.mark.parametrize('data, expected', [
    ('0/1', 0), ('0/2', 1), ('1/1', 2), ('2', 1),
    ('0/9', None), ('7/1', None), ('foo', None), ('', None),
])
def test_tab_for(completionview, tabs, data, expected):
    tab = completionview._tab_for(data)
    assert tab is (None if expected is None else tabs[expected])


def test_preview_follows_first_item_and_selection(completionview, tabs):
    model = _tab_model([('0/1', 'https://a.example/', 'a', ''),
                        ('0/2', 'https://b.example/', 'b', '')])
    completionview.set_model(model)
    completionview.show()
    preview = completionview._preview
    assert preview.isVisible()
    assert completionview.ivy
    # The list sits inside the result card, left of the 45% preview pane.
    viewport = completionview.viewport().geometry()
    assert preview.geometry().left() == 1000 - 450
    assert 0 < viewport.left() and viewport.right() < 1000 - 450
    assert 0 < viewport.top() and viewport.bottom() < 300
    assert preview.current_tab() is tabs[0]

    completionview.completion_item_focus('next')
    completionview.completion_item_focus('next')
    assert preview.current_tab() is tabs[1]

    completionview.hide()
    assert preview.current_tab() is None


def test_no_preview_for_other_models(completionview, tabs):
    model = _tab_model([('0/1', 'https://a.example/', 'a', '')],
                       tab_preview=False)
    completionview.set_model(model)
    completionview.show()
    assert not completionview._preview.isVisible()
    assert not completionview.ivy
    assert completionview.viewportMargins().right() == 0


def test_title_column_moves_back(completionview, tabs):
    """The picker shows the title first; other completions keep the order."""
    header = completionview.header()
    completionview.set_model(_tab_model([('0/1', 'https://a.example/',
                                          'a', '')]))
    assert header.visualIndex(2) == 1
    completionview.set_model(_tab_model([('0/1', 'https://a.example/',
                                          'a', '')], tab_preview=False))
    assert header.visualIndex(2) == 2


def test_no_preview_when_disabled(completionview, tabs, config_stub):
    config_stub.val.completion.tab_preview = False
    completionview.set_model(_tab_model([('0/1', 'https://a.example/',
                                          'a', '')]))
    completionview.show()
    assert not completionview._preview.isVisible()
