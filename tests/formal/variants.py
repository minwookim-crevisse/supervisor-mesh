#!/usr/bin/env python3
"""Broken and fixed variants of tests/formal/WorkItemState.tla, and the TLC trace printer.

Run through the pinned toolchain:  scripts/formal-check.sh --variants [NAME-REGEX]
(formal-check.sh verifies Java and TLC first and passes them in FORMAL_JAVA / FORMAL_JAR).

Each variant is a copy of the spec with deliberate edits, checked against ONE target property in
one scenario cfg. Expected TLC outcome per kind:
  broken    FAIL  the edit breaks the guard the property depends on: TLC reports it violated
                  (the scenario's other properties are left out, so the target is what fails)
  nowit     PASS  the edit removes a witness: the probe that is normally violated now holds,
                  which is invariant 2 or 3 failing (an exit never taken, a state never reached)
  fixed     PASS  a hypothetical design change for a finding: the finding's property now holds,
                  and so does every property of the scenario (a fix may not break another one)
  reading   PASS  the other reading of an ambiguous row: the finding's property holds under it,
                  together with every property of the scenario
Each variant runs in its own dir inside formal-check.sh's work dir, one TLC job at a time. The dir
(spec copy, run.cfg, tlc.log, trace.json) is removed when the variant behaves as expected; it is
kept, and its path printed, when it does not, or for every variant with --keep (FORMAL_KEEP=1).

variants.py --trace TRACE.json prints a TLC JSON trace as numbered steps in the model's words;
variants.py --signature TRACE.json prints its last step, the form of the EXPECT "last step" column.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

SPEC = "WorkItemState"
HOME = os.path.expanduser("~")
V = []  # (name, kind, scenario, cfg line, edits, note)


def var(name, kind, scen, line, edits, note):
    V.append((name, kind, scen, line, edits, note))


# ---- invariant 1 ----
var("inv1-tableonly-close-from-verifying", "broken", "Core", "PROPERTY TableOnly",
    [('/\\ May(i, "close") /\\ StateOf(i) = "verified"',
      '/\\ May(i, "close") /\\ StateOf(i) \\in {"verified", "verifying"}')],
    "close also accepted from verifying (a row the table does not have)")
var("inv1-tableonly-reads-the-state-diff", "broken", "Core", "PROPERTY TableOnly",
    [('     IN Apply(i, rec, L("reject", "pm", i, rec, cls))',
      '     IN Apply(i, [rec EXCEPT !.state = "ready"], L("reject", "pm", i, rec, cls))')],
    "reject writes ready while its step label still says rework (the label is not the state diff)")
var("inv1-tableonly-split-child-not-draft", "broken", "Split", "PROPERTY TableOnlyExceptStructural",
    [('ELSE IF j \\in C THEN [EmptyItem EXCEPT !.state = "draft", !.parent = i]',
      'ELSE IF j \\in C THEN [EmptyItem EXCEPT !.state = "ready", !.parent = i]')],
    "split creates its children as ready, not as drafts (a write to another item than the step's)")
var("inv1-unlisted-not-refused", "broken", "TableWalk", "INVARIANT UnlistedRefused",
    [("       \\/ WithRefuse /\\ \\E v \\in AllVerbs, c \\in Callers : Refuse(i, v, c)\n",
      "       \\/ FALSE\n")],
    "refusal handling removed: an unlisted request is not refused at all")
var("inv1-unlisted-refusal-not-recorded", "broken", "TableWalk", "INVARIANT UnlistedRefused",
    [("  /\\ RefuseStep(c, v, i, NotInTable)",
      "  /\\ step' = StepRec(v, c, \"\", i, StateOf(i), StateOf(i), TRUE, NotInTable)\n"
      "  /\\ UNCHANGED <<item, version, config_hash, clock, events, live, pane, ghost, refusals>>")],
    "an unlisted request is refused but not recorded in the refusal log")
var("inv1-unlisted-refusal-writes", "broken", "TableWalk", "PROPERTY RefusedUnchanged",
    [("  /\\ RefuseStep(c, v, i, NotInTable)",
      "  /\\ Refused(c, v, \"\", i, NotInTable)\n"
      "  /\\ Write1(i, item[i])\n"
      "  /\\ UNCHANGED <<config_hash, clock, events, live, pane, ghost>>")],
    "the refusal of an unlisted request writes the record (bumps its version)")
var("inv1-guard-refusal-writes-evidence", "broken", "Core", "PROPERTY GuardRefusedUnchanged",
    [('     ELSE RefuseStep(Lead, "verify", i, "inputs_changed")',
      '     ELSE /\\ Refused(Lead, "verify", "", i, "inputs_changed")\n'
      '          /\\ item\' = [item EXCEPT ![i].evidence = [run |-> "attempt", commit |-> item[i].attempt.commit,\n'
      '                        contract_version |-> item[i].contract_version, config_hash |-> config_hash]]\n'
      '          /\\ UNCHANGED <<version, config_hash, clock, events, live, pane, ghost>>')],
    "a verify refused as inputs_changed still records evidence on the item")
# ---- invariant 2 ----
var("inv2-table-without-limit-exit", "broken", "Core", "INVARIANT TableExits",
    [('Row("ready",      "pm",            {"draft", "rework", Blocked("awaiting_user"), Blocked("limit")}, {"ready"})',
      'Row("ready",      "pm",            {"draft", "rework", Blocked("awaiting_user")}, {"ready"})')],
    "pre-D52 table: no row leaves blocked(limit) except cancel/override")
var("inv2-dead-reassign-refused", "broken", "Core", "INVARIANT NoDeadItem",
    [('IF item[i].failure.class = "defect"', 'IF FALSE')],
    "reassign always refused: a rework with class defect has no way back")
var("inv2-dead-pm-fail", "broken", "Core", "INVARIANT NoDeadItem",
    [('              ELSE cls = "environment" /\\ ~live[i]', '              ELSE FALSE')],
    "the pm fail row never applies: a running item whose lead is dead has no way out (D45)")
var("inv2-exit-limit-never-taken", "nowit", "Replies", "PROPERTY NoExit_blocked_limit",
    [("THEN IF FreshReplies(i) = {}", "THEN IF TRUE")],
    "ready from blocked(limit) always refused: the exit row exists but its guard can never hold")
# ---- invariant 3 ----
var("inv3-table-without-split-row", "broken", "Core", "INVARIANT TableReach",
    [('    Row("split",      "pm",            {"rework"}, {Blocked("children")}),\n', '')],
    "the split row removed from the table: blocked(children) is not reachable from draft")
var("inv3-children-unreachable", "nowit", "Split", "INVARIANT Unreached_blocked_children",
    [("       \\/ Split(i)\n", "       \\/ FALSE\n")],
    "split removed from Next: blocked(children) can no longer be reached")
# ---- invariants 4-11 ----
var("inv4-lead-fails-with-limit", "broken", "Core", "PROPERTY LimitOnlyByMesh",
    [('IF c = Lead THEN cls \\in {"defect", "conflict", "ambiguity", "environment"}',
      'IF c = Lead THEN cls \\in {"defect", "conflict", "ambiguity", "environment", "limit"}')],
    "pre-D54: a lead may pass class limit to fail")
var("inv5-retry-counted-at-reassign", "broken", "Core", "INVARIANT BoundedStarts",
    [("LET counted == item[i].attempt.id > 0 /\\ ~item[i].next_start_free",
      "LET counted == FALSE"),
     ('     THEN LET rec == [item[i] EXCEPT !.state = "ready", !.failure = NoFailure]\n'
      '          IN /\\ Apply(i, rec, L("reassign", "pm", i, rec, "class route: defect"))',
      '     THEN LET over == item[i].retry + 1 > RetryLimit\n'
      '              rec == IF over\n'
      '                     THEN [item[i] EXCEPT !.state = "blocked", !.blocked_reason = "limit", !.retry = @ + 1,\n'
      '                                          !.failure = [class |-> "limit", reason |-> "retry"]]\n'
      '                     ELSE [item[i] EXCEPT !.state = "ready", !.failure = NoFailure, !.retry = @ + 1]\n'
      '          IN /\\ Apply(i, rec, [L("reassign", "pm", i, rec, "pre-D53: reassign consumes 1 retry")\n'
      '                                EXCEPT !.limit_by = IF over THEN "mesh counter" ELSE ""])')],
    "pre-D53: retries counted per verb (reassign consumes one, ready from rework none)")
var("inv6-verify-ignores-config-hash", "broken", "Core", "INVARIANT VerifiedBound",
    [("InputsUnchanged(i) ==\n  /\\ item[i].attempt.config_hash = config_hash\n",
      "InputsUnchanged(i) ==\n  /\\ TRUE\n")],
    "verify no longer refuses inputs_changed when the verification config changed after start (D47)")
var("inv6-ready-records-stale-failing-before", "broken", "Core", "INVARIANT VerifiedBound",
    [("                        THEN [contract_version |-> item[i].contract_version + 1,",
      "                        THEN [contract_version |-> item[i].contract_version,"),
     ("  /\\ IF InputsUnchanged(i) /\\ FailingBeforeOk(i)", "  /\\ IF InputsUnchanged(i)")],
    "ready records failing_before for the old contract version and verify does not compare it")
var("inv6-cas-verify-write-ignores-inputs", "broken", "Cas", "INVARIANT VerifiedBound",
    [("     THEN IF InputsUnchanged(i) /\\ FailingBeforeOk(i)", "     THEN IF TRUE")],
    "the compare-and-set write of verify skips the inputs_changed check")
var("inv7-close-ignores-acks", "broken", "Replies", "PROPERTY CloseGates",
    [("/\\ IF NoUnacknowledged(i) /\\ ~item[i].revalidate", "/\\ IF ~item[i].revalidate")],
    "pre-D49: close does not wait for ack of attached replies")
var("inv8-start-accepts-verifying-dependency", "broken", "Deps", "PROPERTY StartNeedsDepends",
    [('DependsSatisfied(i) == \\A d \\in item[i].depends : item[d].state \\in {"verified", "closed"}',
      'DependsSatisfied(i) == \\A d \\in item[i].depends : item[d].state \\in {"verifying", "verified", "closed"}')],
    "start accepts a dependency that is still verifying")
var("inv8-ready-skips-cycle-check", "broken", "Deps", "INVARIANT DependsWellFormed",
    [("  /\\ i \\notin Closure(D, N)              \\* no cycle through the recorded depends",
      "  /\\ TRUE")],
    "pre-D62: ready does not refuse dependency cycles")
var("inv9-parent-closes-without-own-verification", "broken", "Split", "INVARIANT ParentOwnVerification",
    [('  /\\ LET rec == [item[i] EXCEPT !.state = "verifying", !.blocked_reason = "none",\n'
      '                   !.attempt = [@ EXCEPT !.commit = MergedCommit,',
      '  /\\ LET rec == [item[i] EXCEPT !.state = "closed", !.blocked_reason = "none",\n'
      '                   !.attempt = [@ EXCEPT !.commit = MergedCommit,')],
    "pre-D61: when every child is closed the mesh closes the parent directly")
var("inv10-limit-resume-accepts-old-reply", "broken", "Replies", "PROPERTY LimitReplyFresh",
    [("FreshReplies(i) == {r \\in item[i].replies : r.ts > item[i].limit_at /\\ ~r.consumed}",
      "FreshReplies(i) == {r \\in item[i].replies : ~r.consumed}")],
    "pre-D63: any attached unconsumed reply unlocks blocked(limit), also one older than the limit event")
var("inv10-single-use-dropped", "broken", "RepliesClockTies", "PROPERTY LimitReplySingleUse",
    [("FreshReplies(i) == {r \\in item[i].replies : r.ts > item[i].limit_at /\\ ~r.consumed}",
      "FreshReplies(i) == {r \\in item[i].replies : r.ts > item[i].limit_at}")],
    "N2: the consumed (single-use) check dropped; a lagging limit-entry time lets a consumed reply unlock again")
var("inv11-cas-compares-attempt-only", "broken", "Cas", "PROPERTY StaleNeverWritten",
    [("IF version[i] = pane[p].read_version /\\ item[i].attempt.id = pane[p].read_attempt",
      "IF item[i].attempt.id = pane[p].read_attempt")],
    "the verify write re-checks the attempt id but not the record version")
var("inv11-submit-ignores-attempt-id", "broken", "Cas", "PROPERTY StaleNeverWritten",
    [("  /\\ IF Owns(p, i)\n", "  /\\ IF TRUE\n")],
    "submit from a pane whose attempt is not the current one is accepted (older attempt ids, section 8)")
var("typeok-cancel-keeps-reason", "broken", "Core", "INVARIANT TypeOK",
    [('!.state = "cancelled", !.blocked_reason = "none"]', '!.state = "cancelled"]')],
    "cancel leaves a stale blocked_reason on the record")
# ---- gap probes ----
var("g2a-confirm-classless-rework-reassignable", "broken", "SplitConsumed", "PROPERTY ClasslessReworkStuck",
    [('IF item[i].failure.class = "defect"', 'IF item[i].failure.class \\in {"defect", "none"}')],
    "reassign also accepts a rework without class (shows ClasslessReworkStuck can fail)")
var("g2a-reading-mesh-sets-defect", "reading", "SplitConsumed", "INVARIANT NoDeadItem",
    [('         rec == [item[i] EXCEPT !.state = "rework", !.blocked_reason = "none",\n',
      '         rec == [item[i] EXCEPT !.state = "rework", !.blocked_reason = "none",\n'
      '                   !.failure = [class |-> "defect", reason |-> "none"],\n')],
    "section-5 reading: mesh (split parent) sets defect on the cancelled-child step, so reassign works")
var("g1-fixed-reject-routes-by-class", "fixed", "Replies", "PROPERTY RouteByClass",
    [('LET rec == [item[i] EXCEPT !.state = "rework", !.failure = [class |-> cls, reason |-> "none"]]',
      'LET rec == [item[i] EXCEPT !.state = RouteOf(cls).state, !.blocked_reason = RouteOf(cls).blocked_reason,\n'
      '                             !.failure = [class |-> cls, reason |-> "none"]]'),
     ('    Row("reject",     "pm",            {"verified"}, {"rework"}),',
      '    Row("reject",     "pm",            {"verified"}, {"rework", Blocked("awaiting_user")}),')],
    "hypothetical: reject routes by section 5 (ambiguity -> blocked(awaiting_user)), and its row says so")
var("g2b-fixed-siblings-cancelled", "fixed", "Split", "INVARIANT NoOrphanChildren",
    [('     IN Apply(i, rec, L("mesh", "mesh", i, rec, "a cancelled child"))',
      '     IN /\\ item\' = [j \\in Items |-> IF j = i THEN rec\n'
      '                    ELSE IF j \\in item[i].children /\\ item[j].state \\notin Terminal\n'
      '                         THEN [item[j] EXCEPT !.state = "cancelled", !.blocked_reason = "none"]\n'
      '                         ELSE item[j]]\n'
      '        /\\ version\' = [j \\in Items |->\n'
      '                         IF j = i \\/ (j \\in item[i].children /\\ item[j].state \\notin Terminal)\n'
      '                         THEN version[j] + 1 ELSE version[j]]\n'
      '        /\\ pane\' = DirtyFor(item[i].children \\cup {i}, "mesh")\n'
      '        /\\ step\' = L("mesh", "mesh", i, rec, "a cancelled child; remaining children cancelled")\n'
      '        /\\ UNCHANGED refusals'),
     ('        /\\ item\'[i] = [EmptyItem EXCEPT !.state = "draft", !.follows = k]\n',
      '        /\\ item\'[i] = [EmptyItem EXCEPT !.state = "draft", !.follows = k]\n'
      '     \\/ /\\ step\'.verb = "mesh" /\\ i \\in item[k].children\n'
      '        /\\ item\'[i] = [item[i] EXCEPT !.state = "cancelled", !.blocked_reason = "none"]\n')],
    "hypothetical: a cancelled child also cancels the remaining children (one possible disposition)")
var("g4-fixed-verify-clears-flag", "fixed", "Core", "INVARIANT FlagClearedByReverify",
    [('  [item[i] EXCEPT !.state = "verified",\n',
      '  [item[i] EXCEPT !.state = "verified", !.revalidate = FALSE,\n')],
    "hypothetical: a new verification clears the revalidate flag")
var("g5-fixed-follow-up-row", "fixed", "FollowUp", "PROPERTY TableOnly",
    [('"revalidate",\n             "override"}', '"revalidate",\n             "override", "(follow-up draft)"}'),
     ('    Row("override",   "operator",      Created, Created) }',
      '    Row("override",   "operator",      Created, Created),\n'
      '    Row("(follow-up draft)", "slack watcher", {"closed"}, {"unchanged"}),\n'
      '    Row("(follow-up draft)", "pm", {"closed"}, {"unchanged"}) }')],
    "hypothetical: the table gets follow-up rows for a reply and for a revalidate flag on a closed item")
var("g8-fixed-parent-verification-row", "fixed", "Split", "PROPERTY TableOnly",
    [('    Row("mesh",       "mesh",          {Blocked("children")}, {"verifying", "rework"}),\n',
      '    Row("mesh",       "mesh",          {Blocked("children")}, {"verifying", "rework"}),\n'
      '    Row("mesh",       "mesh",          {"verifying"}, {"verified", "rework"}),\n')],
    "hypothetical: the table gets a (mesh) row for the split parent's own verification")

EXPECTED = {"broken": "FAIL", "nowit": "PASS", "fixed": "PASS", "reading": "PASS"}


def sanitize(text):
    return text.replace(HOME, "~")


# ---------- trace printer ----------

def _states(path):
    with open(path) as fh:
        data = json.load(fh)
    return [s[1] for s in data.get("counterexample", {}).get("state", [])]


def _items(st):
    it = st.get("item")
    if isinstance(it, dict):
        return sorted(it.items(), key=lambda kv: int(kv[0]))
    return list(enumerate(it or [], 1))


def _name(rec):
    return rec["state"] if rec["state"] != "blocked" else "blocked(%s)" % rec["blocked_reason"]


def _state_of(st, i):
    for k, rec in _items(st):
        if str(k) == str(i):
            return _name(rec)
    return "?"


def _step_line(prev, st):
    """One step; from and to are the step item's states read off the two trace states, not its labels."""
    s = st.get("step", {})
    if s.get("kind") == "env":
        return s.get("verb", "")
    who = s.get("caller", "") + (" pane " + s["pane"] if s.get("pane") else "")
    if s.get("kind") == "read":
        return "%s %s item %s: read-check under the lock, commands run unlocked" % (
            who, s.get("verb"), s.get("item_id"))
    i = s.get("item_id")
    line = "%s %s item %s: %s -> %s" % (who, s.get("verb"), i, _state_of(prev, i), _state_of(st, i))
    if s.get("why"):
        line += " (" + s["why"] + ")"
    if s.get("refused"):
        line += " [REFUSED]"
    return line


