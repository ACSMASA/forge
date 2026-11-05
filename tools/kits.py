"""Show which navigator indexes are stale, and optionally rebuild them.

Usage (from anywhere): python -I tools/kits.py [--build | --build-all]
  (no option)   table: index, built from (branch @ commit per repo, from its header), current HEAD, local edits, status
  --build       regenerate the stale indexes, then run tools/check_themes.py
  --build-all   regenerate every index, then run the checker

An index is STALE when its main repo's HEAD differs from the commit in its header (or the header has none), and
"partly stale" when only a secondary repo moved (e.g. sw.sword for the ontology/action indexes, sw.models for usage
counts). Local edits = uncommitted changes in the dirs an index reads: the index does not see them either way.
Never commits: commit regenerated indexes only when built from your reference branch (see the kit READMEs).
"""
import os
import re
import subprocess
import sys

FORGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASA = os.path.dirname(FORGE)
PY = [sys.executable, "-I"]
SCOPES = {  # dirs whose uncommitted edits an index cannot see
    "sw.sword": ["src/libraries/simulation", "data/app-data/resources"],
    "sw.models": ["data/data/models"],
    "sw.soult": ["data/data/models"],
    "directai": ["src", "data/sparkle"],
}
# index file, repos in the order their `branch` @ `commit` pairs appear in the header, build group
INDEXES = [
    ("docs/sw.sword/dec_function_index.md", ["sw.sword", "sw.models", "sw.soult"], "dec"),
    ("docs/sw.sword/integration_index.md", ["sw.sword", "sw.models", "sw.soult"], "integration"),
    ("docs/directai/dai_lua_index.md", ["directai"], "dai"),
    ("docs/sw.models/decisional_index.md", ["sw.models"], "models"),
    ("docs/sw.models/ontology_index.md", ["sw.models", "sw.sword"], "models"),
    ("docs/sw.models/action_index.md", ["sw.models", "sw.sword"], "models"),
    ("docs/sw.models/mission_index.md", ["sw.models", "sw.sword"], "models"),
]
PAIR = re.compile(r"`([^`\s]+)`\**\s*@\s*`([0-9a-f]{7,})`")
MODEL = re.compile(r"model `([^`]+)`")


def repo(name):
    return os.path.join(MASA, name).replace("\\", "/")


def git(name, *args):
    try:
        return subprocess.run(["git", "-C", repo(name), *args], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def build_cmd(group, model):
    out = lambda f: os.path.join(FORGE, f)
    t = lambda f: os.path.join(FORGE, "tools", f)
    if group == "dec":
        return PY + [t("build_dec_index.py"), repo("sw.sword"), repo("sw.models"), repo("sw.soult"),
                     out("docs/sw.sword/dec_function_index.md")]
    if group == "integration":
        return PY + [t("build_integration_index.py"), repo("sw.sword"), repo("sw.models"), repo("sw.soult"),
                     out("docs/sw.sword/integration_index.md")]
    if group == "dai":
        return PY + [t("build_dai_index.py"), repo("directai"), repo("sw.sword"), repo("sw.models"), repo("sw.soult"),
                     out("docs/directai/dai_lua_index.md")]
    return PY + [t("build_models_index.py"), repo("sw.models"), out("docs/sw.models"), "--model", model,
                 "--sword", repo("sw.sword")]


def status():
    heads = {r: (git(r, "branch", "--show-current") or "(detached)", git(r, "rev-parse", "HEAD"))
             for r in SCOPES}
    dirty = {r: len([l for l in git(r, "status", "--porcelain", "--", *SCOPES[r]).splitlines() if l.strip()])
             for r in SCOPES}
    rows = []
    for f, repos, group in INDEXES:
        path = os.path.join(FORGE, f)
        if not os.path.exists(path):
            rows.append((f, repos, group, "missing", [], "ada"))
            continue
        with open(path, encoding="utf-8") as fh:
            head = "".join(fh.readline() for _ in range(8))
        pairs = PAIR.findall(head)
        model = (MODEL.search(head) or [None, "ada"])[1]
        built = list(zip(repos, pairs))
        if not pairs:
            st = "STALE (no commit in header)"
        elif not heads[repos[0]][1].startswith(pairs[0][1]):
            st = "STALE"
        else:
            moved = [r for r, (_, c) in built[1:] if not heads[r][1].startswith(c)]
            st = f"partly stale ({', '.join(moved)} moved)" if moved else "fresh"
        rows.append((f, repos, group, st, built, model))
    return rows, heads, dirty


def show(rows, heads, dirty):
    print(f"{'index':24} {'status':30} built from")
    for f, repos, _, st, built, _ in rows:
        b = "; ".join(f"{r} {br}@{c[:10]}" for r, (br, c) in built) or "-"
        print(f"{os.path.basename(f):24} {st:30} {b}")
    print("\nnow: " + "; ".join(f"{r} {br}@{c[:10]}" + (f" ({dirty[r]} uncommitted)" if dirty[r] else "")
                               for r, (br, c) in heads.items()))


def main():
    args = sys.argv[1:]
    if args and args[0] not in ("--build", "--build-all"):
        sys.exit(__doc__)
    rows, heads, dirty = status()
    show(rows, heads, dirty)
    if not args:
        sys.exit(0 if all(r[3] == "fresh" for r in rows) else 1)
    groups, mains = {}, set()
    for f, repos, group, st, built, model in rows:
        if args[0] == "--build-all" or st != "fresh":
            groups.setdefault(group, model)
            mains.add(repos[0])
    if not groups:
        print("\nall fresh: nothing to build")
    for group, model in groups.items():
        cmd = build_cmd(group, model)
        print(f"\n$ {' '.join(os.path.relpath(c, FORGE) if c.startswith(FORGE) else c for c in cmd[2:])}", flush=True)
        res = subprocess.run(cmd)
        if res.returncode:
            sys.exit(f"build {group} failed ({res.returncode})")
    branches = {r: heads[r][0] for r in mains if heads[r][0] != "master"}
    if branches:
        print("\nnote: built from non-master branches " + ", ".join(f"{r} {b}" for r, b in branches.items()) +
              ": commit these indexes only if that is your reference branch.")
    print(flush=True)
    sys.exit(subprocess.run(PY + [os.path.join(FORGE, "tools", "check_themes.py")]).returncode)


if __name__ == "__main__":
    main()
