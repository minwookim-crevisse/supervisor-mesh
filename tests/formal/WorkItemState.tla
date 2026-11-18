---------------------------- MODULE WorkItemState ----------------------------
(* TLA+ model of the mesh work-item state machine of                       *)
(* docs/designs/work-item-state-model.md, sections 1-10 (decisions D40-D76;  *)
(* why each row exists: the review records in                                *)
(* docs/designs/pm-led-onboarding.md).  Every action that implements a       *)
(* section-4 row carries a comment  \* row: <verb> | <caller> | <from> | <to> *)
(* copied from the table; steps that are not rows say so.  Names follow the  *)
(* design.  Coverage, bounds, abstractions, findings: tests/formal/README.md. *)
EXTENDS Naturals, FiniteSets, Sequences

CONSTANTS
    N,                  \* item slots 1..N; a slot in state "(none)" is not created yet
    NewItems,           \* slots that `new` / `draft` may create; other slots are split children / follow-ups
    BugfixItems,        \* slots whose contract `type` is bugfix (repro + failing_before, D46/D55/D56)
    ItemVerbs,          \* verbs enabled for items that are not split children (scenario scope)
    ChildVerbs,         \* verbs enabled for split children (scenario scope)
    WithDepends,        \* `ready` may record `depends` (D59/D62)
    RetryLimit,         \* counters.retry limit (section 6, "config")
    EnvLimit,           \* counters.environment_unblock limit (section 6, default 3)
    MaxReplies,         \* bound: replies[] per item
    MaxEvents,          \* bound: operator events recorded by the PM-pane hook (section 9a)
    ConfigEdits,        \* bound: edits of the verification config (D47)
    LeadLoss,           \* the assigned lead may become absent or dead (D45: pm `fail` environment)
    Panes,              \* lead panes that run `verify` as read / unlocked commands / CAS write (section 8); {} = atomic
    WithRefuse,         \* generic refusal of every verb x caller x state not in the table (invariant 1)
    WithOverride,       \* the operator `override` row
    FollowUpOn,         \* what triggers the (mesh) follow-up row on a closed item (D70):
                        \* "thread reply" (section 9), "revalidate flag" (D60)
    RejectTriggersInScope, \* scenario scope: the reject triggers in use (D101), a subset of RejectTriggers
    MaxContractVersion, \* exploration bound (CONSTRAINT Bounds)
    MaxAttempts,        \* exploration bound
    MaxSeq,             \* exploration bound on the mesh seq
    MaxVersion,         \* exploration bound on the record version (CAS scenario only)
    MaxCancelled        \* exploration bound: items in state cancelled

VARIABLES
    item,         \* item record per slot (section 3), without `version`
    version,      \* the record `version` field of section 3 (compare-and-set, D57); kept apart for the fingerprint
    config_hash,  \* effective verification config hash: commands + required evidence (D47)
    seq,          \* the one monotonic mesh `seq` (D74): stamps reply receipts, terminal events, limit entries
    events,       \* .mesh/events.jsonl: operator prompts recorded by the PM-pane hook (section 9a)
    live,         \* the assigned lead pane is live ("agent live", D45)
    pane,         \* lead panes inside a `verify` (section 8); fields `dirty`, `dirty_by` are ghosts
    ghost,        \* history read only by properties, never by an action
    step,         \* label of the last step (verb, caller, from, to, refused); not in View
    refusals      \* refusal log: the last refused (caller, verb, item, reason); not in View

vars == <<item, version, config_hash, seq, events, live, pane, ghost, step, refusals>>

-----------------------------------------------------------------------------
(* Section 1: states and blocked reasons.                                   *)

Items == 1..N
ChildSlots == Items \ NewItems
None == "(none)"
Lead == "lead (own role)"
OtherLead == "lead (other role)"
Callers == {"pm", "slack watcher", Lead, OtherLead, "mesh", "operator"}
AllVerbs == {"new", "draft", "attach", "reply", "revise", "ready", "dispatch",
             "start", "submit", "verify", "fail", "reassign", "split", "mesh",
             "unblock", "reject", "ack", "close", "cancel", "revalidate",
             "override"}
Reasons == {"awaiting_user", "environment", "limit", "children"}
BlockedName == [r \in Reasons |-> "blocked(" \o r \o ")"]
Blocked(r) == BlockedName[r]
NonTerminal == {"draft", "ready", "running", "verifying", "verified", "rework"}
                 \cup {Blocked(r) : r \in Reasons}
Terminal == {"closed", "cancelled"}
Created == NonTerminal \cup Terminal
Classes == {"defect", "conflict", "ambiguity", "environment", "limit"}
MergedCommit == 1000   \* the split parent's merged commit (section 7)
\* D105: commit lineage.  The base commit of attempt k is BaseOf(k); a commit descends from base b when it is
\* later on b's lineage (same tens block).  Attempt ids stay below 50, so base + 501 is on another lineage.
BaseOf(k) == 10 * k
Descends(c, b) == c > b /\ c \div 10 = b \div 10
SubmitCommits(i) == {item[i].attempt.base + 1, item[i].attempt.base + 501}
NotInTable == "not in the transition table"

\* the design's state name, with the blocked reason folded in: "blocked(limit)"
PS(rec) == IF rec.state = "blocked" THEN Blocked(rec.blocked_reason) ELSE rec.state
StateOf(i) == PS(item[i])
ReasonOf(s) == CHOOSE r \in Reasons : Blocked(r) = s
ReproHash(i) == IF i \in BugfixItems THEN 1 ELSE 0

-----------------------------------------------------------------------------
(* Section 4: the transition table as data (verb, caller, from, to), one    *)
(* entry per row of the design; "unchanged" = the row leaves the state.     *)
(* The (mesh) rows share the verb "mesh" and differ by from / to.           *)

Row(v, c, F, T) == [verb |-> v, caller |-> c, from |-> F, to |-> T]
Table ==
  { Row("new",        "pm",            {None}, {"draft"}),
    Row("draft",      "slack watcher", {None}, {"draft"}),
    Row("attach",     "slack watcher", NonTerminal, {"unchanged"}),
    Row("reply",      "pm",            NonTerminal, {"unchanged"}),
    Row("revise",     "pm",            {"ready"}, {"draft"}),
    Row("ready",      "pm",            {"draft", "rework", Blocked("awaiting_user"), Blocked("limit")}, {"ready"}),
    Row("dispatch",   "pm",            {"ready"}, {"unchanged"}),
    Row("start",      Lead,            {"ready"}, {"running", Blocked("limit")}),
    Row("submit",     Lead,            {"running"}, {"verifying"}),
    Row("verify",     Lead,            {"verifying"}, {"verified"}),
    Row("fail",       Lead,            {"running", "verifying"},
                                       {"rework", Blocked("awaiting_user"), Blocked("environment")}),
    Row("fail",       "pm",            {"running", "verifying"}, {Blocked("environment")}),
    Row("reassign",   "pm",            {"rework"}, {"ready"}),
    Row("split",      "pm",            {"rework"}, {Blocked("children")}),
    Row("mesh",       "mesh",          {Blocked("children")}, {"verifying"}),             \* (mesh) children done
    Row("mesh",       "mesh",          {Blocked("children")}, {"rework"}),                \* (mesh) child cancelled
    Row("mesh",       "mesh",          {"verifying"}, {"verified"}),                      \* (mesh) parent verify pass
    Row("mesh",       "mesh",          {"verifying"}, {"rework"}),                        \* (mesh) parent verify fail
    Row("mesh",       "mesh",          {"ready"}, {Blocked("awaiting_user")}),            \* (mesh) dependency cancelled
    Row("mesh",       "mesh",          {"closed"}, {"unchanged"}),                        \* (mesh) follow-up
    Row("unblock",    "pm",            {Blocked("environment")}, {"ready", Blocked("limit")}),
    Row("reject",     "pm",            {"verified"}, {"rework", Blocked("awaiting_user")}),
    Row("ack",        "pm",            NonTerminal, {"unchanged"}),
    Row("close",      "pm",            {"verified"}, {"closed"}),
    Row("cancel",     "pm",            NonTerminal \cup {"cancelled"}, {"cancelled"}),
    Row("revalidate", "pm",            {"verified", "closed"}, {"unchanged"}),
    Row("override",   "operator",      Created, Created \ {Blocked("limit")}) }

\* RowTo[<<verb, caller, from>>] = the states the table allows as `to` ({} = not listed); a constant
RowTo == [k \in AllVerbs \X Callers \X (Created \cup {None}) |->
            UNION {(r.to \ {"unchanged"}) \cup (IF "unchanged" \in r.to THEN {k[3]} ELSE {}) :
                     r \in {x \in Table : x.verb = k[1] /\ x.caller = k[2] /\ k[3] \in x.from}}]
Listed(v, c, s) == <<v, c, s>> \in DOMAIN RowTo /\ RowTo[<<v, c, s>>] # {}
RowAllows(v, c, s, t) == <<v, c, s>> \in DOMAIN RowTo /\ t \in RowTo[<<v, c, s>>]

-----------------------------------------------------------------------------
(* Section 3: the item record.                                              *)

NoAttempt == [id |-> 0, base |-> 0, commit |-> 0, contract_version |-> 0, config_hash |-> 0]
NoFailure == [class |-> "none", reason |-> "none"]
NoFailingBefore == [contract_version |-> 0, repro |-> 0]
NoEvidence == [run |-> "none", commit |-> 0, contract_version |-> 0, config_hash |-> 0]
EmptyItem ==
  [state |-> None, blocked_reason |-> "none",
   contract_version |-> 0, depends |-> {},
   attempt |-> NoAttempt,
   retry |-> 0, environment_unblock |-> 0, next_start_free |-> FALSE,
   failure |-> NoFailure, failing_before |-> NoFailingBefore,
   replies |-> {}, limit_seq |-> 0, pinned_config |-> 0,
   parent |-> 0, children |-> {}, former_children |-> {}, follows |-> 0,
   revalidate |-> FALSE, evidence |-> NoEvidence]
