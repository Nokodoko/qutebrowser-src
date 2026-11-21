# SPDX-FileCopyrightText: Copyright 2026 icarus contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for marking several tabs in the completion (`:tab-select`)."""

import pytest

from qutebrowser.completion import completionwidget, completer
from qutebrowser.completion.models import completionmodel, listcategory


@pytest.fixture
def view(qtbot, status_command_stub, config_stub, win_registry, mocker):
    """A CompletionView with a real Completer attached."""
    mocker.patch(
        'qutebrowser.completion.completiondelegate.CompletionItemDelegate',
        return_value=None)
    widget = completionwidget.CompletionView(cmd=status_command_stub, win_id=0)
    qtbot.add_widget(widget)
    widget.completer_obj = completer.Completer(cmd=status_command_stub,
                                               win_id=0, parent=widget)
    return widget


@pytest.fixture
def wired_view(view):
    """A view whose completer reacts to its selection changes.

    Wiring the view up to the completer like `mainwindow.py` does, so a
    selection change goes through `Completer.on_selection_changed` and can
    overwrite the commandline, which is what the marking must not do.
    """
    view.completer_obj._cmd.update_completion.disconnect()
    view.selection_changed.connect(view.completer_obj.on_selection_changed)
    view.pattern_reset.connect(view.completer_obj.reset_completed_part)
    return view


@pytest.fixture
def tab_model():
    """A pickable tab model, like the one behind `:tab-select`."""
    model = completionmodel.CompletionModel(column_widths=(6, 40, 46, 8),
                                            tab_indices=True)
    model.add_category(listcategory.ListCategory(
        'Tabs', [('0/1', 'https://a.example/', 'a title', ''),
                 ('0/2', 'https://b.example/', 'b title', ''),
                 ('1/1', 'https://c.example/', 'c title', '')],
        sort=False))
    return model


@pytest.fixture
def other_model():
    """A model which is not a tab model."""
    model = completionmodel.CompletionModel()
    model.add_category(listcategory.ListCategory(
        'Commands', [('open', 'open url', ''), ('quit', 'quit', '')],
        sort=False))
    return model


def _select_first(view):
    """Move the selection to the first item (past the category header)."""
    view.completion_item_focus('next')
    return view.currentIndex()


def test_mark_toggles_and_moves(view, tab_model, status_command_stub):
    """Marking toggles a mark on the selected row and moves the focus."""
    status_command_stub.setText(':tab-select ')
    view.set_model(tab_model)
    assert view.marked() == []

    _select_first(view)
    view.completion_item_mark('next')
    assert view.marked() == ['0/1']
    # the focus moved on
    assert view.model().data(view.currentIndex()) == '0/2'

    view.completion_item_mark('next')
    assert view.marked() == ['0/1', '0/2']

    # marking the same row again unmarks it
    view.completion_item_focus('prev')
    view.completion_item_mark('prev')
    assert view.marked() == ['0/1']


def test_mark_order_is_mark_order(view, tab_model, status_command_stub):
    """Marks come back in the order they were made, not in row order."""
    status_command_stub.setText(':tab-select ')
    view.set_model(tab_model)
    view.completion_item_focus('next')  # 0/1
    view.completion_item_focus('next')  # 0/2
    view.completion_item_focus('next')  # 1/1
    view.completion_item_mark('prev')   # mark 1/1, back to 0/2
    view.completion_item_focus('prev')  # 0/1
    view.completion_item_mark('next')   # mark 0/1
    assert view.marked() == ['1/1', '0/1']


def test_fallback_to_focus_for_other_model(view, other_model,
                                           status_command_stub):
    """Anywhere but a tab model this is exactly `:completion-item-focus`."""
    status_command_stub.setText(':open ')
    view.set_model(other_model)
    view.completion_item_mark('next')
    assert view.marked() == []
    assert view.model().data(view.currentIndex()) == 'open'
    view.completion_item_mark('next')
    assert view.marked() == []
    assert view.model().data(view.currentIndex()) == 'quit'
    view.completion_item_mark('prev')
    assert view.model().data(view.currentIndex()) == 'open'


