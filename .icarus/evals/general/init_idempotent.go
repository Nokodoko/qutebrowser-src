//go:build ignore

// Command init_idempotent is the phase-0a general eval for `icarus init`
// (context_management spec, INIT step 7: "a re-run is a no-op; --check prints
// drift and exits 1 … This is eval general/init_idempotent").
//
// It scaffolds a throwaway git repository, runs the scaffolder twice, and
// asserts the properties that make /init safe to run on a live tree:
//
//   - run 2 creates nothing, updates nothing, and leaves `git status
//     --porcelain` empty;
//   - `--check` exits 0 over a clean tree;
//   - prose the author added OUTSIDE the `<!-- init:begin -->` /
//     `<!-- init:end -->` markers survives both a plain re-run and `--force`,
//     and never trips the drift check;
//   - the init-owned region is restorable: tampering inside the markers is
//     repaired by `--force`, byte-for-byte, without touching the author's prose;
//   - drift IS reported (exit 1) when an init-owned header line is wrong or a
//     required file is missing.
//
// Double-compilation guard (spec EVALS, eval_2 rule). This eval NEVER shells
// out to `go build` or `just build`: `just evals` compiles the binary once, and
// the eval only executes the already-built artefact. The binary under test is
// resolved from $ICARUS_BIN, then ./bin/icarus, ../bin/icarus, then $PATH. If
// none of those exist the eval FAILS with a log line — it does not build one
// and it does not silently skip.
//
// Build tag. `//go:build ignore` keeps this file out of `go build ./...` and
// out of its sibling eval's package (two `package main` files cannot share a
// directory), while `go run <file>.go` still compiles and runs it. That is what
// lets the spec's flat `evals/general/eval_*.go` layout work, here and in every
// project this template is stamped into.
//
// Self-contained by design. This file is a scaffold template: it is copied into
// projects that do not have icarus on their import path, so it uses the
// standard library only and shares no helper package with its sibling eval.
// The small result/recorder block duplicated between the two general evals is
// the price of that portability, not an oversight.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// evalName is the skill identifier carried on the result line.
const evalName = "init_idempotent"

// commandTimeout bounds every child process so a hung binary fails with a log
// line instead of wedging the suite.
const commandTimeout = 2 * time.Minute

// Init marker constants, mirrored from internal/initscaffold. The eval speaks
// the on-disk contract, not the Go API, because a stamped copy has neither.
const (
	initBeginMarker = "<!-- init:begin -->"
	initEndMarker   = "<!-- init:end -->"
)

// authorProse is appended AFTER initEndMarker: text the scaffolder must never
// touch, under any flag.
const authorProse = "\n## Author notes\n\nHand written; /init must never rewrite this.\n"

func main() { os.Exit(run()) }

// ---------------------------------------------------------------------------
// result reporting (spec EVALS rule 2: every exit route emits one log line)
// ---------------------------------------------------------------------------

// result is the `skill.eval_pass`/`skill.eval_fail` payload, shaped like
// internal/hooks.SkillEvalPayload so a consumer can lift the line straight onto
// the bus.
type result struct {
	TS        string  `json:"ts"`
	Event     string  `json:"event"`
	Skill     string  `json:"skill"`
	SessionID string  `json:"session_id,omitempty"`
	PassRate  float64 `json:"pass_rate"`
	CaseCount int     `json:"case_count"`
	PassCount int     `json:"pass_count"`
	Detail    string  `json:"detail"`
}

// recorder accumulates per-case verdicts and emits the single result line.
type recorder struct {
	cases  int
	passed int
	firstF string
	// warn carries a caveat about the RUN itself rather than about a case --
	// today, that the binary under test was guessed instead of named. It rides
	// on the result line even when every case passes, because a green verdict
	// against the wrong binary is the failure mode worth shouting about.
	warn string
}

// ok records a passing case and prints its log line.
func (r *recorder) ok(name, format string, a ...any) {
	r.cases++
	r.passed++
	fmt.Printf("ok   case=%s %s\n", name, fmt.Sprintf(format, a...))
}