GhostInit == [starts |-> 0, unlocks |-> {}, own_verify |-> FALSE,
              flag_evidence |-> NoEvidence, cancelled_child |-> FALSE,
              limit_entry |-> 0]   \* seq of the last blocked(limit) entry (limit_seq is the recorded value)
\* section 8 (D76): the attempt-relevant fields the verify compare-and-set re-checks
AttemptFields(rec) == [state |-> PS(rec), attempt |-> rec.attempt.id,
                       contract_version |-> rec.contract_version, commit |-> rec.attempt.commit]
NoFields == [state |-> "", attempt |-> 0, contract_version |-> 0, commit |-> 0]
PaneIdle == [phase |-> "idle", owns |-> 0, item_id |-> 0, read_fields |-> NoFields,
             read_attempt |-> 0, dirty |-> FALSE, dirty_by |-> {}]
NoStep == [kind |-> "init", verb |-> "", caller |-> "", pane |-> "", item_id |-> 0,
           from |-> "", to |-> "", refused |-> FALSE, why |-> "", reply |-> 0,
           limit_by |-> ""]
EnvStep(name) == [NoStep EXCEPT !.kind = "env", !.verb = name]
NoRefusal == [caller |-> "", verb |-> "", item_id |-> 0, reason |-> ""]

Init ==
  /\ item = [i \in Items |-> EmptyItem]
  /\ version = [i \in Items |-> 0]
  /\ config_hash = 1
  /\ seq = 0
  /\ events = [e \in 1..MaxEvents |-> [seq |-> 0, linked |-> FALSE]]
  /\ live = [i \in Items |-> TRUE]
  /\ pane = [p \in Panes |-> PaneIdle]
  /\ ghost = [i \in Items |-> GhostInit]
  /\ step = NoStep
  /\ refusals = NoRefusal

