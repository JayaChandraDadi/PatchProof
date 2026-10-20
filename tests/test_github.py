import json

import httpx
import pytest

from patchproof.agent import ReviewResult
from patchproof.diff import parse_diff
from patchproof.findings import Edit, Finding, Fix
from patchproof.github import GitHubClient, PullRef, build_comments, summary_body
from patchproof.llm import Usage

from .conftest import git_diff

BEFORE = "def f(x):\n    return x\n\n\ndef g(y):\n    return y\n"
AFTER = "def f(x):\n    return x - 1\n\n\ndef g(y):\n    return y * 2\n"
FILES = parse_diff(git_diff("m.py", BEFORE, AFTER))


def finding(line, edits=None, title="t"):
    f = Finding("m.py", line, title, "why", "high", "bug", 0.9, id="F1")
    if edits is not None:
        f.fix = Fix(edits, "diff --git a/m.py b/m.py\n...\n", "s",
                    {"checks": [{"name": "tests"}, {"name": "lint"}]})
    return f


def result(*findings):
    return ReviewResult(list(findings), [], [], Usage(), 1.0, ["m.py"])


def test_pull_ref():
    assert PullRef.parse("octo/repo.js#12") == PullRef("octo", "repo.js", 12)
    with pytest.raises(ValueError):
        PullRef.parse("octo/repo 12")


def test_suggestion_for_single_line_fix():
    c = build_comments(result(finding(2, [Edit("m.py", 2, 2, "    return x + 1")])), FILES)[0]
    assert c["line"] == 2 and c["side"] == "RIGHT" and "start_line" not in c
    assert "```suggestion\n    return x + 1\n```" in c["body"]
    assert "no regressions in tests, lint" in c["body"]


def test_patch_attached_when_edit_is_outside_added_lines():
    c = build_comments(result(finding(2, [Edit("m.py", 1, 2, "def f(x):\n    return x")])), FILES)[0]
    assert "```suggestion" not in c["body"] and "<details>" in c["body"] and c["line"] == 2


def test_unfixed_finding_has_no_code():
    c = build_comments(result(finding(6)), FILES)[0]
    assert c == {"path": "m.py", "side": "RIGHT", "line": 6, "body": c["body"]}
    assert "```" not in c["body"]


def test_client_fetches_diff_and_posts_review():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.method == "GET" and req.headers["accept"].endswith("diff"):
            return httpx.Response(200, text="diff --git a/m.py b/m.py\n")
        if req.method == "GET":
            return httpx.Response(200, json={"head": {"sha": "abc"}, "title": "T"})
        return httpx.Response(200, json={"id": 1})

    gh = GitHubClient("tok", "https://api.example", transport=httpx.MockTransport(handler))
    ref = PullRef("o", "r", 7)
    assert gh.pull(ref)["head"]["sha"] == "abc"
    assert gh.diff(ref).startswith("diff --git")
    comments = build_comments(result(finding(6)), FILES)
    gh.post_review(ref, "abc", summary_body(result(finding(6)), "m"), comments)
    post = calls[-1]
    body = json.loads(post.content)
    assert post.url.path == "/repos/o/r/pulls/7/reviews"
    assert body["commit_id"] == "abc" and body["event"] == "COMMENT" and len(body["comments"]) == 1
    assert post.headers["authorization"] == "Bearer tok"
    assert "1 finding(s), 0 verified fix(es)" in body["body"]
