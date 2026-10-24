# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A tab's icarus conversation: its transcript, tools, session and model."""

import dataclasses
import re
import time
from typing import Any

from qutebrowser.qt.core import QObject, pyqtSignal

from qutebrowser.icarus import client as clientmod


#: How many transcript entries a saved qutebrowser session keeps per tab.
SAVED_ENTRIES = 50
#: How many tool calls the tools pane keeps per tab (oldest dropped).
MAX_TOOLS = 100

_KINDS = {'you', 'icarus', 'step', 'thinking', 'error'}

#: A suggestion line: the page project's AGENTS.md has the model end a reply
#: with ``→ <message>`` lines (serve_bar_page.go in icarus).
_SUGGESTION = re.compile(r'^\s*(?:→|->)\s+(.+?)\s*$')

#: The working indicator's phases, as the web UI's working.js has them.
IDLE, WORKING, RESPONDING, TOOL = '', 'working', 'responding', 'tool'


_STEERING = re.compile(r'^\s*#\s*Steering\s*$')


def split_suggestions(text: str) -> tuple[str, list[str]]:
    """A reply's body and the ``→`` suggestion lines it ends with.

    The suggestions may sit just above a trailing ``# Steering`` block (the
    harness's reply format puts that last); the block stays in the body.
    """
    lines = text.rstrip().split('\n')
    steering: list[str] = []
    for i in range(len(lines) - 1, -1, -1):
        if _STEERING.match(lines[i]):
            steering, lines = lines[i:], lines[:i]
            while lines and not lines[-1].strip():
                lines.pop()
            break
    body, suggestions = _split_tail(lines)
    if steering:
        body = (body + '\n\n' if body else '') + '\n'.join(steering)
    return body, suggestions


def _split_tail(lines: list[str]) -> tuple[str, list[str]]:
    suggestions: list[str] = []
    while lines:
        m = _SUGGESTION.match(lines[-1])
        if m:
            suggestions.insert(0, m.group(1))
            lines.pop()
        elif not lines[-1].strip() and suggestions:
            lines.pop()
        else:
            break
    return '\n'.join(lines).rstrip(), suggestions


@dataclasses.dataclass
class Entry:

    """One block of the transcript.

    kind is ``you``, ``icarus``, ``thinking`` (the model's reasoning), ``step``
    (a laya plan/step line) or ``error``.
    """

    kind: str
    text: str


@dataclasses.dataclass
class ToolCall:

    """One tool call of a turn, for the tools pane.

    state is ``running``, ``done`` or ``error``; result is the output (or the
    error text), ms the time the tool took.
    """

    call: str
    name: str
    args: str
    result: str = ''
    state: str = 'running'
    ms: int = 0


