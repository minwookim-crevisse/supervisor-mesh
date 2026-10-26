#!/usr/bin/env bash
# Model-check the mesh work-item state machine (tests/formal/WorkItemState.tla) with TLC.
# One command: scripts/formal-check.sh   (or: make formal)
#
# Every run in the EXPECT table below is one TLC model check: a scenario config from
# tests/formal/ plus, optionally, one extra property. Each run has an expected result:
#   pass             the model satisfies every property of the run
#   violate:<prop>   TLC reports <prop> violated AND the counterexample's last step is the
#                    run's "last step" column (caller verb item: from -> to, read off the
#                    trace's states; a refusal adds its reason). Another counterexample of
#                    the same property is a different result, not this run.
# Verdicts printed per run:
#   PASS        every property of the run holds within the bounds
#   REACHED     a witness probe was violated as required: an inv 2 exit is taken, an inv 3
#               state is reached, a table row fires, an O3-8 alternative behaviour happens
#   FINDING     a known design finding reproduced; its counterexample is printed
#   READING     a known finding under one reading of an ambiguous row (G2a); printed too
#   STRUCTURAL  a known finding read off the table itself; TLC only replays it (G5, G8)
#   NOTE        a known consequence that breaks no section-10 invariant (N1)
#   UNEXPECTED  anything else (another property, another last step, a TLC error); the
#               trace is printed and the run's logs are kept
# Exit code:
#   0  every run matched its expectation (known findings reproduced and are listed)
#   1  at least one UNEXPECTED (with --variants: a variant not as expected). Look before
#      you edit EXPECT: a changed result means the model or the design changed.
#   2  usage error, or tool setup failed (python3/Java/TLC missing, unverifiable, hash mismatch)
#   3  only with --strict: everything matched, but known findings are still present
#   4  STALE: the design file is not the version the model is synced to (DESIGN_SHA256)
# Options:
#   --jobs N       parallel TLC runs, a positive integer (default nproc/4, between 2 and 6)
#   --only REGEX   only the runs (with --variants: the variants) whose name matches
#   --traces       also print the traces of REACHED runs
#   --keep         keep the whole work dir and print its path
#   --strict       exit 3 while known findings remain
#   --allow-stale  run although the design changed since the sync point (STALE is printed)
#   --variants     run the broken/fixed variants of tests/formal/variants.py instead
# Environment: FORMAL_DIR=<dir> checks a scratch copy of tests/formal instead (triage, a
#   deliberate break); TLA_TOOLS_DIR moves the pinned tools (default ~/.local/share/tlaplus).
# Files: nothing is written into the repo or the system temp dir. Each invocation works in a
#   fresh dir under ${XDG_CACHE_HOME:-~/.cache}/formal-check/ (TMPDIR and TLC's java.io.tmpdir
#   point into it). It is removed at exit, except with --keep and except the dirs of
#   UNEXPECTED runs and NOT AS EXPECTED variants, whose path is printed. Ctrl-C or TERM
#   stops every TLC run, waits for it, then removes the work dir.
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
FORMAL="${FORMAL_DIR:-$ROOT/tests/formal}"
SPEC=WorkItemState
TOOLS="${TLA_TOOLS_DIR:-$HOME/.local/share/tlaplus}"

# Sync point: the design version the model describes (the skill's sync record names it too).
DESIGN="$ROOT/docs/designs/work-item-state-model.md"
DESIGN_SHA256="f7235b6acb7cd57287efd48d785cbe4770143dca43a7af1711e56dfad8827c1b"

# Pinned toolchain. JRE_SHA256 is the published Temurin archive (checked at install);
# JRE_TREE_SHA256 is the manifest of that archive's extracted files (jre_tree_sha: every
# path, link target, owner-executable bit and file sha256), checked on every run.
JRE_VERSION="21.0.12.1+1"
JRE_DIR="$TOOLS/jre-jdk-21.0.12.1+1"
JRE_URL="https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1/OpenJDK21U-jre_x64_linux_hotspot_21.0.12.1_1.tar.gz"
JRE_SHA256="2413149700df0f7d440500a84a8f764c535f21e5a5e87d38328b64eec2c5b500"
JRE_TREE_SHA256="64a45676f2d374bc30eb6e6a868ea42bc62d44217b0e9546fcf971c08568f4a4"
TLA_VERSION="1.8.0"
JAR="$TOOLS/tla2tools-$TLA_VERSION.jar"
TLA_URL="https://github.com/tlaplus/tlaplus/releases/download/v$TLA_VERSION/tla2tools.jar"
TLA_SHA256="5bd7b64db99ae4c6c35f290de5e809c0ee41af43f8699cf696099ffb6452b1e2"
JAVA="$JRE_DIR/bin/java"
# -XX:-UsePerfData: no hsperfdata file in the system temp dir
JAVA_OPTS="${FORMAL_JAVA_OPTS:--XX:+UseParallelGC -XX:ParallelGCThreads=2 -XX:TieredStopAtLevel=1 -Xmx1g -XX:-UsePerfData}"

