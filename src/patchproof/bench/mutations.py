"""Seeded bugs: small, realistic source edits found with ``ast`` and applied as text edits.

Edits are made on exact token positions, so the rest of the file keeps its formatting and
the PR diff shows only the lines a developer would actually have touched.
"""

from __future__ import annotations

import ast
import io
import tokenize
from dataclasses import dataclass

COMPARE_SWAPS = {"<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "=="}
BINOP_SWAPS = {"+": "-", "-": "+", "//": "/", "*": "+"}
BOOL_SWAPS = {"and": "or", "or": "and"}


@dataclass(frozen=True)
class Mutation:
    operator: str
    line: int  # 1-based
    col: int  # 0-based start column on that line
    end_col: int
    original: str
    replacement: str
    function: str

    def apply(self, source: str) -> str:
        lines = source.splitlines(keepends=True)
        text = lines[self.line - 1]
        assert text[self.col : self.end_col] == self.original, (text, self)
        lines[self.line - 1] = text[: self.col] + self.replacement + text[self.end_col :]
        return "".join(lines)

    def describe(self) -> str:
        return f"{self.operator}: `{self.original}` -> `{self.replacement}` in {self.function}()"


def _tokens(source: str) -> list[tokenize.TokenInfo]:
    return list(tokenize.generate_tokens(io.StringIO(source).readline))


def _op_between(
    toks: list[tokenize.TokenInfo], start: tuple[int, int], end: tuple[int, int], options: set[str]
) -> tokenize.TokenInfo | None:
    for t in toks:
        if (start <= t.start and t.end <= end and t.string in options
                and t.type in (tokenize.OP, tokenize.NAME)):
            return t
    return None


def _segment(source_lines: list[str], node: ast.expr) -> str | None:
    if node.lineno != node.end_lineno or node.end_col_offset is None:
        return None
    return source_lines[node.lineno - 1][node.col_offset : node.end_col_offset]


def find_mutations(source: str) -> list[Mutation]:
    """Every single-line mutation site inside function bodies (not docstrings or raises)."""
    tree = ast.parse(source)
    toks = _tokens(source)
    lines = source.splitlines()
    found: list[Mutation] = []

    for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        skip: set[int] = set()
        for n in ast.walk(func):
            if isinstance(n, ast.Raise):
                skip.update(range(n.lineno, (n.end_lineno or n.lineno) + 1))
        for node in ast.walk(func):
            if getattr(node, "lineno", None) in skip:
                continue
            found.extend(_node_mutations(node, func.name, toks, lines))
    unique = {(m.line, m.col, m.end_col, m.replacement): m for m in found}
    return sorted(unique.values(), key=lambda m: (m.line, m.col, m.operator))


def _node_mutations(
    node: ast.AST, fname: str, toks: list[tokenize.TokenInfo], lines: list[str]
) -> list[Mutation]:
    out: list[Mutation] = []
    if isinstance(node, ast.Compare) and node.lineno == node.end_lineno:
        prev: ast.expr = node.left
        for comp in node.comparators:
            t = _op_between(toks, (prev.end_lineno or 0, prev.end_col_offset or 0),
                            (comp.lineno, comp.col_offset), set(COMPARE_SWAPS))
            if t is not None:
                out.append(Mutation("comparison", t.start[0], t.start[1], t.end[1], t.string,
                                    COMPARE_SWAPS[t.string], fname))
            prev = comp
    elif isinstance(node, ast.BinOp) and node.lineno == node.end_lineno:
        t = _op_between(toks, (node.left.end_lineno or 0, node.left.end_col_offset or 0),
                        (node.right.lineno, node.right.col_offset), set(BINOP_SWAPS))
        if t is not None and not (t.string == "*" and isinstance(node.right, ast.Constant)
                                  and node.right.value == 2):
            out.append(Mutation("arithmetic", t.start[0], t.start[1], t.end[1], t.string,
                                BINOP_SWAPS[t.string], fname))
    elif isinstance(node, ast.BoolOp) and node.lineno == node.end_lineno and len(node.values) == 2:
        a, b = node.values
        t = _op_between(toks, (a.end_lineno or 0, a.end_col_offset or 0), (b.lineno, b.col_offset),
                        set(BOOL_SWAPS))
        if t is not None:
            out.append(Mutation("boolean", t.start[0], t.start[1], t.end[1], t.string,
                                BOOL_SWAPS[t.string], fname))
    elif isinstance(node, (ast.If, ast.While)):
        seg = _segment(lines, node.test)
        if seg is not None and not isinstance(node.test, ast.UnaryOp):
            out.append(Mutation("negated-condition", node.test.lineno, node.test.col_offset,
                                node.test.end_col_offset or 0, seg, f"not ({seg})", fname))
    elif isinstance(node, ast.Constant) and isinstance(node.value, int) \
            and not isinstance(node.value, bool) and node.value in (0, 1, 2):
        seg = _segment(lines, node)
        if seg is not None and seg.isdigit():
            new = str(node.value + 1) if node.value != 2 else "1"
            out.append(Mutation("off-by-one", node.lineno, node.col_offset,
                                node.end_col_offset or 0, seg, new, fname))
    elif isinstance(node, ast.Call) and len(node.args) == 2 and not node.keywords:
        a, b = node.args
        sa, sb = _segment(lines, a), _segment(lines, b)
        if sa and sb and sa != sb and a.lineno == b.lineno and not any(
            isinstance(x, ast.Starred) for x in node.args
        ):
            line = lines[a.lineno - 1]
            between = line[a.end_col_offset or 0 : b.col_offset]
            if between.strip() == ",":
                original = line[a.col_offset : b.end_col_offset or 0]
                out.append(Mutation("swapped-arguments", a.lineno, a.col_offset,
                                    b.end_col_offset or 0, original, f"{sb}{between}{sa}", fname))
    return out


def find_renames(source: str) -> list[tuple[str, str, str]]:
    """Behaviour-preserving refactors: (function, old_local, new_local) for local variables."""
    tree = ast.parse(source)
    out: list[tuple[str, str, str]] = []
    names_in_module = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        params = {a.arg for a in func.args.args + func.args.kwonlyargs}
        stored = {n.id for n in ast.walk(func) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
        for name in sorted(stored - params):
            new = {"result": "out", "current": "line", "total": "running_total", "lo": "low",
                   "hi": "high", "mid": "middle", "start": "begin", "covered": "count",
                   "ordered": "sorted_values", "chunk": "window_values", "now": "current_time",
                   "elapsed": "delta", "missing": "shortfall", "slug": "cleaned",
                   "prefix": "shared", "chars": "letters", "squares": "sum_sq",
                   "rank": "position", "weight": "frac", "discounted": "after_discount",
                   "tax": "tax_cents", "cursor": "next_free", "m": "avg", "n": "count_n"}.get(name)
            if new and new not in names_in_module:
                out.append((func.name, name, new))
    return out


def apply_rename(source: str, function: str, old: str, new: str) -> str:
    tree = ast.parse(source)
    func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function)
    first, last = func.lineno, func.end_lineno or func.lineno
    lines = source.splitlines(keepends=True)
    edits = sorted(
        ((t.start[0], t.start[1], t.end[1]) for t in _tokens(source)
         if t.type == tokenize.NAME and t.string == old and first <= t.start[0] <= last),
        reverse=True,
    )
    for row, c0, c1 in edits:
        lines[row - 1] = lines[row - 1][:c0] + new + lines[row - 1][c1:]
    return "".join(lines)
