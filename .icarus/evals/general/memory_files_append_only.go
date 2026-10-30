//go:build ignore

// Command memory_files_append_only is the phase-0a general eval for the
// project-memory append seam (context_management spec, RULES: "append-only +
// gated removal … Eval general/memory_files_append_only").
//
// It drives the seam the way production does — through the icarus binary, whose
// /init registration step appends one JSONL row to .icarus/memory/logs.md per run — and
// asserts the four properties the tier depends on:
//
//   - every byte already on disk is still there, in the same order, after N
//     concurrent writers;
//   - the file grew by exactly N records, one per writer, so no append was lost
//     to a lost update and none was duplicated;
//   - every record is exactly one newline-terminated line of JSON, so a reader
//     can split on '\n' and a line can never lack its [ob1:<id>] pointer;
//   - rotation renames rather than edits: the retired generation (<file>.1) is
//     byte-identical to the content that was there before the rotation.
//
// Why N concurrent PROCESSES rather than N goroutines: the in-process case is
// already covered by internal/projectmem's own -race tests. What only an eval
// can exercise is the cross-process advisory flock, which is what actually
// protects a memory file when a REPL session, a loop and a git agent append at
// the same moment.
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
// directory), while `go run <file>.go` still compiles and runs it.
//
// Self-contained by design. This file is a scaffold template: it is copied into
// projects that do not have icarus on their import path, so it uses the
// standard library only and shares no helper package with its sibling eval.
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
	"sync"
	"time"
)

// evalName is the skill identifier carried on the result line.
const evalName = "memory_files_append_only"

// commandTimeout bounds every child process so a hung binary fails with a log
// line instead of wedging the suite.
const commandTimeout = 2 * time.Minute

// writers is the concurrency the eval drives the seam at. It is deliberately
// larger than the number of icarus surfaces that can append at once (REPL,
// web, loop runner, git agent), so a lock that only happens to hold under light
// contention still fails here.
const writers = 16

// maxFileSize mirrors internal/projectmem.MaxFileSize: the size past which a
// memory file is rotated to <file>.1. It is restated rather than imported
// because a stamped copy of this eval has no icarus packages available.
const maxFileSize = 5 << 20

// logsFile is the memory document /init appends its registration row to.
const logsFile = "logs.md"

// trackedMemoryDocs are the append-only documents git tracks. /init stamps
// their headers and nothing else may rewrite them, so a concurrent burst must
// leave them untouched in the index.
var trackedMemoryDocs = []string{".icarus/memory/lessons.md", ".icarus/memory/rules.md", ".icarus/memory/shas.md"}

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

	fmt.Printf("run  eval=%s bin=%s repo=%s writers=%d\n", evalName, bin, dir, writers)

	m := &mem{bin: bin, dir: dir, r: r}
	if !m.scaffold() {
		return r.emit()
	}
	if !m.concurrentAppends() {
		return r.emit()
	}
	m.trackedDocsUntouched()
	m.rotationPreservesPrefix()
	return r.emit()
}

// mem carries the eval's state across its case groups.
type mem struct {
	bin string
	dir string
	r   *recorder

	// before is .icarus/memory/logs.md as it stood before the concurrent burst.
	before []byte
}

// logsPath is the absolute path of the memory document under test.
func (m *mem) logsPath() string { return filepath.Join(m.dir, ".icarus", "memory", logsFile) }

// scaffold stamps the project once so the memory tier exists, then records the
// bytes the concurrent writers must not disturb.
func (m *mem) scaffold() bool {
	if code, err := m.init(); err != nil || code != 0 {
		m.r.bad("setup_scaffold", "icarus init: err=%v exit=%d", err, code)
		return false
	}
	if _, err := m.git("add", "-A"); err != nil {
		m.r.bad("setup_scaffold", "%v", err)
		return false
	}
	if _, err := m.git(
		"-c", "user.email=evals@icarus.local",
		"-c", "user.name=icarus evals",
		"-c", "commit.gpgsign=false",
		"commit", "-qm", "icarus init scaffold",
	); err != nil {
		m.r.bad("setup_scaffold", "%v", err)
		return false
	}
	blob, err := os.ReadFile(m.logsPath())
	if err != nil {
		m.r.bad("setup_scaffold", "reading .icarus/memory/%s: %v", logsFile, err)
		return false
	}
	if !bytes.HasPrefix(blob, []byte("## ")) {
		m.r.bad("setup_scaffold", ".icarus/memory/%s does not start with its stamped header", logsFile)
		return false
	}
	m.before = blob
	m.r.ok("setup_scaffold", ".icarus/memory/%s stamped, %d byte(s) before the burst", logsFile, len(blob))
	return true
}