# run | scenario cfg | extra cfg line ("-" = none) | expected | last step ("-" for pass) | kind + label
#   kind: check -> PASS, witness -> REACHED, finding -> FINDING, reading -> READING,
#         structural -> STRUCTURAL, note -> NOTE
EXPECT=$(cat <<'TABLE'
core                     | Core             | -                                          | pass                                     | -                                                                                                                    | check O3-9 unblock_pass
core-g4                  | Core             | INVARIANT FlagClearedByReverify            | violate:FlagClearedByReverify            | lead (own role) verify item 1: verifying -> verified                                                                 | finding G4
o39-refused              | CoreO39Refused   | -                                          | pass                                     | -                                                                                                                    | check O3-9 precheck_refused
o39-refused-g3           | CoreO39Refused   | PROPERTY GuardRefusalWritesNothing         | violate:GuardRefusalWritesNothing        | pm unblock item 1: blocked(environment) -> blocked(environment) [REFUSED: precheck refused; environment +1]          | finding G3 (O3-9 precheck_refused: a guard refusal writes the record)
o39-refused-inv5         | CoreO39Refused   | INVARIANT BoundedStarts                    | violate:BoundedStarts                    | lead (own role) start item 1: ready -> running                                                                       | finding O3-9 precheck_refused breaks inv 5
replies                  | Replies          | -                                          | pass                                     | -                                                                                                                    | check
replies-g1               | Replies          | PROPERTY RouteByClass                      | violate:RouteByClass                     | pm reject item 1: verified -> rework                                                                                 | finding G1
replies-ties             | RepliesClockTies | -                                          | pass                                     | -                                                                                                                    | check ClockTies: single use holds (N2)
replies-ties-inv10       | RepliesClockTies | PROPERTY LimitReplyFresh                   | violate:LimitReplyFresh                  | pm ready item 1: blocked(limit) -> ready                                                                             | finding inv 10 under ClockTies (G7)
split                    | Split            | -                                          | pass                                     | -                                                                                                                    | check G2a reading failures kept; O3-8 stay_linked
split-g2b                | Split            | INVARIANT NoOrphanChildren                 | violate:NoOrphanChildren                 | mesh mesh item 1: blocked(children) -> rework                                                                        | finding G2b
split-f2                 | Split            | INVARIANT CancelledParentNoActiveChild     | violate:CancelledParentNoActiveChild     | pm cancel item 1: blocked(children) -> cancelled                                                                     | finding F2
split-g8                 | Split            | PROPERTY TableOnly                         | violate:TableOnly                        | mesh mesh item 1: verifying -> verified                                                                              | structural G8
split-consumed           | SplitConsumed    | -                                          | pass                                     | -                                                                                                                    | check G2a reading split consumes the record: classless rework stuck
split-consumed-g2a       | SplitConsumed    | INVARIANT RouteDefined                     | violate:RouteDefined                     | mesh mesh item 1: blocked(children) -> rework                                                                        | reading G2a (split consumes the failure record)
split-consumed-g2a-inv2  | SplitConsumed    | INVARIANT NoDeadItem                       | violate:NoDeadItem                       | mesh mesh item 1: blocked(children) -> rework                                                                        | reading G2a (inv 2: dead parent)
o38-stay                 | SplitStayLinked  | -                                          | pass                                     | -                                                                                                                    | check O3-8 stay_linked
o38-stay-resplit         | SplitStayLinked  | INVARIANT NoResplitAfterCancelledChild     | violate:NoResplitAfterCancelledChild     | pm split item 1: rework -> blocked(children)                                                                         | witness O3-8 stay_linked: re-split happens
o38-stay-never-completes | SplitStayLinked  | PROPERTY ResplitCanComplete                | violate:ResplitCanComplete               | pm split item 1: rework -> blocked(children)                                                                         | finding O3-8 stay_linked (as written): a re-split never completes
o38-stay-orphans         | SplitStayLinked  | INVARIANT NoOrphanChildren                 | violate:NoOrphanChildren                 | mesh mesh item 1: blocked(children) -> rework                                                                        | finding G2b (O3-8 stay_linked)
o38-unlink               | SplitUnlink      | -                                          | pass                                     | -                                                                                                                    | check O3-8 unlink
o38-unlink-resplit       | SplitUnlink      | INVARIANT NoResplitAfterCancelledChild     | violate:NoResplitAfterCancelledChild     | pm split item 1: rework -> blocked(children)                                                                         | witness O3-8 unlink: re-split happens
o38-unlink-close         | SplitUnlink      | INVARIANT NoMergedCloseAfterCancelledChild | violate:NoMergedCloseAfterCancelledChild | pm close item 1: verified -> closed                                                                                  | witness O3-8 unlink: re-split completes
o38-unlink-orphans       | SplitUnlink      | INVARIANT NoOrphanChildren                 | violate:NoOrphanChildren                 | mesh mesh item 1: blocked(children) -> rework                                                                        | finding G2b (O3-8 unlink)
deps                     | Deps             | -                                          | pass                                     | -                                                                                                                    | check
deps-g6                  | Deps             | INVARIANT NoWaitOnCancelled                | violate:NoWaitOnCancelled                | pm ready item 2: draft -> ready                                                                                      | finding G6
cas                      | Cas              | -                                          | pass                                     | -                                                                                                                    | check
cas-n1                   | Cas              | PROPERTY ReplyNeverDiscardsVerify          | violate:ReplyNeverDiscardsVerify         | lead (own role) verify item 1: verifying -> verifying [REFUSED: stale result discarded: version or attempt mismatch] | note N1
tablewalk                | TableWalk        | -                                          | pass                                     | -                                                                                                                    | check
tablewalk-f1             | TableWalk        | PROPERTY LimitOnlyByMesh                   | violate:LimitOnlyByMesh                  | operator override item 1: draft -> blocked(limit)                                                                    | finding F1 (override sets blocked(limit))
followup                 | FollowUp         | -                                          | pass                                     | -                                                                                                                    | check
followup-g5              | FollowUp         | PROPERTY TableOnly                         | violate:TableOnly                        | slack watcher (follow-up draft) item 1: closed -> closed                                                             | structural G5 (thread reply on a closed item)
followup-g5-flag         | FollowUpFlag     | PROPERTY TableOnly                         | violate:TableOnly                        | pm (follow-up draft) item 1: closed -> closed                                                                        | structural G5 (revalidate flag on a closed item)
reach-draft              | Replies          | INVARIANT Unreached_draft                  | violate:Unreached_draft                  | pm new item 1: (none) -> draft                                                                                       | witness inv 3
reach-ready              | Replies          | INVARIANT Unreached_ready                  | violate:Unreached_ready                  | pm ready item 1: draft -> ready                                                                                      | witness inv 3
reach-running            | Replies          | INVARIANT Unreached_running                | violate:Unreached_running                | lead (own role) start item 1: ready -> running                                                                       | witness inv 3
reach-verifying          | Replies          | INVARIANT Unreached_verifying              | violate:Unreached_verifying              | lead (own role) submit item 1: running -> verifying                                                                  | witness inv 3
reach-verified           | Replies          | INVARIANT Unreached_verified               | violate:Unreached_verified               | lead (own role) verify item 1: verifying -> verified                                                                 | witness inv 3
reach-closed             | Replies          | INVARIANT Unreached_closed                 | violate:Unreached_closed                 | pm close item 1: verified -> closed                                                                                  | witness inv 3
reach-rework             | Replies          | INVARIANT Unreached_rework                 | violate:Unreached_rework                 | lead (own role) fail item 1: running -> rework                                                                       | witness inv 3
reach-awaiting           | Replies          | INVARIANT Unreached_blocked_awaiting_user  | violate:Unreached_blocked_awaiting_user  | lead (own role) fail item 1: running -> blocked(awaiting_user)                                                       | witness inv 3
reach-environment        | Replies          | INVARIANT Unreached_blocked_environment    | violate:Unreached_blocked_environment    | lead (own role) fail item 1: running -> blocked(environment)                                                         | witness inv 3
reach-limit              | Replies          | INVARIANT Unreached_blocked_limit          | violate:Unreached_blocked_limit          | pm unblock item 1: blocked(environment) -> blocked(limit)                                                            | witness inv 3
reach-children           | Split            | INVARIANT Unreached_blocked_children       | violate:Unreached_blocked_children       | pm split item 1: rework -> blocked(children)                                                                         | witness inv 3
reach-cancelled          | Replies          | INVARIANT Unreached_cancelled              | violate:Unreached_cancelled              | pm cancel item 1: draft -> cancelled                                                                                 | witness inv 3
exit-draft               | Replies          | PROPERTY NoExit_draft                      | violate:NoExit_draft                     | pm ready item 1: draft -> ready                                                                                      | witness inv 2
exit-ready               | Replies          | PROPERTY NoExit_ready                      | violate:NoExit_ready                     | lead (own role) start item 1: ready -> running                                                                       | witness inv 2
exit-running             | Replies          | PROPERTY NoExit_running                    | violate:NoExit_running                   | lead (own role) submit item 1: running -> verifying                                                                  | witness inv 2
exit-verifying           | Replies          | PROPERTY NoExit_verifying                  | violate:NoExit_verifying                 | lead (own role) verify item 1: verifying -> verified                                                                 | witness inv 2
exit-verified            | Replies          | PROPERTY NoExit_verified                   | violate:NoExit_verified                  | pm close item 1: verified -> closed                                                                                  | witness inv 2
exit-rework              | Replies          | PROPERTY NoExit_rework                     | violate:NoExit_rework                    | pm reassign item 1: rework -> ready                                                                                  | witness inv 2
exit-awaiting            | Replies          | PROPERTY NoExit_blocked_awaiting_user      | violate:NoExit_blocked_awaiting_user     | pm ready item 1: blocked(awaiting_user) -> ready                                                                     | witness inv 2
exit-environment         | Replies          | PROPERTY NoExit_blocked_environment        | violate:NoExit_blocked_environment       | pm unblock item 1: blocked(environment) -> blocked(limit)                                                            | witness inv 2
exit-limit               | Replies          | PROPERTY NoExit_blocked_limit              | violate:NoExit_blocked_limit             | pm ready item 1: blocked(limit) -> ready                                                                             | witness inv 2
exit-children            | Split            | PROPERTY NoExit_blocked_children           | violate:NoExit_blocked_children          | mesh mesh item 1: blocked(children) -> rework                                                                        | witness inv 2
row-new                  | Replies          | PROPERTY NoRow_new                         | violate:NoRow_new                        | pm new item 1: (none) -> draft                                                                                       | witness row
row-draft                | Core             | PROPERTY NoRow_draft                       | violate:NoRow_draft                      | slack watcher draft item 1: (none) -> draft                                                                          | witness row
row-attach               | Replies          | PROPERTY NoRow_attach                      | violate:NoRow_attach                     | slack watcher attach item 1: draft -> draft                                                                          | witness row
row-reply                | Replies          | PROPERTY NoRow_reply                       | violate:NoRow_reply                      | pm reply item 1: draft -> draft                                                                                      | witness row
row-revise               | Core             | PROPERTY NoRow_revise                      | violate:NoRow_revise                     | pm revise item 1: ready -> draft                                                                                     | witness row
row-ready                | Replies          | PROPERTY NoRow_ready                       | violate:NoRow_ready                      | pm ready item 1: draft -> ready                                                                                      | witness row
row-dispatch             | Core             | PROPERTY NoRow_dispatch                    | violate:NoRow_dispatch                   | pm dispatch item 1: ready -> ready                                                                                   | witness row
row-start                | Replies          | PROPERTY NoRow_start                       | violate:NoRow_start                      | lead (own role) start item 1: ready -> running                                                                       | witness row
row-start-limit          | Replies          | PROPERTY NoRow_start_limit                 | violate:NoRow_start_limit                | lead (own role) start item 1: ready -> blocked(limit)                                                                | witness row
row-submit               | Replies          | PROPERTY NoRow_submit                      | violate:NoRow_submit                     | lead (own role) submit item 1: running -> verifying                                                                  | witness row
row-verify               | Replies          | PROPERTY NoRow_verify                      | violate:NoRow_verify                     | lead (own role) verify item 1: verifying -> verified                                                                 | witness row
row-fail-rework          | Replies          | PROPERTY NoRow_fail_rework                 | violate:NoRow_fail_rework                | lead (own role) fail item 1: running -> rework                                                                       | witness row
row-fail-awaiting        | Replies          | PROPERTY NoRow_fail_awaiting               | violate:NoRow_fail_awaiting              | lead (own role) fail item 1: running -> blocked(awaiting_user)                                                       | witness row
row-fail-environment     | Replies          | PROPERTY NoRow_fail_environment            | violate:NoRow_fail_environment           | lead (own role) fail item 1: running -> blocked(environment)                                                         | witness row
row-fail-pm              | Core             | PROPERTY NoRow_fail_pm                     | violate:NoRow_fail_pm                    | pm fail item 1: running -> blocked(environment)                                                                      | witness row
row-reassign             | Replies          | PROPERTY NoRow_reassign                    | violate:NoRow_reassign                   | pm reassign item 1: rework -> ready                                                                                  | witness row
row-split                | Split            | PROPERTY NoRow_split                       | violate:NoRow_split                      | pm split item 1: rework -> blocked(children)                                                                         | witness row
row-mesh-verifying       | Split            | PROPERTY NoRow_mesh_verifying              | violate:NoRow_mesh_verifying             | mesh mesh item 1: blocked(children) -> verifying                                                                     | witness row
row-mesh-rework          | Split            | PROPERTY NoRow_mesh_rework                 | violate:NoRow_mesh_rework                | mesh mesh item 1: blocked(children) -> rework                                                                        | witness row
row-unblock              | Core             | PROPERTY NoRow_unblock                     | violate:NoRow_unblock                    | pm unblock item 1: blocked(environment) -> ready                                                                     | witness row
row-unblock-limit        | Replies          | PROPERTY NoRow_unblock_limit               | violate:NoRow_unblock_limit              | pm unblock item 1: blocked(environment) -> blocked(limit)                                                            | witness row
row-reject               | Replies          | PROPERTY NoRow_reject                      | violate:NoRow_reject                     | pm reject item 1: verified -> rework                                                                                 | witness row
row-ack                  | Replies          | PROPERTY NoRow_ack                         | violate:NoRow_ack                        | pm ack item 1: draft -> draft                                                                                        | witness row
row-close                | Replies          | PROPERTY NoRow_close                       | violate:NoRow_close                      | pm close item 1: verified -> closed                                                                                  | witness row
row-cancel               | Replies          | PROPERTY NoRow_cancel                      | violate:NoRow_cancel                     | pm cancel item 1: draft -> cancelled                                                                                 | witness row
row-revalidate           | Replies          | PROPERTY NoRow_revalidate                  | violate:NoRow_revalidate                 | pm revalidate item 1: verified -> verified                                                                           | witness row
row-override             | TableWalk        | PROPERTY NoRow_override                    | violate:NoRow_override                   | operator override item 1: draft -> ready                                                                             | witness row
TABLE
)

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; }
die_usage() { echo "formal-check: $1 (see --help)" >&2; exit 2; }
JOBS=""; ONLY=""; TRACES=0; KEEP=0; STRICT=0; VARIANTS=0; ALLOW_STALE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --jobs|--only)
      [ $# -ge 2 ] || die_usage "$1 needs a value"
      if [ "$1" = --jobs ]; then JOBS=$2; else ONLY=$2; fi
      shift 2 ;;
    --traces) TRACES=1; shift ;;
    --keep) KEEP=1; shift ;;
    --strict) STRICT=1; shift ;;
    --allow-stale) ALLOW_STALE=1; shift ;;
    --variants) VARIANTS=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die_usage "unknown option $1" ;;
  esac
