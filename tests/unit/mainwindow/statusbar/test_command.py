# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the statusbar commandline (`:command-accept`).

The completion is handed to the commandline by MainWindow (see
`Command.set_completion`), so a stub stands in for the real CompletionView.
It is registered in the window registry as well - with `command_only=True`,
exactly like `mainwindow.py` does - so that a lookup going through objreg
instead of the wiring fails here the way it fails in the app.
"""

import pytest

from qutebrowser.mainwindow.statusbar import command
from qutebrowser.misc import lineparser
from qutebrowser.utils import objreg


@pytest.fixture
def command_widget(qtbot, config_stub, mocker, win_registry):
    """A real statusbar Command with command mode stubbed out."""
    config_stub.val.search.incremental = False
    mocker.patch.object(command.modeman, 'leave')
    # Registered by the app normally; a unit test has to provide it.
    objreg.register('command-history',
                    lineparser.LimitLineParser('', 'test-cmd-history',
                                               limit=None),
                    update=True)
    widget = command.Command(win_id=0, private=False)
    qtbot.add_widget(widget)
    objreg.register('status-command', widget, scope='window', window=0)
    yield widget
    objreg.delete('status-command', scope='window', window=0)
    objreg.delete('command-history')


@pytest.fixture
def completion_stub(mocker, command_widget):
    """A stub completion wired to the commandline, without marks.

    Handed to the commandline through `set_completion`, which is how
    `MainWindow` wires the real one (see `Command.set_completion`), and
    registered command_only like the real one: reading it back through objreg
    raises CommandOnlyError, so the wiring is the only way to reach it.
    """
    stub = mocker.Mock()
    stub.mark_prefix.return_value = None
    objreg.register('completion', stub, scope='window', window=0,
                    command_only=True)
    command_widget.set_completion(stub)
    yield stub
    objreg.delete('completion', scope='window', window=0)
    command_widget.set_completion(None)


class TestCommandAccept:

    def test_accept_runs_typed_text(self, command_widget, completion_stub,
                                    qtbot):
        """Without marks, the typed command is run and recorded."""
        command_widget.setText(':tab-select foo')
        with qtbot.waitSignal(command_widget.got_cmd[str]) as sig:
            command_widget.command_accept()
        assert sig.args == ['tab-select foo']
        assert command_widget.history.history[-1] == ':tab-select foo'

    def test_accept_runs_marks(self, command_widget, completion_stub, qtbot):
        """With marks, `:tab-pick <marks>` is run instead of the text."""
        completion_stub.mark_prefix.return_value = 'tab-pick 0/3 0/5'
        command_widget.setText(':tab-select ')
        with qtbot.waitSignal(command_widget.got_cmd[str]) as sig:
            command_widget.command_accept()
        assert sig.args == ['tab-pick 0/3 0/5']

    def test_accept_records_the_run_command(self, command_widget,
                                            completion_stub):
        """History holds the command that actually ran, not the typed one."""
        completion_stub.mark_prefix.return_value = 'tab-pick 0/3 0/5'
        command_widget.setText(':tab-select ')
        command_widget.command_accept()
        assert command_widget.history.history[-1] == ':tab-pick 0/3 0/5'

    def test_marks_read_before_leaving_mode(self, command_widget,
                                            completion_stub, mocker):
        """Marks are read before leaving command mode clears them."""
        completion_stub.mark_prefix.return_value = 'tab-pick 0/1'
        calls = []
        mocker.patch.object(command.modeman, 'leave',
                            side_effect=lambda *a, **kw: calls.append('leave'))
        completion_stub.mark_prefix.side_effect = lambda: (
            calls.append('mark_prefix') or 'tab-pick 0/1')
        command_widget.setText(':tab-select ')
        command_widget.command_accept()
        assert calls == ['mark_prefix', 'leave']

    def test_no_completion_wired(self, command_widget, qtbot):
        """No completion wired up to the commandline means the text is run."""
        command_widget.setText(':open example.com')
        with qtbot.waitSignal(command_widget.got_cmd[str]) as sig:
            command_widget.command_accept()
        assert sig.args == ['open example.com']

    def test_completion_is_command_only(self, command_widget, completion_stub):
        """The completion is only reachable through the wiring.

        Regression: `_mark_prefix` looked `completion` up in the window
        registry and only caught KeyError, but MainWindow registers it
        command_only, so every `:` command died with CommandOnlyError. The
        fixture registers it the same way: this asserts the app's shape, and
        that accepting a command still works through the wiring.
        """
        with pytest.raises(objreg.CommandOnlyError):
            objreg.get('completion', scope='window', window=0)

        command_widget.setText(':tab-select foo')
        command_widget.command_accept()
        assert command_widget.history.history[-1] == ':tab-select foo'

    def test_search_unaffected(self, command_widget, completion_stub, qtbot):
        """A search is still a search, marks or no marks."""
        command_widget.setText('/needle')
        with qtbot.waitSignal(command_widget.got_search) as sig:
            command_widget.command_accept()
        assert sig.args == ['needle', False]

    def test_marked_command_is_recorded_in_history(self, command_widget,
                                                   completion_stub, mocker):
        """The pick that ran is what lands in the command history."""
        completion_stub.mark_prefix.return_value = 'tab-pick 0/1 0/3'
        history = mocker.patch.object(command_widget.history, 'append')
        command_widget.setText(':tab-select --monocle ')
        command_widget.command_accept()
        history.assert_called_once_with(':tab-pick 0/1 0/3')
