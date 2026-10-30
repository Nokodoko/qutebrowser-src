## AGENTS

<!-- init:begin -->

To (re)initialize this project, run `icarus init` (add `--no-git` outside a git
repo). Inside an agent harness the command is `/int`: Claude Code and codex ship
a built-in `/init` that cannot be shadowed, so `/int` is the spelling that works
everywhere. It takes the same flags (`--check`, `--no-git`, `--no-webhooks`,
`--json`).

### Project

- name · goal (one paragraph — the user writes it; /init leaves `TODO`) · repo · default branch
- hosts this project runs on (from AI FLEET HOSTS) · local model pins

### Memory system

- tiers: T0 session (icarus JSONL) · T1 project (`memory/*.md`, git-tracked, append-only) ·
  T2 long-term (ob1, cross-project). What each file holds, who writes it, the record format.
- retrieval: Ra auto-injects ≤ 3 memories per turn; `memory.recall` for explicit lookups.
- gates: removal from lessons/rules/shas needs an approved `memory.forget` (RULES).

### Roster

- table: agent → role → tools → model pin → skills (from AGENTS)

### Rules

- pointer to `memory/rules.md` (coding style, data types, max_depth=3, composability)

### Existing instructions (brownfield only)

- table: CLAUDE.md / AGENTS.md / agentic_instructions.md / SPEC/ → path · size · last commit

| Path | Size | Last commit |
|------|------|-------------|
| `.icarus/memory/` | - | 7a288cf5c |
<!-- init:end -->
