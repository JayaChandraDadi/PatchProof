import subprocess
import sys
import textwrap

from patchproof.bench.dataset import DATA, build, load
from patchproof.bench.mutations import apply_rename, find_mutations, find_renames
from patchproof.bench.runner import gate, markdown_table, null_client, oracle_client, run, summarize

SRC = textwrap.dedent('''\
    def f(items, k):
        result = []
        for i in range(len(items) - 1):
            if items[i] < k and i > 0:
                result.append(max(items[i], k) + 1)
        return result
    ''')


def test_mutations_apply_exactly_one_edit():
    muts = find_mutations(SRC)
    ops = {m.operator for m in muts}
    assert {"comparison", "arithmetic", "boolean", "negated-condition", "off-by-one",
            "swapped-arguments"} <= ops
    for m in muts:
        out = m.apply(SRC)
        diff = [(a, b) for a, b in zip(SRC.splitlines(), out.splitlines(), strict=True) if a != b]
        assert len(diff) == 1 and diff[0][0].count(m.original) >= 1
        compile(out, "x.py", "exec")


def test_rename_preserves_behaviour():
    (fn, old, new), *_ = find_renames(SRC)
    out = apply_rename(SRC, fn, old, new)
    assert old == "result" and "out = []" in out and "result" not in out
    ns1, ns2 = {}, {}
    exec(SRC, ns1)
    exec(out, ns2)
    assert ns1["f"]([1, 5, 2, 9], 6) == ns2["f"]([1, 5, 2, 9], 6)


def test_shipped_dataset_shape():
    prs = load(DATA)
    bugs = [p for p in prs if p.kind == "bug"]
    assert len(bugs) == 100 and len(prs) - len(bugs) == 40
    assert len({p.id for p in prs}) == len(prs)
    for p in bugs:
        assert p.bug is not None
        head_line = p.head_source.splitlines()[p.bug.line - 1]
        assert head_line != p.bug.correct_line
        assert f"+{head_line}" in p.diff.splitlines()


def test_generate_small_dataset(tmp_path):
    prs = build(n_bug=2, n_clean=1, seed=3, out=tmp_path / "prs.jsonl", log=False)
    assert [p.kind for p in prs] == ["bug", "bug", "clean"]
    assert len(load(tmp_path / "prs.jsonl")) == 3


def test_oracle_and_null_scores():
    prs = [p for p in load(DATA) if p.id in ("bug-001", "bug-002", "clean-001")]
    oracle = summarize(run(prs, lambda p: oracle_client(p, noisy=True)), "oracle-noisy")
    assert oracle["detection_rate"] == 1.0 and oracle["precision"] == 1.0
    assert oracle["correct_fix_rate"] == 1.0 and oracle["rejected_patches"] == 2
    assert oracle["dropped_model_claims"] == 6
    null = summarize(run(prs, null_client, fix=False), "null")
    assert null["detection_rate"] == 0.0 and null["false_comments_per_pr"] == 0.0
    table = markdown_table([oracle, null])
    assert "| oracle-noisy | 100% | 100% |" in table


def test_gate():
    base = {"detection_rate": 0.7, "false_comments_per_pr": 0.3, "correct_fix_rate": 0.8}
    assert gate(base, dict(base)) == []
    worse = {"detection_rate": 0.6, "false_comments_per_pr": 0.5, "correct_fix_rate": 0.7}
    assert len(gate(base, worse)) == 3


def test_cli_bench_and_compare(tmp_path):
    out = tmp_path / "res"
    cmd = [sys.executable, "-m", "patchproof.cli", "bench", "run", "--reviewer", "oracle",
           "--ids", "bug-003,clean-002", "--out", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "bug-003" in proc.stdout and "caught" in proc.stdout
    summary = out / "oracle" / "summary.json"
    ok = subprocess.run([sys.executable, "-m", "patchproof.cli", "bench", "compare",
                         str(summary), str(summary)], capture_output=True, text=True)
    assert ok.returncode == 0 and "OK" in ok.stdout