-----------------------------------------------------------------------------
(* Every applied verb is one lock-held write of the record (section 8): it  *)
(* bumps `version`.  For every pane whose unlocked verify read this item it  *)
(* records the writing verb (ghost dirty_by) and whether the write changed   *)
(* an attempt-relevant field (ghost dirty).  DirtyFor reads item', so it     *)
(* follows the conjunct that sets item'.  A refused verb records (caller,    *)
(* verb, reason) only.                                                       *)

Write1(i, rec) == /\ item' = [item EXCEPT ![i] = rec]
                  /\ version' = [version EXCEPT ![i] = @ + 1]
DirtyFor(S, v) == [p \in Panes |-> IF pane[p].phase = "computing" /\ pane[p].item_id \in S
                                   THEN LET k == pane[p].item_id IN
                                        [pane[p] EXCEPT
                                           !.dirty = @ \/ AttemptFields(item'[k]) # AttemptFields(item[k]),
                                           !.dirty_by = @ \cup {v}]
                                   ELSE pane[p]]
StepRec(v, c, p, i, fromS, toS, refused, why) ==
  [kind |-> "verb", verb |-> v, caller |-> c, pane |-> p, item_id |-> i,
   from |-> fromS, to |-> toS, refused |-> refused, why |-> why, reply |-> 0,
   limit_by |-> ""]
L(v, c, i, rec, why) == StepRec(v, c, "", i, StateOf(i), PS(rec), FALSE, why)

Apply(i, rec, lbl) ==
  /\ Write1(i, rec)
  /\ pane' = DirtyFor({i}, lbl.verb)
  /\ step' = lbl
  /\ UNCHANGED refusals

Refused(c, v, p, i, why) ==
  /\ step' = StepRec(v, c, p, i, StateOf(i), StateOf(i), TRUE, why)
  /\ refusals' = [caller |-> c, verb |-> v, item_id |-> i, reason |-> why]

RefuseStep(c, v, i, why) ==
  /\ Refused(c, v, "", i, why)
  /\ UNCHANGED <<item, version, config_hash, seq, events, live, pane, ghost>>

\* One write that also cancels the items in K (D66: the active children of a split parent) and moves
\* every ready dependent of an item it cancels to blocked(awaiting_user) (D71, reason
\* dependency_cancelled: the (mesh) dependency cancelled row, in the same write; D81 leaves no other way).
ActiveChildren(i) == {c \in item[i].children : StateOf(c) \in NonTerminal}
CascadeWrite(i, rec, K, lbl) ==
  LET gone == K \cup (IF rec.state = "cancelled" THEN {i} ELSE {})
      D == {j \in Items \ ({i} \cup K) : StateOf(j) = "ready" /\ item[j].depends \cap gone # {}}
      W == {i} \cup K \cup D
  IN /\ item' = [j \in Items |->
                   IF j = i THEN rec
                   ELSE IF j \in K THEN [item[j] EXCEPT !.blocked_reason = "none", !.state = "cancelled"]
                   ELSE IF j \in D THEN [item[j] EXCEPT !.state = "blocked", !.blocked_reason = "awaiting_user"]
                   ELSE item[j]]
     /\ version' = [j \in Items |-> IF j \in W THEN version[j] + 1 ELSE version[j]]
     /\ pane' = DirtyFor(W, lbl.verb)
     /\ step' = lbl
     /\ UNCHANGED refusals

\* scenario scope: which verbs an item may use (children may be restricted)
May(i, v) == IF item[i].parent = 0 THEN v \in ItemVerbs ELSE v \in ChildVerbs

LeadPanes == IF Panes = {} THEN {""} ELSE Panes
Idle(p) == IF p \in Panes THEN pane[p].phase = "idle" ELSE TRUE
\* "current attempt id": a pane may act only on the attempt it started (section 8: older attempt ids are rejected)
Owns(p, i) == IF p \in Panes THEN pane[p].owns = item[i].attempt.id ELSE TRUE

-----------------------------------------------------------------------------
(* Section 4 rows.  A lead step needs the assigned lead pane live (D45): a   *)
(* dead lead sends nothing, which is why the pm `fail` row exists.          *)

\* row: new | pm | (none) | draft | none | none
New(i) ==
  /\ i \in NewItems /\ May(i, "new") /\ item[i].state = None
  /\ LET rec == [item[i] EXCEPT !.state = "draft"]
     IN Apply(i, rec, L("new", "pm", i, rec, ""))
  /\ UNCHANGED <<config_hash, seq, events, live, ghost>>

\* row: draft | slack watcher | (none) | draft | allowlisted user, ts dedup | none
SlackDraft(i) ==
  /\ i \in NewItems /\ May(i, "draft") /\ item[i].state = None
  /\ LET rec == [item[i] EXCEPT !.state = "draft"]
     IN Apply(i, rec, L("draft", "slack watcher", i, rec, "top-level request"))
  /\ UNCHANGED <<config_hash, seq, events, live, ghost>>

\* a replies[] entry: mesh receipt seq (D74), source (slack ts / terminal event id: reference only)
NewReply(src, e, s) ==
  [seq |-> s, source |-> src, event |-> e, acknowledged |-> FALSE, consumed |-> FALSE]

\* row: attach | slack watcher | any except closed, cancelled | unchanged | thread map; notifies the PM; never releases a block | none
\* (mesh stamps the reply receipt with the next seq, D74)
Attach(i) ==
  /\ May(i, "attach") /\ StateOf(i) \in NonTerminal
  /\ Cardinality(item[i].replies) < MaxReplies
  /\ seq' = seq + 1
  /\ LET rec == [item[i] EXCEPT !.replies = @ \cup {NewReply("slack", 0, seq + 1)}]
     IN Apply(i, rec, L("attach", "slack watcher", i, rec, "thread reply"))
  /\ UNCHANGED <<config_hash, events, live, ghost>>

\* not a table row (section 9a): the UserPromptSubmit hook in the PM pane runs
\* `mesh event record --source terminal`; a lead pane records nothing.  Mesh stamps the event with
\* the next seq (D74).
RecordEvent(e) ==
  /\ events[e].seq = 0
  /\ \A f \in 1..(e - 1) : events[f].seq # 0
  /\ seq' = seq + 1
  /\ events' = [events EXCEPT ![e].seq = seq + 1]
  /\ step' = EnvStep("hook: operator prompt recorded in the PM pane")
  /\ UNCHANGED <<item, version, config_hash, live, pane, ghost, refusals>>

\* row: reply | pm | any except closed, cancelled | unchanged | cites an operator event id recorded by the PM-pane prompt hook (section 9a); attaches it as a reply; never releases a block (D64) | none
\* (the reply carries the event's mesh seq: freshness and single use apply to it, section 9a / D74)
Reply(i, e) ==
  /\ May(i, "reply") /\ StateOf(i) \in NonTerminal
  /\ IF events[e].seq # 0 /\ ~events[e].linked
     THEN /\ Cardinality(item[i].replies) < MaxReplies
          /\ events' = [events EXCEPT ![e].linked = TRUE]
          /\ LET rec == [item[i] EXCEPT !.replies = @ \cup {NewReply("terminal", e, events[e].seq)}]
             IN Apply(i, rec, L("reply", "pm", i, rec, "operator answer from the PM terminal"))
          /\ UNCHANGED <<config_hash, seq, live, ghost>>
     ELSE RefuseStep("pm", "reply", i, "unknown or reused event id")

\* row: revise | pm | ready | draft | reason; next ready bumps the contract version | none
Revise(i) ==
  /\ May(i, "revise") /\ StateOf(i) = "ready"
  /\ LET rec == [item[i] EXCEPT !.state = "draft"]
     IN Apply(i, rec, L("revise", "pm", i, rec, "reason"))
  /\ UNCHANGED <<config_hash, seq, events, live, ghost>>

RECURSIVE Closure(_, _)
Closure(S, k) == IF k = 0 THEN S ELSE Closure(S \cup UNION {item[x].depends : x \in S}, k - 1)
DependsOk(i, D) ==
  /\ \A d \in D : item[d].state # None   \* unknown ids refused
  /\ i \notin D                          \* no self-dependency
  /\ i \notin Closure(D, N)              \* no cycle through the recorded depends
DependsChoices == IF WithDepends THEN SUBSET Items ELSE {{}}
\* D81: ready, reassign and unblock refuse a cancelled dependency
CancelledDepends(D) == \E d \in D : item[d].state = "cancelled"
D81Why == "a dependency is cancelled (D81)"
ReproResults(i) == IF i \in BugfixItems THEN {"fails", "passes", "not_runnable"} ELSE {"none"}
\* D63 / D74: fresh = its mesh seq is greater than limit_seq; single use = not consumed
FreshReplies(i) == {r \in item[i].replies : r.seq > item[i].limit_seq /\ ~r.consumed}
\* D74: the seq mesh stamps on a blocked(limit) entry and records as limit_seq (the next seq)
LimitEntrySeq == seq + 1
\* section 5 "way back" from rework through `ready`: conflict (PM revised scope/depends), ambiguity (revised criteria)
ReadyRoute(i) == item[i].failure.class \in {"conflict", "ambiguity"}

\* D104: the contract revision ready needs from rework / blocked(awaiting_user), by the latest failure class:
\* conflict -> scope or depends revised, ambiguity -> done criteria revised; no class (dependency_cancelled,
\* D71) -> no class-specific revision (D81 already forces the cancelled dependency out of depends).  Scope and
\* done criteria are not record fields here: the PM's revision is a nondeterministic kind, in the step label.
Revisions == {"scope or depends", "done criteria", "none"}
RequiredRevision(i) ==
  CASE item[i].failure.class = "conflict"  -> "scope or depends"
    [] item[i].failure.class = "ambiguity" -> "done criteria"
    [] OTHER                               -> "any"
RevisionOk(i, rev) == RequiredRevision(i) = "any" \/ rev = RequiredRevision(i)
RevisionChoices(i) == IF StateOf(i) \in {"rework", Blocked("awaiting_user")} THEN Revisions ELSE {"none"}
RevisedWhy(rev) == "contract complete; revised: " \o rev

ReadyRec(i, D) ==
  [item[i] EXCEPT !.state = "ready", !.blocked_reason = "none",
     !.contract_version = @ + 1, !.depends = D, !.failure = NoFailure, !.pinned_config = config_hash,
     !.failing_before = IF i \in BugfixItems
                        THEN [contract_version |-> item[i].contract_version + 1,
                              repro |-> ReproHash(i)]
                        ELSE NoFailingBefore]

\* row: ready | pm | draft, rework, blocked(awaiting_user), blocked(limit) | ready | contract + type inputs present; dependencies exist, none cancelled (D81), no self-dependency, no cycle; pins the verification config hash (D100); bugfix repro fails at the base commit (126/127, timeout or spawn error refused as not runnable); from rework/awaiting_user: class route (section 5), with the class-specific revision present (conflict: scope or depends revised; ambiguity: done criteria revised; contract version +1) (D104); from blocked(limit): revised contract (version +1) + fresh unconsumed user reply | leaving blocked(limit) resets both counters
Ready(i) ==
  /\ May(i, "ready")
  /\ StateOf(i) \in {"draft", "rework", Blocked("awaiting_user"), Blocked("limit")}
  /\ \E D \in DependsChoices, repro \in ReproResults(i), rev \in RevisionChoices(i) :
       IF ~DependsOk(i, D)
       THEN RefuseStep("pm", "ready", i, "depends: unknown id, self-dependency or cycle")
       ELSE IF CancelledDepends(D)
       THEN RefuseStep("pm", "ready", i, D81Why)
       ELSE IF StateOf(i) = "rework" /\ ~ReadyRoute(i)
       THEN RefuseStep("pm", "ready", i, "class route")
       ELSE IF StateOf(i) \in {"rework", Blocked("awaiting_user")} /\ ~RevisionOk(i, rev)
       THEN RefuseStep("pm", "ready", i, "class-specific contract revision missing (D104)")
       ELSE IF repro = "passes"
       THEN RefuseStep("pm", "ready", i, "repro passes at the base commit: not reproduced")
       ELSE IF repro = "not_runnable"
       THEN RefuseStep("pm", "ready", i, "repro not runnable: 126/127, timeout or spawn error")
       ELSE IF StateOf(i) = Blocked("limit")
       THEN IF FreshReplies(i) = {}
            THEN RefuseStep("pm", "ready", i, "no fresh unconsumed user reply")
            ELSE \E r \in FreshReplies(i) :
                   LET rec == [ReadyRec(i, D) EXCEPT
                                 !.retry = 0, !.environment_unblock = 0,
                                 !.next_start_free = TRUE,
                                 !.replies = (@ \ {r}) \cup {[r EXCEPT !.consumed = TRUE]}]
                   IN /\ Apply(i, rec, [L("ready", "pm", i, rec, "limit resume: revised contract + fresh reply")
                                          EXCEPT !.reply = r.seq])
                      /\ ghost' = [ghost EXCEPT ![i].starts = 0, ![i].unlocks = @ \cup {r.seq}]
                      /\ UNCHANGED <<config_hash, seq, events, live>>
       ELSE LET rec == ReadyRec(i, D)
            IN /\ Apply(i, rec, L("ready", "pm", i, rec,
                                 IF StateOf(i) \in {"rework", Blocked("awaiting_user")} THEN RevisedWhy(rev)
                                 ELSE "contract complete"))
               /\ UNCHANGED <<config_hash, seq, events, live, ghost>>

\* row: dispatch | pm | ready | ready | assigned role; message-ref | none
Dispatch(i) ==
  /\ May(i, "dispatch") /\ StateOf(i) = "ready"
  /\ Apply(i, item[i], L("dispatch", "pm", i, item[i], "message-ref"))
  /\ UNCHANGED <<config_hash, seq, events, live, ghost>>

DependsSatisfied(i) == \A d \in item[i].depends : item[d].state \in {"verified", "closed"}

\* row: start | lead (own role) | ready | running | every dependency verified or closed; agent live; new attempt id; records contract version; verification config hash must equal the one pinned at ready (D100), else refused as inputs_changed | retry +1 for every attempt after the first, except the first after an environment unblock or a limit resume; over the limit -> blocked(limit) + Slack question
Start(i, p) ==
  /\ May(i, "start") /\ StateOf(i) = "ready" /\ live[i] /\ Idle(p)
  /\ IF ~DependsSatisfied(i)
     THEN /\ Refused(Lead, "start", p, i, "dependency not verified or closed")
          /\ UNCHANGED <<item, version, config_hash, seq, events, live, pane, ghost>>
     ELSE IF config_hash # item[i].pinned_config
     THEN /\ Refused(Lead, "start", p, i, "inputs_changed: config hash differs from the one pinned at ready (D100)")
          /\ UNCHANGED <<item, version, config_hash, seq, events, live, pane, ghost>>
     ELSE LET counted == item[i].attempt.id > 0 /\ ~item[i].next_start_free
              retry == IF counted THEN item[i].retry + 1 ELSE item[i].retry
          IN IF retry > RetryLimit
             THEN LET rec == [item[i] EXCEPT !.state = "blocked", !.blocked_reason = "limit",
                                !.retry = retry, !.limit_seq = LimitEntrySeq,
                                !.failure = [class |-> "limit", reason |-> "retry"]]
                  IN /\ seq' = seq + 1
                     /\ Write1(i, rec)
                     /\ pane' = DirtyFor({i}, "start")
                     /\ step' = [StepRec("start", Lead, p, i, StateOf(i), PS(rec), FALSE,
                                         "mesh: retry over the limit; Slack question")
                                 EXCEPT !.limit_by = "mesh counter"]
                     /\ ghost' = [ghost EXCEPT ![i].limit_entry = seq + 1]
                     /\ UNCHANGED <<config_hash, events, live, refusals>>
             ELSE LET rec == [item[i] EXCEPT !.state = "running", !.retry = retry,
                                !.next_start_free = FALSE,
                                !.attempt = [id |-> item[i].attempt.id + 1,
                                             base |-> BaseOf(item[i].attempt.id + 1), commit |-> 0,
                                             contract_version |-> item[i].contract_version,
                                             config_hash |-> config_hash]]
                  IN /\ Write1(i, rec)
                     /\ pane' = IF p \in Panes
                                THEN [DirtyFor({i}, "start") EXCEPT ![p].owns = item[i].attempt.id + 1]
                                ELSE DirtyFor({i}, "start")
                     /\ step' = StepRec("start", Lead, p, i, StateOf(i), PS(rec), FALSE,
                                        IF counted THEN "retry +1" ELSE "not counted")
                     /\ ghost' = [ghost EXCEPT ![i].starts = @ + 1]
                     /\ UNCHANGED <<config_hash, seq, events, live, refusals>>

\* row: submit | lead (own role) | running | verifying | current attempt id; changed commit that descends from the attempt's base commit (D105) | none
\* (commit lineage over small integers: the attempt's base is BaseOf(id), recorded at start; a lead submits a
\* descendant base + 1 or an unrelated commit base + 501, on another lineage)
Submit(i, p) ==
  /\ May(i, "submit") /\ StateOf(i) = "running" /\ live[i] /\ Idle(p)
  /\ \E c \in SubmitCommits(i) :
       IF Owns(p, i)
       THEN IF Descends(c, item[i].attempt.base)
            THEN LET rec == [item[i] EXCEPT !.state = "verifying", !.attempt.commit = c]
                 IN /\ Write1(i, rec)
                    /\ pane' = DirtyFor({i}, "submit")
                    /\ step' = StepRec("submit", Lead, p, i, StateOf(i), PS(rec), FALSE, "changed commit")
                    /\ UNCHANGED <<config_hash, seq, events, live, ghost, refusals>>
            ELSE /\ Refused(Lead, "submit", p, i, "commit does not descend from the attempt's base commit (D105)")
                 /\ UNCHANGED <<item, version, config_hash, seq, events, live, pane, ghost>>
       ELSE /\ Refused(Lead, "submit", p, i, "older attempt id")
            /\ UNCHANGED <<item, version, config_hash, seq, events, live, pane, ghost>>

