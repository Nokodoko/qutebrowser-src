# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A tab's icarus conversation: its transcript, session and model."""

import dataclasses
from typing import Any

from qutebrowser.qt.core import QObject, pyqtSignal

from qutebrowser.icarus import client as clientmod


@dataclasses.dataclass
class Entry:

    """One block of the transcript.

    kind is ``you``, ``icarus``, ``step`` (a laya plan/step or tool line) or
    ``error``.
    """

    kind: str
    text: str


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
        self.session = ''
        self.model: clientmod.Model | None = None
        self.lane = ''
        self._turn: clientmod.Turn | None = None

    @property
    def busy(self) -> bool:
        return self._turn is not None

    def submit(self, text: str, *, url: str = '', title: str = '') -> bool:
        """Send text as the next turn. False when one is still running."""
        text = text.strip()
        if not text or self.busy:
            return False
        self.entries.append(Entry('you', text))
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
        self.lane = ''
        self._turn = self._client.turn(body)
        self._turn.event.connect(self._on_event)
        self._turn.finished.connect(self._on_finished)
        self.changed.emit()
        return True

    def stop(self) -> None:
        if self._turn is not None:
            self._turn.abort()

    def reset(self) -> None:
        """Start over: a new serve-side session on the next submit."""
        self.stop()
        self.entries.clear()
        self.session = ''
        self._client.forget(self.target)
        self.changed.emit()

    def last_reply(self) -> str:
        for entry in reversed(self.entries):
            if entry.kind == 'icarus':
                return entry.text
        return ''

    def _append(self, kind: str, text: str) -> None:
        """Add text, extending the last entry when it is the same answer."""
        if kind == 'icarus' and self.entries and self.entries[-1].kind == 'icarus':
            self.entries[-1].text += text
        else:
            self.entries.append(Entry(kind, text))

    def _on_event(self, event: dict[str, Any]) -> None:
        kind = event.get('kind')
        if kind == 'session':
            self.session = event.get('session') or self.session
            provider, model = event.get('provider'), event.get('model')
            if provider and model:
                self.model = clientmod.Model(provider, model)
        elif kind == 'lane':
            self.lane = event.get('lane') or ''
        elif kind == 'delta':
            self._append('icarus', event.get('text') or '')
        elif kind == 'step':
            self._append('step', event.get('text') or '')
        elif kind == 'open':
            url = event.get('url') or ''
            if url:
                self._append('step', f'opening {url}')
                self.open_url.emit(url)
        elif kind == 'error':
            self._append('error', event.get('text') or 'error')
        else:
            return
        self.changed.emit()

    def _on_finished(self) -> None:
        self._turn = None
        self.changed.emit()
