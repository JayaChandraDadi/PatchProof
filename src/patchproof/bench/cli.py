"""``patchproof bench`` subcommands: generate, run, compare, table."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..llm import OpenAICompatibleClient
from .dataset import DATA, PullRequest, build, load
from .runner import (
    PRScore,
    gate,
    markdown_table,
    null_client,
    oracle_client,
    run,
    summarize,
)


def _select(prs: list[PullRequest], args: argparse.Namespace) -> list[PullRequest]:
    if args.ids:
        wanted = set(args.ids.split(","))
        prs = [p for p in prs if p.id in wanted]
    if args.limit:
        bugs = [p for p in prs if p.kind == "bug"][: args.limit]
        clean = [p for p in prs if p.kind == "clean"][: max(1, args.limit * 2 // 5)]
        prs = bugs + clean
    return prs


def cmd_generate(args: argparse.Namespace) -> int:
    build(args.bugs, args.clean, args.seed, Path(args.out))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    prs = _select(load(Path(args.data)), args)
    if args.reviewer == "llm":
        def factory(pr: PullRequest) -> Any:
            return OpenAICompatibleClient.from_env(model=args.model, base_url=args.base_url)
        label = args.label or f"{factory(prs[0]).name} ({args.prompt_variant})"
    elif args.reviewer == "oracle":
        factory, label = oracle_client, args.label or "oracle"
    elif args.reviewer == "oracle-noisy":
        def factory(pr: PullRequest) -> Any:
            return oracle_client(pr, noisy=True)
        label = args.label or "oracle-noisy"
    else:
        factory, label = null_client, args.label or "null"

    out_dir = Path(args.out) / _slug(label)
    out_dir.mkdir(parents=True, exist_ok=True)

    def progress(s: PRScore) -> None:
        mark = "caught" if s.detected else ("clean" if s.kind == "clean" else "missed")
        fix = " fix-ok" if s.correct_fix else (" fix-verified" if s.verified_fix else "")
        print(f"{s.id:<10} {mark:<7} findings={s.findings} false={s.false_findings}{fix} "
              f"{s.seconds:.1f}s", flush=True)

    scores = run(prs, factory, fix=not args.no_fix, prompt_variant=args.prompt_variant,
                 out=out_dir / "scores.jsonl", progress=progress)
    summary = summarize(scores, label)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print()
    print(markdown_table([summary]))
    print(f"\nwrote {out_dir}/summary.json and scores.jsonl")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    base = json.loads(Path(args.baseline).read_text())
    cand = json.loads(Path(args.candidate).read_text())
    print(markdown_table([base, cand]))
    problems = gate(base, cand, max_detection_drop=args.max_detection_drop,
                    max_false_increase=args.max_false_increase,
                    max_correct_fix_drop=args.max_correct_fix_drop)
    for p in problems:
        print(f"BLOCK: {p}")
    if not problems:
        print("OK: the candidate is within the regression budget")
    return 1 if problems else 0


def cmd_table(args: argparse.Namespace) -> int:
    print(markdown_table([json.loads(Path(p).read_text()) for p in args.summaries]))
    return 0


def _slug(label: str) -> str:
    return "".join(c if c.isalnum() or c in "-._" else "-" for c in label).strip("-").lower()


def add_bench_parser(sub: Any) -> None:
    b = sub.add_parser("bench", help="seeded-bug benchmark")
    bs = b.add_subparsers(dest="bench_cmd", required=True)

    g = bs.add_parser("generate", help="rebuild the benchmark PRs")
    g.add_argument("--bugs", type=int, default=100)
    g.add_argument("--clean", type=int, default=40)
    g.add_argument("--seed", type=int, default=7)
    g.add_argument("--out", default=str(DATA))
    g.set_defaults(func=cmd_generate)

    r = bs.add_parser("run", help="review every benchmark PR and score the results")
    r.add_argument("--reviewer", choices=["llm", "oracle", "oracle-noisy", "null"], default="llm")
    r.add_argument("--model")
    r.add_argument("--base-url")
    r.add_argument("--prompt-variant", choices=["basic", "checklist"], default="checklist")
    r.add_argument("--no-fix", action="store_true")
    r.add_argument("--limit", type=int, help="first N bug PRs (plus a share of clean PRs)")
    r.add_argument("--ids", help="comma-separated PR ids")
    r.add_argument("--label")
    r.add_argument("--data", default=str(DATA))
    r.add_argument("--out", default="results")
    r.set_defaults(func=cmd_run)

    c = bs.add_parser("compare", help="gate a candidate run against a baseline run")
    c.add_argument("baseline")
    c.add_argument("candidate")
    c.add_argument("--max-detection-drop", type=float, default=0.03)
    c.add_argument("--max-false-increase", type=float, default=0.10)
    c.add_argument("--max-correct-fix-drop", type=float, default=0.05)
    c.set_defaults(func=cmd_compare)

    t = bs.add_parser("table", help="print a markdown table of summary.json files")
    t.add_argument("summaries", nargs="+")
    t.set_defaults(func=cmd_table)
