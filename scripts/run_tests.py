"""
Run every backend test script that needs no database account and print one summary.

    venv/Scripts/python.exe scripts/run_tests.py            all of them
    venv/Scripts/python.exe scripts/run_tests.py zone       only files whose name contains "zone"
    venv/Scripts/python.exe scripts/run_tests.py --live     also the ones that use your Supabase (they clean up after themselves)

Each test file is its own program and exits non-zero on a failure; this runs them one after another in a fresh process
(so none can leak state into the next), shows the tail of any that fail, and exits non-zero if any did.
"""
import glob
import os
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TESTS = os.path.join(ROOT, "backend", "tests")
NEEDS_SUPABASE = ("_live", "test_safety_rls")
TIMEOUT_S = 900


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    with_live = "--live" in sys.argv
    files = sorted(glob.glob(os.path.join(TESTS, "test_*.py")))
    if args:
        files = [f for f in files if any(a in os.path.basename(f) for a in args)]
    if not with_live:
        skipped = [f for f in files if any(k in os.path.basename(f) for k in NEEDS_SUPABASE)]
        files = [f for f in files if f not in skipped]
        if skipped:
            print(f"(skipping {len(skipped)} that use Supabase; add --live to include them)")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    results = []
    started = time.time()
    for path in files:
        name = os.path.basename(path)
        t0 = time.time()
        try:
            run = subprocess.run([sys.executable, path], cwd=ROOT, env=env, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
            ok, out = run.returncode == 0, (run.stdout or "") + (run.stderr or "")
        except subprocess.TimeoutExpired:
            ok, out = False, f"timed out after {TIMEOUT_S} s"
        results.append((name, ok, time.time() - t0, out))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  ({time.time() - t0:.0f}s)", flush=True)
    failed = [r for r in results if not r[1]]
    for name, _, _, out in failed:
        print(f"\n--- {name} (last lines) ---")
        print("\n".join(out.strip().splitlines()[-25:]))
    print(f"\n{len(results) - len(failed)} of {len(results)} test files passed in {time.time() - started:.0f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