// bad records a failing case and prints its log line. The eval keeps going: a
// full verdict is more useful to the coding agent than the first stumble.
func (r *recorder) bad(name, format string, a ...any) {
	r.cases++
	detail := fmt.Sprintf(format, a...)
	if r.firstF == "" {
		r.firstF = name + ": " + detail
	}
	fmt.Printf("FAIL case=%s %s\n", name, detail)
}

// emit writes the result line and returns the process exit code.
func (r *recorder) emit() int {
	res := result{
		TS:        time.Now().UTC().Format(time.RFC3339),
		Event:     "skill.eval_pass",
		Skill:     evalName,
		SessionID: sessionID(),
		CaseCount: r.cases,
		PassCount: r.passed,
		Detail:    fmt.Sprintf("%d/%d cases passed", r.passed, r.cases),
	}
	if r.cases > 0 {
		res.PassRate = float64(r.passed) / float64(r.cases)
	}
	code := 0
	if r.passed != r.cases || r.cases == 0 {
		res.Event = "skill.eval_fail"
		res.Detail = r.firstF
		if r.cases == 0 {
			res.Detail = "no cases ran"
		}
		code = 1
	}
	if r.warn != "" {
		res.Detail = "warn: " + r.warn + "; " + res.Detail
	}
	line, err := json.Marshal(res)
	if err != nil {
		// Unreachable: result is a flat struct of marshalable fields.
		fmt.Printf("FAIL eval=%s reason=result_marshal detail=%v\n", evalName, err)
		return 1
	}
	fmt.Println(string(line))
	return code
}

// sessionID is the optional correlation id the CUSTOM dispatcher passes as
// argv[1] (spec EVALS), falling back to $ICARUS_SESSION_ID.
func sessionID() string {
	if len(os.Args) > 1 && os.Args[1] != "" {
		return os.Args[1]
	}
	return os.Getenv("ICARUS_SESSION_ID")
}

// ---------------------------------------------------------------------------
// the eval
// ---------------------------------------------------------------------------

func run() int {
	r := &recorder{}
	bin, warn, err := icarusBin()
	if err != nil {
		r.bad("setup", "%v", err)
		return r.emit()
	}
	if warn != "" {
		r.warn = warn
		fmt.Printf("warn eval=%s %s\n", evalName, warn)
	}

	dir, cleanup, err := tempGitRepo()
	if err != nil {
		r.bad("setup", "%v", err)
		return r.emit()
	}
	defer cleanup()

	fmt.Printf("run  eval=%s bin=%s repo=%s\n", evalName, bin, dir)

	tr := &tree{bin: bin, dir: dir, r: r}
	if !tr.firstRun() {
		return r.emit()
	}
	if !tr.commitScaffold() {
		return r.emit()
	}
	tr.rerunIsANoop()
	tr.checkIsClean()
	tr.authorProseSurvives()
	tr.driftIsReported()
	tr.restore()
	return r.emit()
}

// tree carries the eval's mutable state across its case groups.
type tree struct {
	bin string
	dir string
	r   *recorder

	// agentsCommitted is AGENTS.md as committed, restored by the final case.
	agentsCommitted []byte
	// logsAfterCommit is .icarus/memory/logs.md at commit time; the append-only
	// assertions compare against it.
	logsAfterCommit []byte
}

// firstRun covers INIT steps 1-5: the scaffolder stamps a greenfield tree.
func (t *tree) firstRun() bool {
	out, code, err := t.init("--json")
	if err != nil {
		t.r.bad("run1_stamps_scaffold", "running init: %v", err)
		return false
	}
	if code != 0 {
		t.r.bad("run1_stamps_scaffold", "exit=%d want=0", code)
		return false
	}
	env, err := parseEnvelope(out)
	if err != nil {
		t.r.bad("run1_stamps_scaffold", "%v", err)
		return false
	}
	created := env.count("created")
	if created == 0 {
		t.r.bad("run1_stamps_scaffold", "created=0 want>0 actions=%d", len(env.Actions))
		return false
	}
	t.r.ok("run1_stamps_scaffold", "mode=%s created=%d", env.Mode, created)
	return true
}

