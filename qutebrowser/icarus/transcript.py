# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The bar's transcript and tools pane as HTML, after the icarus web UI.

transcript_html draws a conversation the way the web chat does: a ``you ❯``
row for each prompt, an ``icarus ❯`` row whose reply goes through
render.reply_html (XML envelopes, markdown, highlighted code), the model's
thinking as a dim block (live while it streams, folded to one line after),
laya steps as dim quoted lines, errors in red, then what is queued behind
the running turn and the reply's → suggestions as picks.

tools_html draws the tools pane (the web UI's toolcallpane.js): one entry
per call, ``▸`` with its arguments while it runs, ``◂`` with its result and
duration once answered, ``✗`` with the error; the newest few open, older
ones folded to their header.
"""

import html
import json

from qutebrowser.icarus import conversation, render

#: Lines of a live thinking block shown while it streams.
THINKING_TAIL = 8
#: Tool entries (newest) shown with their payloads; older ones fold.
TOOLS_OPEN = 3
#: Characters of one tool payload shown.
TOOL_TEXT_MAX = 2000


def _esc(text: str) -> str:
    return html.escape(text).replace('\n', '<br>')


def _who(label: str, color: str) -> str:
    return (f'<p style="margin:10px 0 2px 0; color:{color}; font-weight:bold;">'
            f'{label} ❯</p>')


def _thinking(text: str, live: bool, expanded: bool) -> str:
    lines = [line for line in text.strip().split('\n') if line.strip()]
    if not lines:
        return ''
    style = f'color:{render.DIM}; font-style:italic;'
    if live or expanded:
        shown = lines if expanded else lines[-THINKING_TAIL:]
        more = '' if expanded or len(lines) <= THINKING_TAIL else '…<br>'
        return (f'<p style="{style} margin:2px 0 2px 8px;">'
                f'{more}{_esc(chr(10).join(shown))}</p>')
    words = len(text.split())
    return (f'<p style="{style} margin:2px 0 2px 8px;">▸ thought · '
            f'{words} words</p>')


def transcript_html(conv: conversation.Conversation, *, selected: int = -1,
                    expand_thinking: bool = False, indent: int = 16) -> str:
    """A conversation as transcript HTML.

    selected highlights that suggestion (Tab cycles them in the bar).
    """
    out = []
    last = len(conv.entries) - 1
    for i, entry in enumerate(conv.entries):
        if entry.kind == 'you':
            out.append(_who('you', render.BLUE))
            out.append(f'<p style="margin:0 0 0 8px; color:{render.FG};">'
                       f'{_esc(entry.text)}</p>')
        elif entry.kind == 'icarus':
            if i == 0 or conv.entries[i - 1].kind not in ('thinking', 'step'):
                out.append(_who('icarus', render.PURPLE))
            body = conversation.split_suggestions(entry.text)[0]
            out.append(f'<div style="margin-left:8px;">'
                       f'{render.reply_html(body, indent)}</div>')
        elif entry.kind == 'thinking':
            if i == 0 or conv.entries[i - 1].kind == 'you':
                out.append(_who('icarus', render.PURPLE))
            live = conv.busy and i == last
            out.append(_thinking(entry.text, live, expand_thinking))
        elif entry.kind == 'step':
            if i == 0 or conv.entries[i - 1].kind == 'you':
                out.append(_who('icarus', render.PURPLE))
            lines = [line for line in entry.text.split('\n') if line.strip()]
            out.append(f'<p style="color:{render.DIM}; margin:2px 0 2px 8px;">'
                       + '<br>'.join(f'│ {html.escape(line)}' for line in lines)
                       + '</p>')
        else:
            out.append(f'<p style="color:{render.RED}; margin:2px 0 2px 8px;">'
                       f'<b>error:</b> {_esc(entry.text)}</p>')
    for text in conv.queue:
        out.append(f'<p style="color:{render.DIM}; margin:6px 0 0 0;">'
                   f'⧖ queued · {_esc(text)}</p>')
    if conv.suggestions and not conv.busy:
        picks = []
        for n, text in enumerate(conv.suggestions):
            if n == selected:
                style = (f'color:{render.CODE_BG}; background-color:{render.AMBER};'
                         ' font-weight:bold;')
            else:
                style = f'color:{render.AMBER};'
            picks.append(f'<span style="{style}">&nbsp;→ {html.escape(text)}&nbsp;</span>')
        out.append('<p style="margin:10px 0 0 8px;">' + '&nbsp; '.join(picks) +
                   f'<span style="color:{render.DIM};">&nbsp;&nbsp;Tab</span></p>')
    return ''.join(out)


def _pretty(text: str) -> tuple[str, str]:
    """A payload and its language: JSON pretty-printed, else plain."""
    try:
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False), 'json'
    except ValueError:
        return text, ''


def _clip(text: str) -> str:
    return text if len(text) <= TOOL_TEXT_MAX else text[:TOOL_TEXT_MAX] + '…'


def tools_html(tools: list[conversation.ToolCall]) -> str:
    """The tools pane: every call of the tab's turns, newest last."""
    if not tools:
        return (f'<p style="color:{render.DIM};">No tool calls yet. They show '
                'here live: arguments going out, results coming back.</p>')
    out = []
    first_open = len(tools) - TOOLS_OPEN
    for n, call in enumerate(tools):
        glyph, color = {'running': ('▸', render.AMBER),
                        'done': ('◂', render.GREEN),
                        'error': ('✗', render.RED)}[call.state]
        ms = f' · {call.ms} ms' if call.ms else ''
        out.append(f'<p style="margin:6px 0 2px 0;"><span style="color:{color};">'
                   f'{glyph}</span> <b style="color:{render.BLUE};">'
                   f'{html.escape(call.name)}</b><span style="color:{render.DIM};">'
                   f'{ms}</span></p>')
        if n < first_open:
            continue
        if call.args:
            text, lang = _pretty(call.args)
            out.append(f'<pre style="margin:0 0 0 12px;">'
                       f'{render.highlight(_clip(text), lang)}</pre>')
        if call.result:
            text, lang = _pretty(call.result)
            color = render.RED if call.state == 'error' else render.FG
            body = render.highlight(_clip(text), lang) if lang else html.escape(_clip(text))
            out.append(f'<pre style="margin:2px 0 0 12px; color:{color};">{body}</pre>')
    return ''.join(out)
