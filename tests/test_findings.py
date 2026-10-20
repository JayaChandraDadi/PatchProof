import subprocess

import pytest

from patchproof.diff import parse_diff
from patchproof.findings import EditError, build_patch, parse_edits, validate_findings

from .conftest import git_diff

BEFORE = "a = 1\nb = 2\nc = 3\nd = 4\ne = 5\nf = 6\ng = 7\nh = 8\ni = 9\nj = 10\n"
AFTER = BEFORE.replace("e = 5", "e = 50").replace("f = 6", "f = 60")
FILES = parse_diff(git_diff("m.py", BEFORE, AFTER))


def raw(*items):
    return {"findings": list(items)}


def test_keeps_findings_on_changed_lines():
    kept, dropped = validate_findings(raw({"file": "m.py", "line": 5, "title": "x", "confidence": 0.9}), FILES)
    assert [(f.file, f.line, f.id) for f in kept] == [("m.py", 5, "F1")]
    assert dropped == []


def test_drops_hallucinated_file_and_far_line():
    kept, dropped = validate_findings(raw(
        {"file": "nope.py", "line": 5, "title": "x"},
        {"file": "m.py", "line": 10, "title": "far away"},
        {"file": "m.py", "line": "abc", "title": "bad line"},
        {"file": "m.py", "line": 5, "title": ""},
        "not an object",
    ), FILES)
    assert kept == []
    reasons = " | ".join(d.reason for d in dropped)
    assert "not in the diff" in reasons and "not near a changed line" in reasons
    assert "no line number" in reasons and "empty title" in reasons and "not an object" in reasons


def test_snaps_small_misses_and_dedupes():
    kept, dropped = validate_findings(raw(
        {"file": "b/m.py", "line": 8, "title": "near", "confidence": 0.8},
        {"file": "m.py", "line": 6, "title": "same spot", "confidence": 0.7},
    ), FILES)
    assert [f.line for f in kept] == [6]
    assert "duplicate" in dropped[0].reason


def test_confidence_severity_and_cap():
    items = [{"file": "m.py", "line": 5, "title": "low", "confidence": 0.1},
             {"file": "m.py", "line": 5, "title": "a", "severity": "low", "confidence": 0.9},
             {"file": "m.py", "line": 6, "title": "b", "severity": "HIGH", "category": "weird"}]
    kept, dropped = validate_findings(raw(*items), FILES, max_comments=1)
    assert len(kept) == 1 and kept[0].title == "b" and kept[0].severity == "high"
    assert kept[0].category == "other"
    assert any("confidence" in d.reason for d in dropped)
    assert any("comment limit" in d.reason for d in dropped)


def test_parse_edits_errors():
    with pytest.raises(EditError):
        parse_edits({"nope": []})
    with pytest.raises(EditError):
        parse_edits({"edits": []})
    with pytest.raises(EditError):
        parse_edits({"edits": [{"file": "m.py", "start_line": "x", "end_line": 1}]})
    edits, summary = parse_edits({"summary": "s", "edits": [
        {"file": "./m.py", "start_line": 5, "end_line": 5, "replacement": "e = 5"}]})
    assert edits[0].file == "m.py" and summary == "s"


def _applies(tmp_path, source, patch):
    (tmp_path / "m.py").write_text(source)
    (tmp_path / "p.diff").write_text(patch)
    subprocess.run(["git", "apply", "p.diff"], cwd=tmp_path, check=True)
    return (tmp_path / "m.py").read_text()


def test_build_patch_replace_insert_delete(tmp_path):
    edits, _ = parse_edits({"edits": [
        {"file": "m.py", "start_line": 2, "end_line": 2, "replacement": "b = 20"},
        {"file": "m.py", "start_line": 4, "end_line": 3, "replacement": "inserted = True"},
        {"file": "m.py", "start_line": 9, "end_line": 10, "replacement": ""},
    ]})
    patch = build_patch(edits, {"m.py": AFTER}, {"m.py"})
    out = _applies(tmp_path, AFTER, patch)
    assert out.splitlines() == ["a = 1", "b = 20", "c = 3", "inserted = True", "d = 4",
                                "e = 50", "f = 60", "g = 7", "h = 8"]


def test_build_patch_rejects_bad_edits():
    def edits(*e):
        return parse_edits({"edits": list(e)})[0]

    src = {"m.py": AFTER, "other.py": "x\n"}
    with pytest.raises(EditError, match="not part of this PR"):
        build_patch(edits({"file": "other.py", "start_line": 1, "end_line": 1, "replacement": "y"}), src, {"m.py"})
    with pytest.raises(EditError, match="outside"):
        build_patch(edits({"file": "m.py", "start_line": 9, "end_line": 99, "replacement": "y"}), src, {"m.py"})
    with pytest.raises(EditError, match="overlap"):
        build_patch(edits({"file": "m.py", "start_line": 2, "end_line": 4, "replacement": "y"},
                          {"file": "m.py", "start_line": 3, "end_line": 3, "replacement": "z"}), src, {"m.py"})
    with pytest.raises(EditError, match="do not change"):
        build_patch(edits({"file": "m.py", "start_line": 1, "end_line": 1, "replacement": "a = 1"}), src, {"m.py"})
    with pytest.raises(EditError, match="limit"):
        build_patch(edits({"file": "m.py", "start_line": 1, "end_line": 10, "replacement": "x"}), src,
                    {"m.py"}, max_changed_lines=5)
