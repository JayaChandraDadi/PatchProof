"""Build the seeded-bug benchmark: pull requests against the fixture library.

Each PR adds a correct feature and renames a local variable in some function (a harmless
refactor). Bug PRs also carry one seeded mutation in a different function. A mutation is
kept only if the PR's own visible tests still pass (so CI would not catch it) and the hidden
tests fail (so it is a real bug). Clean PRs are checked to pass both suites.
"""

from __future__ import annotations

import difflib
import json
import random
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .catalog import FEATURES
from .mutations import Mutation, apply_rename, find_mutations, find_renames

FIXTURE = Path(__file__).parent / "fixture"
DATA = Path(__file__).parent / "data" / "prs.jsonl"
MODULES = ["intervals", "textutil", "stats", "cart", "search", "ratelimit", "pagination"]


@dataclass
class SeededBug:
    line: int
    operator: str
    original: str
    replacement: str
    function: str
    correct_line: str


@dataclass
class PullRequest:
    id: str
    kind: str  # "bug" or "clean"
    path: str
    title: str
    diff: str
    head_source: str
    bug: SeededBug | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, line: str) -> PullRequest:
        d: dict[str, Any] = json.loads(line)
        bug = SeededBug(**d.pop("bug")) if d.get("bug") else None
        d.pop("bug", None)
        return cls(**d, bug=bug)


def git_style_diff(path: str, before: str, after: str) -> str:
    body = difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                fromfile=f"a/{path}", tofile=f"b/{path}", n=3)
    return f"diff --git a/{path} b/{path}\n" + "".join(body)


def materialize(head_source: str | None, path: str | None, dest: Path, hidden: bool) -> Path:
    """Copy the fixture to dest with the PR applied; hidden tests only when judging."""
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache",
                                    *(() if hidden else ("tests_hidden",)))
    shutil.copytree(FIXTURE, dest, ignore=ignore)
    if head_source is not None and path is not None:
        (dest / path).write_text(head_source)
    return dest


class Hung(Exception):
    """The test run exceeded its timeout (usually a seeded infinite loop)."""


def run_tests(repo: Path, suite: str, timeout: float = 20) -> bool:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", suite],
            cwd=repo, capture_output=True, text=True, timeout=timeout,
            env={"PYTHONPATH": str(repo), "PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"},
        )
    except subprocess.TimeoutExpired as exc:
        raise Hung(suite) from exc
    return proc.returncode == 0


def _passes(head: str, path: str, suite: str) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        repo = materialize(head, path, Path(tmp) / "repo", hidden=True)
        return run_tests(repo, suite)


def build(n_bug: int = 100, n_clean: int = 40, seed: int = 7,
          out: Path = DATA, log: bool = True) -> list[PullRequest]:
    rng = random.Random(seed)
    sources = {m: (FIXTURE / "minilib" / f"{m}.py").read_text() for m in MODULES}
    features = {m: [f for f in FEATURES if f["module"] == m] for m in MODULES}

    candidates: list[tuple[str, Mutation]] = [
        (m, mut) for m in MODULES for mut in find_mutations(sources[m])
    ]
    rng.shuffle(candidates)
    per_key: Counter[tuple[str, str]] = Counter()
    per_module: Counter[str] = Counter()
    module_cap = n_bug // len(MODULES) + 3
    prs: list[PullRequest] = []

    for module, mut in candidates:
        if sum(1 for p in prs if p.kind == "bug") >= n_bug:
            break
        if per_module[module] >= module_cap or per_key[(module, mut.operator)] >= 5:
            continue
        path = f"minilib/{module}.py"
        base = sources[module]
        renames = [r for r in find_renames(base) if r[0] != mut.function]
        feature = rng.choice(features[module])
        correct = base
        if renames:
            fn, old, new = rng.choice(renames)
            correct = apply_rename(base, fn, old, new)
        # The rename is in another function, so the mutation's columns are unchanged.
        try:
            head = mut.apply(correct) + feature["code"]
        except AssertionError:
            continue
        correct_full = correct + feature["code"]
        if head == correct_full:
            continue
        try:
            compile(head, path, "exec")
        except SyntaxError:
            continue
        try:
            if not _passes(head, path, "tests_visible") or _passes(head, path, "tests_hidden"):
                continue
        except Hung:
            continue  # bugs that hang the test run are left out to keep the benchmark fast
        bug = SeededBug(mut.line, mut.operator, mut.original, mut.replacement, mut.function,
                        correct_full.splitlines()[mut.line - 1])
        n = sum(1 for p in prs if p.kind == "bug") + 1
        prs.append(PullRequest(f"bug-{n:03d}", "bug", path, feature["title"],
                               git_style_diff(path, base, head), head, bug))
        per_key[(module, mut.operator)] += 1
        per_module[module] += 1
        if log:
            print(f"bug-{n:03d} {path}:{mut.line} {mut.describe()}", flush=True)

    clean = 0
    attempts = 0
    while clean < n_clean and attempts < n_clean * 5:
        attempts += 1
        module = rng.choice(MODULES)
        path = f"minilib/{module}.py"
        base = sources[module]
        renames = find_renames(base)
        if not renames:
            continue
        fn, old, new = rng.choice(renames)
        feature = rng.choice(features[module])
        head = apply_rename(base, fn, old, new) + feature["code"]
        if not (_passes(head, path, "tests_visible") and _passes(head, path, "tests_hidden")):
            continue
        clean += 1
        prs.append(PullRequest(f"clean-{clean:03d}", "clean", path, feature["title"],
                               git_style_diff(path, base, head), head))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(p.to_json() + "\n" for p in prs))
    if log:
        ops = Counter(p.bug.operator for p in prs if p.bug)
        print(f"wrote {len(prs)} PRs to {out}: {sum(ops.values())} bug, {clean} clean; {dict(ops)}")
    return prs


def load(path: Path = DATA) -> list[PullRequest]:
    return [PullRequest.from_json(ln) for ln in path.read_text().splitlines() if ln.strip()]
