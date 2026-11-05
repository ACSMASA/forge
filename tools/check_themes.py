"""Check that every code path and anchor cited in forge docs still exists.

Usage (from the forge root): python -I tools/check_themes.py [file.md ...]
Defaults to docs/sw.sword/THEMES.md and .claude/agents/sw-sword-navigator.md.
Repos are resolved as siblings of forge (C:/MASARepo/<repo>). Exit code 1 if anything is missing.

Checked tokens (inside backticks):
  - paths starting with an alias (sim/, int/, DEC/) or a known root (src/, data/, build/, sw.models/, sw.soult/, directai/)
  - `path::text`  -> path exists and contains text (recursively if path is a directory)
  - bare file names (`Foo.cpp`, globs allowed) -> exist under the last directory cited earlier on the same line
"""
import glob
import os
import re
import sys

FORGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASA = os.path.dirname(FORGE)
SWORD = os.path.join(MASA, "sw.sword")

ALIASES = {
    "sim/": "src/libraries/simulation/",
    "int/": "data/app-data/resources/integration/",
    "DEC/": "src/libraries/simulation/Decision/",
}
SWORD_ROOTS = ("src/", "data/", "build/")
OTHER_REPOS = ("sw.models/", "sw.soult/", "directai/")
ALLOW_MISSING = {"int/Reports.lua"}  # generated at build time, gitignored
BARE = re.compile(r"^[A-Za-z0-9_*.]+\.(cpp|h|lua|xml|go|py)$")
TICK = re.compile(r"`([^`\n]+)`")


def resolve(token):
    """Return an absolute path (may contain globs) for a path-like token, else None."""
    for alias, real in ALIASES.items():
        if token.startswith(alias):
            return os.path.join(SWORD, real + token[len(alias):])
    if token.startswith(SWORD_ROOTS):
        return os.path.join(SWORD, token)
    if token.startswith(OTHER_REPOS):
        return os.path.join(MASA, token)
    return None


def matches(path):
    return glob.glob(path) if any(c in path for c in "*?[") else ([path] if os.path.exists(path) else [])


def contains(path, text):
    files = [path] if os.path.isfile(path) else [
        os.path.join(r, f) for r, _, fs in os.walk(path) for f in fs]
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                if text in fh.read():
                    return True
        except OSError:
            pass
    return False


def check(md):
    errors, checked = [], 0
    with open(md, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    for n, line in enumerate(lines, 1):
        last_dir = None
        for token in TICK.findall(line):
            token = token.strip()
            path_part, _, anchor = token.partition("::")
            if token in ALLOW_MISSING:
                continue
            target = resolve(path_part)
            if target is None:
                if last_dir and BARE.match(token):
                    checked += 1
                    if not matches(os.path.join(last_dir, token)):
                        errors.append(f"{md}:{n}: `{token}` not found under {os.path.relpath(last_dir, MASA)}")
                continue
            checked += 1
            found = matches(target)
            if not found:
                errors.append(f"{md}:{n}: missing path `{path_part}`")
                continue
            if os.path.isdir(found[0]):
                last_dir = found[0]
            if anchor and not any(contains(p, anchor) for p in found):
                errors.append(f"{md}:{n}: `{anchor}` not found in `{path_part}`")
    return errors, checked


def main():
    files = sys.argv[1:] or [os.path.join(FORGE, "docs/sw.sword/THEMES.md"),
                             os.path.join(FORGE, ".claude/agents/sw-sword-navigator.md")]
    total_err, total = [], 0
    for md in files:
        errs, n = check(md)
        total_err += errs
        total += n
    for e in total_err:
        print(e)
    print(f"{total} references checked, {len(total_err)} problem(s).")
    sys.exit(1 if total_err else 0)


if __name__ == "__main__":
    main()
