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
(The d65-d81 variants restore the behaviour before decisions D65-D76 and D81, one to three per decision.)
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
      '     IN Apply(i, [rec EXCEPT !.state = "ready", !.blocked_reason = "none"], L("reject", "pm", i, rec, cls))')],
    "reject writes ready while its step label still says rework (the label is not the state diff)")
var("inv1-tableonly-split-child-not-draft", "broken", "Split", "PROPERTY TableOnly",
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
      "  /\\ UNCHANGED <<item, version, config_hash, seq, events, live, pane, ghost, refusals>>")],
    "an unlisted request is refused but not recorded in the refusal log")
var("inv1-unlisted-refusal-writes", "broken", "TableWalk", "PROPERTY RefusedUnchanged",
    [("  /\\ RefuseStep(c, v, i, NotInTable)",
      "  /\\ Refused(c, v, \"\", i, NotInTable)\n"
      "  /\\ Write1(i, item[i])\n"
      "  /\\ UNCHANGED <<config_hash, seq, events, live, pane, ghost>>")],
    "the refusal of an unlisted request writes the record (bumps its version)")
var("inv1-guard-refusal-writes-evidence", "broken", "Core", "PROPERTY GuardRefusedUnchanged",
    [('     ELSE RefuseStep(Lead, "verify", i, "inputs_changed")',
      '     ELSE /\\ Refused(Lead, "verify", "", i, "inputs_changed")\n'
      '          /\\ item\' = [item EXCEPT ![i].evidence = [run |-> "attempt", commit |-> item[i].attempt.commit,\n'
      '                        contract_version |-> item[i].contract_version, config_hash |-> config_hash]]\n'
      '          /\\ UNCHANGED <<version, config_hash, seq, events, live, pane, ghost>>')],
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
    [("FreshReplies(i) == {r \\in item[i].replies : r.seq > item[i].limit_seq /\\ ~r.consumed}",
      "FreshReplies(i) == {r \\in item[i].replies : ~r.consumed}")],
    "pre-D63: any attached unconsumed reply unlocks blocked(limit), also one older than the limit event")
var("inv10-single-use-dropped", "broken", "Replies", "PROPERTY LimitReplySingleUse",
    [("FreshReplies(i) == {r \\in item[i].replies : r.seq > item[i].limit_seq /\\ ~r.consumed}",
      "FreshReplies(i) == {r \\in item[i].replies : r.seq > item[i].limit_seq}"),
     ("!.retry = retry, !.limit_seq = LimitEntrySeq,",
      "!.retry = retry, !.limit_seq = IF @ = 0 THEN LimitEntrySeq ELSE @,"),
     ("     !.limit_seq = LimitEntrySeq, !.failure",
      "     !.limit_seq = IF @ = 0 THEN LimitEntrySeq ELSE @, !.failure")],
    "N2: the consumed (single-use) check dropped while a re-entry keeps the first limit_seq; with one seq "
    "and a re-recorded limit_seq freshness alone implies single use, so both are weakened")
var("inv11-cas-compares-attempt-id-only", "broken", "Cas", "PROPERTY StaleNeverWritten",
    [("IF AttemptFields(item[i]) = pane[p].read_fields /\\ item[i].attempt.id = pane[p].read_attempt",
      "IF item[i].attempt.id = pane[p].read_attempt")],
    "the verify write re-checks the attempt id but not state, contract version and submitted commit")
var("inv11-submit-ignores-attempt-id", "broken", "Cas", "PROPERTY StaleNeverWritten",
    [("  /\\ IF Owns(p, i)\n", "  /\\ IF TRUE\n")],
    "submit from a pane whose attempt is not the current one is accepted (older attempt ids, section 8)")
var("typeok-cancel-keeps-reason", "broken", "Core", "INVARIANT TypeOK",
    [('LET rec == [item[i] EXCEPT !.state = "cancelled", !.blocked_reason = "none"]',
      'LET rec == [item[i] EXCEPT !.state = "cancelled"]')],
    "cancel leaves a stale blocked_reason on the record")
# ---- decisions D65-D76: the OLD behaviour (as model-checked in WO-0016) must fail ----
var("d65-reject-always-rework", "broken", "Replies", "PROPERTY RouteByClass",
    [('LET rec == [item[i] EXCEPT !.state = RouteOf(cls).state, !.blocked_reason = RouteOf(cls).blocked_reason,',
      'LET rec == [item[i] EXCEPT !.state = "rework", !.blocked_reason = "none",')],
    "pre-D65 (G1): reject always goes to rework, also for class ambiguity")
