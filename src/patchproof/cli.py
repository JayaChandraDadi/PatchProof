"""Command-line entry point.

    patchproof review --base origin/main            review local changes
    patchproof pr owner/repo#42 --post              review a GitHub PR and post comments
    patchproof bench run --model qwen2.5-coder:7b   run the seeded-bug benchmark
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .agent import ReviewAgent, ReviewResult
from .config import Config
from .diff import parse_diff
from .llm import OpenAICompatibleClient
from .sandbox import Sandbox


def _git_diff(repo: Path, base: str) -> str:
    proc = subprocess.run(["git", "diff", "--no-color", "-U3", f"{base}...HEAD"],
                          cwd=repo, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"git diff failed: {proc.stderr.strip()}")
    return proc.stdout


def render_text(result: ReviewResult) -> str:
    out = []
    for f in result.findings:
        out.append(f"{f.file}:{f.line}  [{f.severity} {f.category} {f.confidence:.2f}] {f.title}")
        if f.explanation:
            out.append("    " + f.explanation.replace("\n", "\n    "))
        if f.fix is not None:
            out.append(f"    verified fix ({f.fix.summary or 'no summary'}):")
            out.append("    " + f.fix.patch.rstrip().replace("\n", "\n    "))
        elif f.fix_rejections:
            out.append(f"    no verified fix; {len(f.fix_rejections)} attempt(s) rejected:")
            out.extend(f"      - {r}" for r in f.fix_rejections)
        out.append("")
    if not result.findings:
        out.append("No issues found.")
    out.append(
        f"{len(result.findings)} finding(s), {result.verified_fixes} verified fix(es), "
        f"{result.rejected_patches} patch(es) rejected, {len(result.dropped)} model claim(s) dropped, "
        f"{result.usage.calls} LLM call(s), {result.seconds:.1f}s"
    )
    for e in result.errors:
        out.append(f"error: {e}")
    return "\n".join(out)


def _llm(args: argparse.Namespace) -> OpenAICompatibleClient:
    return OpenAICompatibleClient.from_env(model=args.model, base_url=args.base_url)


def cmd_review(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    diff = Path(args.diff).read_text() if args.diff else _git_diff(repo, args.base)
    cfg = Config.load(repo)
    if args.docker:
        cfg.sandbox_backend = "docker"
    with Sandbox.from_config(repo, cfg) as sandbox:
        agent = ReviewAgent(_llm(args), repo, cfg, sandbox, fix=not args.no_fix)
        result = agent.run(diff, args.description or "")
    if args.json:
        Path(args.json).write_text(json.dumps(result.to_dict(), indent=2))
    print(render_text(result))
    return 1 if args.fail_on_findings and result.findings else 0


def cmd_pr(args: argparse.Namespace) -> int:
    from .github import GitHubClient, PullRef, build_comments, summary_body

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("set GITHUB_TOKEN to review a pull request")
    ref = PullRef.parse(args.ref)
    gh = GitHubClient(token, os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    try:
        pull = gh.pull(ref)
        diff = gh.diff(ref)
        repo = Path(args.repo).resolve()
        cfg = Config.load(repo)
        llm = _llm(args)
        with Sandbox.from_config(repo, cfg) as sandbox:
            agent = ReviewAgent(llm, repo, cfg, sandbox, fix=not args.no_fix)
            result = agent.run(diff, f"{pull.get('title', '')}\n\n{pull.get('body') or ''}")
        print(render_text(result))
        if args.post:
            comments = build_comments(result, parse_diff(diff))
            gh.post_review(ref, pull["head"]["sha"], summary_body(result, llm.name), comments)
            print(f"posted a review with {len(comments)} comment(s) to {args.ref}")
    finally:
        gh.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="patchproof", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def model_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--model", help="model name (default $PATCHPROOF_MODEL or qwen2.5-coder:7b)")
        sp.add_argument("--base-url", help="OpenAI-compatible endpoint (default Ollama on localhost)")
        sp.add_argument("--no-fix", action="store_true", help="review only; do not propose fixes")

    r = sub.add_parser("review", help="review the current branch against a base")
    r.add_argument("--repo", default=".")
    r.add_argument("--base", default="origin/main")
    r.add_argument("--diff", help="review a diff file instead of git diff")
    r.add_argument("--description", help="what the change is meant to do")
    r.add_argument("--json", help="also write the full result as JSON")
    r.add_argument("--docker", action="store_true", help="verify fixes in a Docker sandbox")
    r.add_argument("--fail-on-findings", action="store_true")
    model_args(r)
    r.set_defaults(func=cmd_review)

    g = sub.add_parser("pr", help="review a GitHub pull request (repo checked out at the PR head)")
    g.add_argument("ref", help="owner/repo#number")
    g.add_argument("--repo", default=".")
    g.add_argument("--post", action="store_true", help="post the review to GitHub")
    model_args(g)
    g.set_defaults(func=cmd_pr)

    from .bench.cli import add_bench_parser

    add_bench_parser(sub)

    args = p.parse_args(argv)
    code: int = args.func(args)
    return code


if __name__ == "__main__":
    sys.exit(main())
