"""The review agent, as a LangGraph state machine.

    parse -> context -> review -> validate -> select --(no fixable finding left)--> finalize
                                                 ^  |
                                                 |  v
                                   (accepted or  verify <- propose
                                    out of tries)    |         ^
                                                     +---------+  (rejected: retry with feedback)

The model writes the review and proposes fixes; the graph decides what reaches the user.
Findings must anchor to changed lines, and a fix is attached only after the sandbox shows
it applies and causes no test, lint or type regression. Rejected fixes are retried with the
failure output, up to ``max_fix_attempts``, and are never shown.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from .config import Config
from .context import ContextBlock, SymbolIndex, gather_context
from .diff import FileDiff, parse_diff, render_diff
from .findings import Dropped, EditError, Finding, Fix, build_patch, parse_edits, validate_findings
from .llm import LLMClient, LLMError, Usage, extract_json
from .prompts import fix_messages, review_messages
from .sandbox import Sandbox


class State(TypedDict, total=False):
    diff: str
    description: str
    files: list[FileDiff]
    diff_text: str
    context: list[ContextBlock]
    raw_review: Any
    findings: list[Finding]
    dropped: list[Dropped]
    current: int | None
    candidate: Fix | None
    feedback: list[str]
    errors: list[str]
    trace: list[dict[str, Any]]


@dataclass
class ReviewResult:
    findings: list[Finding]
    dropped: list[Dropped]
    errors: list[str]
    usage: Usage
    seconds: float
    files_reviewed: list[str]
    trace: list[dict[str, Any]] = field(default_factory=list)

    @property
    def verified_fixes(self) -> int:
        return sum(1 for f in self.findings if f.fix is not None)

    @property
    def rejected_patches(self) -> int:
        return sum(len(f.fix_rejections) for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "dropped": [{"reason": d.reason, "raw": d.raw} for d in self.dropped],
            "errors": self.errors,
            "files_reviewed": self.files_reviewed,
            "verified_fixes": self.verified_fixes,
            "rejected_patches": self.rejected_patches,
            "usage": vars(self.usage),
            "seconds": round(self.seconds, 2),
        }


class ReviewAgent:
    def __init__(
        self,
        llm: LLMClient,
        repo: Path,
        config: Config | None = None,
        sandbox: Sandbox | None = None,
        fix: bool = True,
    ) -> None:
        self.llm = llm
        self.repo = repo.resolve()
        self.config = config or Config.load(self.repo)
        self.sandbox = sandbox
        self.fix = fix and sandbox is not None
        self.graph = self._build()

    # -- graph -------------------------------------------------------------------------
    def _build(self) -> Any:
        g: Any = StateGraph(State)  # langgraph's overloads are stricter than its runtime
        g.add_node("parse", self._parse)
        g.add_node("context", self._context)
        g.add_node("review", self._review)
        g.add_node("validate", self._validate)
        g.add_node("select", self._select)
        g.add_node("propose", self._propose)
        g.add_node("verify", self._verify)
        g.set_entry_point("parse")
        g.add_conditional_edges("parse", lambda s: "context" if s["files"] else END)
        g.add_edge("context", "review")
        g.add_edge("review", "validate")
        g.add_edge("validate", "select")
        g.add_conditional_edges("select", lambda s: "propose" if s.get("current") is not None else END)
        g.add_edge("propose", "verify")
        g.add_conditional_edges("verify", self._after_verify)
        return g.compile()

    def run(self, diff: str, description: str = "") -> ReviewResult:
        start = time.perf_counter()
        before = Usage()
        before.add(self.llm.usage)
        state: State = self.graph.invoke(
            {"diff": diff, "description": description, "errors": [], "trace": [], "feedback": []},
            {"recursion_limit": 200},
        )
        usage = Usage()
        usage.add(self.llm.usage)
        for k in ("calls", "prompt_tokens", "completion_tokens", "seconds"):
            setattr(usage, k, getattr(usage, k) - getattr(before, k))
        return ReviewResult(
            findings=state.get("findings", []),
            dropped=state.get("dropped", []),
            errors=state.get("errors", []),
            usage=usage,
            seconds=time.perf_counter() - start,
            files_reviewed=[f.path for f in state.get("files", [])],
            trace=state.get("trace", []),
        )

    # -- nodes -------------------------------------------------------------------------
    def _parse(self, s: State) -> State:
        files = [f for f in parse_diff(s["diff"]) if not f.is_binary and f.new_path is not None]
        return {"files": files, "diff_text": render_diff(files),
                "trace": [*s["trace"], {"step": "parse", "files": [f.path for f in files]}]}

    def _context(self, s: State) -> State:
        index = SymbolIndex.build(self.repo)
        blocks = gather_context(s["files"], index, self.config.context_budget)
        return {"context": blocks,
                "trace": [*s["trace"], {"step": "context", "blocks": [b.title for b in blocks]}]}

    def _review(self, s: State) -> State:
        msgs = review_messages(s["diff_text"], s["context"], s.get("description", ""),
                               self.config.prompt_variant)
        try:
            raw = extract_json(self.llm.complete(msgs))
        except LLMError as exc:
            return {"raw_review": {"findings": []}, "errors": [*s["errors"], f"review: {exc}"]}
        return {"raw_review": raw}

    def _validate(self, s: State) -> State:
        findings, dropped = validate_findings(
            s["raw_review"], s["files"],
            min_confidence=self.config.min_confidence, max_comments=self.config.max_comments,
        )
        return {"findings": findings, "dropped": dropped, "current": None,
                "trace": [*s["trace"], {"step": "validate", "kept": len(findings),
                                        "dropped": [d.reason for d in dropped]}]}

    def _select(self, s: State) -> State:
        if not self.fix:
            return {"current": None}
        cur = s.get("current")
        start = 0 if cur is None else cur + 1
        for i in range(start, len(s["findings"])):
            if s["findings"][i].fixable:
                return {"current": i, "feedback": [], "candidate": None}
        return {"current": None}

    def _propose(self, s: State) -> State:
        i = s["current"]
        assert i is not None
        f = s["findings"][i]
        f.fix_attempts += 1
        text = self.sandbox.read(f.file) if self.sandbox else None
        if text is None:
            f.fix_rejections.append("could not read the file")
            return {"candidate": None, "feedback": [*s["feedback"], "could not read the file"]}
        try:
            reply = self.llm.complete(fix_messages(f, text, s["feedback"], s["diff_text"]))
            edits, summary = parse_edits(extract_json(reply))
            patch = build_patch(edits, {f.file: text, **self._sources(edits)},
                                {fd.path for fd in s["files"]}, self.config.max_fix_lines)
        except (LLMError, EditError) as exc:
            msg = f"Rejected before running checks: {exc}"
            f.fix_rejections.append(str(exc))
            return {"candidate": None, "feedback": [*s["feedback"], msg]}
        return {"candidate": Fix(edits=edits, patch=patch, summary=summary)}

    def _sources(self, edits: list[Any]) -> dict[str, str]:
        out: dict[str, str] = {}
        for e in edits:
            text = self.sandbox.read(e.file) if self.sandbox else None
            if text is not None:
                out[e.file] = text
        return out

    def _verify(self, s: State) -> State:
        i = s["current"]
        assert i is not None and self.sandbox is not None
        f = s["findings"][i]
        cand = s.get("candidate")
        event: dict[str, Any] = {"step": "verify", "finding": f.id, "attempt": f.fix_attempts}
        if cand is None:
            event["result"] = "no candidate"
            return {"trace": [*s["trace"], event]}
        verdict = self.sandbox.verify(cand.patch)
        event.update(accepted=verdict.accepted, reason=verdict.reason)
        if verdict.accepted:
            cand.verification = verdict.to_dict()
            f.fix = cand
            return {"trace": [*s["trace"], event]}
        f.fix_rejections.append(verdict.reason)
        return {"feedback": [*s["feedback"], f"Patch:\n{cand.patch}\n{verdict.feedback()}"],
                "candidate": None, "trace": [*s["trace"], event]}

    def _after_verify(self, s: State) -> str:
        i = s["current"]
        assert i is not None
        f = s["findings"][i]
        if f.fix is not None or f.fix_attempts >= self.config.max_fix_attempts:
            return "select"
        return "propose"