var("d66-child-cancelled-no-cascade", "broken", "Split", "INVARIANT NoOrphanChildren",
    [('CascadeWrite(i, rec, ActiveChildren(i), L("mesh"', 'CascadeWrite(i, rec, {}, L("mesh"')],
    "pre-D66 (G2b): a cancelled child sends the parent to rework, the other children stay active")
var("d66-parent-cancel-no-cascade", "broken", "Split", "INVARIANT NoOrphanChildren",
    [('CascadeWrite(i, rec, ActiveChildren(i), L("cancel"', 'CascadeWrite(i, rec, {}, L("cancel"')],
    "pre-D66 (F2): cancelling a split parent leaves its children active")
var("d67-resplit-keeps-cancelled-child", "broken", "Resplit", "PROPERTY ResplitCanComplete",
    [("!.children = (@ \\ gone) \\cup C, !.former_children = @ \\cup gone]", "!.children = @ \\cup C]")],
    "pre-D67 (O3-8 stay_linked): a re-split keeps the cancelled child linked")
var("d68-refused-precheck-writes", "broken", "Core", "PROPERTY GuardRefusedUnchanged",
    [('     ELSE RefuseStep("pm", "unblock", i, "precheck refused")',
      '     ELSE /\\ Refused("pm", "unblock", "", i, "precheck refused; environment +1")\n'
      '          /\\ Write1(i, [item[i] EXCEPT !.environment_unblock = @ + 1])\n'
      '          /\\ UNCHANGED <<config_hash, seq, events, live, pane, ghost>>')],
    "pre-D68 (O3-9 precheck_refused, G3): a refused precheck counts and writes the record")
var("d68-passing-unblock-not-counted", "broken", "Core", "INVARIANT BoundedStarts",
    [("     THEN LET e == item[i].environment_unblock + 1 IN", "     THEN LET e == item[i].environment_unblock IN")],
    "pre-D68 (O3-9 precheck_refused, inv 5): a passing unblock is not counted, starts are unbounded")
var("d69-verify-keeps-flag", "broken", "Core", "INVARIANT FlagClearedByReverify",
    [('  [item[i] EXCEPT !.state = "verified", !.revalidate = FALSE,\n', '  [item[i] EXCEPT !.state = "verified",\n')],
    "pre-D69 (G4): a passing verification keeps an older revalidate flag")
var("d70-follow-up-without-row", "broken", "FollowUp", "PROPERTY TableOnly",
    [('    Row("mesh",       "mesh",          {"closed"}, {"unchanged"}),'
      '                        \\* (mesh) follow-up\n', '')],
    "pre-D70 (G5): the follow-up draft for a closed item has no section-4 row")
var("d71-cancel-leaves-dependents-ready", "broken", "Deps", "PROPERTY DependentsBlockedOnCancel",
    [('      D == {j \\in Items \\ ({i} \\cup K) : StateOf(j) = "ready" /\\ item[j].depends \\cap gone # {}}',
      '      D == {}')],
    "pre-D71 (G6): a cancel leaves its ready dependents in ready")
var("d72-parent-verify-without-row", "broken", "Split", "PROPERTY TableOnly",
    [('    Row("mesh",       "mesh",          {"verifying"}, {"verified"}),'
      '                      \\* (mesh) parent verify pass\n'
      '    Row("mesh",       "mesh",          {"verifying"}, {"rework"}),'
      '                        \\* (mesh) parent verify fail\n', '')],
    "pre-D72 (G8): no section-4 row moves a split parent out of verifying")
var("d73-override-enters-limit", "broken", "TableWalk", "PROPERTY LimitOnlyByMesh",
    [('t \\in Created \\ {StateOf(i), Blocked("limit")}', 't \\in Created \\ {StateOf(i)}')],
    "pre-D73 (F1): override (any -> any) may enter blocked(limit)")
var("d74-two-clocks-limit-lags", "broken", "Replies", "PROPERTY LimitReplyFresh",
    [("LimitEntrySeq == seq + 1", "LimitEntrySeq == IF seq > 0 THEN seq - 1 ELSE 0")],
    "pre-D74 (inv 10, G7): the recorded limit time comes from another clock and lags the entry")
