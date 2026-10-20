"""Run a reviewer over the benchmark PRs and score it.

For each PR the agent sees only what a real reviewer would: the diff, the PR title, and the
repository at the PR head with its visible tests. Hidden tests are used afterwards to judge
whether a flagged line is the seeded bug and whether an accepted fix is actually correct.

Metrics
  detection rate      share of bug PRs with a finding within +/-2 lines of the seeded bug
  false comments/PR   findings that do not point at a seeded bug, per PR (bug and clean PRs)
  precision           true findings / all findings
  verified fix rate   share of detected bugs that got a fix which passed the sandbox
  correct fix rate    share of verified fixes that also pass the hidden tests
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..agent import ReviewAgent, ReviewResult
from ..config import CheckSpec, Config
from ..llm import LLMClient, Message, ScriptedClient
from ..prompts import FIX_SYSTEM
from ..sandbox import Sandbox
from .dataset import Hung, PullRequest, materialize, run_tests

TOLERANCE = 2


def bench_checks() -> list[CheckSpec]:
    checks = [CheckSpec("tests", "pytest", "{python} -m pytest -q tests_visible", 120)]
    if importlib.util.find_spec("ruff") is not None:
        checks.append(CheckSpec("lint", "ruff", "{python} -m ruff check --select E9,F minilib", 60))
    return checks


@dataclass
class PRScore:
    id: str
    kind: str
    findings: int
    true_findings: int
    false_findings: int
    detected: bool
    verified_fix: bool
    correct_fix: bool
    rejected_patches: int
    dropped_claims: int
    llm_calls: int
    prompt_tokens: int
    completion_tokens: int
    seconds: float
    errors: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


def judge(pr: PullRequest, result: ReviewResult, workdir: Path) -> PRScore:
    true_ids: set[str] = set()
    if pr.bug is not None:
        for f in result.findings:
            if f.file == pr.path and abs(f.line - pr.bug.line) <= TOLERANCE:
                true_ids.add(f.id)
    detected = bool(true_ids)
    verified = correct = False
    for f in result.findings:
        if f.id in true_ids and f.fix is not None:
            verified = True
            repo = materialize(pr.head_source, pr.path, workdir / f"judge-{f.id}", hidden=True)
            (repo / "fix.patch").write_text(f.fix.patch)
            applied = subprocess.run(["git", "apply", "-p1", "fix.patch"], cwd=repo,
                                     capture_output=True).returncode == 0
            try:
                correct = applied and run_tests(repo, "tests_hidden")
            except Hung:
                correct = False
            shutil.rmtree(repo, ignore_errors=True)
            if correct:
                break
    n = len(result.findings)
    return PRScore(
        id=pr.id, kind=pr.kind, findings=n, true_findings=len(true_ids),
        false_findings=n - len(true_ids), detected=detected, verified_fix=verified,
        correct_fix=correct, rejected_patches=result.rejected_patches,
        dropped_claims=len(result.dropped), llm_calls=result.usage.calls,
        prompt_tokens=result.usage.prompt_tokens, completion_tokens=result.usage.completion_tokens,
        seconds=round(result.seconds, 2), errors=result.errors,
        detail=result.to_dict(),
    )


ClientFactory = Callable[[PullRequest], LLMClient]


def run(prs: Sequence[PullRequest], client_for: ClientFactory, *, fix: bool = True,
        prompt_variant: str = "checklist", out: Path | None = None,
        progress: Callable[[PRScore], None] | None = None) -> list[PRScore]:
    scores: list[PRScore] = []
    sink = out.open("w") if out else None
    try:
        for pr in prs:
            with tempfile.TemporaryDirectory(prefix="pp-bench-") as tmp:
                work = Path(tmp)
                repo = materialize(pr.head_source, pr.path, work / "repo", hidden=False)
                cfg = Config(checks=bench_checks(), prompt_variant=prompt_variant)
                with Sandbox(repo, cfg.checks) as sandbox:
                    agent = ReviewAgent(client_for(pr), repo, cfg, sandbox, fix=fix)
                    result = agent.run(pr.diff, pr.title)
                score = judge(pr, result, work)
            scores.append(score)
            if sink:
                sink.write(json.dumps(asdict(score)) + "\n")
                sink.flush()
            if progress:
                progress(score)
    finally:
        if sink:
            sink.close()
    return scores


def summarize(scores: Sequence[PRScore], label: str = "") -> dict[str, Any]:
    bug = [s for s in scores if s.kind == "bug"]
    clean = [s for s in scores if s.kind == "clean"]
    detected = sum(s.detected for s in bug)
    verified = sum(s.verified_fix for s in bug)
    correct = sum(s.correct_fix for s in bug)
    findings = sum(s.findings for s in scores)
    true = sum(s.true_findings for s in scores)
    false = sum(s.false_findings for s in scores)

    def ratio(a: float, b: float) -> float:
        return round(a / b, 4) if b else 0.0

    return {
        "label": label,
        "bug_prs": len(bug),
        "clean_prs": len(clean),
        "detection_rate": ratio(detected, len(bug)),
        "precision": ratio(true, findings),
        "false_comments_per_pr": ratio(false, len(scores)),
        "false_comments_per_clean_pr": ratio(sum(s.findings for s in clean), len(clean)),
        "verified_fix_rate": ratio(verified, detected),
        "correct_fix_rate": ratio(correct, verified),
        "rejected_patches": sum(s.rejected_patches for s in scores),
        "dropped_model_claims": sum(s.dropped_claims for s in scores),
        "llm_calls": sum(s.llm_calls for s in scores),
        "prompt_tokens": sum(s.prompt_tokens for s in scores),
        "completion_tokens": sum(s.completion_tokens for s in scores),
        "mean_seconds_per_pr": ratio(sum(s.seconds for s in scores), len(scores)),
        "errors": sum(len(s.errors) for s in scores),
    }


def markdown_table(summaries: Sequence[dict[str, Any]]) -> str:
    cols = [("label", "Reviewer"), ("detection_rate", "Detection"), ("precision", "Precision"),
            ("false_comments_per_pr", "False comments/PR"), ("verified_fix_rate", "Verified fixes"),
            ("correct_fix_rate", "Correct fixes"), ("rejected_patches", "Patches rejected"),
            ("mean_seconds_per_pr", "s/PR")]
    pct = {"detection_rate", "precision", "verified_fix_rate", "correct_fix_rate"}
    rows = ["| " + " | ".join(c[1] for c in cols) + " |", "|" + "---|" * len(cols)]
    for s in summaries:
        cells = []
        for key, _ in cols:
            v = s.get(key, "")
            cells.append(f"{v * 100:.0f}%" if key in pct else str(v))
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def gate(baseline: dict[str, Any], candidate: dict[str, Any], *, max_detection_drop: float = 0.03,
         max_false_increase: float = 0.10, max_correct_fix_drop: float = 0.05) -> list[str]:
    """Reasons to block a prompt or model change (empty list = OK to ship)."""
    problems = []
    if candidate["detection_rate"] < baseline["detection_rate"] - max_detection_drop:
        problems.append(f"detection fell from {baseline['detection_rate']:.0%} "
                        f"to {candidate['detection_rate']:.0%}")
    if candidate["false_comments_per_pr"] > baseline["false_comments_per_pr"] + max_false_increase:
        problems.append(f"false comments/PR rose from {baseline['false_comments_per_pr']} "
                        f"to {candidate['false_comments_per_pr']}")
    if candidate["correct_fix_rate"] < baseline["correct_fix_rate"] - max_correct_fix_drop:
        problems.append(f"correct fix rate fell from {baseline['correct_fix_rate']:.0%} "
                        f"to {candidate['correct_fix_rate']:.0%}")
    return problems


# -- scripted reviewers for sanity checks and CI -------------------------------------------

def _is_fix_request(messages: Sequence[Message]) -> bool:
    return messages[0]["content"].startswith(FIX_SYSTEM[:30])


def oracle_client(pr: PullRequest, noisy: bool = False) -> ScriptedClient:
    """A reviewer that knows the seeded bug. Measures the pipeline, not a model.

    With ``noisy=True`` it also makes the mistakes real models make: a claim about a file
    outside the diff, a comment on an unchanged line, and a first fix that breaks the code.
    PatchProof should drop the claims and reject the bad fix before accepting a good one.
    """
    attempts = {"n": 0}

    def respond(messages: Sequence[Message]) -> str:
        if _is_fix_request(messages):
            attempts["n"] += 1
            assert pr.bug is not None
            if noisy and attempts["n"] == 1:
                bad = pr.bug.correct_line.replace("return", "retrun", 1) + " +"
                return json.dumps({"summary": "first try", "edits": [
                    {"file": pr.path, "start_line": pr.bug.line, "end_line": pr.bug.line,
                     "replacement": bad}]})
            return json.dumps({"summary": f"restore {pr.bug.original}", "edits": [
                {"file": pr.path, "start_line": pr.bug.line, "end_line": pr.bug.line,
                 "replacement": pr.bug.correct_line}]})
        findings: list[dict[str, Any]] = []
        if pr.bug is not None:
            findings.append({"file": pr.path, "line": pr.bug.line, "severity": "high",
                             "category": "bug", "confidence": 0.9,
                             "title": f"`{pr.bug.replacement}` should be `{pr.bug.original}`",
                             "explanation": "Seeded bug."})
        if noisy:
            findings.append({"file": "minilib/does_not_exist.py", "line": 3, "title": "made up",
                             "confidence": 0.9})
            findings.append({"file": pr.path, "line": 1, "title": "comment on an unchanged line",
                             "confidence": 0.9})
        return json.dumps({"findings": findings})

    return ScriptedClient(respond, name="oracle-noisy" if noisy else "oracle")


def null_client(pr: PullRequest) -> ScriptedClient:
    return ScriptedClient(lambda m: '{"findings": []}', name="null")


def timed(fn: Callable[[], Any]) -> tuple[Any, float]:
    t = time.perf_counter()
    return fn(), time.perf_counter() - t