def test_marks_survive_filtering(view, tab_model, status_command_stub):
    """Marks are keyed by tab index, so re-filtering keeps them."""
    status_command_stub.setText(':tab-select ')
    view.set_model(tab_model)
    _select_first(view)
    view.completion_item_mark('next')
    assert view.marked() == ['0/1']

    # The user keeps typing: the model is filtered down to what is left.
    view.set_pattern('b')
    assert view.model().data(view.currentIndex()) in (None, '')
    assert view.marked() == ['0/1']
    assert view.is_marked('0/1')
    assert not view.is_marked('0/2')


def test_marks_cleared_on_close(view, tab_model, status_command_stub):
    """Leaving command mode (which clears the completion) drops the marks."""
    status_command_stub.setText(':tab-select ')
    view.set_model(tab_model)
    _select_first(view)
    view.completion_item_mark('next')
    assert view.marked() == ['0/1']
    view.on_clear_completion_selection()
    assert view.marked() == []
    assert not view.is_marked('0/1')


def test_mark_prefix_replaces_command(view, tab_model, status_command_stub):
    """With marks, the typed command becomes `:tab-pick <marks>`."""
    status_command_stub.setText(':tab-select ')
    view.set_model(tab_model)
    assert view.mark_prefix() is None

    _select_first(view)
    view.completion_item_mark('next')
    assert view.model().data(view.currentIndex()) == '0/2'
    view.completion_item_mark('next')
    assert view.mark_prefix() == 'tab-pick 0/1 0/2'


def test_mark_prefix_drops_flags(view, tab_model, status_command_stub):
    """`:tab-pick` takes no flags, so nothing typed carries over."""
    status_command_stub.setText(':tab-select --monocle ')
    view.set_model(tab_model)
    _select_first(view)
    view.completion_item_mark('next')
    assert view.mark_prefix() == 'tab-pick 0/1'


def test_mark_on_header_row_does_not_move(view):
    """Tabbing on a model with no markable row must not raise or advance."""
    empty = completionmodel.CompletionModel(column_widths=(6, 40, 46, 8),
                                            tab_indices=True)
    empty.add_category(listcategory.ListCategory('Tabs', []))
    view.set_model(empty)
    view.completion_item_mark('next')
    assert view.marked() == []


def test_mark_prefix_ignores_the_typed_command(view, tab_model,
                                               status_command_stub):
    """Marks win over whatever flags the typed command carries.

    The completion was opened with `:tab-select --monocle`; Enter with marks
    must still run a plain `:tab-pick`, not carry the flag over.
    """
    status_command_stub.setText(':tab-select --monocle ')
    view.set_model(tab_model)
    _select_first(view)
    view.completion_item_mark('next')
    assert view.mark_prefix() == 'tab-pick 0/1'


def test_mark_prefix_inactive_model(view, tab_model, status_command_stub):
    """No marks, or no active tab model, means no rewrite."""
    status_command_stub.setText(':tab-select ')
    view.set_model(tab_model)
    _select_first(view)
    view.completion_item_mark('next')
    assert view.mark_prefix() is not None
    view.set_model(None)
    assert view.mark_prefix() is None


def test_marks_dropped_when_model_cleared(view, tab_model, status_command_stub,
                                          mocker):
    """Marks belong to the tab list: a cleared completion must not hijack.

    Regression: marks survived the completion being cleared, so after marking
    tabs and typing something that has no completion, the next Enter ran
    `:tab-pick` with the stale marks.
    """
    status_command_stub.setText(':tab-select ')
    view.set_model(tab_model)
    _select_first(view)
    view.completion_item_mark('next')
    assert view.mark_prefix() is not None

    completer_obj = view.completer_obj
    mocker.patch.object(completer_obj, '_get_new_completion',
                        return_value=None)
    status_command_stub.setText(':tab-select --nonsense ')
    completer_obj._update_completion()
    assert view.mark_prefix() is None
    assert view.marked() == []


def test_completer_keeps_pattern_when_marked(view, tab_model, mocker,
                                             status_command_stub):
    """Tabbing through the list does not overwrite the typed pattern."""
    status_command_stub.setText(':tab-select ')
    view.set_model(tab_model)
    _select_first(view)
    view.completion_item_mark('next')

    set_text = mocker.patch.object(status_command_stub, 'setText')
    view.selection_changed.emit('0/1')
    assert not set_text.called


