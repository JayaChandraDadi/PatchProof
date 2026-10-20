"""Verify a proposed patch by running the repository's own checks in an isolated copy.

A fix is accepted only if it applies cleanly and introduces no regression compared with the
unpatched code: no newly failing test, no new lint or type error, and no command that used
to pass and now fails. Comparing against a baseline (instead of requiring everything green)
keeps PatchProof usable on repositories that already have failing tests.

Two backends run the same checks:

* ``LocalRunner`` runs in a temporary copy with secrets stripped from the environment,
  CPU and memory limits, and a timeout.
* ``DockerRunner`` runs in a container with no network, capped memory, CPUs and processes.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .config import CheckSpec, Config

IGNORE = shutil.ignore_patterns(
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "*.pyc", "build", "dist", "*.egg-info",
)
SECRET_ENV = re.compile(r"(TOKEN|SECRET|PASSWORD|API_KEY|ACCESS_KEY|CREDENTIAL)", re.I)
JUNIT = ".patchproof-junit.xml"
_MYPY_LINE = re.compile(r"^(?P<file>[^:\n]+):\d+(?::\d+)?: error: (?P<msg>.*?)(?:\s+\[(?P<code>[\w-]+)\])?$")


@dataclass
class RunOutput:
    exit_code: int
    output: str
    seconds: float
    timed_out: bool = False


class Runner(Protocol):
    name: str

    def run(self, workdir: Path, command: str, timeout: float) -> RunOutput: ...


def _scrubbed_env(workdir: Path, extra_paths: list[str]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join([*extra_paths, env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env["HOME"] = str(workdir)  # keep tools from reading the user's dotfiles
    return env


def _limits(cpu_seconds: int, memory_bytes: int) -> Any:
    def apply() -> None:  # runs in the child before exec
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            if sys.platform.startswith("linux"):
                resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        except (ImportError, ValueError, OSError):
            pass

    return apply if os.name == "posix" else None


@dataclass
class LocalRunner:
    name: str = "local"
    memory_bytes: int = 4 * 1024**3

    def run(self, workdir: Path, command: str, timeout: float) -> RunOutput:
        cmd = command.replace("{python}", shlex.quote(sys.executable))
        paths = [str(workdir / "src"), str(workdir)] if (workdir / "src").is_dir() else [str(workdir)]
        start = time.perf_counter()
        try:
            proc = subprocess.run(
                shlex.split(cmd), cwd=workdir, env=_scrubbed_env(workdir, paths),
                capture_output=True, text=True, timeout=timeout,
                preexec_fn=_limits(int(timeout) + 5, self.memory_bytes),
            )
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            return RunOutput(124, out + "\n[timed out]", time.perf_counter() - start, True)
        except FileNotFoundError as exc:
            return RunOutput(127, str(exc), time.perf_counter() - start)
        return RunOutput(proc.returncode, proc.stdout + proc.stderr, time.perf_counter() - start)


@dataclass
class DockerRunner:
    image: str = "python:3.12-slim"
    memory: str = "2g"
    cpus: str = "2"
    name: str = "docker"

    def command_line(self, workdir: Path, command: str) -> list[str]:
        cmd = command.replace("{python}", "python")
        return [
            "docker", "run", "--rm", "--network", "none",
            "--memory", self.memory, "--cpus", self.cpus, "--pids-limit", "256",
            "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
            "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "PYTHONPATH=/work/src:/work", "-e", "HOME=/work",
            "-v", f"{workdir}:/work", "-w", "/work", self.image, "sh", "-c", cmd,
        ]

    def run(self, workdir: Path, command: str, timeout: float) -> RunOutput:
        start = time.perf_counter()
        try:
            proc = subprocess.run(
                self.command_line(workdir, command), capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            return RunOutput(124, "[timed out]", time.perf_counter() - start, True)
        except FileNotFoundError as exc:
            return RunOutput(127, f"docker not found: {exc}", time.perf_counter() - start)
        return RunOutput(proc.returncode, proc.stdout + proc.stderr, time.perf_counter() - start)


@dataclass
class CheckResult:
    name: str
    kind: str
    exit_code: int
    failures: Counter[str]
    output_tail: str
    seconds: float
    ran: bool = True  # False when the tool could not run at all (collection error, crash)
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.ran and self.exit_code == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "kind": self.kind, "exit_code": self.exit_code, "ran": self.ran,
            "failures": sorted(self.failures.elements()), "seconds": round(self.seconds, 2),
            "timed_out": self.timed_out,
        }


def _tail(text: str, n: int = 2500) -> str:
    return text if len(text) <= n else "..." + text[-n:]


def run_check(runner: Runner, workdir: Path, spec: CheckSpec) -> CheckResult:
    command = spec.command
    if spec.kind == "pytest":
        command += f" -p no:cacheprovider --junitxml={JUNIT}"
    elif spec.kind == "ruff":
        command += " --output-format json --no-cache"
    elif spec.kind == "mypy":
        command += " --no-error-summary --show-error-codes --no-incremental"
    (workdir / JUNIT).unlink(missing_ok=True)
    out = runner.run(workdir, command, spec.timeout)
    failures: Counter[str] = Counter()
    ran = not out.timed_out and out.exit_code not in (126, 127)

    if spec.kind == "pytest":
        junit = workdir / JUNIT
        if junit.exists():
            failures = _junit_failures(junit)
            # Exit codes 2-4 mean pytest stopped early (collection error, usage error).
            if out.exit_code in (2, 3, 4):
                ran = False
        else:
            ran = False if out.exit_code != 5 else ran  # 5 = no tests collected
    elif spec.kind == "ruff":
        failures = _ruff_issues(out.output, workdir)
        if out.exit_code not in (0, 1):
            ran = False
    elif spec.kind == "mypy":
        failures = _mypy_issues(out.output)
        if out.exit_code not in (0, 1):
            ran = False
    elif out.exit_code != 0:
        failures = Counter({f"{spec.name} exited with {out.exit_code}": 1})

    return CheckResult(spec.name, spec.kind, out.exit_code, failures, _tail(out.output),
                       out.seconds, ran, out.timed_out)


def _junit_failures(path: Path) -> Counter[str]:
    failed: Counter[str] = Counter()
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return failed
    for case in root.iter("testcase"):
        if any(child.tag in ("failure", "error") for child in case):
            failed[f"{case.get('classname', '')}::{case.get('name', '')}"] += 1
    return failed


def _ruff_issues(output: str, workdir: Path) -> Counter[str]:
    issues: Counter[str] = Counter()
    start = output.find("[")
    if start == -1:
        return issues
    try:
        data = json.loads(output[start : output.rfind("]") + 1])
    except ValueError:
        return issues
    for item in data:
        fname = str(item.get("filename", ""))
        for base in (str(workdir) + os.sep, "/work/"):
            fname = fname.removeprefix(base)
        # Line numbers are left out on purpose: a fix shifts them without adding an issue.
        issues[f"{fname}: {item.get('code')} {item.get('message')}"] += 1
    return issues


def _mypy_issues(output: str) -> Counter[str]:
    issues: Counter[str] = Counter()
    for line in output.splitlines():
        m = _MYPY_LINE.match(line.strip())
        if m:
            issues[f"{m.group('file')}: [{m.group('code') or 'misc'}] {m.group('msg')}"] += 1
    return issues


@dataclass
class Verdict:
    accepted: bool
    applied: bool
    regressions: list[str] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    reason: str = ""
    seconds: float = 0.0

    def feedback(self) -> str:
        """A short explanation of the rejection, fed back to the model on the next attempt."""
        lines = [f"Rejected: {self.reason}"]
        for r in self.regressions[:8]:
            lines.append(f"- {r}")
        for c in self.checks:
            if c.failures or not c.ran:
                lines.append(f"--- {c.name} output (tail) ---\n{c.output_tail[-1200:]}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted, "applied": self.applied, "reason": self.reason,
            "regressions": self.regressions, "fixed": self.fixed,
            "checks": [c.to_dict() for c in self.checks], "seconds": round(self.seconds, 2),
        }


def compare(before: list[CheckResult], after: list[CheckResult]) -> tuple[list[str], list[str]]:
    """Return (regressions, fixed) between a baseline and a patched run of the same checks."""
    regressions: list[str] = []
    fixed: list[str] = []
    base = {c.name: c for c in before}
    for a in after:
        b = base.get(a.name)
        if not a.ran:
            if b is None or b.ran:
                regressions.append(f"{a.name}: could not run after the patch (exit {a.exit_code})")
            continue
        if b is None or not b.ran:
            if not a.ok:
                regressions.append(f"{a.name}: failing ({sum(a.failures.values())} problems)")
            continue
        new = a.failures - b.failures
        regressions.extend(f"{a.name}: {k}" for k in sorted(new.elements()))
        if a.kind == "command" and b.ok and not a.ok:
            regressions.append(f"{a.name}: passed before the patch, fails after it")
        fixed.extend(f"{a.name}: {k}" for k in sorted((b.failures - a.failures).elements()))
    return regressions, fixed


class Sandbox:
    """Copies of the PR head, a cached baseline run, and verification of patches."""

    def __init__(self, repo: Path, checks: list[CheckSpec], runner: Runner | None = None) -> None:
        self.repo = repo.resolve()
        self.checks = checks
        self.runner: Runner = runner or LocalRunner()
        self._tmp = Path(tempfile.mkdtemp(prefix="patchproof-"))
        self._baseline: list[CheckResult] | None = None
        self._cache: dict[str, Verdict] = {}
        self._n = 0

    @classmethod
    def from_config(cls, repo: Path, cfg: Config) -> Sandbox:
        runner: Runner
        if cfg.sandbox_backend == "docker":
            runner = DockerRunner(cfg.sandbox_image, cfg.sandbox_memory, cfg.sandbox_cpus)
        else:
            runner = LocalRunner()
        return cls(repo, cfg.checks, runner)

    def close(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def __enter__(self) -> Sandbox:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _copy(self) -> Path:
        self._n += 1
        dest = self._tmp / f"ws{self._n}"
        shutil.copytree(self.repo, dest, ignore=IGNORE, symlinks=True)
        return dest

    def baseline(self) -> list[CheckResult]:
        if self._baseline is None:
            ws = self._copy()
            self._baseline = [run_check(self.runner, ws, c) for c in self.checks]
            shutil.rmtree(ws, ignore_errors=True)
        return self._baseline

    def verify(self, patch: str) -> Verdict:
        if patch in self._cache:
            return self._cache[patch]
        start = time.perf_counter()
        ws = self._copy()
        try:
            patch_file = ws / ".patchproof.patch"
            patch_file.write_text(patch)
            chk = subprocess.run(["git", "apply", "--check", "-p1", patch_file.name],
                                 cwd=ws, capture_output=True, text=True)
            if chk.returncode != 0:
                verdict = Verdict(False, False, reason="patch does not apply: " + chk.stderr.strip()[:300])
            else:
                subprocess.run(["git", "apply", "-p1", patch_file.name], cwd=ws, check=True,
                               capture_output=True)
                patch_file.unlink()
                if not self.checks:
                    verdict = Verdict(False, True, reason="no checks configured; nothing can verify the fix")
                else:
                    after = [run_check(self.runner, ws, c) for c in self.checks]
                    regressions, fixed = compare(self.baseline(), after)
                    verdict = Verdict(
                        accepted=not regressions, applied=True, regressions=regressions,
                        fixed=fixed, checks=after,
                        reason="" if not regressions else f"{len(regressions)} regression(s)",
                    )
        finally:
            shutil.rmtree(ws, ignore_errors=True)
        verdict.seconds = time.perf_counter() - start
        self._cache[patch] = verdict
        return verdict

    def read(self, path: str) -> str | None:
        p = (self.repo / path).resolve()
        if not p.is_relative_to(self.repo) or not p.is_file():
            return None
        return p.read_text(errors="replace")
