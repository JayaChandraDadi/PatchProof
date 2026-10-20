"""Prompts for review and fix generation. Variants are named so the benchmark can compare them."""

from __future__ import annotations

from .context import ContextBlock
from .findings import Finding
from .llm import Message

REVIEW_SYSTEM = """You are a careful senior engineer reviewing a pull request.
Report only real defects introduced or exposed by the change: wrong results, crashes,
off-by-one errors, inverted conditions, wrong operators, swapped arguments, missing
error handling, security problems, race conditions. Do not report style preferences,
naming, missing docs, or speculative issues. If the change looks correct, return an empty list.

Reply with JSON only:
{"findings": [{"file": "<path as shown after +++ b/>", "line": <right-side line number from the diff>,
  "severity": "high|medium|low",
  "category": "bug|security|performance|error-handling|concurrency|style|other",
  "title": "<one sentence>", "explanation": "<why it is wrong and what input breaks it>",
  "confidence": <0.0-1.0>}]}"""

REVIEW_CHECKLIST = """
Before answering, check every changed line against this list:
1. Comparisons: < vs <=, > vs >=, == vs !=; boundary values (0, 1, len-1, len).
2. Arithmetic: + vs -, * vs /, integer division, rounding.
3. Boolean logic: and vs or, a negated condition, early returns.
4. Calls: argument order, a changed default, a missing return value.
5. Loops and slices: range bounds, slice ends, empty inputs.
Read the surrounding functions and call sites given as context: a line can look fine on
its own and be wrong for its callers."""

PROMPT_VARIANTS = ("basic", "checklist")

FIX_SYSTEM = """You fix one defect in a pull request with the smallest correct change.
You are given the full file with line numbers. Replace whole lines only.
Do not reformat, rename, or change unrelated code. Keep the PR's intended behaviour.

Reply with JSON only:
{"summary": "<one sentence>", "edits": [{"file": "<path>", "start_line": <first line to replace>,
  "end_line": <last line to replace, inclusive>,
  "replacement": "<new text for those lines, no line numbers>"}]}"""


def review_messages(
    diff_text: str, context: list[ContextBlock], description: str = "", variant: str = "checklist"
) -> list[Message]:
    system = REVIEW_SYSTEM + (REVIEW_CHECKLIST if variant == "checklist" else "")
    parts = []
    if description.strip():
        parts.append("## Pull request description\n" + description.strip()[:2000])
    parts.append(
        "## Diff\nRight-side line numbers are shown before each line; use them in your answer.\n"
        "```diff\n" + diff_text + "\n```"
    )
    if context:
        parts.append("## Context from the repository\n" + "\n\n".join(b.render() for b in context))
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(parts)}]


def numbered(text: str) -> str:
    return "\n".join(f"{i:>5}| {line}" for i, line in enumerate(text.splitlines(), 1))


def fix_messages(
    finding: Finding, file_text: str, feedback: list[str], diff_text: str
) -> list[Message]:
    user = [
        f"## Defect to fix\n{finding.file}:{finding.line} [{finding.severity} {finding.category}] "
        f"{finding.title}\n{finding.explanation}",
        "## Pull request diff\n```diff\n" + diff_text[:6000] + "\n```",
        f"## Current contents of {finding.file}\n```\n{numbered(file_text)}\n```",
    ]
    if feedback:
        user.append(
            "## Previous attempts were rejected\nThe earlier fixes failed verification. "
            "Do not repeat them.\n" + "\n\n".join(feedback[-2:])
        )
    return [{"role": "system", "content": FIX_SYSTEM}, {"role": "user", "content": "\n\n".join(user)}]