// commitScaffold tracks the stamped tree so `git status --porcelain` becomes a
// meaningful idempotence assertion: only the gitignored runtime artefacts
// (.icarus/memory/logs.md and friends) may change on a re-run.
func (t *tree) commitScaffold() bool {
	if _, err := t.git("add", "-A"); err != nil {
		t.r.bad("setup_commit", "%v", err)
		return false
	}
	if _, err := t.git(
		"-c", "user.email=evals@icarus.local",
		"-c", "user.name=icarus evals",
		"-c", "commit.gpgsign=false",
		"commit", "-qm", "icarus init scaffold",
	); err != nil {
		t.r.bad("setup_commit", "%v", err)
		return false
	}
	var err error
	if t.agentsCommitted, err = os.ReadFile(t.path("AGENTS.md")); err != nil {
		t.r.bad("setup_commit", "reading AGENTS.md: %v", err)
		return false
	}
	if t.logsAfterCommit, err = os.ReadFile(t.path(".icarus", "memory", "logs.md")); err != nil {
		t.r.bad("setup_commit", "reading .icarus/memory/logs.md: %v", err)
		return false
	}
	return true
}

// rerunIsANoop is the eval's headline property: INIT step 7's "a re-run is a
// no-op", measured three ways — the envelope, the work tree, and the one file
// a re-run is ALLOWED to grow.
func (t *tree) rerunIsANoop() {
	out, code, err := t.init("--json")
	switch {
	case err != nil:
		t.r.bad("run2_reports_no_changes", "running init: %v", err)
		return
	case code != 0:
		t.r.bad("run2_reports_no_changes", "exit=%d want=0", code)
		return
	}
	env, err := parseEnvelope(out)
	if err != nil {
		t.r.bad("run2_reports_no_changes", "%v", err)
		return
	}
	if n := env.count("created") + env.count("updated") + env.count("planned"); n != 0 {
		t.r.bad("run2_reports_no_changes", "%d file(s) written on the second run", n)
	} else {
		t.r.ok("run2_reports_no_changes", "exists=%d created=0 updated=0", env.count("exists"))
	}

	porcelain, err := t.git("status", "--porcelain")
	switch {
	case err != nil:
		t.r.bad("git_status_clean_after_rerun", "%v", err)
	case strings.TrimSpace(porcelain) != "":
		t.r.bad("git_status_clean_after_rerun", "work tree dirty: %s", oneLine(porcelain))
	default:
		t.r.ok("git_status_clean_after_rerun", "porcelain empty")
	}

	// .icarus/memory/logs.md is gitignored precisely because step 5 appends one
	// registration row per run: the file grows, the bytes already on disk do
	// not move.
	logs, err := os.ReadFile(t.path(".icarus", "memory", "logs.md"))
	if err != nil {
		t.r.bad("logs_append_only_on_rerun", "reading .icarus/memory/logs.md: %v", err)
		return
	}
	added, ok := appendedSuffix(t.logsAfterCommit, logs)
	if !ok {
		t.r.bad("logs_append_only_on_rerun", "the %d bytes present before the re-run were rewritten", len(t.logsAfterCommit))
		return
	}
	rows, err := jsonRows(added, "init")
	switch {
	case err != nil:
		t.r.bad("logs_append_only_on_rerun", "%v", err)
	case rows != 1:
		t.r.bad("logs_append_only_on_rerun", "appended %d row(s), want 1", rows)
	default:
		t.r.ok("logs_append_only_on_rerun", "prefix intact, +1 row")
	}
	t.logsAfterCommit = logs
}

