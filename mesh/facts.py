"""The production facts provider: outside-world observations for the rows that need them, gathered UNLOCKED.

`chain(*providers)` merges providers field by field (the first non-None wins); each provider answers only the facts it
can observe. `projmux_facts` answers LEAD_AGENT and LEAD_LIVE; `git_facts` answers HEAD, COMMIT_EXISTS, MERGED_COMMIT
and the facts that run commands in a disposable worktree (REPRO, PRECHECK, VERIFICATION: mesh/verify.py).
A provider never supplies OPERATOR_EVENT: the engine reads that one from the store. An observation that fails (projmux
down, unusable output) leaves the fact None, so its guard refuses with `fact_unavailable`: fail closed, never a guess.
"""

from __future__ import annotations

import os
from dataclasses import fields, replace
from typing import Callable, Mapping

from . import git, verify
from .engine import FactsProvider
from .model import (
    BUGFIX,
    NO_FACTS,
    PREPARE_KIND,
    CallerKind,
    Command,
    Config,
    Doc,
    Fact,
    Facts,
    InvalidArgs,
    LeadAgent,
    PrecheckOutcome,
    ReproOutcome,
    Request,
    RunStatus,
    Verb,
    VerificationResult,
)
from .projmux import Projmux, ProjmuxUnavailable


def chain(*providers: FactsProvider) -> FactsProvider:
    def provide(needs: frozenset[Fact], doc: Doc, req: Request, cfg: Config) -> Facts:
        out = NO_FACTS
        for p in providers:
            got = p(needs, doc, req, cfg)
            out = replace(out, **{f.name: getattr(got, f.name) for f in fields(Facts) if getattr(out, f.name) is None})
        return out

    return provide


def projmux_facts(pmx: Projmux, root: str | os.PathLike) -> FactsProvider:
    def provide(needs: frozenset[Fact], doc: Doc, req: Request, cfg: Config) -> Facts:
        wanted = needs & {Fact.LEAD_AGENT, Fact.LEAD_LIVE}
        item = doc.items.get(req.item_id or "")
        if not wanted or item is None:
            return NO_FACTS
        try:
            members = pmx.live_members(root, role=item.contract.role)
        except ProjmuxUnavailable:
            return NO_FACTS
        out = NO_FACTS
        if Fact.LEAD_AGENT in wanted:
            out = replace(out, lead_agent=LeadAgent(members[0].uid) if len(members) == 1 else None)
        if Fact.LEAD_LIVE in wanted:
            out = replace(out, lead_live=_lead_uid(item, req) in {m.uid for m in members})
        return out

    return provide


def _lead_uid(item, req: Request) -> str | None:
    """Whose liveness the row asks about: a lead asks about itself (start); the PM about the attempt's lead."""
    if req.caller.kind is CallerKind.LEAD:
        return req.caller.agent_uid
    attempt = item.current_attempt
    return attempt.lead_uid if attempt else None


def git_facts(root: str | os.PathLike, env: Mapping[str, str], read_config: Callable[[], Config]) -> FactsProvider:
    """`env` is the server's environment: git and the commands see only its allowlisted part (verify.ENV_ALLOW).
    `read_config` re-reads .mesh/config.toml: the verification hashes it before AND after its run."""
    genv = verify.child_env(env)

    def provide(needs: frozenset[Fact], doc: Doc, req: Request, cfg: Config) -> Facts:
        out = NO_FACTS
        if needs & {Fact.HEAD, Fact.MERGED_COMMIT}:
            head = git.head(root, genv)
            if Fact.HEAD in needs:
                out = replace(out, head=head)
            if Fact.MERGED_COMMIT in needs:  # the children's work is merged into the shared tree's HEAD (D72)
                out = replace(out, merged_commit=head)
        if Fact.COMMIT_EXISTS in needs:
            commit = getattr(req.args, "commit", None)
            try:
                out = replace(out, commit_exists=git.commit_exists(root, commit, genv))
            except ValueError:  # not a 40-hex sha: never handed to git, never an existing commit
                out = replace(out, commit_exists=False)
        if Fact.COMMIT_DESCENDS in needs:
            out = replace(out, commit_descends=_descends(root, genv, doc, req))
        if Fact.REPRO in needs:
            out = replace(out, repro=_repro(root, env, doc, req, cfg))
        if Fact.PRECHECK in needs:
            out = replace(out, precheck=_precheck(root, env, cfg))
        if Fact.VERIFICATION in needs:
            out = replace(out, verification=_verification(root, env, read_config, doc, req, cfg))
        return out

    return provide