// concurrentAppends is the eval's headline property: N processes append at the
// same time and the file behaves as an append-only log.
func (m *mem) concurrentAppends() bool {
	var wg sync.WaitGroup
	codes := make([]int, writers)
	errs := make([]error, writers)
	wg.Add(writers)
	for i := range writers {
		go func(i int) {
			defer wg.Done()
			codes[i], errs[i] = m.init()
		}(i)
	}
	wg.Wait()

	failed := 0
	var firstErr string
	for i := range writers {
		if errs[i] != nil || codes[i] != 0 {
			failed++
			if firstErr == "" {
				firstErr = fmt.Sprintf("writer %d: err=%v exit=%d", i, errs[i], codes[i])
			}
		}
	}
	if failed != 0 {
		m.r.bad("concurrent_appends_exit_zero", "%d/%d writers failed; first: %s", failed, writers, firstErr)
		return false
	}
	m.r.ok("concurrent_appends_exit_zero", "%d concurrent writers, all exit=0", writers)

	after, err := os.ReadFile(m.logsPath())
	if err != nil {
		m.r.bad("prefix_immutable", "reading .icarus/memory/%s: %v", logsFile, err)
		return false
	}
	added, intact := appendedSuffix(m.before, after)
	if !intact {
		m.r.bad("prefix_immutable", "the %d byte(s) present before the burst were rewritten (file is now %d byte(s))", len(m.before), len(after))
		return false
	}
	m.r.ok("prefix_immutable", "all %d prior byte(s) unchanged, +%d appended", len(m.before), len(added))

	lines, err := splitTerminatedLines(added)
	if err != nil {
		m.r.bad("every_line_terminated", "%v", err)
		return false
	}
	m.r.ok("every_line_terminated", "%d appended line(s), each newline-terminated, none blank", len(lines))

	if len(lines) != writers {
		m.r.bad("line_count_matches_writers", "%d appended record(s), want %d — an append was lost or duplicated", len(lines), writers)
	} else {
		m.r.ok("line_count_matches_writers", "%d record(s) for %d writer(s)", len(lines), writers)
	}

	if err := assertJSONRows(lines, "init"); err != nil {
		m.r.bad("every_record_is_one_json_row", "%v", err)
	} else {
		m.r.ok("every_record_is_one_json_row", "%d row(s), all event_type=init", len(lines))
	}

	m.before = after
	return true
}

// trackedDocsUntouched is the git-visible half of the append-only claim: the
// documents the spec calls append-only (lessons, rules, shas) are stamped once
// and no run may edit them, so a burst of writers leaves the index clean.
func (m *mem) trackedDocsUntouched() {
	args := append([]string{"status", "--porcelain", "--"}, trackedMemoryDocs...)
	porcelain, err := m.git(args...)
	switch {
	case err != nil:
		m.r.bad("tracked_docs_untouched", "%v", err)
	case strings.TrimSpace(porcelain) != "":
		m.r.bad("tracked_docs_untouched", "append-only documents changed: %s", oneLine(porcelain))
	default:
		m.r.ok("tracked_docs_untouched", "%s unchanged", strings.Join(trackedMemoryDocs, ","))
	}
}

