import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from patchproof.config import CheckSpec, Config, detect_checks
from patchproof.sandbox import (
    CheckResult,
    DockerRunner,
    LocalRunner,
    Sandbox,
    _mypy_issues,
    _ruff_issues,
    compare,
)

from .conftest import git_diff

TESTS = [CheckSpec("tests", "pytest", "{python} -m pytest -q tests", 60)]


def patch_for(repo: Path, old: str, new: str) -> str:
    before = (repo / "pkg" / "calc.py").read_text()
    return git_diff("pkg/calc.py", before, before.replace(old, new))


def test_good_patch_is_accepted(tiny_repo):
    with Sandbox(tiny_repo, TESTS) as sb:
        v = sb.verify(patch_for(tiny_repo, "    return x\n", "    return int(x)\n"))
    assert v.applied and v.accepted, v.feedback()
    assert v.checks[0].failures == Counter()


def test_patch_that_breaks_a_test_is_rejected(tiny_repo):
    with Sandbox(tiny_repo, TESTS) as sb:
        v = sb.verify(patch_for(tiny_repo, "return a + b", "return a - b"))
    assert v.applied and not v.accepted
    assert any("test_add" in r for r in v.regressions)
    assert any("test_total" in r for r in v.regressions)
    assert "Rejected" in v.feedback() and "test_add" in v.feedback()


def test_syntax_error_patch_cannot_run(tiny_repo):
    with Sandbox(tiny_repo, TESTS) as sb:
        v = sb.verify(patch_for(tiny_repo, "return a + b", "return a +"))
    assert not v.accepted
    assert "could not run after the patch" in v.regressions[0]


def test_patch_that_does_not_apply(tiny_repo):
    bogus = git_diff("pkg/calc.py", "nothing like this\n", "changed\n")
    with Sandbox(tiny_repo, TESTS) as sb:
        v = sb.verify(bogus)
    assert not v.applied and not v.accepted and "does not apply" in v.reason


def test_existing_failures_do_not_block(tiny_repo):
    (tiny_repo / "tests" / "test_broken.py").write_text("def test_already_broken():\n    assert False\n")
    with Sandbox(tiny_repo, TESTS) as sb:
        assert sb.baseline()[0].failures
        v = sb.verify(patch_for(tiny_repo, "    return x\n", "    return int(x)\n"))
    assert v.accepted, v.regressions


def test_fixing_a_failure_is_reported(tiny_repo):
    calc = tiny_repo / "pkg" / "calc.py"
    calc.write_text(calc.read_text().replace("return a + b", "return a - b"))
    with Sandbox(tiny_repo, TESTS) as sb:
        v = sb.verify(patch_for(tiny_repo, "return a - b", "return a + b"))
    assert v.accepted and any("test_add" in f for f in v.fixed)


def test_verify_is_cached_and_does_not_touch_the_repo(tiny_repo):
    original = (tiny_repo / "pkg" / "calc.py").read_text()
    p = patch_for(tiny_repo, "return a + b", "return a - b")
    with Sandbox(tiny_repo, TESTS) as sb:
        assert sb.verify(p) is sb.verify(p)
    assert (tiny_repo / "pkg" / "calc.py").read_text() == original


def test_new_lint_issue_is_a_regression(tiny_repo):
    pytest.importorskip("ruff")
    checks = [CheckSpec("lint", "ruff", "{python} -m ruff check --select F pkg", 60)]
    with Sandbox(tiny_repo, checks) as sb:
        v = sb.verify(patch_for(tiny_repo, "    return x\n", "    return undefined_name\n"))
    assert not v.accepted
    assert any("F821" in r for r in v.regressions)


def test_no_checks_means_no_verification(tiny_repo):
    with Sandbox(tiny_repo, []) as sb:
        v = sb.verify(patch_for(tiny_repo, "    return x\n", "    return int(x)\n"))
    assert not v.accepted and "no checks" in v.reason


def test_local_runner_strips_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("HARMLESS", "visible")
    out = LocalRunner().run(tmp_path, "{python} -c 'import os; print(sorted(os.environ))'", 30)
    assert "HARMLESS" in out.output
    assert "GITHUB_TOKEN" not in out.output and "OPENAI_API_KEY" not in out.output


