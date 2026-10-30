## RULES

### Live qutebrowser is off limits for probes
- Never run `:debug-pyeval`, `:session-save`, or any other IPC command against the user's running qutebrowser (default basedir, DevTools 127.0.0.1:9223). On 2026-10-22 a `~/.cache/qbprobe.py` pyeval loop hung the main thread at 100% CPU, dropped every window, and needed SIGKILL.
- Probes, benches and config experiments go to a throwaway instance: `python -m qutebrowser --basedir /tmp/<name> ...` (or `--temp-basedir`). The user's instance is only ever relaunched by the user or via `~/.local/bin/qutebrowser -r _autosave` when it is confirmed dead.

<!-- icky:builtin-skills v1 -->
### Builtin skills (shipped in the icarus binary; seeded by icky)
- `/em --614|--jr|--ecco|--gov <task>` sends mail from a named account under the client-facing formatting policy (one spec → text/plain + text/html). Invoke the skill; never hand-roll a send or guess a sender. No flag = stop and ask.
- `/browse <task>` drives the agent-owned browser through the browser tools.