\* The contract-version half of InputsUnchanged and FailingBeforeOk are structurally implied
\* here (both change only at `ready`, which running/verifying cannot take); README, inv 6.
InputsUnchanged(i) ==
  /\ item[i].attempt.config_hash = config_hash
  /\ item[i].attempt.contract_version = item[i].contract_version
FailingBeforeOk(i) ==
  i \notin BugfixItems
    \/ item[i].failing_before = [contract_version |-> item[i].contract_version, repro |-> ReproHash(i)]
\* a passing verification binds the evidence and clears a revalidate flag (D69)
VerifiedRec(i, run) ==
  [item[i] EXCEPT !.state = "verified", !.revalidate = FALSE,
     !.evidence = [run |-> run, commit |-> item[i].attempt.commit,
                   contract_version |-> item[i].contract_version, config_hash |-> config_hash]]

\* row: verify | lead (own role); mesh runs commands | verifying | verified | all commands pass on the submitted commit; type evidence present; bugfix failing-before matches the current contract version and repro hash; config hash and inputs unchanged, else refused as inputs_changed; clears a revalidate flag (D69) | none
\* (atomic encoding; commands that fail lead to `fail`, so only passing runs are modelled here)
Verify(i) ==
  /\ Panes = {} /\ May(i, "verify") /\ live[i]
  /\ StateOf(i) = "verifying" /\ item[i].attempt.commit # MergedCommit
  /\ IF InputsUnchanged(i) /\ FailingBeforeOk(i)
     THEN LET rec == VerifiedRec(i, "attempt")
          IN /\ Apply(i, rec, L("verify", Lead, i, rec, "all commands pass on the submitted commit"))
             /\ ghost' = [ghost EXCEPT ![i].own_verify = TRUE]
             /\ UNCHANGED <<config_hash, seq, events, live>>
     ELSE RefuseStep(Lead, "verify", i, "inputs_changed")

\* the reason a compare-and-set mismatch is recorded with (section 8)
StaleWhy == "stale result discarded: attempt-relevant field or attempt mismatch"

\* same row, section 8 encoding: lock held to read-check, commands run unlocked, ...
VerifyRead(i, p) ==
  /\ p \in Panes /\ May(i, "verify") /\ live[i] /\ pane[p].phase = "idle"
  /\ StateOf(i) = "verifying" /\ item[i].attempt.commit # MergedCommit /\ Owns(p, i)
  /\ pane' = [pane EXCEPT ![p] = [@ EXCEPT !.phase = "computing", !.item_id = i,
                                   !.read_fields = AttemptFields(item[i]),
                                   !.read_attempt = item[i].attempt.id, !.dirty = FALSE,
                                   !.dirty_by = {}]]
  /\ step' = [StepRec("verify", Lead, p, i, StateOf(i), StateOf(i), FALSE,
                      "read-check under the lock; commands start unlocked")
              EXCEPT !.kind = "read"]
  /\ UNCHANGED <<item, version, config_hash, seq, events, live, ghost, refusals>>

\* ... and the write re-checks the attempt-relevant fields (state, current attempt id, contract version, submitted commit) and the attempt id (compare-and-set); a mismatch discards the result as stale.  Reply-side writes (attach, reply, ack) do not touch these fields and do not discard a running verification (D76)
VerifyWrite(i, p) ==
  /\ p \in Panes /\ live[i] /\ pane[p].phase = "computing" /\ pane[p].item_id = i
  /\ IF AttemptFields(item[i]) = pane[p].read_fields /\ item[i].attempt.id = pane[p].read_attempt
     THEN IF InputsUnchanged(i) /\ FailingBeforeOk(i)
          THEN LET rec == VerifiedRec(i, "attempt")
               IN /\ Write1(i, rec)
                  /\ pane' = [DirtyFor({i}, "verify") EXCEPT ![p] = [PaneIdle EXCEPT !.owns = pane[p].owns]]
                  /\ step' = StepRec("verify", Lead, p, i, StateOf(i), PS(rec), FALSE,
                                     "compare-and-set write")
                  /\ ghost' = [ghost EXCEPT ![i].own_verify = TRUE]
                  /\ UNCHANGED <<config_hash, seq, events, live, refusals>>
          ELSE /\ pane' = [pane EXCEPT ![p] = [PaneIdle EXCEPT !.owns = pane[p].owns]]
               /\ Refused(Lead, "verify", p, i, "inputs_changed")
               /\ UNCHANGED <<item, version, config_hash, seq, events, live, ghost>>
     ELSE /\ pane' = [pane EXCEPT ![p] = [PaneIdle EXCEPT !.owns = pane[p].owns]]
          /\ Refused(Lead, "verify", p, i, StaleWhy)
          /\ UNCHANGED <<item, version, config_hash, seq, events, live, ghost>>

\* section 5: where each failure class goes
RouteOf(cls) ==
  CASE cls \in {"defect", "conflict"} -> [state |-> "rework", blocked_reason |-> "none"]
    [] cls = "ambiguity"   -> [state |-> "blocked", blocked_reason |-> "awaiting_user"]
    [] cls = "environment" -> [state |-> "blocked", blocked_reason |-> "environment"]
    [] cls = "limit"       -> [state |-> "blocked", blocked_reason |-> "limit"]
CallerMayFail(c, cls, i) ==
  IF c = Lead THEN cls \in {"defect", "conflict", "ambiguity", "environment"}
              ELSE cls = "environment" /\ ~live[i]

\* row: fail | lead (own role): defect, conflict, ambiguity, environment; pm: environment only when the lead is absent or dead | running, verifying | per class (section 5) | failure record: class + evidence | none
Fail(i, c, p, cls, why) ==
  /\ May(i, "fail") /\ StateOf(i) \in {"running", "verifying"} /\ Idle(p)
  /\ (c = Lead => live[i])
  /\ (why = "too_large" => cls = "conflict")
  /\ IF CallerMayFail(c, cls, i) /\ Owns(p, i)
     THEN LET rec == [item[i] EXCEPT !.state = RouteOf(cls).state,
                        !.blocked_reason = RouteOf(cls).blocked_reason,
                        !.failure = [class |-> cls, reason |-> why]]
          IN /\ Write1(i, rec)
             /\ pane' = DirtyFor({i}, "fail")
             /\ step' = [StepRec("fail", c, p, i, StateOf(i), PS(rec), FALSE,
                                 IF why = "too_large" THEN cls \o ", reason too_large" ELSE cls)
                         EXCEPT !.limit_by = IF cls = "limit" THEN c ELSE ""]
             /\ UNCHANGED <<config_hash, seq, events, live, ghost, refusals>>
     ELSE /\ Refused(c, "fail", p, i, IF Owns(p, i) THEN "class not allowed for this caller"
                                                   ELSE "older attempt id")
          /\ UNCHANGED <<item, version, config_hash, seq, events, live, pane, ghost>>

\* row: reassign | pm | rework | ready | class route (section 5); no dependency cancelled (D81); re-pins the verification config hash (D106) | none (counted at start)
Reassign(i) ==
  /\ May(i, "reassign") /\ StateOf(i) = "rework"
  /\ IF CancelledDepends(item[i].depends)
     THEN RefuseStep("pm", "reassign", i, D81Why)
     ELSE IF item[i].failure.class = "defect"
     THEN LET rec == [item[i] EXCEPT !.state = "ready", !.failure = NoFailure, !.pinned_config = config_hash]
          IN /\ Apply(i, rec, L("reassign", "pm", i, rec, "class route: defect"))
             /\ UNCHANGED <<config_hash, seq, events, live, ghost>>
     ELSE RefuseStep("pm", "reassign", i, "class route")

\* row: split | pm | rework (conflict, reason too_large) | blocked(children) | at least two child drafts linked by parent; already-cancelled children move to former_children[] (D67); failures[] kept (D75) | none
\* (bound: exactly two children, in the two lowest free child slots)
Lowest(S) == CHOOSE x \in S : \A y \in S : x <= y
FreeSlots == {c \in ChildSlots : item[c].state = None}
Split(i) ==
  LET C == {Lowest(FreeSlots), Lowest(FreeSlots \ {Lowest(FreeSlots)})}
      gone == {c \in item[i].children : item[c].state = "cancelled"}
  IN
  /\ May(i, "split") /\ StateOf(i) = "rework" /\ Cardinality(FreeSlots) >= 2
  /\ IF item[i].failure = [class |-> "conflict", reason |-> "too_large"]
     THEN LET rec == [item[i] EXCEPT !.state = "blocked", !.blocked_reason = "children",
                        !.children = (@ \ gone) \cup C, !.former_children = @ \cup gone]
          IN /\ item' = [j \in Items |->
                           IF j = i THEN rec
                           ELSE IF j \in C THEN [EmptyItem EXCEPT !.state = "draft", !.parent = i]
                           ELSE item[j]]
             /\ version' = [j \in Items |-> IF j \in C \cup {i} THEN version[j] + 1 ELSE version[j]]
             /\ pane' = DirtyFor(C \cup {i}, "split")
             /\ step' = L("split", "pm", i, rec, "child drafts linked by parent")
             /\ ghost' = [ghost EXCEPT ![i].own_verify = FALSE]
             /\ UNCHANGED <<config_hash, seq, events, live, refusals>>
     ELSE RefuseStep("pm", "split", i, "class route: needs conflict with reason too_large")

