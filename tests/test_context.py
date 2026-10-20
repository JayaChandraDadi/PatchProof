from patchproof.context import SymbolIndex, gather_context
from patchproof.diff import parse_diff

from .conftest import git_diff


def test_index_and_context(tiny_repo):
    path = tiny_repo / "pkg" / "calc.py"
    before = path.read_text()
    after = before.replace("out = add(out, v)", "out = add(out, clamp(v, 0, 9))")
    path.write_text(after)
    index = SymbolIndex.build(tiny_repo)
    assert {d.path for d in index.definitions["add"]} == {"pkg/calc.py"}
    assert index.enclosing("pkg/calc.py", 14).name == "total"

    files = parse_diff(git_diff("pkg/calc.py", before, after))
    blocks = gather_context(files, index)
    titles = [b.title for b in blocks]
    assert titles[0] == "pkg/calc.py: total (changed)"
    assert any("clamp (called by the change)" in t for t in titles)
    assert any("add (called by the change)" in t for t in titles)
    assert any(t.startswith("tests/test_calc.py") and "calls total" in t for t in titles)


def test_budget_limits_context(tiny_repo):
    index = SymbolIndex.build(tiny_repo)
    before = (tiny_repo / "pkg" / "calc.py").read_text()
    files = parse_diff(git_diff("pkg/calc.py", before, before.replace("a + b", "a - b")))
    assert gather_context(files, index, budget_chars=10) == []


def test_syntax_errors_are_tolerated(tmp_path):
    (tmp_path / "bad.py").write_text("def broken(:\n")
    index = SymbolIndex.build(tmp_path)
    assert index.definitions == {} and "bad.py" in index.sources
