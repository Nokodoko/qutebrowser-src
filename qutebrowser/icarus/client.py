# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The HTTP side of the icarus bar: icarus serve's /bar routes.

POST /bar/turn runs one submit for one tab and streams the turn back as
server-sent events, one JSON object per ``data:`` line (see
cmd/icarus/serve_bar.go in icarus): ``session``, ``lane``, ``delta``,
``reasoning``, ``tool``, ``step``, ``open``, ``error`` and ``done``.
POST /bar/greet streams the same for the opening turn. Serve keeps one
conversation per tab, keyed by the tab's CDP endpoint and target id, and
drives the tab itself (laya) when the submit asks for a page action. GET /provider is the model
catalog the picker lists. Nothing here blocks the event loop.
"""

import dataclasses
import json
import os
from collections.abc import Callable
from typing import Any

from qutebrowser.qt.core import QObject, QUrl, pyqtSignal
from qutebrowser.qt.network import (QNetworkAccessManager, QNetworkReply,
                                    QNetworkRequest)

from qutebrowser.config import config
from qutebrowser.utils import log


@dataclasses.dataclass(frozen=True)
class Model:

    """One provider/model pair of the catalog."""

    provider: str
    model: str
    local: bool = False

    def __str__(self) -> str:
        return f'{self.provider}/{self.model}'


def parse_catalog(data: Any) -> tuple[list[Model], Model | None]:
    """The models of a GET /provider answer, and serve's default.

    Local models first (the fleet), then cloud, each sorted.
    """
    models = []
    for provider in (data or {}).get('all') or []:
        pid = provider.get('id')
        if not pid:
            continue
        for model in provider.get('models') or {}:
            models.append(Model(pid, model, bool(provider.get('local'))))
    models.sort(key=lambda m: (not m.local, str(m)))
    default = None
    for pid, model in ((data or {}).get('default') or {}).items():
        default = Model(pid, model)
    return models, default


def resolve_model(spec: str, models: list[Model]) -> Model | None:
    """Find ``provider/model`` or a bare model id (local providers first)."""
    spec = spec.strip()
    if not spec:
        return None
    for m in models:
        if str(m) == spec:
            return m
    for m in models:
        if m.model == spec:
            return m
    return None


def cdp_url() -> str:
    """This browser's own CDP endpoint, as icarus serve knows it."""
    url = config.val.icarus.cdp_url
    if url:
        return url
    debugging = os.environ.get('QTWEBENGINE_REMOTE_DEBUGGING', '')
    if not debugging:
        return ''
    if ':' not in debugging:
        debugging = '127.0.0.1:' + debugging
    return 'http://' + debugging


class SSEParser:

    """Split a server-sent event stream into the JSON of its data lines."""

    def __init__(self) -> None:
        self._buf = b''

    def feed(self, chunk: bytes) -> list[dict[str, Any]]:
        self._buf += chunk.replace(b'\r\n', b'\n')
        events = []
        while b'\n\n' in self._buf:
            block, self._buf = self._buf.split(b'\n\n', 1)
            data = [line[5:].lstrip() for line in block.split(b'\n')
                    if line.startswith(b'data:')]
            if not data:
                continue
            try:
                event = json.loads(b'\n'.join(data))
            except ValueError:
                log.misc.debug(f"icarus: bad event {block!r}")
                continue
            if isinstance(event, dict):
                events.append(event)
        return events