def _final_lines(st):
    out = []
    for i, rec in _items(st):
        if rec.get("state") == "(none)":
            continue
        extra = []
        f = rec.get("failure", {})
        if f.get("class") != "none" or rec["state"] in ("rework", "blocked"):
            extra.append("failure=%s/%s" % (f.get("class"), f.get("reason")))
        for key in ("depends", "children", "parent", "follows", "retry", "environment_unblock"):
            if rec.get(key):
                extra.append("%s=%s" % (key, rec[key]))
        if rec.get("revalidate"):
            extra.append("revalidate flag set")
        if rec.get("replies"):
            extra.append("replies=%d" % len(rec["replies"]))
        out.append(" ".join(["    final: item %s %s" % (i, _name(rec))] + extra))
    return out


def _limit_at(st):
    """The recorded limit_at of the item a limit-entry step put in blocked(limit), else None."""
    s = st.get("step", {})
    if s.get("limit_by") != "mesh counter":
        return None
    for i, rec in _items(st):
        if str(i) == str(s.get("item_id")):
            return rec.get("limit_at")
    return None


def _times(st):
    """The times a step records (reply ts, event ts, limit_at against its entry tick)."""
    s = st.get("step", {})
    lt = _limit_at(st)
    if lt is not None:
        return " [limit_at recorded as %s, entry at tick %s]" % (lt, st.get("clock"))
    if s.get("verb") == "attach" and not s.get("refused"):
        return " [reply ts %s]" % st.get("clock")
    if s.get("kind") == "env" and s.get("verb", "").startswith("hook:"):
        return " [event ts %s]" % st.get("clock")
    if s.get("reply"):
        return " [reply ts %s]" % s["reply"]
    return ""