def test_local_runner_timeout(tmp_path):
    out = LocalRunner().run(tmp_path, "{python} -c 'import time; time.sleep(5)'", 0.5)
    assert out.timed_out and out.exit_code == 124


def test_compare_logic():
    def cr(name, kind, failures, ran=True, code=0):
        return CheckResult(name, kind, code, Counter(failures), "", 0.0, ran)

    regs, fixed = compare(
        [cr("t", "pytest", {"a": 1, "b": 1}), cr("c", "command", {}), cr("x", "pytest", {}, ran=False)],
        [cr("t", "pytest", {"b": 1, "c": 1}, code=1), cr("c", "command", {"c exited with 1": 1}, code=1),
         cr("x", "pytest", {}, ran=False)],
    )
    assert "t: c" in regs and any("passed before" in r for r in regs)
    assert fixed == ["t: a"]
    assert not any(r.startswith("x") for r in regs)  # broken before and after: not this patch's fault


def test_tool_output_parsers(tmp_path):
    mypy = "pkg/a.py:3: error: Incompatible return value type  [return-value]\nSuccess: no issues\n"
    assert _mypy_issues(mypy) == Counter({"pkg/a.py: [return-value] Incompatible return value type": 1})
    ruff = f'[{{"filename": "{tmp_path}/pkg/a.py", "code": "F401", "message": "unused", "location": {{"row": 1}}}}]'
    assert _ruff_issues(ruff, tmp_path) == Counter({"pkg/a.py: F401 unused": 1})


def test_docker_command_is_locked_down(tmp_path):
    cmd = DockerRunner("img:1").command_line(tmp_path, "{python} -m pytest")
    joined = " ".join(cmd)
    assert "--network none" in joined and "--cap-drop ALL" in joined and "--pids-limit 256" in joined
    assert cmd[-1] == "python -m pytest" and "img:1" in cmd


def _docker_ok() -> bool:
    return shutil.which("docker") is not None and subprocess.run(
        ["docker", "info"], capture_output=True).returncode == 0


@pytest.mark.docker
@pytest.mark.skipif(not _docker_ok(), reason="no Docker daemon")
def test_docker_sandbox_end_to_end(tiny_repo):
    image = "patchproof-test:latest"
    subprocess.run(["docker", "build", "-q", "-t", image, "-"], check=True,
                   input=b"FROM python:3.12-slim\nRUN pip install -q pytest\n")
    sb = Sandbox(tiny_repo, TESTS, DockerRunner(image))
    try:
        assert sb.verify(patch_for(tiny_repo, "    return x\n", "    return int(x)\n")).accepted
        assert not sb.verify(patch_for(tiny_repo, "return a + b", "return a - b")).accepted
        net = DockerRunner(image).run(tiny_repo, "python -c \"import urllib.request as u; u.urlopen('http://1.1.1.1', timeout=3)\"", 60)
        assert net.exit_code != 0  # no network inside the sandbox
    finally:
        sb.close()


def test_config_file_and_detection(tmp_path):
    (tmp_path / "tests").mkdir()
    assert [c.kind for c in detect_checks(tmp_path)][0] == "pytest"
    (tmp_path / ".patchproof.toml").write_text(
        'max_comments = 3\nprompt_variant = "basic"\n[sandbox]\nbackend = "docker"\nimage = "x"\n'
        '[[check]]\nname = "unit"\nkind = "pytest"\ncommand = "{python} -m pytest -q"\ntimeout = 30\n'
    )
    cfg = Config.load(tmp_path)
    assert cfg.max_comments == 3 and cfg.prompt_variant == "basic"
    assert cfg.sandbox_backend == "docker" and cfg.sandbox_image == "x"
    assert cfg.checks == [CheckSpec("unit", "pytest", "{python} -m pytest -q", 30.0)]
    (tmp_path / ".patchproof.toml").write_text('[[check]]\nname = "x"\nkind = "bogus"\ncommand = "y"\n')
    with pytest.raises(ValueError):
        Config.load(tmp_path)