class Turn(QObject):

    """One running POST /bar/turn.

    Signals:
        event: One decoded frame of the stream.
        finished: The stream ended (done, an error, or an abort).
    """

    event = pyqtSignal(dict)
    finished = pyqtSignal()

    def __init__(self, reply: QNetworkReply, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._reply = reply
        self._parser = SSEParser()
        self._done = False
        reply.readyRead.connect(self._on_ready_read)
        reply.finished.connect(self._on_finished)

    def abort(self) -> None:
        """Cancel the turn: serve stops it when the request goes away."""
        if not self._done:
            self._reply.abort()

    def _on_ready_read(self) -> None:
        for event in self._parser.feed(bytes(self._reply.readAll())):
            self.event.emit(event)

    def _on_finished(self) -> None:
        self._on_ready_read()
        error = self._reply.error()
        if error == QNetworkReply.NetworkError.OperationCanceledError:
            self.event.emit({'kind': 'error', 'text': 'stopped'})
        elif error != QNetworkReply.NetworkError.NoError:
            self.event.emit({'kind': 'error',
                             'text': f"icarus serve: {self._reply.errorString()}"})
        self._done = True
        self._reply.deleteLater()
        self.finished.emit()


class Client(QObject):

    """Talks to icarus serve (icarus.url)."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        self._models: list[Model] = []
        self._default: Model | None = None

    def _url(self, path: str) -> QUrl:
        return QUrl(config.val.icarus.url.rstrip('/') + path)

    def _post(self, path: str, body: dict[str, Any]) -> QNetworkReply:
        request = QNetworkRequest(self._url(path))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader,
                          'application/json')
        # A turn streams for as long as it runs; no transfer timeout.
        request.setTransferTimeout(0)
        reply = self._nam.post(request, json.dumps(body).encode('utf-8'))
        assert reply is not None
        return reply

    def turn(self, body: dict[str, Any]) -> Turn:
        """Start a turn; body is serve_bar.go's barTurnRequest."""
        return Turn(self._post('/bar/turn', body), self)

    def greet(self, body: dict[str, Any]) -> Turn:
        """Start the opening turn on a tab (POST /bar/greet)."""
        return Turn(self._post('/bar/greet', body), self)

    def forget(self, tab: str) -> None:
        """Drop a tab's conversation on the serve side (POST /bar/new)."""
        reply = self._post('/bar/new', {'cdp': cdp_url(), 'tab': tab})
        reply.finished.connect(reply.deleteLater)

    @property
    def models(self) -> list[Model]:
        return self._models

    @property
    def default(self) -> Model | None:
        return self._default

    def get_json(self, path: str, callback: Callable[[Any], None]) -> None:
        """GET path in the background and hand callback its JSON (None on failure)."""
        request = QNetworkRequest(self._url(path))
        request.setTransferTimeout(15000)
        reply = self._nam.get(request)
        assert reply is not None

        def on_finished() -> None:
            reply.deleteLater()
            data = None
            if reply.error() == QNetworkReply.NetworkError.NoError:
                try:
                    data = json.loads(bytes(reply.readAll()))
                except ValueError:
                    pass
            else:
                log.misc.debug(f"icarus: GET {path} failed: {reply.errorString()}")
            callback(data)

        reply.finished.connect(on_finished)

    def fetch_history(self, callback: Callable[[list[str]], None]) -> None:
        """The shared prompt-history log, oldest first (GET /api/prompt-history)."""
        self.get_json(f'/api/prompt-history?limit={1000}',
                      lambda data: callback(data if isinstance(data, list) else []))

    def fetch_skills(self, callback: Callable[[list[tuple[str, str]]], None]) -> None:
        """The /name commands a bar submit can expand: serve's skills
        (GET /command, minus the REPL built-ins, which only the REPL runs)."""
        def on_data(data: Any) -> None:
            out = []
            for cmd in data if isinstance(data, list) else []:
                if not isinstance(cmd, dict) or cmd.get('source') == 'builtin':
                    continue
                name = cmd.get('name')
                if isinstance(name, str) and name.startswith('/'):
                    out.append((name, str(cmd.get('description') or '')))
            callback(sorted(out))
        self.get_json('/command', on_data)

    def fetch_models(self, callback: Callable[[list[Model]], None] | None = None) -> None:
        """Refresh the catalog in the background (cold serve takes seconds)."""
        request = QNetworkRequest(self._url('/provider'))
        request.setTransferTimeout(15000)
        reply = self._nam.get(request)
        assert reply is not None

        def on_finished() -> None:
            reply.deleteLater()
            if reply.error() != QNetworkReply.NetworkError.NoError:
                log.misc.debug(f"icarus: /provider failed: {reply.errorString()}")
            else:
                try:
                    data = json.loads(bytes(reply.readAll()))
                except ValueError:
                    data = None
                if data is not None:
                    self._models, self._default = parse_catalog(data)
            if callback is not None:
                callback(self._models)

        reply.finished.connect(on_finished)