// checkIsClean is INIT step 7's happy path: no drift over a freshly stamped
// tree, exit 0.
func (t *tree) checkIsClean() {
	_, code, err := t.init("--check")
	switch {
	case err != nil:
		t.r.bad("check_exit_zero", "%v", err)
	case code != 0:
		t.r.bad("check_exit_zero", "exit=%d want=0", code)
	default:
		t.r.ok("check_exit_zero", "no drift")
	}
}

// authorProseSurvives covers the marker contract from both sides: everything
// OUTSIDE the markers belongs to the author and is never rewritten, while the
// region INSIDE them is init-owned and a forced re-stamp restores it exactly.
func (t *tree) authorProseSurvives() {
	// Reach the --force fixed point first. The first --force in a repository
	// that has just become brownfield legitimately re-renders the region with
	// the "Existing instructions" link table (INIT step 4), so the baseline is
	// the SECOND force, not the initial stamp.
	if _, code, err := t.init("--force"); err != nil || code != 0 {
		t.r.bad("force_is_idempotent", "reaching the force fixed point: err=%v exit=%d", err, code)
		return
	}
	base, err := os.ReadFile(t.path("AGENTS.md"))
	if err != nil {
		t.r.bad("force_is_idempotent", "reading AGENTS.md: %v", err)
		return
	}
	if _, code, err := t.init("--force"); err != nil || code != 0 {
		t.r.bad("force_is_idempotent", "second force: err=%v exit=%d", err, code)
		return
	}
	again, err := os.ReadFile(t.path("AGENTS.md"))
	if err != nil {
		t.r.bad("force_is_idempotent", "re-reading AGENTS.md: %v", err)
		return
	}
	if !bytes.Equal(base, again) {
		t.r.bad("force_is_idempotent", "a repeated --force rewrote AGENTS.md (%d -> %d bytes)", len(base), len(again))
		return
	}
	t.r.ok("force_is_idempotent", "AGENTS.md byte-identical across two --force runs")

	// Author prose lives after the end marker.
	want := append(append([]byte(nil), base...), authorProse...)
	if err := os.WriteFile(t.path("AGENTS.md"), want, 0o644); err != nil {
		t.r.bad("author_region_preserved", "writing AGENTS.md: %v", err)
		return
	}
	if _, code, err := t.init(); err != nil || code != 0 {
		t.r.bad("author_region_preserved", "plain re-run: err=%v exit=%d", err, code)
		return
	}
	got, err := os.ReadFile(t.path("AGENTS.md"))
	if err != nil {
		t.r.bad("author_region_preserved", "reading AGENTS.md: %v", err)
		return
	}
	if !bytes.Equal(want, got) {
		t.r.bad("author_region_preserved", "a plain re-run rewrote AGENTS.md")
		return
	}
	if _, code, err := t.init("--check"); err != nil || code != 0 {
		t.r.bad("author_region_preserved", "--check flagged author prose: err=%v exit=%d", err, code)
		return
	}
	t.r.ok("author_region_preserved", "prose outside %s survives a re-run and --check", initEndMarker)

	// Now damage the init-owned region and let --force repair it.
	tampered, err := tamperMarkerRegion(want)
	if err != nil {
		t.r.bad("force_restores_init_region", "%v", err)
		return
	}
	if err := os.WriteFile(t.path("AGENTS.md"), tampered, 0o644); err != nil {
		t.r.bad("force_restores_init_region", "writing AGENTS.md: %v", err)
		return
	}
	if _, code, err := t.init("--force"); err != nil || code != 0 {
		t.r.bad("force_restores_init_region", "--force: err=%v exit=%d", err, code)
		return
	}
	got, err = os.ReadFile(t.path("AGENTS.md"))
	if err != nil {
		t.r.bad("force_restores_init_region", "reading AGENTS.md: %v", err)
		return
	}
	if !bytes.Equal(want, got) {
		t.r.bad("force_restores_init_region", "--force did not restore the marker region byte-for-byte")
		return
	}
	t.r.ok("force_restores_init_region", "region between the markers repaired, author prose untouched")
}

