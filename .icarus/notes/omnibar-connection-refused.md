# Omnibar "connection refused" — findings (monty migration)

Session: 1792910279438452857-05352f90 (cwd /data/icarus/projects/qutebrowser, host monty).
Continuation after "continue, server restarted".

## Causal chain (each link verified this session)

1. qutebrowser's icarus bar POSTs every submit to `icarus.url` + `/bar/turn`
   (`qutebrowser/icarus/client.py:169`, `qutebrowser/mainwindow/icarusbar.py:800`).
   `icarus.url` default is `http://127.0.0.1:7654`
   (`qutebrowser/config/configdata.yml:1717`); no override in
   `~/.config/qutebrowser/config.py` or `autoconfig.yml`.

2. On monty the gateway serve (`icarus-gateway.service`,
   `ExecStart=/usr/local/bin/icarus serve --addr 10.0.0.1:7654`) answers on BOTH
   127.0.0.1:7654 and 10.0.0.1:7654 (loopback sidecar listener, deployed commit
   b4a3cca7, `/health` 200 on both). So the bar reaches serve — the refusal is
   not a bind-address problem any more (that was b4a3cca7's fix, live since
   02:43).

3. The deployed binary `/usr/local/bin/icarus` (commit b4a3cca7, built
   2026-10-25T06:40:52Z) does NOT contain the omnibar `/bar` routes:
   - `git ls-tree b4a3cca7 cmd/icarus/` has no `serve_bar*.go`.
   - `strings /usr/local/bin/icarus | rg 'bar/turn|omnibar-bar'` → no match.
   - Live probe: `POST /bar/turn` → 501
     `{"error":"not implemented in icarus","endpoint":"/bar/turn",
     "detail":"this opencode protocol endpoint is not yet exposed ..."}`
     (the ServeMux catch-all `notImplemented`, internal/web/rest.go).
     `GET /bar/sessions` → 200 but it is the SPA index.html (49348 B, same as
     `/`), i.e. the SPA fallback, not the bar handler.

4. The `/bar` feature exists only on `backup/coAST`
   (`/home/n0ko/backups/icarus.git`): 185bc9d8 (feat: /bar routes),
   9dc97d97, 0a1b9cb2 (merge), 4f1a3465 (bar session list/history),
   f7a3c09f + 21ba3fb0 (GET /editor), tip d528c741 (2026-10-24 13:12,
   "merge: deploy/omnibar"). `HEAD` (local coAST @ 9af6193f) and
   `origin/coAST` do NOT contain it; merge-base of HEAD and d528c741 is
   13d6868a. The deployed b4a3cca7 (fix/serve-loopback-sidecar, built from
   HEAD + loopback fix) therefore shipped without the bar.

5. Secondary: the bar's CDP side. `browser.cdp_url=http://127.0.0.1:9222` and
   `extra_cdp_urls=[...:9223]` are both dead on monty (curl rc=7, no qutebrowser
   process, no X display, `graphical-session.target` inactive). serve logs
   `overlay rescan inject failed ... 9222/9223 connection refused` every 5s.
   The bar's own turn does not need CDP for chat, but lane-B browser steps and
   page text do (`serve_bar.go` clientFor/pageText).

## Why the user sees "connection refused"

Two candidate surfaces, both consistent with the evidence:
- qutebrowser's own error text for a failed QNetworkReply
  (`client.py:151` emits `icarus serve ...` error frames from `reply.errorString()`),
  which reads "connection refused" when serve was down/restarting (it restarted
  repeatedly 02:28–02:43 during the loopback-sidecar deploy).
- With serve up, the bar now gets 501 "not implemented" instead — the remaining
  fault is the missing build, not the network.

## Remediation (not yet executed — needs operator go-ahead)

Rebuild serve from a tree that has the bar and redeploy:
1. Merge `d528c741` (backup/coAST, deploy/omnibar tip) into the deployment
   branch that produced b4a3cca7 (fix/serve-loopback-sidecar), or cherry-pick
   185bc9d8 + 9dc97d97 + 4f1a3465 + f7a3c09f onto it. d528c741 lacks the
   loopback sidecar fix (b4a3cca7), so a plain switch back to d528c741 would
   re-break child bridge/notify 403s — merge, don't checkout.
2. `just build` + install to /usr/local/bin/icarus, `systemctl --user restart
   icarus-gateway.service` (and the icarus-serve@76xx fleet if they serve bars).
3. Verify: `curl -s -X POST http://127.0.0.1:7654/bar/turn -d '{}'` returns the
   bar handler's 400/403 (loopback gate), not the 501 T12 body; `GET
   /bar/sessions` returns JSON, not index.html.
4. CDP: the bar works for chat without it; for lane B, qutebrowser must run with
   `--remote-debugging-port=9222` on monty (no display currently — the browser
   runs on lewis, whose CDP would have to be reachable and configured in
   `browser.cdp_url`/`extra_cdp_urls` instead).

## Done 2026-10-25 (operator: "remediate")

- Merged d528c741 into fix/serve-loopback-sidecar (worktree
  /home/n0ko/icarus-wt/loopback-sidecar) as 1ec2b32e, clean, not pushed.
- go build ./... ok; go test ./cmd/icarus -run Bar|Overlay|Loopback ok;
  go test ./internal/web ok; `just install` -> /usr/local/bin/icarus 1ec2b32e.
- Restarted icarus-gateway.service only. Verified: /health commit 1ec2b32e;
  POST /bar/turn bad body -> 400; GET /bar/sessions -> 200 JSON; from
  10.0.0.1 -> 403 loopback only; a real turn streamed session/lane/done with no
  error frame on llamacpp-monty-qwen1.
- Still stale (old binary): the icarus-serve@76xx fleet. They do not serve the bar.

## Still refused after the rebuild — third fault, fixed 2026-10-25

The qutebrowser that draws the bar runs on lewis (10.0.0.4), not monty. Its
`icarus.url` default `http://127.0.0.1:7654` hit lewis's own loopback, where
nothing listens since the serves moved to monty → the real "connection refused".
Pointing it at monty alone would have hit the bar's loopback-only gate (403).

- icarus `bead9cef` (fix/serve-loopback-sidecar): `barHandler.peerAllowed`
  admits loopback OR `web.serve.trusted_peer_cidrs` (already `10.0.0.4/32` on
  monty); parsed once in serve.go and shared with the (A) routes. Installed;
  gateway + the 18 `icarus-serve@76xx` (not 7670, this session) restarted on it.
- lewis `~/.config/qutebrowser/config.py` inside the `icarus.url` guard:
  `config.set("icarus.url", "http://10.0.0.1:7654")`. py_compile ok. Needs
  `:config-source` (or a restart) in the running qutebrowser — not done by me
  (rule: no IPC against the user's instance).
- Verified from lewis: `GET /bar/sessions` 200 JSON; `POST /bar/turn` streamed
  session → lane → delta "pong" → done; gateway logged
  `bar: admitted via trusted CIDR peer=10.0.0.4`.
- Not fixed: CDP. lewis's `QTWEBENGINE_REMOTE_DEBUGGING=127.0.0.1:9223` is
  loopback-bound, unreachable from monty, so lane-B page actions and page text
  stay off; chat works.

## Open question for the operator (superseded)

The bar work sits on the `backup` remote only — it was never merged to
origin/coAST. Confirm the intended merge target (coAST) before I touch the
branch, and whether the 76xx per-project serves should also be rebuilt.