def trace_lines(path):
    """A TLC -dumpTrace json file as numbered steps in the model's words, then the final items.

    Times are printed only when a limit entry recorded a limit_at other than its tick (ClockTies)."""
    try:
        states = _states(path)
    except (OSError, ValueError) as exc:
        return ["(no trace: %s)" % exc]
    skew = any(_limit_at(st) not in (None, st.get("clock")) for st in states[1:])
    out = ["%2d. %s%s" % (k, _step_line(states[k - 1], st), _times(st) if skew else "")
           for k, st in enumerate(states[1:], 1)]
    if states:
        out += _final_lines(states[-1])
    return out


def signature(path):
    """The last step of a TLC trace in one line: caller verb item: from -> to, with from and to read off
    the last two states (not the step's labels); a refused step adds its reason; an env step its text."""
    try:
        states = _states(path)
    except (OSError, ValueError):
        return "(no trace)"
    if len(states) < 2:
        return "(initial state)"
    s = states[-1].get("step", {})
    if s.get("kind") == "env":
        return s.get("verb", "")
    i = s.get("item_id")
    line = "%s %s item %s: %s -> %s" % (s.get("caller"), s.get("verb"), i,
                                        _state_of(states[-2], i), _state_of(states[-1], i))
    if s.get("kind") == "read":
        line += " (read-check)"
    if s.get("refused"):
        line += " [REFUSED: %s]" % s.get("why")
    return line


