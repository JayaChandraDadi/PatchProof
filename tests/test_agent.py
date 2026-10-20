import json

from patchproof.agent import ReviewAgent
from patchproof.config import CheckSpec, Config
from patchproof.llm import LLMError, ScriptedClient
from patchproof.prompts import FIX_SYSTEM
from patchproof.sandbox import Sandbox

from .conftest import git_diff

CHECKS = [CheckSpec("tests", "pytest", "{python} -m pytest -q tests", 60)]


def make_pr(repo):
    """The PR loosens the clamp bound: hi + 1 now leaks through. Its tests do not notice."""
    calc = repo / "pkg" / "calc.py"
    before = calc.read_text()
    after = before.replace("    if x > hi:", "    if x > hi + 1:")
    calc.write_text(after)
    return git_diff("pkg/calc.py", before, after)


def bug_line(repo):
    lines = (repo / "pkg" / "calc.py").read_text().splitlines()
    return lines.index("    if x > hi + 1:") + 1


def scripted(repo, fixes):
    line = bug_line(repo)
    queue = list(fixes)

    def respond(messages):
        if messages[0]["content"].startswith(FIX_SYSTEM[:30]):
            return json.dumps({"summary": "fix", "edits": [
                {"file": "pkg/calc.py", "start_line": line, "end_line": line, "replacement": queue.pop(0)}]})
        return json.dumps({"findings": [
            {"file": "pkg/calc.py", "line": line, "severity": "high", "category": "bug",
             "title": "upper bound is off by one", "explanation": "clamp(hi + 1, lo, hi) returns hi + 1",
             "confidence": 0.9},
            {"file": "pkg/calc.py", "line": 1, "title": "unchanged line", "confidence": 0.9},
        ]})

    return ScriptedClient(respond)


def test_finds_and_fixes(tiny_repo):
    diff = make_pr(tiny_repo)
    client = scripted(tiny_repo, ["    if x > hi:"])
    cfg = Config(checks=CHECKS)
    with Sandbox(tiny_repo, cfg.checks) as sb:
        result = ReviewAgent(client, tiny_repo, cfg, sb).run(diff, "Tidy clamp")
    assert [f.title for f in result.findings] == ["upper bound is off by one"]
    f = result.findings[0]
    assert f.fix is not None and f.fix.verification["accepted"]
    assert "-    if x > hi + 1:\n+    if x > hi:" in f.fix.patch
    assert [d.reason for d in result.dropped] == ["line 1 is not near a changed line"]
    assert result.usage.calls == 2
    review_prompt = client.transcript[0][1]["content"]
    assert "Tidy clamp" in review_prompt and "clamp (changed)" in review_prompt


def test_rejected_fix_is_retried_with_feedback(tiny_repo):
    # The first fix breaks a case the visible tests do cover, so it must be rejected.
    diff = make_pr(tiny_repo)
    client = scripted(tiny_repo, ["    if x >= lo:", "    if x > hi:"])
    cfg = Config(checks=CHECKS)
    with Sandbox(tiny_repo, cfg.checks) as sb:
        result = ReviewAgent(client, tiny_repo, cfg, sb).run(diff)
    f = result.findings[0]
    assert f.fix_attempts == 2 and len(f.fix_rejections) == 1 and f.fix is not None
    retry_prompt = client.transcript[2][1]["content"]
    assert "Previous attempts were rejected" in retry_prompt and "test_clamp" in retry_prompt
    assert result.rejected_patches == 1


def test_gives_up_after_max_attempts(tiny_repo):
    diff = make_pr(tiny_repo)
    client = scripted(tiny_repo, ["    if x >= lo:"] * 5)
    cfg = Config(checks=CHECKS, max_fix_attempts=2)
    with Sandbox(tiny_repo, cfg.checks) as sb:
        result = ReviewAgent(client, tiny_repo, cfg, sb).run(diff)
    f = result.findings[0]
    assert f.fix is None and f.fix_attempts == 2 and result.usage.calls == 3


def test_review_only_mode(tiny_repo):
    diff = make_pr(tiny_repo)
    client = scripted(tiny_repo, [])
    result = ReviewAgent(client, tiny_repo, Config(checks=CHECKS), sandbox=None).run(diff)
    assert len(result.findings) == 1 and result.findings[0].fix is None and result.usage.calls == 1


def test_llm_failure_is_reported_not_raised(tiny_repo):
    def boom(messages):
        raise LLMError("server down")

    diff = make_pr(tiny_repo)
    result = ReviewAgent(ScriptedClient(boom), tiny_repo, Config(checks=CHECKS)).run(diff)
    assert result.findings == [] and "server down" in result.errors[0]


def test_empty_diff_short_circuits(tiny_repo):
    make_pr(tiny_repo)
    client = scripted(tiny_repo, [])
    result = ReviewAgent(client, tiny_repo, Config(checks=CHECKS)).run("")
    assert result.findings == [] and client.usage.calls == 0
