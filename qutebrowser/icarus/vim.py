# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Vim NORMAL mode for the icarus bar's input.

Pure: :meth:`Normal.feed` takes one typed character with the buffer and
cursor (a str index) and returns an :class:`Outcome` saying what to do; the
bar applies it to its QPlainTextEdit (so edits land on Qt's undo stack).

Supported, with counts: motions ``h l j k w b e W B E 0 ^ $ gg G f F t T ;
,``; operators ``d c y`` over any motion, doubled for whole lines (``dd cc
yy``); ``x X s S r D C Y p P``; ``i a I A o O``; ``u`` (``<Ctrl+r>`` is the
bar's). The register is the bar's own, not the clipboard.
"""

import dataclasses


@dataclasses.dataclass
class Outcome:

    """What a NORMAL key does.

    Attributes:
        pos: The cursor afterwards (index into the buffer after ``edit``).
        edit: ``(start, end, text)``: replace buffer[start:end] with text.
        insert: Switch to INSERT.
        scroll: A vertical motion the input could not take: scroll the
                transcript instead (+1/-1 a step, +2/-2 to the end).
        history: ``'undo'`` for the editor's undo.
        handled: False when the key is not a NORMAL command.
    """

    pos: int | None = None
    edit: tuple[int, int, str] | None = None
    insert: bool = False
    scroll: int = 0
    history: str = ''
    handled: bool = True


_MOTIONS = set('hlwbeWBE0^$jkG')
_INCLUSIVE = set('eE$ft')
_LINEWISE = {'j', 'k', 'G', 'gg'}
_ALIASES = {'x': ('d', 'l'), 'X': ('d', 'h'), 's': ('c', 'l'),
            'S': ('c', 'c'), 'D': ('d', '$'), 'C': ('c', '$'),
            'Y': ('y', 'y')}


def line_start(text: str, pos: int) -> int:
    return text.rfind('\n', 0, pos) + 1


def line_end(text: str, pos: int) -> int:
    end = text.find('\n', pos)
    return len(text) if end == -1 else end


def first_nonblank(text: str, pos: int) -> int:
    pos, end = line_start(text, pos), line_end(text, pos)
    while pos < end and text[pos] in ' \t':
        pos += 1
    return pos


def clamp(text: str, pos: int) -> int:
    """A NORMAL cursor: on a character, never past the end of its line."""
    pos = max(0, min(pos, len(text)))
    start, end = line_start(text, pos), line_end(text, pos)
    return min(pos, max(start, end - 1))


def escape_pos(text: str, pos: int) -> int:
    """Where INSERT's cursor lands in NORMAL: one left, as vim does."""
    if pos > line_start(text, pos):
        pos -= 1
    return clamp(text, pos)


def _cls(ch: str, big: bool) -> int:
    if ch.isspace():
        return 0
    if big:
        return 1
    return 2 if ch.isalnum() or ch == '_' else 1


def _word_forward(text: str, pos: int, big: bool) -> int:
    n = len(text)
    if pos >= n:
        return n
    cls = _cls(text[pos], big)
    if cls:
        while pos < n and _cls(text[pos], big) == cls:
            pos += 1
    while pos < n and text[pos].isspace():
        pos += 1
    return pos


def _word_end(text: str, pos: int, big: bool) -> int:
    n = len(text)
    pos += 1
    while pos < n and text[pos].isspace():
        pos += 1
    if pos >= n:
        return max(n - 1, 0)
    cls = _cls(text[pos], big)
    while pos + 1 < n and _cls(text[pos + 1], big) == cls:
        pos += 1
    return pos


def _word_back(text: str, pos: int, big: bool) -> int:
    pos -= 1
    while pos > 0 and text[pos].isspace():
        pos -= 1
    if pos <= 0:
        return 0
    cls = _cls(text[pos], big)
    while pos > 0 and _cls(text[pos - 1], big) == cls:
        pos -= 1
    return pos


def _line_offset(text: str, pos: int, lines: int,
                 col: int | None = None) -> int | None:
    """Column ``col`` (else pos's) ``lines`` lines down (up when negative).

    None when there are not that many lines.
    """
    if col is None:
        col = pos - line_start(text, pos)
    start = line_start(text, pos)
    for _ in range(abs(lines)):
        if lines > 0:
            end = line_end(text, start)
            if end >= len(text):
                return None
            start = end + 1
        else:
            if start == 0:
                return None
            start = line_start(text, start - 1)
    return min(start + col, max(start, line_end(text, start) - 1))


def _find(text: str, pos: int, kind: str, ch: str, count: int) -> int | None:
    start, end = line_start(text, pos), line_end(text, pos)
    for _ in range(count):
        if kind in 'ft':
            nxt = text.find(ch, pos + 1, end)
            if nxt == -1:
                return None
            pos = nxt - 1 if kind == 't' else nxt
        else:
            prev = text.rfind(ch, start, pos)
            if prev == -1:
                return None
            pos = prev + 1 if kind == 'T' else prev
    return pos


class Normal:

    """The NORMAL mode key parser (counts, operators, pending keys)."""

    def __init__(self) -> None:
        self.register = ''
        self.linewise_register = False
        self._last_find: tuple[str, str] | None = None
        self._count = ''
        self._op = ''
        self._opcount = 1
        self._pending = ''
        #: The column j/k aim for across shorter lines (vim's curswant).
        self._want: int | None = None
        self._kept_want = False

    def forget_column(self) -> None:
        """The cursor moved outside the engine: j/k aim at its column."""
        self._want = None

    def reset(self) -> None:
        self._count = ''
        self._op = ''
        self._opcount = 1
        self._pending = ''

    @property
    def pending(self) -> bool:
        """Whether a count, operator or pending key is waiting."""
        return bool(self._count or self._op or self._pending)

    def _take_count(self) -> int:
        count = int(self._count or 1) * self._opcount
        self._count = ''
        return count

    def feed(self, key: str, text: str, pos: int) -> Outcome:
        """Handle one typed character on (text, pos)."""
        self._kept_want = False
        out = self._feed(key, text, pos)
        if not self._kept_want and not self.pending:
            self._want = None
        return out

    def _feed(self, key: str, text: str, pos: int) -> Outcome:
        if self._pending:
            pending, self._pending = self._pending, ''
            if pending == 'g':
                if key == 'g':
                    return self._motion('gg', text, pos)
                self.reset()
                return Outcome()
            if pending == 'r':
                count = self._take_count()
                self.reset()
                end = line_end(text, pos)
                if pos + count > end or key == '\n':
                    return Outcome()
                return Outcome(pos=pos + count - 1,
                               edit=(pos, pos + count, key * count))
            self._last_find = (pending, key)
            return self._motion(pending + key, text, pos)

        if key.isdigit() and (key != '0' or self._count):
            self._count += key
            return Outcome()
        if self._op and key == self._op:
            return self._lines(text, pos)
        if key in 'gfFtT' or (key == 'r' and not self._op):
            self._pending = key
            return Outcome()
        if key in ';,':
            if self._last_find is None:
                self.reset()
                return Outcome()
            kind, ch = self._last_find
            if key == ',':
                kind = kind.swapcase()
            return self._motion(kind + ch, text, pos)
        if key in _MOTIONS:
            return self._motion(key, text, pos)
        if self._op:
            self.reset()
            return Outcome()
        if key in 'dcy':
            self._opcount = int(self._count or 1)
            self._count = ''
            self._op = key
            return Outcome()
        if key in _ALIASES:
            op, motion = _ALIASES[key]
            self._opcount = int(self._count or 1)
            self._count = ''
            self._op = op
            if motion == op:
                return self._lines(text, pos)
            return self._motion(motion, text, pos)
        self._count = ''
        return self._simple(key, text, pos)

    def _simple(self, key: str, text: str, pos: int) -> Outcome:
        start, end = line_start(text, pos), line_end(text, pos)
        if key == 'i':
            return Outcome(pos=pos, insert=True)
        if key == 'a':
            return Outcome(pos=min(pos + 1, end), insert=True)
        if key == 'I':
            return Outcome(pos=first_nonblank(text, pos), insert=True)
        if key == 'A':
            return Outcome(pos=end, insert=True)
        if key == 'o':
            return Outcome(pos=end + 1, edit=(end, end, '\n'), insert=True)
        if key == 'O':
            return Outcome(pos=start, edit=(start, start, '\n'), insert=True)
        if key == 'u':
            return Outcome(history='undo')
        if key in 'pP' and self.register:
            reg = self.register
            if self.linewise_register:
                body = reg.rstrip('\n')
                if key == 'p':
                    return Outcome(pos=end + 1, edit=(end, end, '\n' + body))
                return Outcome(pos=start, edit=(start, start, body + '\n'))
            at = min(pos + 1, end) if key == 'p' else pos
            return Outcome(pos=at + len(reg) - 1, edit=(at, at, reg))
        if key in 'pP':
            return Outcome()
        return Outcome(handled=False)

    def _target(self, name: str, text: str, pos: int,
                count: int) -> int | None:
        start, end = line_start(text, pos), line_end(text, pos)
        if name == 'h':
            return max(start, pos - count)
        if name == 'l':
            return min(end, pos + count)
        if name == '0':
            return start
        if name == '^':
            return first_nonblank(text, pos)
        if name == '$':
            return max(start, end - 1)
        if name in 'wWbBeE':
            step = {'w': _word_forward, 'b': _word_back, 'e': _word_end}
            fn = step[name.lower()]
            for _ in range(count):
                pos = fn(text, pos, name.isupper())
            return pos
        if name in 'jk':
            if self._want is None:
                self._want = pos - start
            self._kept_want = True
            return _line_offset(text, pos, count if name == 'j' else -count,
                                self._want)
        if name == 'gg':
            return None if '\n' not in text else first_nonblank(text, 0)
        if name == 'G':
            return (None if '\n' not in text
                    else first_nonblank(text, len(text)))
        return _find(text, pos, name[0], name[1:], count)

    def _motion(self, name: str, text: str, pos: int) -> Outcome:
        count = self._take_count()
        op = self._op
        self.reset()
        if op == 'c' and name == 'w' and pos < len(text) and not text[pos].isspace():
            name = 'e'  # cw changes to the end of the word, as in vim
        target = self._target(name, text, pos, count)
        if target is None:
            if not op and name in _LINEWISE:
                return Outcome(scroll={'j': 1, 'k': -1, 'G': 2, 'gg': -2}[name])
            return Outcome()
        if not op:
            return Outcome(pos=clamp(text, target))
        if name in _LINEWISE:
            return self._apply_lines(op, text, min(pos, target), max(pos, target))
        if name in 'wW' and target > line_end(text, pos):
            target = line_end(text, pos)  # dw stops at the end of the line
        first, last = min(pos, target), max(pos, target)
        if name[0] in _INCLUSIVE and text[last:last + 1] not in ('', '\n'):
            last += 1
        return self._apply(op, text, first, last)

    def _lines(self, text: str, pos: int) -> Outcome:
        """A doubled operator (dd, cc, yy) over count lines."""
        count = self._take_count()
        op = self._op
        self.reset()
        last = pos
        for _ in range(count - 1):
            nxt = _line_offset(text, last, 1)
            if nxt is None:
                break
            last = nxt
        return self._apply_lines(op, text, pos, last)

    def _apply(self, op: str, text: str, start: int, end: int) -> Outcome:
        self.register = text[start:end]
        self.linewise_register = False
        if op == 'y':
            return Outcome(pos=clamp(text, start))
        new = text[:start] + text[end:]
        if op == 'c':
            return Outcome(pos=start, edit=(start, end, ''), insert=True)
        return Outcome(pos=clamp(new, start), edit=(start, end, ''))

    def _apply_lines(self, op: str, text: str, first: int,
                     last: int) -> Outcome:
        start, end = line_start(text, first), line_end(text, last)
        self.register = text[start:end] + '\n'
        self.linewise_register = True
        if op == 'y':
            return Outcome(pos=clamp(text, first))
        if op == 'c':
            return Outcome(pos=start, edit=(start, end, ''), insert=True)
        if end < len(text):
            cut = (start, end + 1)
        elif start > 0:
            cut = (start - 1, end)
        else:
            cut = (start, end)
        new = text[:cut[0]] + text[cut[1]:]
        return Outcome(pos=first_nonblank(new, min(cut[0], len(new))),
                       edit=(cut[0], cut[1], ''))