# ---------- variant runs ----------

def scenario_cfg(spec_dir, scen, line, keep_props):
    with open(os.path.join(spec_dir, scen + ".cfg")) as fh:
        base = fh.read().splitlines()
    keep = base if keep_props else [x for x in base if not x.startswith(("INVARIANT", "PROPERTY"))]
    return "\n".join(keep + [line]) + "\n"


def apply_edits(name, text, edits):
    for old, new in edits:
        n = text.count(old)
        if n != 1:
            raise SystemExit("%s: edit target found %d times: %r" % (name, n, old[:60]))
        text = text.replace(old, new)
    return text


def run_tlc(java, opts, jar, vdir):
    cmd = [java] + opts.split() + [
        "-Djava.io.tmpdir=" + vdir, "-cp", jar, "tlc2.TLC", "-workers", "1",
        "-metadir", os.path.join(vdir, "meta"), "-noGenerateSpecTE",
        "-dumpTrace", "json", os.path.join(vdir, "trace.json"),
        "-config", "run.cfg", SPEC + ".tla"]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=vdir, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    shutil.rmtree(os.path.join(vdir, "meta"), ignore_errors=True)
    log = sanitize(proc.stdout + proc.stderr)
    with open(os.path.join(vdir, "tlc.log"), "w") as fh:
        fh.write(log)
    return proc.returncode, log, time.time() - t0