# ---- the pattern must survive marking ------------------------------------
#
# The bug: the first `:completion-item-mark` moves the focus before anything
# is marked, and that selection change ran the stock fill, replacing the
# pattern the user typed with the tab index (`0/3`), so they could not keep
# fuzzy-searching. The tests below drive the real Completer with its
# `selection_changed` connection in place, as `mainwindow.py` wires it.


def test_first_mark_keeps_pattern(wired_view, tab_model, status_command_stub):
    """The first mark must not replace the typed pattern with a tab index."""
    status_command_stub.setText(':tab-select title')
    wired_view.set_model(tab_model)
    wired_view.set_pattern('title')
    assert wired_view.model().count() == 3

    wired_view.completion_item_mark('next')

    # The commandline still holds what the user typed...
    assert status_command_stub.text() == ':tab-select title'
    # ...and the model is still filtered by it, not by the marked index.
    assert wired_view.pattern == 'title'
    assert wired_view.model().count() == 3
    assert wired_view.marked() == ['0/1']


def test_keep_searching_after_marking(wired_view, tab_model,
                                      status_command_stub):
    """After marking, typing another filter re-filters and keeps the marks."""
    status_command_stub.setText(':tab-select title')
    wired_view.set_model(tab_model)
    wired_view.set_pattern('title')
    wired_view.completion_item_mark('next')
    assert wired_view.marked() == ['0/1']

    # The user keeps fuzzy-searching for another tab.
    status_command_stub.setText(':tab-select c title')
    wired_view.set_pattern('c title')
    assert status_command_stub.text() == ':tab-select c title'
    assert wired_view.model().count() == 1
    assert wired_view.is_marked('0/1')

    # Mark the tab the new filter leaves: both marks are kept, in order. That
    # filter singled it out, so the pattern is handed back empty (see
    # test_mark_single_match_keeps_list) and the whole list is ready again.
    wired_view.completion_item_mark('next')
    assert wired_view.marked() == ['0/1', '1/1']
    assert wired_view.mark_prefix() == 'tab-pick 0/1 1/1'
    assert status_command_stub.text() == ':tab-select '
    assert wired_view.model().count() == 3


def test_mark_unmark_mark_keeps_pattern(wired_view, tab_model,
                                        status_command_stub):
    """Marking, unmarking everything, then marking again keeps the pattern."""
    status_command_stub.setText(':tab-select title')
    wired_view.set_model(tab_model)
    wired_view.set_pattern('title')

    # Mark every row: each mark toggles the row the focus is on and moves on.
    wired_view.completion_item_mark('next')
    assert wired_view.marked() == ['0/1']
    wired_view.completion_item_mark('next')
    assert wired_view.marked() == ['0/1', '0/2']
    wired_view.completion_item_mark('next')
    assert wired_view.marked() == ['0/1', '0/2', '1/1']

    # Unmark them all, so nothing is marked while the focus moves: marking
    # toggles the row the focus is on and leaves the focus there.
    wired_view.completion_item_mark('prev')    # unmark 0/1
    assert wired_view.marked() == ['0/2', '1/1']
    wired_view.completion_item_mark('prev')    # unmark 0/2
    assert wired_view.marked() == ['0/2']
    wired_view.completion_item_mark('prev')    # unmark 0/2 again: no marks left
    assert wired_view.marked() == []
    assert status_command_stub.text() == ':tab-select title'

    # A mark move with no marks left: still no fill.
    wired_view.completion_item_mark('prev')
    assert status_command_stub.text() == ':tab-select title'
    assert wired_view.pattern == 'title'
    assert wired_view.model().count() == 3
    assert wired_view.marked() == ['0/1']


def test_focus_without_marks_still_fills(wired_view, tab_model,
                                         status_command_stub):
    """Plain Up/Down with no marks keeps completing like it always did."""
    status_command_stub.setText(':tab-select title')
    wired_view.set_model(tab_model)
    wired_view.set_pattern('title')

    wired_view.completion_item_focus('next')
    assert status_command_stub.text() == ':tab-select 0/1'

    wired_view.completion_item_focus('next')
    assert status_command_stub.text() == ':tab-select 0/2'