// driftIsReported is the other half of INIT step 7: --check exits 1 when an
// init-owned header line is wrong or a required file is gone. Each case
// restores what it damaged so the next one starts from a clean tree.
func (t *tree) driftIsReported() {
	rules := t.path(".icarus", "memory", "rules.md")
	original, err := os.ReadFile(rules)
	if err != nil {
		t.r.bad("drift_detected_on_header", "reading .icarus/memory/rules.md: %v", err)
		return
	}
	_, rest, _ := bytes.Cut(original, []byte("\n"))
	if err := os.WriteFile(rules, append([]byte("## rules\n"), rest...), 0o644); err != nil {
		t.r.bad("drift_detected_on_header", "writing .icarus/memory/rules.md: %v", err)
		return
	}
	out, code, err := t.init("--check", "--json")
	switch {
	case err != nil:
		t.r.bad("drift_detected_on_header", "%v", err)
	case code != 1:
		t.r.bad("drift_detected_on_header", "exit=%d want=1", code)
	default:
		env, perr := parseEnvelope(out)
		switch {
		case perr != nil:
			t.r.bad("drift_detected_on_header", "%v", perr)
		case !env.driftMentions(".icarus/memory/rules.md"):
			t.r.bad("drift_detected_on_header", "drift list does not name .icarus/memory/rules.md: %v", env.Drift)
		default:
			t.r.ok("drift_detected_on_header", "exit=1 drift=%q", oneLine(strings.Join(env.Drift, "; ")))
		}
	}
	if err := os.WriteFile(rules, original, 0o644); err != nil {
		t.r.bad("drift_detected_on_header", "restoring .icarus/memory/rules.md: %v", err)
		return
	}

	evals := t.path(".icarus", "evals", "justfile")
	body, err := os.ReadFile(evals)
	if err != nil {
		t.r.bad("drift_detected_on_missing_file", "reading .icarus/evals/justfile: %v", err)
		return
	}
	if err := os.Remove(evals); err != nil {
		t.r.bad("drift_detected_on_missing_file", "removing .icarus/evals/justfile: %v", err)
		return
	}
	out, code, err = t.init("--check", "--json")
	switch {
	case err != nil:
		t.r.bad("drift_detected_on_missing_file", "%v", err)
	case code != 1:
		t.r.bad("drift_detected_on_missing_file", "exit=%d want=1", code)
	default:
		env, perr := parseEnvelope(out)
		switch {
		case perr != nil:
			t.r.bad("drift_detected_on_missing_file", "%v", perr)
		case !env.driftMentions(".icarus/evals/justfile"):
			t.r.bad("drift_detected_on_missing_file", "drift list does not name .icarus/evals/justfile: %v", env.Drift)
		default:
			t.r.ok("drift_detected_on_missing_file", "exit=1 drift=%q", oneLine(strings.Join(env.Drift, "; ")))
		}
	}
	if err := os.WriteFile(evals, body, 0o644); err != nil {
		t.r.bad("drift_detected_on_missing_file", "restoring .icarus/evals/justfile: %v", err)
	}
}

// restore proves the eval left no residue: with the author's prose reverted the
// work tree is clean again and --check is quiet, so nothing above depended on a
// permanently damaged repository.
func (t *tree) restore() {
	if err := os.WriteFile(t.path("AGENTS.md"), t.agentsCommitted, 0o644); err != nil {
		t.r.bad("tree_restored", "restoring AGENTS.md: %v", err)
		return
	}
	porcelain, err := t.git("status", "--porcelain")
	switch {
	case err != nil:
		t.r.bad("tree_restored", "%v", err)
		return
	case strings.TrimSpace(porcelain) != "":
		t.r.bad("tree_restored", "work tree dirty: %s", oneLine(porcelain))
		return
	}
	if _, code, err := t.init("--check"); err != nil || code != 0 {
		t.r.bad("tree_restored", "--check: err=%v exit=%d want 0", err, code)
		return
	}
	t.r.ok("tree_restored", "porcelain empty, --check exit=0")
}

