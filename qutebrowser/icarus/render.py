# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A model reply as HTML for the bar's transcript, the way the web UI shows it.

The web UI (icarus internal/web/static: xml.js, markdown.js, highlight.js)
renders three things, and so does this:

- XML envelopes: a line that is entirely ``<name ...>``, ``</name>`` or
  ``<name/>`` is a tag line in bold magenta (--xml-tag), and the text between
  an open and its close is indented 2ch, rendered as markdown. An envelope
  whose close has not streamed in yet takes amber, as live state does there.
  A tag inside a code fence is code.
- Markdown prose: headings, lists, tables, quotes, emphasis, links and inline
  code, through Qt's own markdown reader.
- Fenced code: highlighted with pygments in the web UI's tokyonight token
  colours (highlight.js / app.css .tok-*), unknown languages plain.

The output is the HTML subset QTextBrowser understands. Everything a model
wrote is escaped; no tag it writes becomes markup.
"""

import dataclasses
import html
import re

import pygments
import pygments.formatters
import pygments.lexers
import pygments.util
from pygments.style import Style
from pygments.token import (Comment, Keyword, Name, Number, Operator,
                            String, Token, Generic)
from qutebrowser.qt.gui import QTextDocument

#: tokyonight, as app.css :root has it.
FG = '#c0caf5'
DIM = '#565f89'
BLUE = '#7aa2f7'
PURPLE = '#bb9af7'
GREEN = '#9ece6a'
AMBER = '#e0af68'
RED = '#f7768e'
CYAN = '#7dcfff'
XML_TAG = '#ff66ff'
CODE_BG = '#1f2335'


class TokyoNight(Style):

    """pygments colours matching the web UI's .tok-* rules."""

    background_color = CODE_BG
    styles = {
        Token: FG,
        Comment: f'italic {DIM}',
        Keyword: PURPLE,
        Keyword.Type: CYAN,
        Keyword.Constant: AMBER,
        Name.Builtin: CYAN,
        Name.Class: CYAN,
        Name.Function: BLUE,
        Name.Variable: RED,
        Name.Decorator: PURPLE,
        Name.Tag: PURPLE,
        Name.Attribute: BLUE,
        Comment.Preproc: PURPLE,
        String: GREEN,
        Number: AMBER,
        Operator: CYAN,
        Generic.Deleted: RED,
        Generic.Inserted: GREEN,
        Generic.Heading: f'bold {BLUE}',
    }


_FENCE = re.compile(r'^(\s*)(`{3,}|~{3,})\s*([\w+#.-]*)')
_TAG_LINE = re.compile(r'^\s*<(/?)([A-Za-z][\w.:-]*)((?:\s+[^<>]*)?)\s*(/?)>\s*$')

_formatter = pygments.formatters.HtmlFormatter(
    style=TokyoNight, noclasses=True, nowrap=True)


@dataclasses.dataclass
class _Code:
    lang: str
    lines: list[str]
    closed: bool = False


@dataclasses.dataclass
class _Element:
    name: str
    attrs: str
    children: list = dataclasses.field(default_factory=list)
    closed: bool = False


def _parse(text: str) -> list:
    """Blocks of a reply: prose strings, _Code and _Element (nested)."""
    root = _Element('', '')
    stack = [root]
    prose: list[str] = []
    code: _Code | None = None
    fence = ''

    def flush() -> None:
        if prose:
            stack[-1].children.append('\n'.join(prose))
            prose.clear()

    for line in text.split('\n'):
        if code is not None:
            if line.strip().startswith(fence) and not line.strip()[len(fence):].strip():
                code.closed = True
                code = None
            else:
                want = '  ' * (len(stack) - 1)
                code.lines.append(line[len(want):] if line.startswith(want) else line)
            continue
        fm = _FENCE.match(line)
        if fm:
            flush()
            fence = fm.group(2)
            code = _Code(fm.group(3).lower(), [])
            stack[-1].children.append(code)
            continue
        tm = _TAG_LINE.match(line)
        if tm:
            closing, name, attrs, selfclose = tm.groups()
            if closing:
                if len(stack) > 1 and stack[-1].name == name:
                    flush()
                    stack[-1].closed = True
                    stack.pop()
                    continue
            elif selfclose:
                flush()
                stack[-1].children.append(_Element(name, attrs.strip(), closed=True))
                continue
            else:
                # A whole-line open always opens (xml.js's block form); one
                # whose close has not streamed in yet renders as live.
                flush()
                element = _Element(name, attrs.strip())
                stack[-1].children.append(element)
                stack.append(element)
                continue
            # A close that is not the innermost open stays text (xml.js's
            # balance rule).
        prose.append(_dedent(line, len(stack) - 1))
    flush()
    return root.children