def test_mark_on_other_model_fills(wired_view, other_model,
                                   status_command_stub):
    """On a model that is not a tab model, marking fills like focusing."""
    status_command_stub.setText(':o')
    wired_view.set_model(other_model)
    wired_view.set_pattern('o')

    wired_view.completion_item_mark('next')
    # The single match is quick-completed, exactly like `:completion-item-focus`.
    assert status_command_stub.text() == ':open '
    assert wired_view.marked() == []

    status_command_stub.setText(':open q')
    wired_view.set_pattern('q')
    wired_view.completion_item_mark('next')
    assert status_command_stub.text() == ':open quit'


def test_keeps_pattern_predicate(wired_view, tab_model, other_model,
                                 status_command_stub):
    """What `keeps_pattern` says for every state the view can be in.

    The predicate is what stops the completer filling the commandline, so
    pin it down directly: only a pickable tab model, with a mark held or a
    `:completion-item-mark` in progress, may keep the pattern.
    """
    status_command_stub.setText(':tab-select title')
    wired_view.set_model(tab_model)
    wired_view.set_pattern('title')

    # Tab model, nothing marked, no mark call in progress: the stock fill.
    assert wired_view.keeps_pattern() is False

    # A mark call in progress with nothing marked yet (the first Tab): the
    # flag `completion_item_mark` holds around its focus moves.
    wired_view._marking = True
    assert wired_view.keeps_pattern() is True
    wired_view._marking = False
    assert wired_view.keeps_pattern() is False

    # A mark held: every later selection change keeps the pattern.
    wired_view.completion_item_mark('next')
    assert wired_view.marked() == ['0/1']
    assert wired_view.keeps_pattern() is True

    # Not a tab model: never keeps the pattern, marks or no marks.
    wired_view.set_model(other_model)
    assert wired_view.keeps_pattern() is False

    # A tab model again, with no marks (what the completer does is set the new
    # model and then clear the completion, which drops the marks): the fill is
    # back on.
    fresh = completionmodel.CompletionModel(column_widths=(6, 40, 46, 8),
                                            tab_indices=True)
    fresh.add_category(listcategory.ListCategory(
        'Tabs', [('0/1', 'https://a.example/', 'a title', '')], sort=False))
    wired_view.set_model(fresh)
    wired_view.on_clear_completion_selection()
    assert wired_view.marked() == []
    assert wired_view.keeps_pattern() is False


# ---- the list must not vanish when the filter leaves one match -----------
#
# The bug: the user types until exactly one tab matches, presses Tab to mark
# it, and the completion disappears, because `completion_item_focus` hides the
# view on a single match (`completion.quick`) and marking calls it. They then
# had to delete the typed text to get the list back before searching for the
# next tab. Marking must instead keep the view open and, when the filter had
# singled out that one tab, hand the empty pattern back so the whole list is
# there again with the mark retained.


@pytest.fixture
def quick(config_stub):
    """The user's setup: quick completion, always-shown completion."""
    config_stub.val.completion.quick = True
    config_stub.val.completion.show = 'always'
    return config_stub


@pytest.fixture
def tab_model_matching():
    """Build a tab model; factory so a test can pick its own rows."""
    def _build(rows):
        model = completionmodel.CompletionModel(column_widths=(6, 40, 46, 8),
                                                tab_indices=True)
        model.add_category(listcategory.ListCategory('Tabs', rows, sort=False))
        return model
    return _build


def _type(wired_view, status_command_stub, text, pattern):
    """Type `text` with `pattern` as the token under the cursor, and filter."""
    status_command_stub.setText(text)
    status_command_stub.setCursorPosition(len(text))
    wired_view.set_pattern(pattern)


