# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the icarus bar's serve client and conversation."""

import pytest

from qutebrowser.icarus import client, conversation
from qutebrowser.mainwindow import icarusbar


CATALOG = {
    'default': {'llamacpp-monty-qwen1': 'qwen3.8-flash-next'},
    'all': [
        {'id': 'anthropic', 'local': False,
         'models': {'claude-opus-5-5': {}, 'claude-fable-5': {}}},
        {'id': 'llamacpp-monty-qwen1', 'local': True,
         'models': {'qwen3.8-flash-next': {}}},
        {'id': 'dsv4.1017', 'local': True,
         'models': {'deepseek-v4.1-flash': {}}},
    ],
}


def test_parse_catalog_lists_the_fleet_first():
    models, default = client.parse_catalog(CATALOG)
    assert [str(m) for m in models] == [
        'dsv4.1017/deepseek-v4.1-flash',
        'llamacpp-monty-qwen1/qwen3.8-flash-next',
        'anthropic/claude-fable-5',
        'anthropic/claude-opus-5-5',
    ]
    assert default == client.Model('llamacpp-monty-qwen1', 'qwen3.8-flash-next')


@pytest.mark.parametrize('data', [None, {}, {'all': None}, {'all': [{}]}])
def test_parse_catalog_tolerates_junk(data):
    assert client.parse_catalog(data) == ([], None)


@pytest.mark.parametrize('spec, expected', [
    ('qwen3.8-flash-next', 'llamacpp-monty-qwen1/qwen3.8-flash-next'),
    ('anthropic/claude-opus-5-5', 'anthropic/claude-opus-5-5'),
    ('  claude-fable-5 ', 'anthropic/claude-fable-5'),
    ('nope', None),
    ('', None),
])
def test_resolve_model(spec, expected):
    models, _default = client.parse_catalog(CATALOG)
    found = client.resolve_model(spec, models)
    assert (str(found) if found else None) == expected


def test_sse_parser_splits_frames_across_reads():
    parser = client.SSEParser()
    assert parser.feed(b'data: {"kind": "ses') == []
    assert parser.feed(b'sion", "session": "s1"}\n\ndata: {"kind":"delta","text":"hi"}\r\n\r\n'
                       b': comment\n\ndata: not json\n\ndata: {"kind"') == [
        {'kind': 'session', 'session': 's1'},
        {'kind': 'delta', 'text': 'hi'},
    ]
    assert parser.feed(b':"done"}\n\n') == [{'kind': 'done'}]


@pytest.mark.parametrize('setting, env, expected', [
    ('', '127.0.0.1:9223', 'http://127.0.0.1:9223'),
    ('', '9223', 'http://127.0.0.1:9223'),
    ('', None, ''),
    ('http://127.0.0.1:9333', '127.0.0.1:9223', 'http://127.0.0.1:9333'),
])
def test_cdp_url(config_stub, monkeypatch, setting, env, expected):
    config_stub.val.icarus.cdp_url = setting
    if env is None:
        monkeypatch.delenv('QTWEBENGINE_REMOTE_DEBUGGING', raising=False)
    else:
        monkeypatch.setenv('QTWEBENGINE_REMOTE_DEBUGGING', env)
    assert client.cdp_url() == expected


class FakeTurn:

    def __init__(self, body):
        self.body = body
        self.aborted = False
        self.on_event = None
        self.on_finished = None
        self.event = self
        self.finished = _Signal()

    def connect(self, fn):  # self.event.connect
        self.on_event = fn

    def abort(self):
        self.aborted = True


class _Signal:

    def __init__(self):
        self.fn = None

    def connect(self, fn):
        self.fn = fn

    def emit(self):
        self.fn()


class FakeClient:

    def __init__(self):
        self.turns = []
        self.forgotten = []

    def turn(self, body):
        turn = FakeTurn(body)
        self.turns.append(turn)
        return turn

    def forget(self, tab):
        self.forgotten.append(tab)


@pytest.fixture
def conv(config_stub, monkeypatch):
    monkeypatch.setenv('QTWEBENGINE_REMOTE_DEBUGGING', '127.0.0.1:9223')
    config_stub.val.icarus.cdp_url = ''
    fake = FakeClient()
    c = conversation.Conversation(fake, 'TARGET')
    return c, fake


def test_conversation_turn(conv, qtbot):
    c, fake = conv
    c.model = client.Model('llamacpp-monty-qwen1', 'qwen3.8-flash-next')
    with qtbot.wait_signal(c.changed):
        assert c.submit('  read this page  ', url='https://x.test', title='X')
    turn = fake.turns[0]
    assert turn.body == {
        'cdp': 'http://127.0.0.1:9223', 'tab': 'TARGET', 'text': 'read this page',
        'session': '', 'url': 'https://x.test', 'title': 'X',
        'provider': 'llamacpp-monty-qwen1', 'model': 'qwen3.8-flash-next',
    }
    assert c.busy and not c.submit('again')

    turn.on_event({'kind': 'session', 'session': 'S1', 'provider': 'p', 'model': 'm'})
    turn.on_event({'kind': 'lane', 'lane': 'browser_act'})
    turn.on_event({'kind': 'step', 'text': 'plan: 1) click Sign in'})
    turn.on_event({'kind': 'delta', 'text': 'Hel'})
    turn.on_event({'kind': 'delta', 'text': 'lo'})
    turn.on_event({'kind': 'bogus'})
    turn.finished.emit()

    assert c.session == 'S1' and c.model == client.Model('p', 'm')
    assert c.lane == 'browser_act' and not c.busy
    assert [(e.kind, e.text) for e in c.entries] == [
        ('you', 'read this page'),
        ('step', 'plan: 1) click Sign in'),
        ('icarus', 'Hello'),
    ]
    assert c.last_reply() == 'Hello'


def test_conversation_open_and_reset(conv, qtbot):
    c, fake = conv
    c.submit('?weather')
    with qtbot.wait_signal(c.open_url) as blocker:
        fake.turns[0].on_event({'kind': 'open', 'url': 'https://search.test/?q=weather'})
    assert blocker.args == ['https://search.test/?q=weather']
    c.session = 'S1'
    c.reset()
    assert fake.turns[0].aborted
    assert fake.forgotten == ['TARGET']
    assert c.entries == [] and c.session == ''


def test_render_transcript():
    entries = [
        conversation.Entry('you', 'click sign in'),
        conversation.Entry('step', 'plan: 1) click Sign in\n\nstep 1 click #login'),
        conversation.Entry('icarus', 'Done.'),
        conversation.Entry('error', 'turn failed'),
    ]
    assert icarusbar.render(entries) == (
        '**▸ you**\n\nclick sign in\n\n'
        '> plan: 1) click Sign in  \n> step 1 click #login  \n\n'
        '**▸ icarus**\n\nDone.\n\n'
        '> **error:** turn failed')