def outcome(rc, log, prop):
    """('FAIL'|'PASS'|'ERROR', detail) for one TLC run checked against one property."""
    viol = re.search(r"Error: (?:Invariant|Action property) (\w+) is violated", log)
    cviol = re.search(r"Error: The invariant of (\w+) is equal to FALSE", log)
    stats = re.findall(r"([\d,]+) states generated, ([\d,]+) distinct states found", log)
    gen, dis = stats[-1] if stats else ("-", "-")
    where = "TLC exit %d; %s generated, %s distinct" % (rc, gen, dis)
    if rc in (12, 13) and viol:
        return ("FAIL" if viol.group(1) == prop else "ERROR"), "%s violated (%s)" % (viol.group(1), where)
    if rc == 151 and cviol:
        return (("FAIL" if cviol.group(1) == prop else "ERROR"),
                "%s is FALSE at startup: constant-level table walk, no behaviour trace (%s)" % (cviol.group(1), where))
    if rc == 0 and "No error has been found" in log:
        return "PASS", "every property of the run holds (%s)" % where
    return "ERROR", where


def _on_term(signum, frame):
    # formal-check.sh sends TERM on Ctrl-C/TERM: leave through SystemExit so that
    # subprocess.run kills the running TLC JVM before this process exits
    raise SystemExit(128 + signum)


