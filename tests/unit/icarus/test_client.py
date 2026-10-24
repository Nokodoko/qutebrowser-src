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
        # Like the real Turn: an abort ends the stream with 'stopped'.
        self.aborted = True
        if self.on_event is not None:
            self.on_event({'kind': 'error', 'text': 'stopped'})
        if self.finished.fn is not None:
            self.finished.emit()


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

    def greet(self, body):
        turn = FakeTurn(dict(body, greet=True))
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
    assert c.busy

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


def test_conversation_state_round_trip(conv, monkeypatch):
    c, fake = conv
    assert c.state() is None
    monkeypatch.setattr(conversation, 'SAVED_ENTRIES', 2)
    c.session = 'S1'
    c.model = client.Model('llamacpp-monty-qwen1', 'qwen3.8-flash-next')
    c.entries = [conversation.Entry('you', 'a'), conversation.Entry('icarus', 'b'),
                 conversation.Entry('step', 'c')]
    state = c.state()
    assert state == {
        'session': 'S1',
        'model': 'llamacpp-monty-qwen1/qwen3.8-flash-next',
        'entries': [['icarus', 'b'], ['step', 'c']],
    }

    other = conversation.Conversation(fake, 'NEW-TARGET')
    other.restore(state)
    assert other.session == 'S1' and other.model == c.model
    assert [(e.kind, e.text) for e in other.entries] == [('icarus', 'b'), ('step', 'c')]
    other.submit('more')
    assert fake.turns[-1].body['session'] == 'S1'
    assert fake.turns[-1].body['tab'] == 'NEW-TARGET'


@pytest.mark.parametrize('data', [
    None, 'junk', [], {'session': 3, 'model': 'nomodel', 'entries': 'x'},
    {'entries': [['bogus', 'x'], ['you'], ['you', 5], 'x', ['you', 'ok']]},
])
def test_conversation_restore_tolerates_junk(conv, data):
    c, _fake = conv
    c.restore(data)
    assert c.session == '' and c.model is None
    assert [(e.kind, e.text) for e in c.entries] in ([], [('you', 'ok')])


def test_conversation_thinking_tools_and_phases(conv):
    c, fake = conv
    c.submit('read it')
    turn = fake.turns[0]
    assert c.phase == conversation.WORKING
    turn.on_event({'kind': 'reasoning', 'text': 'let me '})
    turn.on_event({'kind': 'reasoning', 'text': 'look'})
    turn.on_event({'kind': 'tool', 'phase': 'call', 'call': 'c1', 'name': 'read',
                   'text': '{"path": "a"}'})
    assert (c.phase, c.tool) == (conversation.TOOL, 'read')
    turn.on_event({'kind': 'tool', 'phase': 'result', 'call': 'c1', 'name': 'read',
                   'text': 'data', 'ms': 12})
    assert c.phase == conversation.WORKING
    turn.on_event({'kind': 'tool', 'phase': 'error', 'call': 'c9', 'name': 'bash',
                   'text': 'refused'})
    turn.on_event({'kind': 'tool', 'phase': 'call', 'call': 'c2', 'name': 'grep',
                   'text': '{}'})
    turn.on_event({'kind': 'delta', 'text': 'Answer.\n\n→ summarise it\n→ click Sign in\n'})
    assert c.phase == conversation.RESPONDING
    turn.finished.emit()

    assert c.phase == conversation.IDLE
    assert [(e.kind, e.text) for e in c.entries][:2] == [
        ('you', 'read it'), ('thinking', 'let me look')]
    assert [(t.name, t.state, t.result, t.ms) for t in c.tools] == [
        ('read', 'done', 'data', 12),
        ('bash', 'error', 'refused', 0),
        ('grep', 'error', 'no result', 0),  # never answered
    ]
    assert c.suggestions == ['summarise it', 'click Sign in']
    assert c.last_reply() == 'Answer.'


@pytest.mark.parametrize('text, body, suggestions', [
    ('plain', 'plain', []),
    ('a\n\n→ one\n-> two', 'a', ['one', 'two']),
    ('→ only', '', ['only']),
    ('a → inline\nb', 'a → inline\nb', []),
])
def test_split_suggestions(text, body, suggestions):
    assert conversation.split_suggestions(text) == (body, suggestions)


def test_conversation_greet_once(conv):
    c, fake = conv
    assert c.greet(url='https://x.test', title='X')
    assert fake.turns[0].body['greet'] and fake.turns[0].body['text'] == ''
    fake.turns[0].finished.emit()
    assert not c.greet()
    assert c.entries == []  # the greeting adds no 'you' entry


def test_conversation_steer(conv):
    c, fake = conv
    c.submit('first')
    fake.turns[0].on_event({'kind': 'delta', 'text': 'partial'})
    assert c.submit('actually, second', url='https://x.test')
    assert fake.turns[0].aborted
    assert len(fake.turns) == 2 and fake.turns[1].body['text'] == 'actually, second'
    assert [(e.kind, e.text) for e in c.entries] == [
        ('you', 'first'), ('icarus', 'partial'), ('step', 'steered'),
        ('you', 'actually, second')]


def test_conversation_queue(conv):
    c, fake = conv
    c.submit('one')
    c.submit('two', mid_turn='queue')
    c.submit('three', mid_turn='queue')
    c.submit('four', mid_turn='queue')
    assert c.unqueue() == 'four'
    assert c.queue == ['two', 'three'] and not fake.turns[0].aborted
    fake.turns[0].finished.emit()
    assert fake.turns[1].body['text'] == 'two' and c.queue == ['three']
    c.cancel()
    assert c.queue == [] and fake.turns[1].aborted
    assert len(fake.turns) == 2  # a cancel runs nothing after it
    assert c.unqueue() == ''


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