\* row: (mesh) children done | mesh | blocked(children) | verifying | every linked child closed | none
MeshChildrenClosed(i) ==
  /\ May(i, "mesh") /\ StateOf(i) = Blocked("children")
  /\ \A c \in item[i].children : item[c].state = "closed"
  /\ LET rec == [item[i] EXCEPT !.state = "verifying", !.blocked_reason = "none",
                   !.attempt = [@ EXCEPT !.commit = MergedCommit,
                                         !.contract_version = item[i].contract_version,
                                         !.config_hash = config_hash]]
     IN Apply(i, rec, L("mesh", "mesh", i, rec, "every linked child closed"))
  /\ UNCHANGED <<config_hash, seq, events, live, ghost>>

\* row: (mesh) parent verify pass | mesh | verifying (split parent) | verified | the parent's own verification commands pass on the merged commit; evidence bound as for verify; clears a revalidate flag (D72, D69) | none
\* row: (mesh) parent verify fail | mesh | verifying (split parent) | rework | failure record class defect (D72) | none
MeshParentVerify(i) ==
  /\ May(i, "mesh") /\ StateOf(i) = "verifying" /\ item[i].attempt.commit = MergedCommit
  /\ \/ /\ InputsUnchanged(i)
        /\ LET rec == VerifiedRec(i, "merged")
           IN Apply(i, rec, L("mesh", "mesh", i, rec, "parent's own verification passed"))
        /\ ghost' = [ghost EXCEPT ![i].own_verify = TRUE]
        /\ UNCHANGED <<config_hash, seq, events, live>>
     \/ /\ LET rec == [item[i] EXCEPT !.state = "rework",
                         !.failure = [class |-> "defect", reason |-> "none"]]
           IN Apply(i, rec, L("mesh", "mesh", i, rec, "parent's own verification failed"))
        /\ UNCHANGED <<config_hash, seq, events, live, ghost>>

\* row: (mesh) child cancelled | mesh | blocked(children) | rework | a linked child is cancelled; remaining active children of the parent -> cancelled (reason parent_rework); the cancelled child is unlinked to former_children[] at the next split (D66, D67) | none
\* (no failure record of its own: the parent's latest record stays the split's conflict/too_large, D75)
MeshCancelledChild(i) ==
  /\ May(i, "mesh") /\ StateOf(i) = Blocked("children")
  /\ \E c \in item[i].children : item[c].state = "cancelled"
  /\ LET rec == [item[i] EXCEPT !.state = "rework", !.blocked_reason = "none"]
     IN CascadeWrite(i, rec, ActiveChildren(i), L("mesh", "mesh", i, rec,
                       "a linked child is cancelled; remaining active children cancelled (parent_rework)"))
  /\ ghost' = [ghost EXCEPT ![i].cancelled_child = TRUE]
  /\ UNCHANGED <<config_hash, seq, events, live>>

\* row: (mesh) dependency cancelled | mesh | ready | blocked(awaiting_user) | an item in depends was cancelled; reason dependency_cancelled (D71) | none
\* (no action of its own: a ready item can depend on a cancelled item only through the write that cancels
\* it, since ready, reassign and unblock refuse a cancelled dependency (D81); CascadeWrite moves it there)

\* row: (mesh) follow-up | mesh | closed | unchanged | a Slack thread reply (section 9) or a revalidate flag on a closed item creates one new draft with follows (D70) | none
\* (the new draft j is the row's declared side effect; one per closed item: "a burst ... one item")
FollowUp(i, j, why) ==
  /\ why \in FollowUpOn /\ May(i, "mesh") /\ StateOf(i) = "closed"
  /\ (why = "revalidate flag" => item[i].revalidate)
  /\ j \in ChildSlots /\ item[j].state = None
  /\ \A k \in Items : item[k].follows # i
  /\ item' = [item EXCEPT ![j] = [EmptyItem EXCEPT !.state = "draft", !.follows = i]]
  /\ version' = [version EXCEPT ![j] = @ + 1]
  /\ pane' = DirtyFor({j}, "mesh")
  /\ step' = StepRec("mesh", "mesh", "", i, "closed", "closed", FALSE,
                     why \o " on a closed item -> new draft with follows")
  /\ UNCHANGED <<config_hash, seq, events, live, ghost, refusals>>

EnvOverLimit(i, e) ==
  [item[i] EXCEPT !.state = "blocked", !.blocked_reason = "limit", !.environment_unblock = e,
     !.limit_seq = LimitEntrySeq, !.failure = [class |-> "limit", reason |-> "environment"]]
UnblockedRec(i, e) ==
  [item[i] EXCEPT !.state = "ready", !.blocked_reason = "none", !.environment_unblock = e,
     !.next_start_free = TRUE, !.failure = NoFailure, !.pinned_config = config_hash]

\* row: unblock | pm | blocked(environment) | ready | precheck passes; no dependency cancelled (D81); re-pins the verification config hash (D106); a refused precheck is a guard refusal and writes nothing to the item (D68) | environment +1 per passing unblock (D68); over its limit (default 3) -> blocked(limit) + Slack question
Unblock(i, precheck) ==
  /\ May(i, "unblock") /\ StateOf(i) = Blocked("environment")
  /\ IF precheck = "passes" /\ CancelledDepends(item[i].depends)
     THEN RefuseStep("pm", "unblock", i, D81Why)
     ELSE IF precheck = "passes"
     THEN LET e == item[i].environment_unblock + 1 IN
          IF e > EnvLimit
          THEN LET rec == EnvOverLimit(i, e)
               IN /\ seq' = seq + 1
                  /\ Apply(i, rec, [L("unblock", "pm", i, rec,
                                      "mesh: environment unblock over the limit; Slack question")
                                    EXCEPT !.limit_by = "mesh counter"])
                  /\ ghost' = [ghost EXCEPT ![i].limit_entry = seq + 1]
                  /\ UNCHANGED <<config_hash, events, live>>
          ELSE LET rec == UnblockedRec(i, e)
               IN /\ Apply(i, rec, L("unblock", "pm", i, rec, "precheck passes; environment +1"))
                  /\ UNCHANGED <<config_hash, seq, events, live, ghost>>
     ELSE RefuseStep("pm", "unblock", i, "precheck refused")

\* row: reject | pm | verified | rework, or blocked(awaiting_user) for --class ambiguity | --class required (defect, conflict, ambiguity; defect for a revalidate flag); trigger: an attached reply, a revalidate flag, or the PM's own evidence review with a written reason (D101); failure record (D65) | none
RejectTriggers == {"reply", "revalidate flag", "review"}
ReviewWhy(cls) == cls \o ": own evidence review with a written reason (D101)"
Reject(i, cls, trig) ==
  /\ May(i, "reject") /\ StateOf(i) = "verified"
  /\ \/ trig = "reply" /\ item[i].replies # {}
     \/ trig = "revalidate flag" /\ cls = "defect" /\ item[i].revalidate
     \/ trig = "review"
  /\ LET rec == [item[i] EXCEPT !.state = RouteOf(cls).state, !.blocked_reason = RouteOf(cls).blocked_reason,
                   !.failure = [class |-> cls, reason |-> "none"]]
     IN Apply(i, rec, L("reject", "pm", i, rec, IF trig = "review" THEN ReviewWhy(cls) ELSE cls))
  /\ ghost' = [ghost EXCEPT ![i].own_verify = FALSE]
  /\ UNCHANGED <<config_hash, seq, events, live>>

\* row: ack | pm | any non-terminal state with attached replies | unchanged | marks replies acknowledged | none
Ack(i) ==
  /\ May(i, "ack") /\ StateOf(i) \in NonTerminal
  /\ \E r \in item[i].replies : ~r.acknowledged
  /\ LET rec == [item[i] EXCEPT !.replies = {[r EXCEPT !.acknowledged = TRUE] : r \in @}]
     IN Apply(i, rec, L("ack", "pm", i, rec, "replies acknowledged"))
  /\ UNCHANGED <<config_hash, seq, events, live, ghost>>

NoUnacknowledged(i) == \A r \in item[i].replies : r.acknowledged

\* row: close | pm | verified | closed | no unacknowledged replies; no revalidate flag | none
Close(i) ==
  /\ May(i, "close") /\ StateOf(i) = "verified"
  /\ IF NoUnacknowledged(i) /\ ~item[i].revalidate
     THEN LET rec == [item[i] EXCEPT !.state = "closed"]
          IN /\ Apply(i, rec, L("close", "pm", i, rec, "accepted by the PM"))
             /\ UNCHANGED <<config_hash, seq, events, live, ghost>>
     ELSE RefuseStep("pm", "close", i, "unacknowledged replies or revalidate flag")

\* row: cancel | pm | any except closed | cancelled | reason; on a split parent its active children -> cancelled (reason parent_cancelled, D66); dependents in ready -> blocked(awaiting_user) (D71) | none
\* (cancel on an already cancelled item is listed but changes nothing; not modelled as a step)
Cancel(i) ==
  /\ May(i, "cancel") /\ StateOf(i) \in NonTerminal
  /\ LET rec == [item[i] EXCEPT !.state = "cancelled", !.blocked_reason = "none"]
     IN CascadeWrite(i, rec, ActiveChildren(i), L("cancel", "pm", i, rec, "reason"))
  /\ UNCHANGED <<config_hash, seq, events, live, ghost>>