def _dedent(line: str, depth: int) -> str:
    """Drop the 2 spaces per level a tagged section's text is written with."""
    want = '  ' * depth
    if line.startswith(want):
        return line[len(want):]
    return line.lstrip(' ') if depth else line


def _markdown(text: str) -> str:
    """Prose as HTML through Qt's markdown reader (body content only)."""
    if not text.strip():
        return ''
    doc = QTextDocument()
    doc.setMarkdown(text, QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
    out = doc.toHtml()
    start = out.find('<body')
    start = out.find('>', start) + 1 if start != -1 else 0
    end = out.rfind('</body>')
    return out[start:end if end != -1 else len(out)].strip()


def highlight(code: str, lang: str) -> str:
    """Code as coloured HTML spans (no wrapper), plain when lang is unknown."""
    try:
        lexer = pygments.lexers.get_lexer_by_name(lang) if lang else None
    except pygments.util.ClassNotFound:
        lexer = None
    if lexer is None:
        return html.escape(code)
    return pygments.highlight(code, lexer, _formatter).rstrip('\n')


def _code_html(block: _Code) -> str:
    body = highlight('\n'.join(block.lines), block.lang)
    rail = AMBER if not block.closed else DIM
    label = (f'<div style="color:{DIM}; font-size:small;">'
             f'{html.escape(block.lang)}</div>' if block.lang else '')
    return (f'<table width="100%" cellpadding="6" cellspacing="0" '
            f'style="background-color:{CODE_BG}; margin:4px 0;">'
            f'<tr><td width="2" style="background-color:{rail};"></td>'
            f'<td>{label}<pre style="margin:0; color:{FG};">{body}</pre></td></tr></table>')


def _tag(name: str, attrs: str, kind: str, streaming: bool) -> str:
    color = AMBER if streaming else XML_TAG
    inner = {'open': f'&lt;{name}', 'close': f'&lt;/{name}', 'self': f'&lt;{name}'}[kind]
    if attrs and kind != 'close':
        inner += ' ' + html.escape(attrs)
    inner += '/&gt;' if kind == 'self' else '&gt;'
    return f'<div style="color:{color}; font-weight:bold;">{inner}</div>'


def _blocks_html(blocks: list, indent: int) -> str:
    out = []
    for block in blocks:
        if isinstance(block, str):
            out.append(_markdown(block))
        elif isinstance(block, _Code):
            out.append(_code_html(block))
        elif not block.children and block.closed and not block.name:
            continue
        elif not block.children and block.closed:
            out.append(_tag(block.name, block.attrs, 'self', False))
        else:
            streaming = not block.closed
            out.append(_tag(block.name, block.attrs, 'open', streaming))
            out.append(f'<div style="margin-left:{indent}px;">'
                       f'{_blocks_html(block.children, indent)}</div>')
            if block.closed:
                out.append(_tag(block.name, '', 'close', False))
    return ''.join(out)


def reply_html(text: str, indent: int = 16) -> str:
    """A reply (markdown, XML envelopes, fenced code) as transcript HTML.

    indent is the 2ch of the web UI in pixels (two character widths).
    """
    return _blocks_html(_parse(text), indent)
