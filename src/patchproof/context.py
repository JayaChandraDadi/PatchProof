"""Gather review context beyond the diff: enclosing functions, callee definitions, call sites.

A diff alone hides most bugs that matter in review. If a PR changes ``parse_amount``, the
reviewer needs to see the whole function, the helpers it now calls, and the places that
call it. For Python files we build a small symbol index with ``ast``; other files get the
surrounding lines only. Everything is bounded by a character budget.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from .diff import FileDiff

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", "build", "dist"}


@dataclass(frozen=True)
class Definition:
    name: str
    qualname: str
    path: str
    start: int
    end: int


@dataclass(frozen=True)
class CallSite:
    callee: str
    path: str
    line: int


@dataclass
class SymbolIndex:
    root: Path
    definitions: dict[str, list[Definition]] = field(default_factory=dict)
    calls: dict[str, list[CallSite]] = field(default_factory=dict)
    sources: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def build(cls, root: Path, max_files: int = 2000) -> SymbolIndex:
        index = cls(root=root)
        for n, path in enumerate(_python_files(root)):
            if n >= max_files:
                break
            index.add_file(path.relative_to(root).as_posix(), path.read_text(errors="replace"))
        return index

    def add_file(self, rel: str, text: str) -> None:
        self.sources[rel] = text.splitlines()
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return
        _Visitor(self, rel).visit(tree)

    def enclosing(self, path: str, line: int) -> Definition | None:
        """The innermost function or class in ``path`` that contains ``line``."""
        best: Definition | None = None
        for defs in self.definitions.values():
            for d in defs:
                inside = d.path == path and d.start <= line <= d.end
                if inside and (best is None or (d.end - d.start) < (best.end - best.start)):
                    best = d
        return best

    def snippet(self, path: str, start: int, end: int) -> str:
        lines = self.sources.get(path, [])
        start, end = max(1, start), min(len(lines), end)
        return "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(start, end + 1))


class _Visitor(ast.NodeVisitor):
    def __init__(self, index: SymbolIndex, path: str) -> None:
        self.index = index
        self.path = path
        self.stack: list[str] = []

    def _define(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        qual = ".".join([*self.stack, node.name])
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        d = Definition(node.name, qual, self.path, start, node.end_lineno or node.lineno)
        self.index.definitions.setdefault(node.name, []).append(d)
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _define
    visit_AsyncFunctionDef = _define
    visit_ClassDef = _define

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        if name:
            site = CallSite(name, self.path, node.lineno)
            self.index.calls.setdefault(name, []).append(site)
        self.generic_visit(node)


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _python_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for p in sorted(root.rglob("*.py")):
        if not any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            out.append(p)
    return out


@dataclass
class ContextBlock:
    title: str
    body: str

    def render(self) -> str:
        return f"### {self.title}\n```\n{self.body}\n```"


def gather_context(
    files: list[FileDiff], index: SymbolIndex, budget_chars: int = 12000
) -> list[ContextBlock]:
    """Build context blocks in priority order until the budget is spent."""
    blocks: list[ContextBlock] = []
    seen: set[tuple[str, int, int]] = set()
    changed_funcs: list[Definition] = []
    called: set[str] = set()

    for f in files:
        if f.new_path is None or f.is_binary:
            continue
        path = f.new_path
        for line in sorted(f.added_lines):
            d = index.enclosing(path, line)
            if d and (d.path, d.start, d.end) not in seen:
                seen.add((d.path, d.start, d.end))
                changed_funcs.append(d)
                blocks.append(
                    ContextBlock(f"{path}: {d.qualname} (changed)", index.snippet(path, d.start, d.end))
                )
        src = index.sources.get(path)
        if src is not None and not path.endswith(".py"):
            for h in f.hunks:
                start, end = h.new_start - 10, h.new_start + h.new_len + 10
                blocks.append(ContextBlock(f"{path} lines {start}-{end}", index.snippet(path, start, end)))
        for line in f.added_lines:
            text = _line(index, path, line)
            for name in _names_called(text):
                called.add(name)

    # Definitions of functions the new code calls, from anywhere in the repo.
    changed_names = {d.name for d in changed_funcs}
    for name in sorted(called - changed_names):
        for d in index.definitions.get(name, [])[:2]:
            key = (d.path, d.start, d.end)
            if key not in seen:
                seen.add(key)
                blocks.append(
                    ContextBlock(f"{d.path}: {d.qualname} (called by the change)",
                                 index.snippet(d.path, d.start, d.end))
                )

    # Call sites of changed functions: who is affected if the behaviour changes.
    for d in changed_funcs:
        for site in index.calls.get(d.name, [])[:6]:
            enclosing = index.enclosing(site.path, site.line)
            if enclosing and enclosing.name == d.name:
                continue  # recursion inside the function itself
            key = (site.path, site.line, site.line)
            if key not in seen:
                seen.add(key)
                blocks.append(
                    ContextBlock(f"{site.path}:{site.line} calls {d.name}",
                                 index.snippet(site.path, site.line - 2, site.line + 2))
                )

    out: list[ContextBlock] = []
    used = 0
    for b in blocks:
        size = len(b.body) + len(b.title)
        if used + size > budget_chars:
            continue
        out.append(b)
        used += size
    return out


def _line(index: SymbolIndex, path: str, line: int) -> str:
    lines = index.sources.get(path, [])
    return lines[line - 1] if 0 < line <= len(lines) else ""


def _names_called(line: str) -> set[str]:
    try:
        tree = ast.parse(line.strip(), mode="exec")
    except SyntaxError:
        # Partial statements (e.g. "if x(y):" without a body) still contain calls.
        return set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", line)) - {"if", "while", "for", "return"}
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            n = _call_name(node.func)
            if n:
                names.add(n)
    return names
