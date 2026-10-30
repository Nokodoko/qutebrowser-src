---
name: webhook-setup
description: Wire this repository's GitHub issues + pull_request deliveries to the rayne callback server (determinism D0 — runs as `icarus init` step 6, no model call)
tools:
  - shell
one_call_per_turn: true
---

# webhook-setup

Reconcile this repository's GitHub webhook so a collaborator's issue or pull
request starts work without a human dispatching it.

## This skill is code, not a prompt

`webhook-setup` is determinism class **D0**: the contract is implemented in Go
(`internal/skills/webhooksetup`) and runs as **step 6 of `icarus init`**. This
file is the catalogue entry and the operator's reference — it is **not** the
execution path.

**Do not improvise `gh` calls from this document.** Creating a hook by hand
skips the idempotency key, the non-shrinking event rule and the secret handling
below, and a mistake there silently breaks every delivery. Run the command:

```sh
icarus init                 # wires it, as step 6
icarus init --dry-run       # reads only; prints the exact gh argv it would issue
icarus init --no-webhooks   # skips the step entirely
```

## Contract

| Input | Default |
|---|---|
| repo | `owner/name` from `git remote get-url origin`; a non-`github.com` origin is a **skip**, not an error |
| callback | `https://webhooks.n0kos.com/v1/webhooks/icarus` (PLANNED — pending the rayne route) |
| events | `issues,pull_request` |
| secret | `GITHUB_WEBHOOK_SECRET_<OWNER>_<NAME>`, else `GITHUB_WEBHOOK_SECRET`, else generated |

1. **List** hooks and find the one whose `config.url` is the callback. That URL
   is the identity — never a name, never an index.
2. **Create or update.** Absent ⇒ create. Present and matching ⇒ `unchanged`,
   with **no API write and no ping** (a ping is a delivery). Present and drifted
   ⇒ patch.
3. **Verify** with a ping.
4. **Roll back** — delete — only a hook *this run* created whose ping failed.

## Rules that are easy to get wrong

- **`events` is declarative.** GitHub REPLACES the set with whatever is sent, so
  an update with no explicit event list sends the **union** of the hook's
  current events and the defaults. Never unsubscribe a hook someone else shares.
- **A `PATCH` that includes `config` must include the secret.** Omitting it
  CLEARS the stored secret and the hook starts delivering unsigned. If no secret
  is held, send no `config` block at all — never a freshly generated one, which
  is just as destructive and much harder to notice.
- **Export the secret before the first run.** A generated secret is not
  persisted anywhere, so the receiver cannot verify signatures.

Reference: `docs/configuration.md` → *`webhook-setup` — the step-6 skill*, and
Recipe 44.
