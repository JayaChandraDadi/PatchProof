"""Parse unified diffs (``git diff`` output) into files, hunks and line positions.

The reviewer needs two things from a diff: the text to show the model, and the set of
right-side lines GitHub will accept an inline comment on. Both come from here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


@dataclass(frozen=True)
class DiffLine:
    kind: str  # "+", "-" or " "
    text: str
    old_no: int | None
    new_no: int | None


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    header: str
    lines: list[DiffLine] = field(default_factory=list)

    @property
    def added(self) -> list[int]:
        return [ln.new_no for ln in self.lines if ln.kind == "+" and ln.new_no is not None]

    @property
    def right_side(self) -> list[int]:
        """Right-side line numbers inside this hunk (added and context lines)."""
        return [ln.new_no for ln in self.lines if ln.kind != "-" and ln.new_no is not None]


@dataclass
class FileDiff:
    old_path: str | None
    new_path: str | None
    hunks: list[Hunk] = field(default_factory=list)
    is_binary: bool = False

    @property
    def path(self) -> str:
        """The path a reviewer would comment on (the new path unless the file was deleted)."""
        return self.new_path or self.old_path or ""

    @property
    def status(self) -> str:
        if self.old_path is None:
            return "added"
        if self.new_path is None:
            return "deleted"
        if self.old_path != self.new_path:
            return "renamed"
        return "modified"

    @property
    def added_lines(self) -> set[int]:
        return {n for h in self.hunks for n in h.added}

    @property
    def commentable_lines(self) -> set[int]:
        return {n for h in self.hunks for n in h.right_side}

    def render(self) -> str:
        """Render the file's hunks with right-side line numbers, as shown to the model."""
        out = [f"--- {self.old_path or '/dev/null'}", f"+++ {self.new_path or '/dev/null'}"]
        for h in self.hunks:
            out.append(f"@@ -{h.old_start},{h.old_len} +{h.new_start},{h.new_len} @@{h.header}")
            for ln in h.lines:
                num = f"{ln.new_no:>5}" if ln.new_no is not None else "     "
                out.append(f"{num} {ln.kind}{ln.text}")
        return "\n".join(out)


def _strip_prefix(path: str) -> str | None:
    path = path.strip()
    if "\t" in path:  # git may append a timestamp after a tab
        path = path.split("\t", 1)[0]
    if path == "/dev/null":
        return None
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    if path[:2] in ("a/", "b/"):
        path = path[2:]
    return path


def parse_diff(text: str) -> list[FileDiff]:
    """Parse a unified diff. Tolerates ``git diff`` headers, renames and binary markers."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: Hunk | None = None
    old_no = new_no = 0

    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            current = FileDiff(old_path=None, new_path=None)
            m = re.match(r"diff --git a/(.+) b/(.+)$", raw)
            if m:
                current.old_path, current.new_path = m.group(1), m.group(2)
            files.append(current)
            hunk = None
            continue
        if raw.startswith("--- ") and (hunk is None or _hunk_done(hunk, old_no, new_no)):
            if current is None or current.hunks:
                current = FileDiff(old_path=None, new_path=None)
                files.append(current)
            current.old_path = _strip_prefix(raw[4:])
            hunk = None
            continue
        if raw.startswith("+++ ") and current is not None and hunk is None:
            current.new_path = _strip_prefix(raw[4:])
            continue
        if current is None:
            continue
        if raw.startswith("new file mode"):
            current.old_path = None
            continue
        if raw.startswith("deleted file mode"):
            current.new_path = None
            continue
        if raw.startswith("rename from "):
            current.old_path = raw[len("rename from ") :]
            continue
        if raw.startswith("rename to "):
            current.new_path = raw[len("rename to ") :]
            continue
        if raw.startswith("Binary files ") or raw.startswith("GIT binary patch"):
            current.is_binary = True
            continue
        m = _HUNK_RE.match(raw)
        if m:
            hunk = Hunk(
                old_start=int(m.group(1)),
                old_len=int(m.group(2)) if m.group(2) is not None else 1,
                new_start=int(m.group(3)),
                new_len=int(m.group(4)) if m.group(4) is not None else 1,
                header=m.group(5),
            )
            current.hunks.append(hunk)
            old_no, new_no = hunk.old_start, hunk.new_start
            continue
        if hunk is None:
            continue
        if raw.startswith("\\"):  # "\ No newline at end of file"
            continue
        kind, body = (raw[0], raw[1:]) if raw else (" ", "")
        if kind == "+":
            hunk.lines.append(DiffLine("+", body, None, new_no))
            new_no += 1
        elif kind == "-":
            hunk.lines.append(DiffLine("-", body, old_no, None))
            old_no += 1
        elif kind == " ":
            hunk.lines.append(DiffLine(" ", body, old_no, new_no))
            old_no += 1
            new_no += 1
    return [f for f in files if f.hunks or f.is_binary or f.status in ("added", "deleted")]


def _hunk_done(hunk: Hunk, old_no: int, new_no: int) -> bool:
    return old_no >= hunk.old_start + hunk.old_len and new_no >= hunk.new_start + hunk.new_len


def render_diff(files: list[FileDiff]) -> str:
    return "\n\n".join(f.render() for f in files if not f.is_binary)