done
if [ -n "$JOBS" ]; then
  [[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || die_usage "--jobs needs a positive integer, got '$JOBS'"
else
  n=$(nproc 2>/dev/null || echo 2); JOBS=$(( n / 4 )); [ "$JOBS" -lt 2 ] && JOBS=2; [ "$JOBS" -gt 6 ] && JOBS=6
fi
if [ -n "$ONLY" ]; then [[ "" =~ $ONLY ]]; [ $? -ne 2 ] || die_usage "--only is not a valid regex: $ONLY"; fi
show() { printf '%s' "${1/#$HOME/\~}"; }   # never print the home path
sha_of() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1; }

# ---------- sync point: the design file must be the version the model was checked against ----------
design_sha=$(sha_of "$DESIGN")
if [ "$design_sha" != "$DESIGN_SHA256" ]; then
  echo "STALE: ${DESIGN#"$ROOT"/} sha256 is ${design_sha:-missing}, the model is synced to $DESIGN_SHA256." >&2
  echo "  Re-read the changed sections, update the model, run this check, then move DESIGN_SHA256" >&2
  echo "  in this script and the sync record in .claude/skills/verify-work-item-state/SKILL.md." >&2
  if [ "$ALLOW_STALE" -eq 0 ]; then
    echo "  To run against the changed design anyway (while you update the model): --allow-stale." >&2
    exit 4
  fi
  echo "  --allow-stale: running anyway; the EXPECT table describes the synced design, not this one." >&2
fi
command -v python3 >/dev/null 2>&1 || { echo "formal-check: python3 is needed (trace signatures, variants)" >&2; exit 2; }

# ---------- toolchain: verify, or install user-local and verify ----------
jre_tree_sha() {  # manifest of the extracted JRE: paths, link targets, owner-executable files, file sha256
  (cd "$JRE_DIR" 2>/dev/null || exit 1
   LC_ALL=C find . \( -type f -o -type l \) -printf '%y %p %l\n' | LC_ALL=C sort
   echo "executable:"; LC_ALL=C find . -type f -perm -u+x -print | LC_ALL=C sort
   LC_ALL=C find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) | sha256sum | cut -d' ' -f1
}
fetch() {  # url dest
  if command -v curl >/dev/null 2>&1; then curl -fsSL --retry 2 -o "$2" "$1"
  elif command -v wget >/dev/null 2>&1; then wget -q -O "$2" "$1"
  else return 1; fi
}
manual_steps() {
  cat >&2 <<EOF
formal-check: install the pinned tools by hand (no sudo needed), then re-run:
  mkdir -p "$TOOLS" && cd "$TOOLS"
  curl -fL -o tla2tools-$TLA_VERSION.jar "$TLA_URL"
  echo "$TLA_SHA256  tla2tools-$TLA_VERSION.jar" | sha256sum -c -
  curl -fL -o jre.tar.gz "$JRE_URL"
  echo "$JRE_SHA256  jre.tar.gz" | sha256sum -c -
  # the JRE directory must not exist yet (remove a broken one first: rm -rf "$JRE_DIR")
  mkdir jre.tmp && tar -xzf jre.tar.gz -C jre.tmp && mv jre.tmp/jdk-$JRE_VERSION-jre "$JRE_DIR" && rmdir jre.tmp && rm jre.tar.gz
EOF
}
install_jar() {
  echo "formal-check: installing tla2tools $TLA_VERSION into $(show "$TOOLS")"
  mkdir -p "$TOOLS" || return 1
  fetch "$TLA_URL" "$JAR.part" || { rm -f "$JAR.part"; return 1; }
  [ "$(sha_of "$JAR.part")" = "$TLA_SHA256" ] || { echo "formal-check: tla2tools.jar sha256 mismatch" >&2; rm -f "$JAR.part"; return 1; }
  mv "$JAR.part" "$JAR"
}
install_jre() {
  if [ -e "$JRE_DIR" ]; then  # never install into (and nest inside) an existing directory
    echo "formal-check: $(show "$JRE_DIR") exists but holds no runnable bin/java;" \
         "remove the whole directory and re-run to reinstall the JRE" >&2
    return 1
  fi
  echo "formal-check: installing Temurin JRE $JRE_VERSION into $(show "$JRE_DIR")"
  [ "$(uname -s)-$(uname -m)" = "Linux-x86_64" ] || { echo "formal-check: the pinned JRE is Linux x86_64 only" >&2; return 1; }
  mkdir -p "$TOOLS" || return 1
  local tmp; tmp=$(mktemp -d "$TOOLS/jre.XXXXXX") || return 1
  fetch "$JRE_URL" "$tmp/jre.tar.gz" || { rm -rf "$tmp"; return 1; }
  [ "$(sha_of "$tmp/jre.tar.gz")" = "$JRE_SHA256" ] || { echo "formal-check: JRE archive sha256 mismatch" >&2; rm -rf "$tmp"; return 1; }
  tar -xzf "$tmp/jre.tar.gz" -C "$tmp" && mv "$tmp/jdk-$JRE_VERSION-jre" "$JRE_DIR" || { rm -rf "$tmp"; return 1; }
  rm -rf "$tmp"
}
if [ ! -f "$JAR" ]; then install_jar || { manual_steps; exit 2; }; fi
if [ ! -x "$JAVA" ]; then install_jre || { manual_steps; exit 2; }; fi
jar_sha=$(sha_of "$JAR"); tree_sha=$(jre_tree_sha)
if [ "$jar_sha" != "$TLA_SHA256" ] || [ "$tree_sha" != "$JRE_TREE_SHA256" ]; then
  echo "formal-check: pinned tool hash mismatch under $(show "$TOOLS"):" >&2
  echo "  tla2tools.jar  $jar_sha (want $TLA_SHA256)" >&2
  echo "  JRE file tree  $tree_sha (want $JRE_TREE_SHA256)" >&2
  echo "  remove what mismatches (the jar file, or the whole JRE directory $(show "$JRE_DIR"))" \
       "and re-run: the script reinstalls it" >&2
  exit 2
fi
if ! "$JAVA" -XX:-UsePerfData -version 2>&1 | grep -q "build $JRE_VERSION"; then
  echo "formal-check: $(show "$JAVA") does not run as Temurin $JRE_VERSION" >&2; exit 2
fi
echo "tools: Temurin JRE $JRE_VERSION + tla2tools $TLA_VERSION (sha256 verified) in $(show "$TOOLS")"

# ---------- work dir and signals ----------
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/formal-check"
mkdir -p "$CACHE" && WORK=$(mktemp -d "$CACHE/run.XXXXXX") \
  || { echo "formal-check: cannot create a work dir under $(show "$CACHE")" >&2; exit 2; }
mkdir -p "$WORK/tmp"; export TMPDIR="$WORK/tmp"
PRESERVE=0   # 1: an UNEXPECTED run or a NOT AS EXPECTED variant; its dir stays for diagnosis
cleanup() {
  if [ "$KEEP" -eq 1 ] || [ "$PRESERVE" -eq 1 ]; then rm -rf "$WORK/tmp"; return; fi
  rm -rf "$WORK"; rmdir "$CACHE" 2>/dev/null || true
}
stop_runs() {  # TERM every background job (each is a TLC JVM, or the variants runner) and wait for it
  local p
  for p in $(jobs -p); do kill -TERM "$p" 2>/dev/null; done
  wait 2>/dev/null
}
on_signal() {
  trap '' INT TERM
  echo "formal-check: interrupted; stopping the TLC runs" >&2
  stop_runs; KEEP=0; PRESERVE=0; exit "$1"
}
trap cleanup EXIT
trap 'on_signal 130' INT
trap 'on_signal 143' TERM

# ---------- variants: broken / fixed copies of the spec, one TLC run at a time ----------
if [ "$VARIANTS" -eq 1 ]; then
  FORMAL_JAVA="$JAVA" FORMAL_JAR="$JAR" FORMAL_JAVA_OPTS="$JAVA_OPTS" FORMAL_SPEC_DIR="$FORMAL" \
    FORMAL_WORK="$WORK" FORMAL_KEEP="$KEEP" python3 -I "$FORMAL/variants.py" ${ONLY:+"$ONLY"} </dev/null &
  wait $!; rc=$?
  if [ "$rc" -ne 0 ]; then PRESERVE=1; echo "work dir kept: $(show "$WORK")"
  elif [ "$KEEP" -eq 1 ]; then echo "work dir kept: $(show "$WORK")"; fi
  exit "$rc"
fi

# ---------- runs ----------
trim() { local s=$1; s="${s#"${s%%[![:space:]]*}"}"; printf '%s' "${s%"${s##*[![:space:]]}"}"; }
names=(); scens=(); extras=(); expects=(); sigs=(); labels=()
while IFS='|' read -r n s x e g l; do
  n=$(trim "$n"); [ -z "$n" ] && continue
  if [ -n "$ONLY" ] && ! [[ "$n" =~ $ONLY ]]; then continue; fi
  names+=("$n"); scens+=("$(trim "$s")"); extras+=("$(trim "$x")")
  expects+=("$(trim "$e")"); sigs+=("$(trim "$g")"); labels+=("$(trim "$l")")
done <<< "$EXPECT"
[ ${#names[@]} -gt 0 ] || { echo "formal-check: no run matches --only $ONLY" >&2; exit 2; }

declare -A RUN_OF=()   # pid of a running TLC JVM -> run index
launch() {  # k: start run k in the background; the job is the JVM itself (exec), so stop_runs reaches it
  local k=$1 dir="$WORK/${names[$1]}" workers=1
  mkdir -p "$dir"
  if ! cp "$FORMAL/$SPEC.tla" "$dir/" || ! cp "$FORMAL/${scens[$k]}.cfg" "$dir/run.cfg"; then
    echo "error" > "$dir/rc"; return
  fi
  [ "${extras[$k]}" != "-" ] && printf '%s\n' "${extras[$k]}" >> "$dir/run.cfg"
  [ "${extras[$k]}" = "-" ] && workers="${FORMAL_WORKERS:-2}"
  date +%s > "$dir/t0"
  # shellcheck disable=SC2086
  (cd "$dir" && exec "$JAVA" $JAVA_OPTS -Djava.io.tmpdir="$dir" -cp "$JAR" tlc2.TLC -workers "$workers" \
      -metadir "$dir/meta" -noGenerateSpecTE -dumpTrace json "$dir/trace.json" \
      -config run.cfg "$SPEC.tla") </dev/null > "$dir/tlc.log" 2>&1 &
  RUN_OF[$!]=$k
}
reap() {  # wait for one run to end; record its exit code and seconds
  local pid="" rc k dir
  wait -n -p pid; rc=$?
  if [ -z "$pid" ]; then [ "$rc" -eq 127 ] && RUN_OF=(); return; fi   # 127: no job left
  k=${RUN_OF[$pid]:-}; [ -n "$k" ] || return
  unset "RUN_OF[$pid]"; dir="$WORK/${names[$k]}"
  echo "$rc" > "$dir/rc"; echo $(( $(date +%s) - $(cat "$dir/t0") )) > "$dir/secs"
  rm -rf "$dir/meta"
}

T0=$(date +%s)
echo "runs: ${#names[@]} (parallel jobs: $JOBS)"
for k in "${!names[@]}"; do
  while [ "${#RUN_OF[@]}" -ge "$JOBS" ]; do reap; done
  launch "$k"
done
while [ "${#RUN_OF[@]}" -gt 0 ]; do reap; done

trace_summary() { python3 -I "$FORMAL/variants.py" --trace "$1" | sed 's/^/      /'; }

unexpected=0; n_pass=0; n_reached=0; n_find=0; n_read=0; n_struct=0; n_note=0
printf '\n%-24s %-16s %10s %10s %5s  %s\n' RUN SCENARIO GENERATED DISTINCT SECS VERDICT
for k in "${!names[@]}"; do
  n=${names[$k]}; dir="$WORK/$n"; log="$dir/tlc.log"
  rc=$(cat "$dir/rc" 2>/dev/null || echo error); secs=$(cat "$dir/secs" 2>/dev/null || echo "?")
  gen=$(grep -oE '^[0-9,]+ states generated' "$log" 2>/dev/null | tail -1 | cut -d' ' -f1 | tr -d ,)
  dis=$(grep -oE '[0-9,]+ distinct states found' "$log" 2>/dev/null | tail -1 | cut -d' ' -f1 | tr -d ,)
  viol=$(grep -oE '^Error: (Invariant|Action property) [A-Za-z0-9_]+ is violated' "$log" 2>/dev/null | head -1 | awk '{print $(NF-2)}')
  # a constant-level invariant (TableExits, TableReach) that is FALSE is reported at startup, exit 151
  cviol=$(grep -oE '^Error: The invariant of [A-Za-z0-9_]+ is equal to FALSE' "$log" 2>/dev/null | head -1 | awk '{print $5}')
  if [ "$rc" = "0" ] && grep -q "Model checking completed. No error has been found." "$log"; then got=pass
  elif { [ "$rc" = "12" ] || [ "$rc" = "13" ]; } && [ -n "$viol" ]; then got="violate:$viol"
  elif [ "$rc" = "151" ] && [ -n "$cviol" ]; then viol=$cviol; got="violate:$viol"
  else got="error(rc=$rc)"; fi
  sig=""; [[ "$got" == violate:* ]] && sig=$(python3 -I "$FORMAL/variants.py" --signature "$dir/trace.json")
  exp=${expects[$k]}; want=${sigs[$k]}; label=${labels[$k]}; kind=${label%% *}; text=${label#"$kind"}; text=${text# }
  if [ "$got" = "$exp" ] && { [ "$got" = pass ] || [ "$sig" = "$want" ]; }; then
    case "$kind" in
      check)      verdict="PASS${text:+ ($text)}"; n_pass=$((n_pass + 1)) ;;
      witness)    verdict="REACHED $text ($viol violated as required)"; n_reached=$((n_reached + 1)) ;;
      finding)    verdict="FINDING $text ($viol violated, known)"; n_find=$((n_find + 1)) ;;
      reading)    verdict="READING $text ($viol violated under this reading, known)"; n_read=$((n_read + 1)) ;;
      structural) verdict="STRUCTURAL $text ($viol violated, known; the table has no row for the step)"
                  n_struct=$((n_struct + 1)) ;;
      note)       verdict="NOTE $text ($viol violated, known)"; n_note=$((n_note + 1)) ;;
      *)          verdict="UNEXPECTED: unknown kind '$kind' in EXPECT"; unexpected=$((unexpected + 1)) ;;
    esac
  elif [ "$got" = "$exp" ]; then
    verdict="UNEXPECTED: $viol violated as expected, but by another counterexample: last step '$sig', expected '$want'"
    unexpected=$((unexpected + 1))
  else
    verdict="UNEXPECTED: got $got, expected $exp"; unexpected=$((unexpected + 1))
  fi
  printf '%-24s %-16s %10s %10s %5s  %s\n' "$n" "${scens[$k]}" "${gen:--}" "${dis:--}" "$secs" "$verdict"
  bad=0; [[ "$verdict" == UNEXPECTED* ]] && bad=1
  if [ "$got" != pass ] && { [ "$bad" -eq 1 ] || [ "$TRACES" -eq 1 ] || [ "$kind" != witness ]; }; then
    [ -s "$dir/trace.json" ] && trace_summary "$dir/trace.json"
  fi
  if [[ "$got" == error* ]]; then
    grep -E "Error|rror:|Exception|is false" "$log" | head -5 | sed "s#$HOME#~#g; s/^/      /"
  fi
  if [ "$bad" -eq 1 ]; then echo "      logs: $(show "$dir")"; elif [ "$KEEP" -eq 0 ]; then rm -rf "$dir"; fi
done
findings=$((n_find + n_read + n_struct + n_note))

echo
echo "total: ${#names[@]} runs: $n_pass PASS, $n_reached REACHED, $n_find FINDING, $n_read READING," \
     "$n_struct STRUCTURAL, $n_note NOTE, $unexpected UNEXPECTED; $(( $(date +%s) - T0 ))s wall"
if [ "$unexpected" -gt 0 ]; then
  PRESERVE=1
  echo "logs of the UNEXPECTED runs (tlc.log, trace.json, run.cfg) kept in $(show "$WORK"); delete it when done"
  exit 1
fi
[ "$KEEP" -eq 1 ] && echo "work dir kept: $(show "$WORK")"
if [ "$STRICT" -eq 1 ] && [ "$findings" -gt 0 ]; then exit 3; fi
exit 0