var("d75-split-consumes-failure", "broken", "Split", "INVARIANT RouteDefined",
    [("!.children = (@ \\ gone) \\cup C, !.former_children = @ \\cup gone]",
      "!.children = (@ \\ gone) \\cup C, !.former_children = @ \\cup gone, !.failure = NoFailure]")],
    "pre-D75 (G2a reading): split consumes the conflict/too_large record; a cancelled child leaves a classless rework")
var("d76-cas-on-record-version", "broken", "Cas", "PROPERTY ReplyNeverDiscardsVerify",
    [("PaneIdle == [phase |-> \"idle\", owns |-> 0, item_id |-> 0, read_fields |-> NoFields,",
      "PaneIdle == [phase |-> \"idle\", owns |-> 0, item_id |-> 0, read_fields |-> NoFields, read_version |-> 0,"),
     ("                                   !.read_fields = AttemptFields(item[i]),\n",
      "                                   !.read_fields = AttemptFields(item[i]), !.read_version = version[i],\n"),
     ("IF AttemptFields(item[i]) = pane[p].read_fields /\\ item[i].attempt.id = pane[p].read_attempt",
      "IF version[i] = pane[p].read_version /\\ item[i].attempt.id = pane[p].read_attempt")],
    "pre-D76 (N1): the verify write re-checks the record version, so any reply-side write discards the run")

var("d81-ready-accepts-cancelled-dependency", "broken", "Deps", "INVARIANT NoWaitOnCancelled",
    [("       ELSE IF CancelledDepends(D)\n", "       ELSE IF FALSE\n")],
    "pre-D81 (WO-0020 deps-inv14): ready accepts a dependency that is already cancelled")
var("d81-reassign-keeps-cancelled-dependency", "broken", "DepsRoutes", "PROPERTY DependsNotCancelledAtReady",
    [('  /\\ IF CancelledDepends(item[i].depends)\n     THEN RefuseStep("pm", "reassign", i, D81Why)',
      '  /\\ IF FALSE\n     THEN RefuseStep("pm", "reassign", i, D81Why)')],
    "pre-D81: reassign returns to ready with a dependency cancelled while the item was in rework")
var("d81-unblock-keeps-cancelled-dependency", "broken", "DepsRoutes", "INVARIANT NoWaitOnCancelled",
    [('  /\\ IF precheck = "passes" /\\ CancelledDepends(item[i].depends)', '  /\\ IF FALSE')],
    "pre-D81: unblock returns to ready with a dependency cancelled while the item was blocked(environment)")

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
        for key in ("depends", "children", "former_children", "parent", "follows", "retry", "environment_unblock"):
            if rec.get(key):
                extra.append("%s=%s" % (key, rec[key]))
        if rec.get("revalidate"):
            extra.append("revalidate flag set")
        if rec.get("replies"):
            extra.append("replies=%d" % len(rec["replies"]))
        out.append(" ".join(["    final: item %s %s" % (i, _name(rec))] + extra))
    return out


def _limit_seq(st):
    """The recorded limit_seq of the item a limit-entry step put in blocked(limit), else None."""
    s = st.get("step", {})
    if s.get("limit_by") != "mesh counter":
        return None
    for i, rec in _items(st):
        if str(i) == str(s.get("item_id")):
            return rec.get("limit_seq")
    return None


def _times(st):
    """The seqs a step records (reply seq, event seq, limit_seq against its entry seq)."""
    s = st.get("step", {})
    lt = _limit_seq(st)
    if lt is not None:
        return " [limit_seq recorded as %s, entry at seq %s]" % (lt, st.get("seq"))
    if s.get("verb") == "attach" and not s.get("refused"):
        return " [reply seq %s]" % st.get("seq")
    if s.get("kind") == "env" and s.get("verb", "").startswith("hook:"):
        return " [event seq %s]" % st.get("seq")
    if s.get("reply"):
        return " [reply seq %s]" % s["reply"]
    return ""


def trace_lines(path):
    """A TLC -dumpTrace json file as numbered steps in the model's words, then the final items.

    Seqs are printed only when a limit entry recorded a limit_seq other than its own seq (a broken D74)."""
    try:
        states = _states(path)
    except (OSError, ValueError) as exc:
        return ["(no trace: %s)" % exc]
    skew = any(_limit_seq(st) not in (None, st.get("seq")) for st in states[1:])
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