// ---------------------------------------------------------------------------
// envelope
// ---------------------------------------------------------------------------

// envelope is the subset of projectinit.Result this eval reads.
type envelope struct {
	Success bool   `json:"success"`
	Mode    string `json:"mode"`
	Actions []struct {
		Path   string `json:"path"`
		Status string `json:"status"`
	} `json:"actions"`
	Drift []string `json:"drift"`
}

// parseEnvelope decodes the --json acknowledgement.
func parseEnvelope(out []byte) (*envelope, error) {
	var env envelope
	if err := json.Unmarshal(out, &env); err != nil {
		return nil, fmt.Errorf("decoding the --json envelope: %v (got %q)", err, oneLine(string(out)))
	}
	return &env, nil
}

// count returns how many actions carry status.
func (e *envelope) count(status string) int {
	n := 0
	for _, a := range e.Actions {
		if a.Status == status {
			n++
		}
	}
	return n
}

// driftMentions reports whether any drift line names path.
func (e *envelope) driftMentions(path string) bool {
	for _, d := range e.Drift {
		if strings.Contains(d, path) {
			return true
		}
	}
	return false
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

// icarusBin resolves the ALREADY-BUILT binary under test. It never builds one:
// `just evals` performs the single compile (spec EVALS eval_2), and a second
// compile here would be exactly the double-compilation the rule forbids.
//
// It returns the path, and a warning whenever that path was GUESSED rather than
// named by $ICARUS_BIN. Only the environment variable is a statement of intent;
// bin/icarus and especially an `icarus` on $PATH may be an install from weeks
// ago, and an eval that passes green against a stale binary is worse than one
// that fails. The fallbacks stay -- a stamped project runs against its installed
// icarus -- but they announce themselves.
func icarusBin() (string, string, error) {
	if p := strings.TrimSpace(os.Getenv("ICARUS_BIN")); p != "" {
		abs, err := filepath.Abs(p)
		if err != nil {
			return "", "", fmt.Errorf("resolving $ICARUS_BIN %q: %v", p, err)
		}
		if !executable(abs) {
			return "", "", fmt.Errorf("$ICARUS_BIN %q is not an executable file", abs)
		}
		return abs, "", nil
	}
	for _, cand := range []string{"bin/icarus", "../bin/icarus"} {
		if abs, err := filepath.Abs(cand); err == nil && executable(abs) {
			return abs, fmt.Sprintf("ICARUS_BIN unset, using %s (guessed from the working directory; it may predate your changes)", abs), nil
		}
	}
	abs, err := exec.LookPath("icarus")
	if err != nil {
		return "", "", errors.New("no icarus binary: set $ICARUS_BIN, build one into bin/icarus, or install it on $PATH (this eval never builds one itself)")
	}
	return abs, fmt.Sprintf("ICARUS_BIN unset, using %s (found on $PATH; it may be a stale install, not the build under review)", abs), nil
}

// executable reports whether path is a regular file with an execute bit.
func executable(path string) bool {
	fi, err := os.Stat(path)
	return err == nil && fi.Mode().IsRegular() && fi.Mode().Perm()&0o111 != 0
}

// tempGitRepo creates an empty git work tree for the eval to scaffold.
func tempGitRepo() (string, func(), error) {
	dir, err := os.MkdirTemp("", "icarus-eval-init-*")
	if err != nil {
		return "", func() {}, fmt.Errorf("creating a temporary repository: %v", err)
	}
	cleanup := func() { _ = os.RemoveAll(dir) }
	// The scaffolder resolves its root through `git rev-parse --show-toplevel`,
	// which reports the symlink-free path; resolve here so byte comparisons of
	// the recorded root line up on hosts where /tmp is a link.
	resolved, err := filepath.EvalSymlinks(dir)
	if err != nil {
		cleanup()
		return "", func() {}, fmt.Errorf("resolving %q: %v", dir, err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, "git", "init", "-q")
	cmd.Dir = resolved
	if out, err := cmd.CombinedOutput(); err != nil {
		cleanup()
		return "", func() {}, fmt.Errorf("git init: %v: %s", err, oneLine(string(out)))
	}
	return resolved, cleanup, nil
}

// path joins parts onto the repository root.
func (t *tree) path(parts ...string) string {
	return filepath.Join(append([]string{t.dir}, parts...)...)
}

// init runs the binary under test with --dir pinned to the eval's repository.
// It returns stdout, the exit code, and an error only for a failure to RUN the
// process — a non-zero exit is data, not an error.
func (t *tree) init(args ...string) ([]byte, int, error) {
	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout)
	defer cancel()
	full := append([]string{"init", "--dir", t.dir}, args...)
	cmd := exec.CommandContext(ctx, t.bin, full...)
	cmd.Dir = t.dir
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	var exit *exec.ExitError
	switch {
	case err == nil:
		return stdout.Bytes(), 0, nil
	case errors.As(err, &exit):
		return stdout.Bytes(), exit.ExitCode(), nil
	default:
		return stdout.Bytes(), -1, fmt.Errorf("running %s %s: %v: %s", t.bin, strings.Join(full, " "), err, oneLine(stderr.String()))
	}
}

// git runs a git command in the eval's repository and returns its stdout.
func (t *tree) git(args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, "git", args...)
	cmd.Dir = t.dir
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return "", fmt.Errorf("git %s: %v: %s", strings.Join(args, " "), err, oneLine(stderr.String()))
	}
	return stdout.String(), nil
}