def _descends(root, genv, doc: Doc, req: Request) -> bool | None:
    """The submitted commit has the current attempt's base commit (HEAD at `start`) as an ancestor."""
    item = doc.items.get(req.item_id or "")
    attempt = item.current_attempt if item else None
    if attempt is None or attempt.base_commit is None:
        return None
    try:
        return git.descends(root, getattr(req.args, "commit", None), attempt.base_commit, genv)
    except ValueError:
        return False


UNREADABLE = "unreadable"  # a config that no longer parses hashes to this: never equal to a real hash


def _verification(root, env, read_config, doc: Doc, req: Request, cfg: Config) -> VerificationResult | None:
    """The configured commands at the current attempt's commit (the submitted commit; the merged commit for a split
    parent). The config is hashed before (the commands that run) and after (re-read): a mid-run edit shows as a
    difference and `decide` refuses it as `inputs_changed`. None when nothing could run (the fact is unavailable)."""
    item = doc.items.get(req.item_id or "")
    attempt = item.current_attempt if item else None
    if attempt is None or attempt.commit is None:
        return None
    if req.verb is Verb.VERIFY and getattr(req.args, "attempt_id", None) != attempt.id:
        return None  # `attempt_current` refuses it: run nothing for a stale attempt
    before = cfg.verify_hash()
    if not cfg.verify_commands:
        return VerificationResult("none", attempt.commit, before, _hash_now(read_config), ())
    try:
        run_id, runs = verify.run_at(root, attempt.commit, cfg.verify_commands, env, prepare=cfg.verify_prepare)
    except (ValueError, verify.WorktreeUnavailable):
        return None
    return VerificationResult(run_id, attempt.commit, before, _hash_now(read_config), runs)


def _hash_now(read_config: Callable[[], Config]) -> str:
    try:
        return read_config().verify_hash()
    except InvalidArgs:
        return UNREADABLE


def _repro(root, env, doc: Doc, req: Request, cfg: Config) -> ReproOutcome | None:
    """A bugfix's repro argv (the `ready` patch's when it carries one) at the base commit, the shared tree's HEAD.
    exit 0 passes (not reproduced); 126/127, a spawn error or a timeout is not runnable; any other exit fails."""
    item = doc.items.get(req.item_id or "")
    if item is None or item.contract.type != BUGFIX:
        return None
    patch = getattr(req.args, "patch", None)
    argv = patch.repro if patch is not None and patch.repro is not None else item.contract.repro
    base = git.head(root, verify.child_env(env))
    if not argv or base is None:
        return None
    try:
        _, runs = verify.run_at(
            root, base, (Command(tuple(argv), "repro", cfg.repro_timeout_s),), env, prepare=cfg.verify_prepare
        )
    except (ValueError, verify.WorktreeUnavailable):
        return None
    run = runs[-1]
    if run.kind == PREPARE_KIND:  # the prepare failed: the repro never ran
        return ReproOutcome("not_runnable", base, run.exit_code)
    if run.status is RunStatus.PASS:
        return ReproOutcome("passes", base, 0)
    if run.status is RunStatus.FAIL:
        return ReproOutcome("fails", base, run.exit_code)
    return ReproOutcome("not_runnable", base, run.exit_code)


def _precheck(root, env, cfg: Config) -> PrecheckOutcome | None:
    """The environment precheck at HEAD. `[[precheck.commands]]` when configured: every one must pass. Without them the
    verification commands stand in and must be RUNNABLE (no not_run, no timeout): a failing test is a defect for the
    lead, not a broken environment. Nothing configured at all refuses: an unblock needs something that ran. The detail
    names kinds and statuses only (never argv, paths or output)."""
    explicit = bool(cfg.precheck_commands)
    commands = cfg.precheck_commands or cfg.verify_commands
    if not commands:
        return PrecheckOutcome(False, "no [[precheck.commands]] or [[verify.commands]] in .mesh/config.toml")
    head = git.head(root, verify.child_env(env))
    if head is None:
        return None
    try:
        _, runs = verify.run_at(root, head, commands, env, prepare=cfg.verify_prepare)
    except (ValueError, verify.WorktreeUnavailable):
        return None
    bad = [
        r
        for r in runs
        if (r.status is not RunStatus.PASS if explicit else r.status in (RunStatus.NOT_RUN, RunStatus.TIMEOUT))
    ]
    if bad:
        return PrecheckOutcome(False, "precheck: " + ", ".join(f"{r.kind} {r.status.value}" for r in bad))
    return PrecheckOutcome(True)