// rotationPreservesPrefix drives the file past the rotation ceiling and asserts
// the retired generation is a rename, not an edit: <file>.1 must be
// byte-identical to what was on disk, and the fresh file must hold exactly the
// one record that triggered the rotation.
//
// The oversized body is written directly rather than through the seam: it is
// fixture content, and its byte-for-byte survival IS the assertion. Nothing in
// this case asks the seam to produce those bytes, only to move them intact.
func (m *mem) rotationPreservesPrefix() {
	filler := m.fillerBlock()
	if err := os.WriteFile(m.logsPath(), filler, 0o644); err != nil {
		m.r.bad("rotation_preserves_prefix", "writing the oversized fixture: %v", err)
		return
	}
	if code, err := m.init(); err != nil || code != 0 {
		m.r.bad("rotation_preserves_prefix", "icarus init: err=%v exit=%d", err, code)
		return
	}

	retired, err := os.ReadFile(m.logsPath() + ".1")
	if err != nil {
		m.r.bad("rotation_preserves_prefix", "reading the rotated generation: %v", err)
		return
	}
	if !bytes.Equal(filler, retired) {
		m.r.bad("rotation_preserves_prefix", "the rotated generation is %d byte(s), want the original %d — rotation edited the file", len(retired), len(filler))
		return
	}

	fresh, err := os.ReadFile(m.logsPath())
	if err != nil {
		m.r.bad("rotation_preserves_prefix", "reading the fresh generation: %v", err)
		return
	}
	lines, err := splitTerminatedLines(fresh)
	switch {
	case err != nil:
		m.r.bad("rotation_preserves_prefix", "fresh generation: %v", err)
	case len(lines) != 1:
		m.r.bad("rotation_preserves_prefix", "fresh generation holds %d record(s), want 1", len(lines))
	default:
		if err := assertJSONRows(lines, "init"); err != nil {
			m.r.bad("rotation_preserves_prefix", "fresh generation: %v", err)
			return
		}
		m.r.ok("rotation_preserves_prefix", "%d byte(s) moved to %s.1 unchanged, 1 record in the fresh file", len(retired), logsFile)
	}
}

// fillerBlock builds a body just past the rotation ceiling out of well-formed
// records, so the fixture is indistinguishable from a genuinely long log.
func (m *mem) fillerBlock() []byte {
	row, err := json.Marshal(map[string]any{
		"ts":         time.Now().UTC().Format(time.RFC3339),
		"event_type": "init",
		"detail":     strings.Repeat("x", 200),
	})
	if err != nil {
		// Unreachable: the map holds only strings.
		row = []byte(`{"event_type":"init"}`)
	}
	row = append(row, '\n')
	n := maxFileSize/len(row) + 1
	return bytes.Repeat(row, n)
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
	dir, err := os.MkdirTemp("", "icarus-eval-mem-*")
	if err != nil {
		return "", func() {}, fmt.Errorf("creating a temporary repository: %v", err)
	}
	cleanup := func() { _ = os.RemoveAll(dir) }
	// The scaffolder resolves its root through `git rev-parse --show-toplevel`,
	// which reports the symlink-free path; resolve here so the eval and the
	// binary agree on where the repository is.
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

// init runs one `icarus init` against the eval's repository and returns its
// exit code. An error means the process could not be RUN; a non-zero exit is
// data, not an error.
func (m *mem) init() (int, error) {
	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, m.bin, "init", "--dir", m.dir)
	cmd.Dir = m.dir
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	var exit *exec.ExitError
	switch {
	case err == nil:
		return 0, nil
	case errors.As(err, &exit):
		return exit.ExitCode(), nil
	default:
		return -1, fmt.Errorf("running %s init: %v: %s", m.bin, err, oneLine(stderr.String()))
	}
}

// git runs a git command in the eval's repository and returns its stdout.
func (m *mem) git(args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, "git", args...)
	cmd.Dir = m.dir
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

// splitTerminatedLines splits blob into records, refusing anything that is not
// exactly one newline-terminated, non-blank, carriage-return-free line.
func splitTerminatedLines(blob []byte) ([][]byte, error) {
	if len(blob) == 0 {
		return nil, nil
	}
	if blob[len(blob)-1] != '\n' {
		return nil, errors.New("the block is not newline-terminated: a writer left a torn record")
	}
	if bytes.ContainsRune(blob, '\r') {
		return nil, errors.New("a record carries a carriage return; records are one LF-terminated line")
	}
	lines := bytes.Split(bytes.TrimSuffix(blob, []byte("\n")), []byte("\n"))
	for i, line := range lines {
		if len(bytes.TrimSpace(line)) == 0 {
			return nil, fmt.Errorf("record %d is blank", i+1)
		}
	}
	return lines, nil
}

// assertJSONRows checks every record is one JSON object of the given
// event_type: a torn or interleaved write shows up here as a parse failure.
func assertJSONRows(lines [][]byte, eventType string) error {
	for i, line := range lines {
		var row struct {
			EventType string `json:"event_type"`
		}
		if err := json.Unmarshal(line, &row); err != nil {
			return fmt.Errorf("record %d is not one JSON row: %v", i+1, err)
		}
		if row.EventType != eventType {
			return fmt.Errorf("record %d has event_type=%q, want %q", i+1, row.EventType, eventType)
		}
	}
	return nil
}

// oneLine flattens output for a single-line log record.
func oneLine(s string) string {
	return strings.Join(strings.Fields(s), " ")
}
