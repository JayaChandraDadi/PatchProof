"""Per-repository settings, read from ``.patchproof.toml`` (all keys optional).

Example::

    min_confidence = 0.5
    max_comments = 6
    max_fix_attempts = 3

    [[check]]
    name = "tests"
    kind = "pytest"            # pytest | ruff | mypy | command
    command = "{python} -m pytest -q tests"

    [sandbox]
    backend = "docker"         # local | docker
    image = "my-project-dev:latest"
"""

from __future__ import annotations

import importlib.util
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KINDS = ("pytest", "ruff", "mypy", "command")


@dataclass(frozen=True)
class CheckSpec:
    name: str
    kind: str
    command: str
    timeout: float = 300.0


@dataclass
class Config:
    checks: list[CheckSpec] = field(default_factory=list)
    min_confidence: float = 0.4
    max_comments: int = 8
    max_fix_attempts: int = 3
    max_fix_lines: int = 40
    context_budget: int = 12000
    prompt_variant: str = "checklist"
    sandbox_backend: str = "local"
    sandbox_image: str = "python:3.12-slim"
    sandbox_memory: str = "2g"
    sandbox_cpus: str = "2"

    @classmethod
    def load(cls, repo: Path) -> Config:
        cfg = cls()
        path = repo / ".patchproof.toml"
        data: dict[str, Any] = tomllib.loads(path.read_text()) if path.exists() else {}
        for key in ("min_confidence", "max_comments", "max_fix_attempts", "max_fix_lines",
                    "context_budget", "prompt_variant"):
            if key in data:
                setattr(cfg, key, type(getattr(cfg, key))(data[key]))
        sandbox = data.get("sandbox", {})
        cfg.sandbox_backend = str(sandbox.get("backend", cfg.sandbox_backend))
        cfg.sandbox_image = str(sandbox.get("image", cfg.sandbox_image))
        cfg.sandbox_memory = str(sandbox.get("memory", cfg.sandbox_memory))
        cfg.sandbox_cpus = str(sandbox.get("cpus", cfg.sandbox_cpus))
        specs = data.get("check", [])
        if specs:
            for s in specs:
                kind = str(s.get("kind", "command"))
                if kind not in KINDS:
                    raise ValueError(f"unknown check kind {kind!r}; expected one of {KINDS}")
                cfg.checks.append(
                    CheckSpec(str(s["name"]), kind, str(s["command"]), float(s.get("timeout", 300)))
                )
        else:
            cfg.checks = detect_checks(repo)
        return cfg


def detect_checks(repo: Path) -> list[CheckSpec]:
    """Pick sensible checks for a Python repository when none are configured."""
    checks: list[CheckSpec] = []
    pyproject = (repo / "pyproject.toml").read_text() if (repo / "pyproject.toml").exists() else ""
    has_tests = (
        (repo / "tests").is_dir() or any(repo.glob("test_*.py")) or any(repo.glob("*/test_*.py"))
    )
    if has_tests:
        checks.append(CheckSpec("tests", "pytest", "{python} -m pytest -q"))
    has_ruff_config = "[tool.ruff" in pyproject or (repo / "ruff.toml").exists()
    if has_ruff_config and importlib.util.find_spec("ruff") is not None:
        checks.append(CheckSpec("lint", "ruff", "{python} -m ruff check ."))
    has_mypy = importlib.util.find_spec("mypy") is not None
    if has_mypy and ("[tool.mypy" in pyproject or (repo / "mypy.ini").exists()):
        checks.append(CheckSpec("types", "mypy", "{python} -m mypy ."))
    return checks
