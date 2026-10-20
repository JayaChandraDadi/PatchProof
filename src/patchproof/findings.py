"""Review findings and proposed fixes, plus the checks that keep model output honest.

The model's reply is treated as untrusted input. A finding survives only if it points at
a file and line that are actually in the diff, and a proposed fix becomes a patch only if
its edits are in range, non-overlapping, small, and limited to files the PR touched.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any

from .diff import FileDiff

SEVERITIES = ("high", "medium", "low")
CATEGORIES = ("bug", "security", "performance", "error-handling", "concurrency", "style", "other")
FIXABLE = {"bug", "security", "error-handling", "concurrency", "performance"}


@dataclass
class Finding:
    file: str
    line: int
    title: str
    explanation: str = ""
    severity: str = "medium"
    category: str = "bug"
    confidence: float = 0.5
    id: str = ""
    fix: Fix | None = None
    fix_attempts: int = 0
    fix_rejections: list[str] = field(default_factory=list)

    @property
    def fixable(self) -> bool:
        return self.category in FIXABLE and self.severity in ("high", "medium")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "file": self.file,
            "line": self.line,
            "title": self.title,
            "explanation": self.explanation,
            "severity": self.severity,
            "category": self.category,
            "confidence": self.confidence,
            "fix": self.fix.to_dict() if self.fix else None,
            "fix_attempts": self.fix_attempts,
            "fix_rejections": self.fix_rejections,
        }


@dataclass(frozen=True)
class Edit:
    file: str
    start_line: int
    end_line: int  # inclusive; end_line == start_line - 1 means "insert before start_line"
    replacement: str


@dataclass
class Fix:
    edits: list[Edit]
    patch: str
    summary: str = ""
    verification: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"summary": self.summary, "patch": self.patch, "verification": self.verification}


@dataclass
class Dropped:
    raw: dict[str, Any]
    reason: str


def _as_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def validate_findings(
    raw: Any,
    files: list[FileDiff],
    *,
    min_confidence: float = 0.4,
    max_comments: int = 8,
    snap_distance: int = 3,
) -> tuple[list[Finding], list[Dropped]]:
    """Turn the model's ``findings`` list into anchored findings, dropping the rest."""
    by_path = {f.path: f for f in files if f.new_path is not None and not f.is_binary}
    items = raw.get("findings", []) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return [], [Dropped({"raw": raw}, "reply has no findings list")]

    kept: list[Finding] = []
    dropped: list[Dropped] = []
    seen: set[tuple[str, int]] = set()
    for item in items:
        if not isinstance(item, dict):
            dropped.append(Dropped({"raw": item}, "finding is not an object"))
            continue
        path = str(item.get("file", "")).removeprefix("b/").removeprefix("./")
        line = _as_int(item.get("line"))
        title = str(item.get("title") or item.get("message") or "").strip()
        if path not in by_path:
            dropped.append(Dropped(item, f"file {path!r} is not in the diff"))
            continue
        if line is None:
            dropped.append(Dropped(item, "no line number"))
            continue
        if not title:
            dropped.append(Dropped(item, "empty title"))
            continue
        fd = by_path[path]
        anchored = _anchor(fd, line, snap_distance)
        if anchored is None:
            dropped.append(Dropped(item, f"line {line} is not near a changed line"))
            continue
        try:
            confidence = float(item.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        if confidence < min_confidence:
            dropped.append(Dropped(item, f"confidence {confidence:.2f} below {min_confidence}"))
            continue
        if (path, anchored) in seen:
            dropped.append(Dropped(item, "duplicate of an earlier finding"))
            continue
        seen.add((path, anchored))
        severity = str(item.get("severity", "medium")).lower()
        category = str(item.get("category", "bug")).lower()
        kept.append(
            Finding(
                file=path,
                line=anchored,
                title=title[:200],
                explanation=str(item.get("explanation", "")).strip()[:1500],
                severity=severity if severity in SEVERITIES else "medium",
                category=category if category in CATEGORIES else "other",
                confidence=max(0.0, min(1.0, confidence)),
            )
        )

    order = {s: i for i, s in enumerate(SEVERITIES)}
    kept.sort(key=lambda f: (order[f.severity], -f.confidence))
    for f in kept[max_comments:]:
        dropped.append(Dropped(f.to_dict(), "over the per-PR comment limit"))
    kept = kept[:max_comments]
    for i, f in enumerate(kept, 1):
        f.id = f"F{i}"
    return kept, dropped


def _anchor(fd: FileDiff, line: int, snap: int) -> int | None:
    """Keep lines that were added; snap small misses to the nearest added line."""
    added = fd.added_lines
    if line in added:
        return line
    near = [a for a in added if abs(a - line) <= snap]
    if near:
        return min(near, key=lambda a: (abs(a - line), a))
    return None


class EditError(ValueError):
    pass


def parse_edits(raw: Any) -> tuple[list[Edit], str]:
    if not isinstance(raw, dict) or not isinstance(raw.get("edits"), list):
        raise EditError("reply has no edits list")
    edits: list[Edit] = []
    for e in raw["edits"]:
        if not isinstance(e, dict):
            raise EditError("edit is not an object")
        start, end = _as_int(e.get("start_line")), _as_int(e.get("end_line"))
        if start is None or end is None:
            raise EditError("edit needs integer start_line and end_line")
        repl = e.get("replacement", "")
        if not isinstance(repl, str):
            raise EditError("replacement must be a string")
        path = str(e.get("file", "")).removeprefix("b/").removeprefix("./")
        edits.append(Edit(path, start, end, repl))
    if not edits:
        raise EditError("no edits")
    return edits, str(raw.get("summary", "")).strip()[:300]


def build_patch(
    edits: list[Edit],
    sources: dict[str, str],
    allowed_files: set[str],
    max_changed_lines: int = 40,
) -> str:
    """Apply line-range edits in memory and return a unified diff ``git apply`` accepts."""
    per_file: dict[str, list[Edit]] = {}
    for e in edits:
        if e.file not in allowed_files:
            raise EditError(f"edit touches {e.file!r}, which is not part of this PR")
        if e.file not in sources:
            raise EditError(f"cannot read {e.file!r}")
        per_file.setdefault(e.file, []).append(e)

    chunks: list[str] = []
    changed = 0
    for path, file_edits in sorted(per_file.items()):
        original = sources[path]
        lines = original.splitlines(keepends=True)
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        file_edits.sort(key=lambda e: e.start_line)
        prev_end = 0
        for e in file_edits:
            if e.start_line < 1 or e.end_line > len(lines) or e.end_line < e.start_line - 1:
                raise EditError(
                    f"edit range {e.start_line}-{e.end_line} is outside {path} ({len(lines)} lines)"
                )
            if e.start_line <= prev_end:
                raise EditError("edits overlap")
            prev_end = e.end_line
        new = list(lines)
        for e in reversed(file_edits):
            repl = e.replacement
            repl_lines = [] if repl == "" else [ln + "\n" for ln in repl.split("\n")]
            if repl.endswith("\n") and repl_lines:
                repl_lines = repl_lines[:-1]  # "a\n" -> ["a\n"], not ["a\n", "\n"]
            new[e.start_line - 1 : e.end_line] = repl_lines
        diff = list(difflib.unified_diff(lines, new, fromfile=f"a/{path}", tofile=f"b/{path}", n=3))
        if not diff:
            continue
        changed += sum(1 for d in diff[2:] if d[:1] in "+-")
        chunks.append(f"diff --git a/{path} b/{path}\n" + "".join(diff))
    if not chunks:
        raise EditError("edits do not change anything")
    if changed > max_changed_lines:
        raise EditError(f"fix changes {changed} lines; the limit is {max_changed_lines}")
    return "".join(chunks)