\* row: revalidate | pm (integration gate) | verified, closed | unchanged (flag) | fingerprint re-check; flag on a closed item -> (mesh) follow-up row (D70) | none
\* (a re-check that finds no drift changes nothing and is not modelled)
Revalidate(i) ==
  /\ May(i, "revalidate") /\ StateOf(i) \in {"verified", "closed"} /\ ~item[i].revalidate
  /\ LET rec == [item[i] EXCEPT !.revalidate = TRUE]
     IN Apply(i, rec, L("revalidate", "pm", i, rec, "fingerprint re-check found drift"))
  /\ ghost' = [ghost EXCEPT ![i].flag_evidence = item[i].evidence]
  /\ UNCHANGED <<config_hash, seq, events, live>>

\* row: override | operator only (ask rule) | any | any except blocked(limit) (D73) | actor + reason recorded | none
Override(i, t) ==
  /\ WithOverride /\ StateOf(i) \in Created /\ t \in Created \ {StateOf(i), Blocked("limit")}
  /\ LET rec == IF t \in {Blocked(r) : r \in Reasons}
                THEN [item[i] EXCEPT !.state = "blocked", !.blocked_reason = ReasonOf(t)]
                ELSE [item[i] EXCEPT !.state = t, !.blocked_reason = "none"]
     IN Apply(i, rec, L("override", "operator", i, rec, "actor + reason recorded"))
  /\ UNCHANGED <<config_hash, seq, events, live, ghost>>

\* section 4 preamble / invariant 1: unlisted verb x caller x state combinations are refused and recorded
Refuse(i, v, c) ==
  /\ WithRefuse /\ ~Listed(v, c, StateOf(i))
  /\ RefuseStep(c, v, i, NotInTable)

\* environment steps (not verbs)
EditConfig ==
  /\ config_hash < 1 + ConfigEdits
  /\ config_hash' = config_hash + 1
  /\ step' = EnvStep("verification config edited (commands or required evidence)")
  /\ UNCHANGED <<item, version, seq, events, live, pane, ghost, refusals>>
LeadDown(i) ==
  /\ LeadLoss /\ live[i] /\ StateOf(i) \in {"running", "verifying"}
  /\ live' = [live EXCEPT ![i] = FALSE]
  /\ step' = EnvStep("assigned lead pane absent or dead")
  /\ UNCHANGED <<item, version, config_hash, seq, events, pane, ghost, refusals>>
LeadUp(i) ==
  /\ LeadLoss /\ ~live[i]
  /\ live' = [live EXCEPT ![i] = TRUE]
  /\ step' = EnvStep("lead pane live again")
  /\ UNCHANGED <<item, version, config_hash, seq, events, pane, ghost, refusals>>

\* Cheap guards come before each quantifier: TLC does not enumerate parameters of disabled actions.
Next ==
  \/ \E i \in Items :
       \/ item[i].state = None /\ (New(i) \/ SlackDraft(i))
       \/ Attach(i) \/ Revise(i) \/ Ready(i) \/ Dispatch(i)
       \/ StateOf(i) \in {"ready", "running"} /\ \E p \in LeadPanes : Start(i, p) \/ Submit(i, p)
       \/ Verify(i)
       \/ Panes # {} /\ \E p \in Panes : VerifyRead(i, p) \/ VerifyWrite(i, p)
       \/ StateOf(i) \in {"running", "verifying"} /\
            \E cls \in Classes, why \in {"none", "too_large"} :
              \/ \E p \in LeadPanes : Fail(i, Lead, p, cls, why)
              \/ Fail(i, "pm", "", cls, why)
       \/ Reassign(i)
       \/ Split(i)
       \/ MeshChildrenClosed(i) \/ MeshParentVerify(i) \/ MeshCancelledChild(i)
       \/ StateOf(i) = Blocked("environment") /\ \E pc \in {"passes", "refused"} : Unblock(i, pc)
       \/ StateOf(i) = "verified" /\ \E cls \in {"defect", "conflict", "ambiguity"}, t \in RejectTriggersInScope :
            Reject(i, cls, t)
       \/ Ack(i) \/ Close(i) \/ Cancel(i) \/ Revalidate(i)
       \/ MaxEvents > 0 /\ \E e \in 1..MaxEvents : Reply(i, e)
       \/ WithOverride /\ \E t \in Created : Override(i, t)
       \/ FollowUpOn # {} /\ StateOf(i) = "closed" /\ \E j \in Items, w \in FollowUpOn : FollowUp(i, j, w)
       \/ WithRefuse /\ \E v \in AllVerbs, c \in Callers : Refuse(i, v, c)
       \/ LeadLoss /\ (LeadDown(i) \/ LeadUp(i))
  \/ MaxEvents > 0 /\ \E e \in 1..MaxEvents : RecordEvent(e)
  \/ EditConfig

Spec == Init /\ [][Next]_vars