def test_mark_single_match_keeps_list(wired_view, status_command_stub, quick,
                                      tab_model_matching):
    """Marking the only match must not hide the completion or eat the filter.

    The reported bug: one match, Tab, and the whole list disappeared until the
    typed text was deleted.
    """
    wired_view.set_model(tab_model_matching([
        ('0/1', 'https://a.example/', 'a title', ''),
        ('0/2', 'https://b.example/', 'b title', ''),
        ('1/1', 'https://c.example/', 'c title', '')]))
    _type(wired_view, status_command_stub, ':tab-select b', 'b')
    assert wired_view.model().count() == 1
    wired_view.show()

    wired_view.completion_item_mark('next')

    # The list is still up: marking never hides it, whatever completion.show
    # or completion.quick say.
    assert wired_view.isVisible()
    # The tab the filter singled out is marked...
    assert wired_view.marked() == ['0/2']
    # ...the typed filter is handed back, so there is nothing to delete...
    assert status_command_stub.text() == ':tab-select '
    # ...and the whole tab list is showing again.
    assert wired_view.pattern == ''
    assert wired_view.model().count() == 3


def test_mark_single_match_keeps_flags(wired_view, status_command_stub, quick,
                                       tab_model_matching):
    """Resetting the pattern keeps the command and its flags."""
    wired_view.set_model(tab_model_matching([
        ('0/1', 'https://a.example/', 'a title', ''),
        ('0/2', 'https://b.example/', 'b title', ''),
        ('1/1', 'https://c.example/', 'c title', '')]))
    _type(wired_view, status_command_stub, ':tab-select --monocle b', 'b')
    assert wired_view.model().count() == 1

    wired_view.completion_item_mark('next')

    assert status_command_stub.text() == ':tab-select --monocle '
    assert wired_view.marked() == ['0/2']
    assert wired_view.mark_prefix() == 'tab-pick 0/2'
    assert wired_view.model().count() == 3
    assert wired_view.isVisible()


def test_mark_with_several_matches_keeps_pattern(wired_view,
                                                 status_command_stub, quick,
                                                 tab_model):
    """With more than one match the filter stays: mark the rest of them."""
    wired_view.set_model(tab_model)
    _type(wired_view, status_command_stub, ':tab-select title', 'title')
    assert wired_view.model().count() == 3

    wired_view.completion_item_mark('next')

    assert status_command_stub.text() == ':tab-select title'
    assert wired_view.pattern == 'title'
    assert wired_view.model().count() == 3
    assert wired_view.marked() == ['0/1']
    assert wired_view.isVisible()

    # A second mark on the same filtered list: both marks, still in order.
    wired_view.completion_item_mark('next')
    assert wired_view.marked() == ['0/1', '0/2']
    assert wired_view.mark_prefix() == 'tab-pick 0/1 0/2'
    assert status_command_stub.text() == ':tab-select title'


def test_marks_survive_the_reset(wired_view, status_command_stub, quick,
                                 tab_model_matching):
    """The marks are keyed by tab index, so the reset cannot drop them."""
    wired_view.set_model(tab_model_matching([
        ('0/1', 'https://a.example/', 'a title', ''),
        ('0/2', 'https://b.example/', 'b title', ''),
        ('1/1', 'https://c.example/', 'c title', '')]))
    _type(wired_view, status_command_stub, ':tab-select b', 'b')

    wired_view.completion_item_mark('next')

    assert wired_view.model().count() == 3
    assert wired_view.marked() == ['0/2']
    assert wired_view.is_marked('0/2')
    assert wired_view.mark_prefix() == 'tab-pick 0/2'

    # The marked row is still recognisable as marked in the full list.
    idx = wired_view._model().first_item().sibling(1, 0)
    assert wired_view._model().data(idx) == '0/2'


def test_single_match_focus_still_hides(wired_view, status_command_stub,
                                        quick, other_model):
    """Plain focus on a single match still quick-hides, as upstream."""
    status_command_stub.setText(':o')
    wired_view.set_model(other_model)
    wired_view.set_pattern('o')
    assert wired_view.model().count() == 1
    wired_view.show()
    assert wired_view.isVisible()

    wired_view.completion_item_focus('next')
    assert not wired_view.isVisible()


def test_single_match_mark_on_other_model_still_hides(wired_view,
                                                      status_command_stub,
                                                      quick, other_model):
    """A mark on a non-tab model is plain focus, quick-hide included."""
    status_command_stub.setText(':o')
    wired_view.set_model(other_model)
    wired_view.set_pattern('o')
    wired_view.show()

    wired_view.completion_item_mark('next')
    assert not wired_view.isVisible()
    assert wired_view.marked() == []