class Conversation(QObject):

    """The chat a tab carries; lives in ``tab.data.icarus``.

    Signals:
        changed: The transcript or the state changed.
        open_url: The search lane asked for a URL to be opened.
    """

    changed = pyqtSignal()
    open_url = pyqtSignal(str)

    def __init__(self, client: clientmod.Client, target: str,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self.target = target
        self.entries: list[Entry] = []
        self.tools: list[ToolCall] = []
        self.suggestions: list[str] = []
        self.session = ''
        self.model: clientmod.Model | None = None
        self.lane = ''
        #: The working indicator: IDLE, WORKING, RESPONDING or TOOL.
        self.phase = IDLE
        #: The tool running in phase TOOL.
        self.tool = ''
        #: When the running turn started (time.monotonic), for its timer.
        self.started = 0.0
        #: Whether the bar's opening greeting has run (or been skipped).
        self.greeted = False
        #: Prompts waiting behind the running turn (queue mode), oldest first.
        self.queue: list[str] = []
        self._turn: clientmod.Turn | None = None
        #: The prompt a steer runs once the interrupted turn has settled.
        self._steer: tuple[str, str, str] | None = None
        #: The url/title queued prompts are sent with (the latest submit's).
        self._page = ('', '')

    @property
    def busy(self) -> bool:
        return self._turn is not None

    def _body(self, text: str, url: str, title: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            'cdp': clientmod.cdp_url(),
            'tab': self.target,
            'text': text,
            'session': self.session,
            'url': url,
            'title': title,
        }
        if self.model is not None:
            body['provider'] = self.model.provider
            body['model'] = self.model.model
        return body

    def _start(self, turn: clientmod.Turn) -> None:
        self.lane = ''
        self.suggestions = []
        self.phase = WORKING
        self.tool = ''
        self.started = time.monotonic()
        self._turn = turn
        turn.event.connect(self._on_event)
        turn.finished.connect(self._on_finished)
        self.changed.emit()

    def submit(self, text: str, *, url: str = '', title: str = '',
               mid_turn: str = 'steer') -> bool:
        """Send text as the next turn.

        While a turn runs, mid_turn decides (icarus.mid_turn): ``steer``
        interrupts it, keeping its partial answer, and runs text right away;
        ``queue`` runs text after it (and after what is queued already).
        """
        text = text.strip()
        if not text:
            return False
        self.greeted = True
        self._page = (url, title)
        if self.busy and mid_turn == 'queue':
            self.queue.append(text)
            self.changed.emit()
            return True
        if self.busy:
            self._steer = (text, url, title)
            self.stop()
            return True
        self._send(text, url, title)
        return True

    def _send(self, text: str, url: str, title: str) -> None:
        self.entries.append(Entry('you', text))
        self._start(self._client.turn(self._body(text, url, title)))

    def unqueue(self) -> str:
        """Drop the newest queued prompt and return it ('' when none)."""
        if not self.queue:
            return ''
        text = self.queue.pop()
        self.changed.emit()
        return text

    def greet(self, *, url: str = '', title: str = '') -> bool:
        """Run the opening turn: what the page is, what could be done."""
        if self.greeted or self.busy or self.entries:
            return False
        self.greeted = True
        self._start(self._client.greet(self._body('', url, title)))
        return True

    def stop(self) -> None:
        """Abort the running turn (a steer keeps its own pending prompt)."""
        if self._turn is not None:
            self._turn.abort()

    def cancel(self) -> None:
        """Stop the running turn and everything waiting behind it."""
        self._steer = None
        self.queue.clear()
        self.stop()
        self.changed.emit()

    def reset(self) -> None:
        """Start over: a new serve-side session on the next submit."""
        self._steer = None
        self.queue.clear()
        self.stop()
        self.entries.clear()
        self.tools.clear()
        self.suggestions = []
        self.session = ''
        self.greeted = False
        self._client.forget(self.target)
        self.changed.emit()

    def attach(self, session: str, turns: list[dict[str, Any]]) -> None:
        """Carry on with another serve session (the bar's session list).

        turns is its GET /bar/history: [{role, text}], oldest first. The next
        submit sends this session, and serve re-binds the tab to it.
        """
        self._steer = None
        self.queue.clear()
        self.stop()
        self.session = session
        self.entries = [
            Entry('you' if t.get('role') == 'user' else 'icarus', t.get('text') or '')
            for t in turns if isinstance(t, dict) and (t.get('text') or '').strip()
        ]
        self.tools.clear()
        self.greeted = True
        last = self.entries[-1] if self.entries else None
        self.suggestions = (split_suggestions(last.text)[1]
                            if last is not None and last.kind == 'icarus' else [])
        self.changed.emit()

    def state(self) -> dict[str, Any] | None:
        """What a qutebrowser session saves of this chat; None when empty.

        The serve-side session id is what lets the chat carry on after a
        restart (serve re-binds it to the tab's new DevTools id); the
        transcript tail is only there so the bar can show it again.
        """
        if not self.session and not self.entries:
            return None
        data: dict[str, Any] = {
            'session': self.session,
            'entries': [[e.kind, e.text] for e in self.entries[-SAVED_ENTRIES:]],
        }
        if self.model is not None:
            data['model'] = str(self.model)
        return data

    def restore(self, data: Any) -> None:
        """Take back a state() saved by a qutebrowser session.

        The data comes from a hand-editable YAML file, so anything malformed
        is skipped rather than raised.
        """
        if not isinstance(data, dict):
            return
        session = data.get('session')
        if isinstance(session, str):
            self.session = session
        model = data.get('model')
        if isinstance(model, str) and '/' in model:
            provider, model_id = model.split('/', 1)
            if provider and model_id:
                self.model = clientmod.Model(provider, model_id)
        entries = data.get('entries')
        if isinstance(entries, list):
            self.entries = [
                Entry(kind, text) for kind, text in
                (e for e in entries if isinstance(e, list) and len(e) == 2)
                if kind in _KINDS and isinstance(text, str)
            ]
        self.greeted = bool(self.entries or self.session)
        self.changed.emit()

    def last_reply(self) -> str:
        for entry in reversed(self.entries):
            if entry.kind == 'icarus':
                return split_suggestions(entry.text)[0]
        return ''

    def _append(self, kind: str, text: str) -> None:
        """Add text, extending the last entry when it is the same block."""
        if (kind in ('icarus', 'thinking') and self.entries and
                self.entries[-1].kind == kind):
            self.entries[-1].text += text
        else:
            self.entries.append(Entry(kind, text))

    def _tool(self, event: dict[str, Any]) -> None:
        phase, call = event.get('phase'), event.get('call') or ''
        name, text = event.get('name') or 'tool', event.get('text') or ''
        if phase == 'call':
            self.tools.append(ToolCall(call, name, text))
            del self.tools[:-MAX_TOOLS]
            self.phase, self.tool = TOOL, name
            return
        entry = next((t for t in reversed(self.tools) if call and t.call == call), None)
        if entry is None:  # an error with no call before it (a preflight refusal)
            entry = ToolCall(call, name, '')
            self.tools.append(entry)
            del self.tools[:-MAX_TOOLS]
        entry.result = text
        entry.state = 'error' if phase == 'error' else 'done'
        entry.ms = int(event.get('ms') or 0)
        if not any(t.state == 'running' for t in self.tools):
            self.phase, self.tool = WORKING, ''

    def _on_event(self, event: dict[str, Any]) -> None:
        kind = event.get('kind')
        if kind == 'session':
            self.session = event.get('session') or self.session
            provider, model = event.get('provider'), event.get('model')
            if provider and model:
                self.model = clientmod.Model(provider, model)
        elif kind == 'lane':
            self.lane = event.get('lane') or ''
        elif kind == 'reasoning':
            self.phase = WORKING
            self._append('thinking', event.get('text') or '')
        elif kind == 'delta':
            self.phase = RESPONDING
            self._append('icarus', event.get('text') or '')
        elif kind == 'tool':
            self._tool(event)
        elif kind == 'step':
            self._append('step', event.get('text') or '')
        elif kind == 'open':
            url = event.get('url') or ''
            if url:
                self._append('step', f'opening {url}')
                self.open_url.emit(url)
        elif kind == 'error':
            if self._steer is not None and event.get('text') == 'stopped':
                # A steer, not a failure: the web UI's "steered" partial.
                self._append('step', 'steered')
            else:
                self._append('error', event.get('text') or 'error')
        else:
            return
        self.changed.emit()

    def _on_finished(self) -> None:
        self._turn = None
        self.phase, self.tool = IDLE, ''
        self._settle()
        if self._steer is not None:
            text, url, title = self._steer
            self._steer = None
            self._send(text, url, title)
        elif self.queue:
            self._send(self.queue.pop(0), *self._page)

    def _settle(self) -> None:
        """Close the finished turn: open tool calls, its suggestions."""
        for entry in self.tools:
            if entry.state == 'running':
                entry.state, entry.result = 'error', entry.result or 'no result'
        last = self.entries[-1] if self.entries else None
        if last is not None and last.kind == 'icarus':
            self.suggestions = split_suggestions(last.text)[1]
        self.changed.emit()