\* exploration bounds (TLC still checks the first state past a bound)
Bounds ==
  /\ seq <= MaxSeq
  /\ \A i \in Items : /\ item[i].contract_version <= MaxContractVersion
                      /\ item[i].attempt.id <= MaxAttempts
                      /\ (Panes # {} => version[i] <= MaxVersion)
  /\ Cardinality({i \in Items : item[i].state = "cancelled"}) <= MaxCancelled

\* state fingerprint: `step` and `refusals` are labels; `version` matters only with panes
View == <<item, IF Panes = {} THEN 0 ELSE version,
          config_hash, seq, events, live, pane, ghost>>

-----------------------------------------------------------------------------
(* Properties; invariant numbers are section 10's.  Where each is checked:  *)
(* the scenario cfgs (summary in the README).                               *)

StateNames == {None, "draft", "ready", "running", "verifying", "verified", "closed",
               "rework", "blocked", "cancelled"}
TypeOK ==
  /\ config_hash \in 1..(1 + ConfigEdits)
  /\ \A i \in Items :
       /\ item[i].state \in StateNames
       /\ item[i].blocked_reason \in Reasons \cup {"none"}
       /\ (item[i].state = "blocked") <=> (item[i].blocked_reason # "none")
       /\ item[i].retry \in Nat /\ item[i].environment_unblock \in Nat
       /\ item[i].failure.class \in Classes \cup {"none"}
       /\ item[i].depends \subseteq Items /\ item[i].children \subseteq Items
       /\ item[i].former_children \subseteq Items /\ item[i].children \cap item[i].former_children = {}
       /\ Cardinality(item[i].replies) <= MaxReplies

\* Invariant 1, part 1: every change of an item record is a section-4 row, read off the state diff
\* (the step's verb and caller with the item's actual state before and after, not its labels).  A
\* refused request may not move the state (its field writes: GuardRefusedUnchanged).  Declared side
\* effects on other items: split's child drafts; the follow-up draft of the (mesh) follow-up row;
\* the cascade cancel of a split parent's active children (cancel row, (mesh) child cancelled row,
\* D66) and of ready dependents of a cancelled item ((mesh) dependency cancelled row, D71).
Changed(i) == item'[i] # item[i] \/ version'[i] # version[i]
RowStep(i) ==
  /\ step'.kind = "verb" /\ step'.item_id = i
  /\ \/ ~step'.refused /\ RowAllows(step'.verb, step'.caller, StateOf(i), PS(item'[i]))
     \/ step'.refused /\ Listed(step'.verb, step'.caller, StateOf(i))
        /\ PS(item'[i]) \in RowTo[<<step'.verb, step'.caller, StateOf(i)>>] \cup {StateOf(i)}
NewlyCancelled == {d \in Items : item'[d].state = "cancelled" /\ item[d].state # "cancelled"}
SideEffect(i) ==
  LET k == step'.item_id IN
  /\ step'.kind = "verb" /\ ~step'.refused /\ k \in Items /\ k # i
  /\ RowAllows(step'.verb, step'.caller, StateOf(k), PS(item'[k]))
  /\ \/ /\ step'.verb = "split" /\ item[i].state = None
        /\ item'[i] = [EmptyItem EXCEPT !.state = "draft", !.parent = k]
     \/ /\ step'.verb = "mesh" /\ StateOf(k) = "closed" /\ item[i].state = None
        /\ item'[i] = [EmptyItem EXCEPT !.state = "draft", !.follows = k]
     \/ /\ step'.verb \in {"cancel", "mesh"} /\ i \in item[k].children /\ StateOf(i) \in NonTerminal
        /\ item'[i] = [item[i] EXCEPT !.state = "cancelled", !.blocked_reason = "none"]
     \/ /\ RowAllows("mesh", "mesh", StateOf(i), Blocked("awaiting_user"))
        /\ StateOf(i) = "ready" /\ item[i].depends \cap NewlyCancelled # {}
        /\ item'[i] = [item[i] EXCEPT !.state = "blocked", !.blocked_reason = "awaiting_user"]
TableRowsOnly == \A i \in Items : Changed(i) => RowStep(i) \/ SideEffect(i)
TableOnly == [][TableRowsOnly]_vars

\* Invariant 1, part 2: an unlisted combination is refused, recorded and leaves the record unchanged ...
RefusalRecorded ==
  refusals' = [caller |-> step'.caller, verb |-> step'.verb, item_id |-> step'.item_id, reason |-> step'.why]
RecordUnchanged == item' = item /\ version' = version
RefusedUnchanged == [][(step'.refused /\ step'.why = NotInTable) => RefusalRecorded /\ RecordUnchanged]_vars
\* ... and in every reachable state Next can refuse every unlisted combination that way (the step
\* is bound first so TLC prunes Next).  Only with WithRefuse; other scenarios leave them out of scope.
RefusalOf(i, v, c) ==
  /\ step' = StepRec(v, c, "", i, StateOf(i), StateOf(i), TRUE, NotInTable)
  /\ refusals' = [caller |-> c, verb |-> v, item_id |-> i, reason |-> NotInTable]
  /\ RecordUnchanged
UnlistedRefused ==
  \A i \in Items, v \in AllVerbs, c \in Callers :
    ~Listed(v, c, StateOf(i)) => ENABLED (RefusalOf(i, v, c) /\ Next)
\* Model-added (not section 10): a guard refusal of a listed combination is recorded and leaves the
\* record unchanged; the refused unblock precheck included (D68).
GuardRefusedUnchanged ==
  [][(step'.refused /\ step'.why # NotInTable) => RefusalRecorded /\ RecordUnchanged]_vars

\* Invariant 2, table walk: every non-terminal state has a row to another state that is neither cancel nor override.
TableExits ==
  \A s \in NonTerminal :
    \E r \in Table : /\ s \in r.from /\ r.verb \notin {"cancel", "override"}
                     /\ \E t \in r.to : t \notin {s, "unchanged"}

\* Invariant 2, behaviour: probes, each expected to be VIOLATED (a witness that the exit is taken).
NoExitStep(s) ==
  \A i \in Items : (StateOf(i) = s /\ PS(item'[i]) # s) => step'.verb \in {"cancel", "override"}
NoExit_draft                  == [][NoExitStep("draft")]_vars
NoExit_ready                  == [][NoExitStep("ready")]_vars
NoExit_running                == [][NoExitStep("running")]_vars
NoExit_verifying              == [][NoExitStep("verifying")]_vars
NoExit_verified               == [][NoExitStep("verified")]_vars
NoExit_rework                 == [][NoExitStep("rework")]_vars
NoExit_blocked_awaiting_user  == [][NoExitStep(Blocked("awaiting_user"))]_vars
NoExit_blocked_environment    == [][NoExitStep(Blocked("environment"))]_vars
NoExit_blocked_limit          == [][NoExitStep(Blocked("limit"))]_vars
NoExit_blocked_children       == [][NoExitStep(Blocked("children"))]_vars

\* Invariant 2, every reachable state: each non-terminal item has an enabled step (`Progress`:
\* every row action but cancel and override) that changes its state, or it waits on something
\* outside its record.  Meaningful only where the verb scope holds every way out (README).
Progress(i) ==
  \/ Ready(i) \/ Revise(i)
  \/ \E p \in LeadPanes : Start(i, p) \/ Submit(i, p)
  \/ Verify(i)
  \/ \E p \in Panes : VerifyWrite(i, p)
  \/ \E cls \in Classes, why \in {"none", "too_large"} :
       (\E p \in LeadPanes : Fail(i, Lead, p, cls, why)) \/ Fail(i, "pm", "", cls, why)
  \/ Reassign(i) \/ Split(i)
  \/ MeshChildrenClosed(i) \/ MeshParentVerify(i) \/ MeshCancelledChild(i)
  \/ \E pc \in {"passes", "refused"} : Unblock(i, pc)
  \/ \E cls \in {"defect", "conflict", "ambiguity"}, t \in RejectTriggersInScope : Reject(i, cls, t)
  \/ Close(i)
Moves(i) == PS(item'[i]) # StateOf(i)
Waiting(i) ==
  \/ StateOf(i) = Blocked("limit")     \* the way out is a fresh user reply (D52, D63)
  \/ StateOf(i) = Blocked("children") /\ \E c \in item[i].children : StateOf(c) \in NonTerminal
  \/ StateOf(i) = "verifying" /\ \E p \in Panes : pane[p].phase = "computing" /\ pane[p].item_id = i
  \/ StateOf(i) = "ready" /\ ~live[i]  \* start needs a live lead pane (D45)
  \/ StateOf(i) = "ready" /\ Panes # {} /\ \A p \in Panes : pane[p].phase # "idle"
  \/ StateOf(i) = "ready" /\ ~DependsSatisfied(i)
       /\ \A d \in item[i].depends : item[d].state # "cancelled"
NoDeadItem ==
  \A i \in Items : StateOf(i) \in NonTerminal => (ENABLED (Progress(i) /\ Moves(i)) \/ Waiting(i))

\* Invariant 3: probes, each expected to be VIOLATED (a witness that the state is reachable from draft).
Unreached(s) == \A i \in Items : StateOf(i) # s
Unreached_draft                 == Unreached("draft")
Unreached_ready                 == Unreached("ready")
Unreached_running               == Unreached("running")
Unreached_verifying             == Unreached("verifying")
Unreached_verified              == Unreached("verified")
Unreached_closed                == Unreached("closed")
Unreached_rework                == Unreached("rework")
Unreached_blocked_awaiting_user == Unreached(Blocked("awaiting_user"))
Unreached_blocked_environment   == Unreached(Blocked("environment"))
Unreached_blocked_limit         == Unreached(Blocked("limit"))
Unreached_blocked_children      == Unreached(Blocked("children"))
Unreached_cancelled             == Unreached("cancelled")

\* Invariant 3, table walk: every state is reachable from draft through rows other than override.
RECURSIVE TableClosure(_)
TableClosure(S) ==
  LET R == {r \in Table : r.verb # "override" /\ r.from \cap S # {}}
      T == S \cup UNION {r.to \ {"unchanged"} : r \in R}
  IN IF T = S THEN S ELSE TableClosure(T)
TableReach == TableClosure({"draft"}) = Created

\* Invariant 4: `limit` is never set by a caller, override included (only the mesh counters set it).
LimitOnlyByMesh ==
  [][\A i \in Items :
       (\/ (PS(item'[i]) = Blocked("limit") /\ StateOf(i) # Blocked("limit"))
        \/ (item'[i].failure.class = "limit" /\ item[i].failure.class # "limit"))
       => step'.limit_by = "mesh counter"]_vars

\* Invariant 5: whichever verb returns the item to ready, the starts between two counter resets are bounded:
\* the first, at most RetryLimit counted retries, at most one uncounted start per counted environment unblock.
BoundedStarts == \A i \in Items : ghost[i].starts <= 1 + RetryLimit + EnvLimit

\* Invariant 6: verified carries evidence bound to the submitted commit, contract version, config hash, failing_before.
VerifiedBound ==
  \A i \in Items : item[i].state = "verified" =>
    LET ev == item[i].evidence
        a == item[i].attempt
    IN /\ ev.run \in {"attempt", "merged"}
       /\ a.commit # 0 /\ ev.commit = a.commit
       /\ ev.contract_version = item[i].contract_version
       /\ a.contract_version = item[i].contract_version
       /\ ev.config_hash = a.config_hash
       /\ (ev.run = "attempt" /\ i \in BugfixItems)
            => item[i].failing_before = [contract_version |-> item[i].contract_version,
                                         repro |-> ReproHash(i)]

\* D104: ready from rework / blocked(awaiting_user) carries the revision the latest failure class needs.
ReadyRevisesByClass ==
  [][\A i \in Items : (/\ step'.verb = "ready" /\ ~step'.refused /\ step'.item_id = i
                        /\ StateOf(i) \in {"rework", Blocked("awaiting_user")} /\ PS(item'[i]) = "ready"
                        /\ RequiredRevision(i) # "any")
                        => step'.why = RevisedWhy(RequiredRevision(i))]_vars

\* D105: every submitted commit descends from the attempt's base commit.
SubmitDescendsFromBase ==
  [][\A i \in Items : (StateOf(i) = "running" /\ PS(item'[i]) = "verifying")
                        => Descends(item'[i].attempt.commit, item'[i].attempt.base)]_vars

\* D106: every PM verb that enters ready (ready, reassign, unblock) pins the current verification config hash.
PinnedOnEnteringReady ==
  [][\A i \in Items : (/\ step'.verb \in {"ready", "reassign", "unblock"} /\ ~step'.refused
                        /\ step'.item_id = i /\ PS(item'[i]) = "ready")
                        => item'[i].pinned_config = config_hash]_vars

\* D100: no attempt starts under a verification config hash other than the one pinned at ready.
StartUnderPinnedConfig ==
  [][\A i \in Items : (StateOf(i) = "ready" /\ PS(item'[i]) = "running")
                        => item'[i].attempt.config_hash = item[i].pinned_config]_vars

\* Invariant 7: closed never happens with unacknowledged replies or a revalidate flag.
CloseGates ==
  [][\A i \in Items : (item'[i].state = "closed" /\ item[i].state # "closed")
                        => (NoUnacknowledged(i) /\ ~item[i].revalidate)]_vars

\* Invariant 8: a dependency is satisfied only by verified or closed ...
StartNeedsDepends ==
  [][\A i \in Items : (item[i].state = "ready" /\ item'[i].state = "running")
                        => \A d \in item[i].depends : item[d].state \in {"verified", "closed"}]_vars
\* ... cancelled dependencies are refused at ready, reassign and unblock (D81) ...
DependsNotCancelledAtReady ==
  [][\A i \in Items : (step'.item_id = i /\ step'.verb \in {"ready", "reassign", "unblock"} /\ ~step'.refused
                         /\ PS(item'[i]) = "ready")
                        => ~\E d \in item'[i].depends : item'[d].state = "cancelled"]_vars
\* ... and unknown ids, self-dependencies and cycles never get past `ready`.
DependsWellFormed ==
  \A i \in Items : item[i].state \notin {None, "draft"} =>
    /\ i \notin item[i].depends
    /\ \A d \in item[i].depends : item[d].state # None
    /\ i \notin Closure(item[i].depends, N)

\* Invariant 9: a split parent closes only through its own verification (and the close gates, CloseGates).
ParentOwnVerification ==
  \A i \in Items : (item[i].children # {} /\ item[i].state = "closed") => ghost[i].own_verify

\* Invariant 10: a user reply unlocks blocked(limit) at most once (LimitReplySingleUse) and only
\* if its mesh seq is greater than limit_seq (LimitReplyFresh): the consumed reply's seq against the
\* entry's seq kept by the ghost, not against the recorded limit_seq.
LimitResume(i) == StateOf(i) = Blocked("limit") /\ PS(item'[i]) = "ready"
LimitReplySingleUse ==
  [][\A i \in Items : LimitResume(i) =>
       /\ \E r \in item[i].replies : r.seq = step'.reply
       /\ step'.reply \notin ghost[i].unlocks
       /\ ghost'[i].unlocks = ghost[i].unlocks \cup {step'.reply}]_vars
LimitReplyFresh ==
  [][\A i \in Items : LimitResume(i) =>
       \E r \in item[i].replies : r.seq = step'.reply /\ r.seq > ghost[i].limit_entry]_vars

\* Invariant 11: a stale result (section 8: an attempt-relevant field or the attempt id changed since the
\* read) is never written.  `dirty` is a ghost set by every write after the pane's read that changed an
\* attempt-relevant field, even if a later write changed it back.
StaleNeverWritten ==
  [][/\ (step'.kind = "verb" /\ step'.verb = "verify" /\ step'.pane \in Panes /\ ~step'.refused)
          => (~pane[step'.pane].dirty /\ pane[step'.pane].read_attempt = item[step'.item_id].attempt.id)
     /\ (step'.verb \in {"submit", "fail"} /\ step'.pane \in Panes /\ ~step'.refused)
          => pane[step'.pane].owns = item[step'.item_id].attempt.id]_vars

\* Section 8 (D76, was note N1): a reply-side write (attach, reply, ack) during an unlocked verify does
\* not make the compare-and-set discard the run.
ReplyNeverDiscardsVerify ==
  [][(step'.verb = "verify" /\ step'.refused /\ step'.why = StaleWhy /\ step'.pane \in Panes)
       => ~(/\ pane[step'.pane].dirty_by # {}
            /\ pane[step'.pane].dirty_by \subseteq {"attach", "reply", "ack"})]_vars

\* Invariant 12 (no command run changes the shared working tree) is outside the model:
\* a run-time check of the implementation (README, invariant 12).

\* Invariant 13 (D66): no active child remains linked to a split parent that is in rework or cancelled.
NoOrphanChildren ==
  \A i \in Items : item[i].state \in {"rework", "cancelled"} =>
    \A c \in item[i].children : item[c].state \in Terminal

\* Invariant 14 (D71, D81): a ready item never depends on a cancelled item ...
NoWaitOnCancelled ==
  \A i \in Items : item[i].state = "ready" => \A d \in item[i].depends : item[d].state # "cancelled"
\* ... and the cancel row's part of it: a write that cancels an item moves its ready dependents to
\* blocked(awaiting_user) in the same write.
DependentsBlockedOnCancel ==
  [][\A i \in Items : (StateOf(i) = "ready" /\ i \notin NewlyCancelled
                         /\ item[i].depends \cap NewlyCancelled # {})
                        => PS(item'[i]) = Blocked("awaiting_user")]_vars

\* Invariant 15 (D69): a passing verification leaves no revalidate flag from before it: a flag on a
\* verified item was set after its current verification.
FlagClearedByReverify ==
  \A i \in Items : (item[i].state = "verified" /\ item[i].revalidate)
                     => ghost[i].flag_evidence = item[i].evidence

\* Row coverage: NoRow_<row> says that row outcome (verb, caller, from, to) never fires; each is
\* expected VIOLATED in a scenario with the row in scope (row-* runs); one that holds is a dead row.
Fires(v, c, F, T) ==
  /\ step'.kind = "verb" /\ ~step'.refused /\ step'.verb = v /\ step'.caller = c
  /\ step'.item_id \in Items /\ StateOf(step'.item_id) \in F /\ PS(item'[step'.item_id]) \in T
NoRow_new              == [][~Fires("new", "pm", {None}, {"draft"})]_vars
NoRow_draft            == [][~Fires("draft", "slack watcher", {None}, {"draft"})]_vars
NoRow_attach           == [][~Fires("attach", "slack watcher", NonTerminal, NonTerminal)]_vars
NoRow_reply            == [][~Fires("reply", "pm", NonTerminal, NonTerminal)]_vars
NoRow_revise           == [][~Fires("revise", "pm", {"ready"}, {"draft"})]_vars
NoRow_ready            == [][~Fires("ready", "pm", {"draft", "rework", Blocked("awaiting_user"), Blocked("limit")}, {"ready"})]_vars
NoRow_dispatch         == [][~Fires("dispatch", "pm", {"ready"}, {"ready"})]_vars
NoRow_start            == [][~Fires("start", Lead, {"ready"}, {"running"})]_vars
NoRow_start_limit      == [][~Fires("start", Lead, {"ready"}, {Blocked("limit")})]_vars
NoRow_submit           == [][~Fires("submit", Lead, {"running"}, {"verifying"})]_vars
NoRow_verify           == [][~Fires("verify", Lead, {"verifying"}, {"verified"})]_vars
NoRow_fail_rework      == [][~Fires("fail", Lead, {"running", "verifying"}, {"rework"})]_vars
NoRow_fail_awaiting    == [][~Fires("fail", Lead, {"running", "verifying"}, {Blocked("awaiting_user")})]_vars
NoRow_fail_environment == [][~Fires("fail", Lead, {"running", "verifying"}, {Blocked("environment")})]_vars
NoRow_fail_pm          == [][~Fires("fail", "pm", {"running", "verifying"}, {Blocked("environment")})]_vars
NoRow_reassign         == [][~Fires("reassign", "pm", {"rework"}, {"ready"})]_vars
NoRow_split            == [][~Fires("split", "pm", {"rework"}, {Blocked("children")})]_vars
NoRow_mesh_verifying   == [][~Fires("mesh", "mesh", {Blocked("children")}, {"verifying"})]_vars
NoRow_mesh_rework      == [][~Fires("mesh", "mesh", {Blocked("children")}, {"rework"})]_vars
NoRow_mesh_verified    == [][~Fires("mesh", "mesh", {"verifying"}, {"verified"})]_vars
NoRow_mesh_verify_fail == [][~Fires("mesh", "mesh", {"verifying"}, {"rework"})]_vars
\* (mesh) dependency cancelled runs inside the cancelling write, on the dependents of the step's item
NoRow_mesh_dependency  == [][~\E j \in Items : /\ step'.kind = "verb" /\ ~step'.refused
                                               /\ StateOf(j) = "ready" /\ PS(item'[j]) = Blocked("awaiting_user")
                                               /\ item[j].depends \cap NewlyCancelled # {}]_vars
NoRow_mesh_follow_up   == [][~Fires("mesh", "mesh", {"closed"}, {"closed"})]_vars
NoRow_unblock          == [][~Fires("unblock", "pm", {Blocked("environment")}, {"ready"})]_vars
NoRow_unblock_limit    == [][~Fires("unblock", "pm", {Blocked("environment")}, {Blocked("limit")})]_vars
NoRow_reject           == [][~Fires("reject", "pm", {"verified"}, {"rework"})]_vars
NoRow_reject_awaiting  == [][~Fires("reject", "pm", {"verified"}, {Blocked("awaiting_user")})]_vars
NoRow_ack              == [][~Fires("ack", "pm", NonTerminal, NonTerminal)]_vars
NoRow_close            == [][~Fires("close", "pm", {"verified"}, {"closed"})]_vars
NoRow_cancel           == [][~Fires("cancel", "pm", NonTerminal, {"cancelled"})]_vars
NoRow_revalidate       == [][~Fires("revalidate", "pm", {"verified", "closed"}, {"verified", "closed"})]_vars
NoRow_override         == [][~Fires("override", "operator", Created, Created)]_vars

-----------------------------------------------------------------------------
(* Decision probes (README, resolved findings): each states a behaviour a   *)
(* WO-0016 finding showed missing; under D65-D76 each holds.                 *)

\* D101: in every reachable state the PM can reject a verified item on its own evidence review, with
\* any of the three classes, without an attached reply or a revalidate flag.
ReviewRejectEnabled ==
  \A i \in Items : (StateOf(i) = "verified" /\ May(i, "reject"))
    => \A cls \in {"defect", "conflict", "ambiguity"} : ENABLED Reject(i, cls, "review")
\* D101 witness, expected VIOLATED: a reject on the PM's own review fires (not a NoRow_ probe: the
\* trigger is a guard of the reject row, not a row outcome of its own).
NoReviewReject ==
  [][~(step'.verb = "reject" /\ ~step'.refused
       /\ \E cls \in {"defect", "conflict", "ambiguity"} : step'.why = ReviewWhy(cls))]_vars

\* D65 (was G1): every failure record sends the item where section 5 routes its class.
RouteByClass ==
  [][\A i \in Items :
       (item'[i].failure # item[i].failure
          /\ item'[i].failure.class \in {"defect", "conflict", "ambiguity", "environment"})
       => PS(item'[i]) = PS(RouteOf(item'[i].failure.class))]_vars

\* D75 (was G2a): every rework has a class with a section-5 way back (reassign, ready or split).
RouteDefined ==
  \A i \in Items : item[i].state = "rework" => item[i].failure.class \in {"defect", "conflict", "ambiguity"}

\* Re-split witnesses (D67): after a cancelled child the parent is never split again ...
NoResplitAfterCancelledChild ==
  \A i \in Items : ~(ghost[i].cancelled_child /\ StateOf(i) = Blocked("children"))
\* ... and never closes through its children (merged verification).  Both are expected VIOLATED.
NoMergedCloseAfterCancelledChild ==
  \A i \in Items : ~(ghost[i].cancelled_child /\ item[i].state = "closed" /\ item[i].evidence.run = "merged")

\* D67 (was O3-8): a split leaves no already-cancelled child linked; else "every linked child closed" never
\* holds and the (mesh) child cancelled row fires at once: the re-split would never complete.
ResplitCanComplete ==
  [][\A i \in Items : (StateOf(i) = "rework" /\ PS(item'[i]) = Blocked("children"))
                        => \A c \in item'[i].children : item'[c].state # "cancelled"]_vars
=============================================================================
