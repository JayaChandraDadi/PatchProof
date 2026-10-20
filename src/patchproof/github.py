"""GitHub pull-request integration: fetch the diff, post one review with inline comments.

Comments go on the right-side line of the diff. A verified fix whose edits sit inside the
PR's changed lines is posted as a GitHub ``suggestion`` block, so the author can apply it
with one click; any other verified fix is attached as a patch. Unverified fixes are never
posted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from .agent import ReviewResult
from .diff import FileDiff
from .findings import Finding

_PR_REF = re.compile(r"^(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)#(?P<number>\d+)$")


@dataclass(frozen=True)
class PullRef:
    owner: str
    repo: str
    number: int

    @classmethod
    def parse(cls, ref: str) -> PullRef:
        m = _PR_REF.match(ref.strip())
        if not m:
            raise ValueError(f"expected owner/repo#number, got {ref!r}")
        return cls(m.group("owner"), m.group("repo"), int(m.group("number")))


class GitHubClient:
    def __init__(self, token: str, api_url: str = "https://api.github.com",
                 transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(
            base_url=api_url,
            headers={
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "patchproof",
            },
            timeout=30.0,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def pull(self, ref: PullRef) -> dict[str, Any]:
        r = self._http.get(f"/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}",
                           headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data

    def diff(self, ref: PullRef) -> str:
        r = self._http.get(f"/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}",
                           headers={"Accept": "application/vnd.github.v3.diff"})
        r.raise_for_status()
        return r.text

    def post_review(self, ref: PullRef, commit_id: str, body: str,
                    comments: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {"commit_id": commit_id, "body": body, "event": "COMMENT", "comments": comments}
        r = self._http.post(f"/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}/reviews",
                            json=payload, headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data


def suggestion_range(f: Finding, files: dict[str, FileDiff]) -> tuple[int, int, str] | None:
    """If the verified fix is one edit inside the PR's added lines, return (start, end, text)."""
    if f.fix is None or len(f.fix.edits) != 1:
        return None
    e = f.fix.edits[0]
    fd = files.get(e.file)
    if fd is None or e.file != f.file or e.end_line < e.start_line:
        return None
    added = fd.added_lines
    if not all(n in added for n in range(e.start_line, e.end_line + 1)):
        return None
    if not (e.start_line <= f.line <= e.end_line):
        return None
    return e.start_line, e.end_line, e.replacement.rstrip("\n")


def comment_body(f: Finding, suggestion: str | None) -> str:
    parts = [f"**{f.title}** ({f.severity} {f.category}, confidence {f.confidence:.2f})"]
    if f.explanation:
        parts.append(f.explanation)
    if f.fix is not None:
        checks = ", ".join(c["name"] for c in f.fix.verification.get("checks", []))
        note = f"Verified fix: applies cleanly, no regressions in {checks or 'configured checks'}."
        if suggestion is not None:
            parts.append(f"{note}\n```suggestion\n{suggestion}\n```")
        else:
            parts.append(f"{note}\n<details><summary>Patch</summary>\n\n```diff\n{f.fix.patch}```\n</details>")
    return "\n\n".join(parts)


def build_comments(result: ReviewResult, files: list[FileDiff]) -> list[dict[str, Any]]:
    by_path = {f.path: f for f in files}
    comments: list[dict[str, Any]] = []
    for f in result.findings:
        sug = suggestion_range(f, by_path)
        c: dict[str, Any] = {"path": f.file, "side": "RIGHT",
                             "body": comment_body(f, sug[2] if sug else None)}
        if sug and sug[0] != sug[1]:
            c["start_line"], c["start_side"], c["line"] = sug[0], "RIGHT", sug[1]
        elif sug:
            c["line"] = sug[0]
        else:
            c["line"] = f.line
        comments.append(c)
    return comments


def summary_body(result: ReviewResult, model: str) -> str:
    n = len(result.findings)
    head = f"PatchProof reviewed {len(result.files_reviewed)} file(s) with `{model}`: "
    head += "no issues found." if n == 0 else f"{n} finding(s), {result.verified_fixes} verified fix(es)."
    if result.rejected_patches:
        head += f" {result.rejected_patches} unverified patch(es) were discarded."
    return head
