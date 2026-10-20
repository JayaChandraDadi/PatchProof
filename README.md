# PatchProof

**An LLM code-review agent that only suggests fixes it has verified.** PatchProof reads a pull request, gathers the code around the change, and posts inline review comments. When it proposes a fix, the fix runs through your own tests, linter and type checker in a sandbox first. Fixes that do not apply, or that break anything that passed before, are thrown away and retried with the failure output. They are never shown to the author.

[![CI](https://github.com/JayaChandraDadi/patchproof/actions/workflows/ci.yml/badge.svg)](https://github.com/JayaChandraDadi/patchproof/actions/workflows/ci.yml)

Real output on benchmark PR `bug-004`, using the scripted `oracle-noisy` reviewer described [below](#pipeline-results-scripted-reviewers-all-140-prs). It invents two bad claims and sends one broken patch first; PatchProof drops both claims and rejects the broken patch:

```
minilib/stats.py:49  [high bug 0.90] `-` should be `+`
    Seeded bug.
    verified fix (restore +):
    diff --git a/minilib/stats.py b/minilib/stats.py
    --- a/minilib/stats.py
    +++ b/minilib/stats.py
    @@ -46,7 +46,7 @@
         if window <= 0:
             raise ValueError("window must be positive")
         result = []
    -    for i in range(len(values) - window - 1):
    +    for i in range(len(values) - window + 1):
             chunk = values[i : i + window]
             result.append(sum(chunk) / window)
         return result

1 finding(s), 1 verified fix(es), 1 patch(es) rejected, 2 model claim(s) dropped, 3 LLM call(s), 3.3s
```

## Why

AI review tools are fast and often wrong. Two kinds of mistakes waste the most time:

1. **Comments that point at nothing.** The model names a file that is not in the PR, or a line nobody changed.
2. **Fixes that do not work.** A suggested patch that does not apply, breaks a test, or adds a lint error is worse than no suggestion.

PatchProof treats the model's output as untrusted input and checks both.

## How it works

The agent is a [LangGraph](https://github.com/langchain-ai/langgraph) state machine:

```
parse -> context -> review -> validate -> select --(no fixable finding left)--> done
                                            ^  |
                       (accepted, or out    |  v
                        of attempts)      verify <-- propose
                                            |            ^
                                            +------------+  rejected: retry with the failure output
```

| Step | What it does |
|---|---|
| **parse** | Parses the unified diff into files, hunks and right-side line numbers (renames, new and deleted files, binary files). |
| **context** | Builds an `ast` symbol index of the repository and adds, within a character budget: the whole function around each changed line, definitions of functions the new code calls, and call sites of the changed functions. |
| **review** | Asks the model for findings as JSON. Two prompt variants (`basic`, `checklist`) can be compared on the benchmark. |
| **validate** | Keeps a finding only if its file is in the diff and its line is a changed line (or within 3 lines of one, snapped to it). Drops low-confidence and duplicate findings and caps comments per PR. Every dropped claim is kept with its reason. |
| **propose** | For bug, security, error-handling, concurrency and performance findings of medium or high severity, asks for line-range edits. PatchProof builds the patch itself. Edits must stay in range, must not overlap, may only touch files in the PR, and are capped at 40 changed lines. |
| **verify** | Applies the patch to a fresh copy of the PR head and runs the configured checks. The patch is accepted only if no test that passed before now fails, no new lint or type error appears, and no check that ran before now crashes. Comparing against a baseline means a repository with existing failing tests can still get fixes. |

### Sandbox

Two backends run the same checks:

- **local** (default): a temporary copy of the repository, with tokens and API keys stripped from the environment, `HOME` pointed at the copy, CPU-time and memory limits, and a timeout. The copy's `src/` is put first on `PYTHONPATH`, so an editable install of the original never shadows the patched code.
- **docker**: the same copy mounted into a container with `--network none`, `--cap-drop ALL`, `no-new-privileges`, and caps on memory, CPUs and processes.

### Output

- **CLI**: `patchproof review --base main` prints findings and verified patches, and `--json` writes the full result, including every rejected patch and dropped claim.
- **GitHub**: `patchproof pr owner/repo#42 --post` posts one review. A verified fix that sits inside the PR's added lines becomes a one-click GitHub `suggestion`; any other verified fix is attached as a patch. See [`examples/patchproof-review.yml`](examples/patchproof-review.yml) for a workflow.

## Quick start

```bash
pip install -e ".[dev]"

# Any OpenAI-compatible endpoint works: Ollama, vLLM, llama.cpp server, or a hosted API.
ollama pull qwen2.5-coder:7b
export PATCHPROOF_MODEL=qwen2.5-coder:7b              # default
export PATCHPROOF_BASE_URL=http://localhost:11434/v1  # default

cd your-repo
patchproof review --base main             # review the current branch
patchproof review --base main --no-fix    # comments only
patchproof review --diff change.patch     # review a patch file
patchproof review --base main --docker    # verify fixes in Docker
```

Per-repository settings live in `.patchproof.toml` (see [`examples/.patchproof.toml`](examples/.patchproof.toml)). Without one, PatchProof detects pytest, ruff and mypy.

## Benchmark

`src/patchproof/bench` holds a seeded-bug benchmark of **140 pull requests** against a small fixture library (intervals, text utilities, statistics, a shopping cart, binary search, a token-bucket rate limiter and pagination).

- **100 bug PRs.** Each adds a real feature, renames a local variable in some function (a harmless refactor), and carries one seeded bug in a different function. The bugs are 28 arithmetic, 27 off-by-one, 23 comparison, 15 negated conditions, 4 boolean and 3 swapped arguments.
- **40 clean PRs.** Same shape with no bug. These measure false alarms.

Every bug was checked when the dataset was built. The PR's **visible tests**, the ones the agent's sandbox runs, still pass, so CI would not catch it. A separate set of **hidden tests**, which the agent never sees, fails. Hidden tests then judge the results:

| Metric | Meaning |
|---|---|
| Detection | Share of bug PRs with a finding within ±2 lines of the seeded bug |
| Precision | True findings / all findings |
| False comments/PR | Findings that do not point at a seeded bug, per PR |
| Verified fixes | Share of detected bugs that got a fix which passed the sandbox |
| Correct fixes | Share of verified fixes that also pass the hidden tests |

The gap between *verified* and *correct* is the honest limit of this design: a fix can only be verified against the tests the repository has.

```bash
patchproof bench run --model qwen2.5-coder:7b                  # all 140 PRs
patchproof bench run --model qwen2.5-coder:7b --limit 10       # quick look
./scripts/run_model_bench.sh qwen2.5-coder:7b llama3.1:8b      # models x prompt variants
patchproof bench compare results/a/summary.json results/b/summary.json   # exits 1 on regression
patchproof bench generate --bugs 100 --clean 40 --seed 7       # rebuild the dataset
```

`bench compare` is the gate for prompt and model changes. It blocks a candidate whose detection drops by more than 3 points, whose false comments rise by more than 0.1 per PR, or whose correct-fix rate drops by more than 5 points.

### Pipeline results (scripted reviewers, all 140 PRs)

These rows test the machinery, not a model. `oracle-noisy` knows each seeded bug, but on every PR it also invents a finding in a file that is not in the diff and comments on an unchanged line. On every bug PR, its first fix is a broken patch. CI runs it on every push.

| Reviewer | Detection | Precision | False comments/PR | Verified fixes | Correct fixes | Patches rejected | s/PR |
|---|---|---|---|---|---|---|---|
| oracle-noisy | 100% | 100% | 0.0 | 100% | 100% | 100 | 2.3903 |
| null | 0% | 0% | 0.0 | 0% | 0% | 0 | 0.0102 |

Across the 140 PRs, all 280 invented claims were dropped and all 100 broken first patches were rejected. Every retried fix that passed the sandbox also passed the hidden tests. The `null` reviewer is the floor.

### Model results

Run `./scripts/run_model_bench.sh` with the models you have and paste `results/RESULTS.md` here. These numbers depend on the model and the hardware, so none are claimed until they have been run.

## Development

```bash
pip install -e ".[dev]"
pytest -q          # unit and end-to-end tests; Docker tests run when a daemon is available
ruff check src tests
mypy src           # strict
```

```
src/patchproof/
  diff.py        unified-diff parser
  context.py     ast symbol index and context selection
  llm.py         OpenAI-compatible client, scripted client, JSON extraction
  prompts.py     review and fix prompts (basic / checklist)
  findings.py    finding validation, edit parsing, patch building
  sandbox.py     local and Docker runners, check parsers, baseline comparison
  agent.py       LangGraph review agent
  github.py      PR diff, inline comments, suggestions
  cli.py         patchproof review | pr | bench
  bench/         fixture library, mutations, dataset, runner, scoring, gate
```

## Limits

- Verification is only as strong as the repository's tests. A fix can pass every check and still be wrong; the benchmark measures how often that happens.
- Context gathering is Python-aware (`ast`). Other languages get diff-only review with surrounding lines, and the sandbox runs whatever commands you configure.
- The local backend limits resources and strips secrets, but it is not a security boundary. Use `docker` when reviewing untrusted PRs.

## License

MIT
