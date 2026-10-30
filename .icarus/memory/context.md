## Context

### 2026-10-30 — download backend / path completion (monty vs lewis)

**The browser backend is still lewis, and that is not why completion is missing.**

- monty is the **icarus backend** host (`/etc/hostname` = monty, cwd
  `/data/icarus/projects/qutebrowser`, `HOME=/data/icarus/home`).
- The **browser** still runs on lewis: `ps` on lewis shows two qutebrowser
  instances (`/home/n0ko/Programs/qutebrowser/.venv`, `HOME=/home/n0ko`) — the
  user's (DevTools 9223) and the agent-owned one (`--basedir
  ~/.icarus/browser/qute-agent`, 9222, via `launch.sh`).
- monty reaches them through an SSH tunnel started on monty:
  `ssh -N -T -L 127.0.0.1:9222:127.0.0.1:9222 -L 127.0.0.1:9223:127.0.0.1:9223 lewis`
  (pid 622312). `ss -ltnp` on monty shows both ports owned by that `ssh`.
- Downloads land on lewis at `/home/n0ko/Downloads` because
  `c.downloads.location.directory = None` → `downloads.download_dir()` →
  `standarddir.download()` → Qt `DownloadLocation` → `$HOME/Downloads` of the
  **browser host**. Confirmed by a recent file:
  `/home/n0ko/Downloads/fine_tuning_techs.png` (2026-10-29 09:01).
- `/data/icarus` does not exist on lewis; `/home` is separate local ext4 on
  each box (no NFS/fuse share). A monty path cannot be completed or written by
  the lewis browser process.

**The actual completion gap (fixed).** `:download` had **no completion
registered at all** — unlike `:open` (`qutebrowser/browser/commands.py:469`
has `@cmdutils.argument('url', completion=urlmodel.url)`), `download()` had
none for `dest`. The download prompt's `QFileSystemModel`
(`mainwindow/prompt.py`, `FilenamePrompt`) browses the browser host's local
filesystem, which is lewis.

Commit `096fe71c3` on monty's `main`:

- `qutebrowser/browser/commands.py` — `@cmdutils.argument('dest',
  completion=urlmodel.download_dest)`.
- `qutebrowser/completion/models/urlmodel.py` — new `download_dest()`:
  paths-only (never URLs/history), seeded with `downloads.download_dir` so an
  empty pattern offers the directory a download would have landed in.
- `qutebrowser/completion/models/filepathcategory.py` — `FilePathCategory`
  takes an optional `initial_path` callable used for the empty pattern instead
  of `completion.favorite_paths`.
- `tests/unit/completion/test_models.py` — 3 new tests. `80 passed` in that
  file; completion+prompt `328 passed, 1 skipped, 1 xfailed`;
  cmdutils/commands `283 passed, 1 skipped`; `browser/test_downloads.py`
  `26 passed`.

Shipped to lewis as branch `icarus-download-completion` via
`git push ssh://lewis/home/n0ko/Programs/qutebrowser main:refs/heads/icarus-download-completion`
(a direct push to `main` is refused there — it is checked out). **Not yet
checked out or restarted on lewis**; the running browser is still old code.

**Open question for the user (not resolvable in code).** "Browser backend is
monty" is an infra move, not a config edit — qutebrowser needs a Wayland/X
session and QtWebEngine. Options:

1. Keep the browser on lewis and point `c.downloads.location.directory` at a
   path lewis can write that monty also sees (needs a share — none exists
   today).
2. Actually run qutebrowser on monty (needs a display + QtWebEngine there).
   monty's repo `.venv` is a copy from lewis with broken shebangs
   (`.venv/bin/pytest` points at `/home/n0ko/Programs/qutebrowser/.venv`), so
   run tests with `.venv/bin/python -m pytest`.

### 2026-10-30 — shared download path is LIVE (option 1 shipped)

Downloads from the lewis browser now land on monty. Verified end-to-end: a
`:download` through the running instance produced `/data/icarus/incoming/test.md`
visible on monty within seconds.

Topology:

- monty exports `/data/icarus/incoming` (NFS, `/etc/exports`, `all_squash`
  anonuid/gid=1000, client `10.0.0.4` = lewis on wg0). `rpcbind` +
  `nfs-server` enabled at boot. Same dir bind-mounted at `/mnt/monty` on monty
  (fstab), so the path string is identical on both hosts.
- lewis mounts `10.0.0.1:/data/icarus/incoming` at `/mnt/monty` (fstab entry
  with `_netdev,x-systemd.automount`; `nfs-client.target` enabled).
- lewis `~/.config/qutebrowser/config.py` (backup `config.py.bak-20261030`):
  the stock `c.downloads.location.directory = None` at ~line 982 is commented
  out and the bottom of the file sets `c.downloads.location.directory =
  "/mnt/monty"`. Applied to the running instance with
  `qutebrowser ":config-source"` (needs `DISPLAY=:0
  XDG_RUNTIME_DIR=/run/user/1000` in the ssh env or the remote-control client
  aborts in Qt xcb init).

Traps found:

- **NFSv4 mounts fail between these two hosts** (`mount(2): Invalid argument`
  for every 4.x variant: 4.0/4.1/4.2, nocto/nofsc/norecovery/noxattr/
  nconnect/client_ip — all rejected; NFSv3 works fine, ~32 MB/s over wg0).
  Server-side `nfsdctl autostart` also errors `Invalid argument` on the 7.2.11
  kernel. If someone reboots and wonders why v4 "should work": use v3, or fix
  nfsd first. `rpc.nfsd 8` + `exportfs -r` is the manual bring-up.
- `c.downloads.location.prompt = True` means the prompt still pre-fills from
  the configured directory; `:download -d <path>` overrides explicitly and
  works regardless.
- The completion code (branch `icarus-download-completion` on lewis) is still
  NOT checked out on lewis — the running browser is old code, so `:download -d
  <TAB>` completion is not active yet. Needs a browser restart by the user.
