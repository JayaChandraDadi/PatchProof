import json

from patchproof import cli
from patchproof.llm import ScriptedClient

from .conftest import git_diff


def test_review_diff_file(tiny_repo, tmp_path, monkeypatch, capsys):
    calc = tiny_repo / "pkg" / "calc.py"
    before = calc.read_text()
    after = before.replace("return a + b", "return a + b + 0")
    calc.write_text(after)
    diff = tmp_path / "pr.diff"
    diff.write_text(git_diff("pkg/calc.py", before, after))
    monkeypatch.setattr(cli, "_llm", lambda args: ScriptedClient(lambda m: '{"findings": []}'))
    out_json = tmp_path / "r.json"
    code = cli.main(["review", "--repo", str(tiny_repo), "--diff", str(diff), "--json", str(out_json)])
    assert code == 0
    assert "No issues found." in capsys.readouterr().out
    assert json.loads(out_json.read_text())["files_reviewed"] == ["pkg/calc.py"]


def test_review_git_base(tiny_repo, monkeypatch, capsys):
    import subprocess

    def git(*a):
        subprocess.run(["git", *a], cwd=tiny_repo, check=True, capture_output=True)

    git("-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    git("branch", "-M", "main")
    git("checkout", "-qb", "feature")
    calc = tiny_repo / "pkg" / "calc.py"
    calc.write_text(calc.read_text().replace("return a + b", "return b + a"))
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "swap")

    line = calc.read_text().splitlines().index("    return b + a") + 1
    reply = json.dumps({"findings": [{"file": "pkg/calc.py", "line": line, "title": "order changed",
                                      "severity": "low", "category": "style", "confidence": 0.8}]})
    monkeypatch.setattr(cli, "_llm", lambda args: ScriptedClient(lambda m: reply))
    code = cli.main(["review", "--repo", str(tiny_repo), "--base", "main", "--fail-on-findings"])
    out = capsys.readouterr().out
    assert code == 1 and f"pkg/calc.py:{line}" in out and "order changed" in out
