# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fish-style history ghost text for the bar's input.

The web UI's autosuggest.js (the twin of the REPL's historySuggester): the
suggestion is the tail of the most recent history entry that has the input
as a strict prefix, shown only with the cursor at the end of the input. The
ring holds the shared prompt-history log (icarus serve's GET
/api/prompt-history, the log every icarus surface writes) plus what this
browser sent since, newest last, capped at 1000; blank entries and
consecutive duplicates are dropped.
"""

#: HISTORY_MAX in autosuggest.js.
HISTORY_MAX = 1000


class HistoryRing:

    """The prompts the ghost text matches against."""

    def __init__(self) -> None:
        self._entries: list[str] = []
        self.seeded = False

    def __len__(self) -> int:
        return len(self._entries)

    def add(self, prompt: str) -> None:
        prompt = prompt.strip()
        if not prompt or (self._entries and self._entries[-1] == prompt):
            return
        self._entries.append(prompt)
        del self._entries[:-HISTORY_MAX]

    def seed(self, prompts: list[str]) -> None:
        """Put the shared log (oldest first) before what was added here."""
        mine, self._entries = self._entries, []
        for prompt in prompts:
            if isinstance(prompt, str):
                self.add(prompt)
        for prompt in mine:
            self.add(prompt)
        self.seeded = True

    def suggest(self, text: str) -> str:
        """The ghost suffix for text, or ''."""
        if not text.strip():
            return ''
        for entry in reversed(self._entries):
            if len(entry) > len(text) and entry.startswith(text):
                return entry[len(text):]
        return ''

    def recent(self) -> list[str]:
        """Newest first (the ↑ recall order)."""
        return list(reversed(self._entries))
