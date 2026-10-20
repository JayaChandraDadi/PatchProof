import subprocess
import textwrap
from pathlib import Path

import pytest


@pytest.fixture
def tiny_repo(tmp_path: Path) -> Path:
    """A small Python project with a test suite, used by sandbox and agent tests."""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "calc.py").write_text(textwrap.dedent('''\
        def add(a, b):
            return a + b


        def clamp(x, lo, hi):
            if x < lo:
                return lo
            if x > hi:
                return hi
            return x


        def total(values):
            out = 0
            for v in values:
                out = add(out, v)
            return out
        '''))
    (repo / "tests" / "test_calc.py").write_text(textwrap.dedent('''\
        from pkg.calc import add, clamp, total


        def test_add():
            assert add(2, 3) == 5


        def test_clamp():
            assert clamp(5, 0, 10) == 5
            assert clamp(-1, 0, 10) == 0


        def test_total():
            assert total([1, 2, 3]) == 6
        '''))
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def git_diff(path: str, before: str, after: str) -> str:
    import difflib

    body = difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                fromfile=f"a/{path}", tofile=f"b/{path}")
    return f"diff --git a/{path} b/{path}\n" + "".join(body)