def run_variants(pattern):
    java, jar, work = (os.environ.get(k) for k in ("FORMAL_JAVA", "FORMAL_JAR", "FORMAL_WORK"))
    if not java or not jar or not work:
        raise SystemExit("variants.py: run it as  scripts/formal-check.sh --variants  (verifies the pinned tools)")
    signal.signal(signal.SIGTERM, _on_term)
    keep = os.environ.get("FORMAL_KEEP") == "1"
    opts = os.environ.get("FORMAL_JAVA_OPTS", "")
    spec_dir = os.environ.get("FORMAL_SPEC_DIR", os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(spec_dir, SPEC + ".tla")) as fh:
        spec = fh.read()
    chosen = [v for v in V if not pattern or re.search(pattern, v[0])]
    if not chosen:
        raise SystemExit("variants.py: no variant matches %r" % pattern)
    report = []
    for k, (name, kind, scen, line, edits, note) in enumerate(chosen, 1):
        prop = line.split()[1]
        vdir = os.path.join(work, name)
        os.makedirs(vdir)
        with open(os.path.join(vdir, SPEC + ".tla"), "w") as fh:
            fh.write(apply_edits(name, spec, edits))
        with open(os.path.join(vdir, "run.cfg"), "w") as fh:
            fh.write(scenario_cfg(spec_dir, scen, line, kind in ("fixed", "reading")))
        rc, log, secs = run_tlc(java, opts, jar, vdir)
        got, detail = outcome(rc, log, prop)
        want = EXPECTED[kind]
        ok = got == want
        print("[%d/%d] %s (%s) scenario=%s %s%s" % (
            k, len(chosen), name, kind, scen, line,
            " + the scenario's properties" if kind in ("fixed", "reading") else ""))
        print("    edit:     %s" % note)
        print("    expected: %s" % want)
        print("    actual:   %s: %s, %.0fs" % (got, detail, secs))
        print("    result:   %s" % ("as expected" if ok else "NOT AS EXPECTED"))
        if got != "PASS" and rc in (12, 13):
            print("    counterexample:")
            for s in trace_lines(os.path.join(vdir, "trace.json")):
                print("      " + s)
        if got == "ERROR":
            for x in [x for x in log.splitlines() if "rror" in x or "Exception" in x][:5]:
                print("      " + x)
        if not ok:
            print("    kept:     %s (run.cfg, tlc.log, trace.json)" % sanitize(vdir))
        elif not keep:
            shutil.rmtree(vdir)
        print(flush=True)
        report.append((name, kind, want, got, ok))
    print("%-46s %-9s %-8s %-6s %s" % ("VARIANT", "KIND", "EXPECTED", "ACTUAL", "RESULT"))
    for name, kind, want, got, ok in report:
        print("%-46s %-9s %-8s %-6s %s" % (name, kind, want, got, "ok" if ok else "NOT AS EXPECTED"))
    bad = [r for r in report if not r[4]]
    print("variants: %d run, %d as expected, %d not" % (len(report), len(report) - len(bad), len(bad)))
    return 1 if bad else 0


def main(argv):
    if len(argv) == 2 and argv[0] == "--trace":
        for s in trace_lines(argv[1]):
            print(s)
        return 0
    if len(argv) == 2 and argv[0] == "--signature":
        print(signature(argv[1]))
        return 0
    return run_variants(argv[0] if argv else None)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