// appendedSuffix returns the bytes added after before, and reports whether
// before survived as an exact prefix. This is the append-only predicate.
func appendedSuffix(before, after []byte) ([]byte, bool) {
	if len(after) < len(before) || !bytes.Equal(after[:len(before)], before) {
		return nil, false
	}
	return after[len(before):], true
}

// jsonRows asserts every line of blob is a newline-terminated JSON object
// carrying the given event_type, and returns how many there were.
func jsonRows(blob []byte, eventType string) (int, error) {
	if len(blob) == 0 {
		return 0, nil
	}
	if blob[len(blob)-1] != '\n' {
		return 0, errors.New("the appended block is not newline-terminated")
	}
	lines := bytes.Split(bytes.TrimSuffix(blob, []byte("\n")), []byte("\n"))
	for i, line := range lines {
		if len(bytes.TrimSpace(line)) == 0 {
			return 0, fmt.Errorf("appended line %d is empty", i+1)
		}
		var row struct {
			EventType string `json:"event_type"`
		}
		if err := json.Unmarshal(line, &row); err != nil {
			return 0, fmt.Errorf("appended line %d is not one JSON row: %v", i+1, err)
		}
		if row.EventType != eventType {
			return 0, fmt.Errorf("appended line %d has event_type=%q, want %q", i+1, row.EventType, eventType)
		}
	}
	return len(lines), nil
}

// tamperMarkerRegion replaces everything between the init markers, leaving the
// bytes outside them alone.
func tamperMarkerRegion(doc []byte) ([]byte, error) {
	begin := bytes.Index(doc, []byte(initBeginMarker))
	end := bytes.Index(doc, []byte(initEndMarker))
	if begin < 0 || end < begin {
		return nil, fmt.Errorf("AGENTS.md has no %s/%s region", initBeginMarker, initEndMarker)
	}
	out := make([]byte, 0, len(doc))
	out = append(out, doc[:begin]...)
	out = append(out, initBeginMarker...)
	out = append(out, "\nTAMPERED: the init-owned region was overwritten.\n"...)
	return append(out, doc[end:]...), nil
}

// oneLine flattens output for a single-line log record.
func oneLine(s string) string {
	return strings.Join(strings.Fields(s), " ")
}
