"""Build an index of the Lua-visible DirectAI/Spark runtime API and of the Spark validation diagnostics.

Usage: python -I build_dai_index.py <directai root> <sw.sword root> <sw.models root> <sw.soult root> <out.md>

Sections:
  1. spark.* API (functions and aliases defined by the spark plugin)
  2. compatibility aliases sw.ai.* and masalife.brain.* -> spark.* target
  3. shared globals (Lua setsharedglobal, C++-registered, skill-script definers)
  4. validation diagnostics: code -> message template -> where raised (level)
Usage counts are approximate token counts in Lua/.bms files (comments stripped, strings not).
"""
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

dai, sword, models, soult, out = sys.argv[1:6]
SPARK = os.path.join(dai, "src", "libraries", "plugins", "spark")
SPARKLIB = os.path.join(dai, "src", "libraries", "spark")
CORE = os.path.join(dai, "src", "libraries", "directai")
SPARKCHECK = os.path.join(dai, "src", "applications", "sparkcheck")


def rel(p):
    return os.path.relpath(p, dai).replace("\\", "/")


def read(p):
    with open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def files(base, exts):
    for root, _, fs in os.walk(base):
        for f in fs:
            if f.endswith(exts):
                yield os.path.join(root, f)


def line_of(txt, pos):
    return txt.count("\n", 0, pos) + 1


def git(repo, *args):
    try:
        return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "?"


# ---------------------------------------------------------------- 1. spark.* API
api = {}  # name -> (relpath, line, alias_of or "")
DEF_FN = re.compile(r"^[ \t]*function[ \t]+(spark(?:\.[A-Za-z_]\w*)+)[ \t]*\(", re.M)
DEF_AS = re.compile(r"^[ \t]*(spark(?:\.[A-Za-z_]\w*)+)[ \t]*=[ \t]*([^\n]+)", re.M)
for p in sorted(files(SPARK, (".lua",))):
    txt = read(p)
    for m in DEF_FN.finditer(txt):
        if ".internal." not in m.group(1):
            api.setdefault(m.group(1), (rel(p), line_of(txt, m.start()), ""))
    for m in DEF_AS.finditer(txt):
        name, rhs = m.group(1), m.group(2).strip().rstrip(";")
        if ".internal." in name or name in api:
            continue
        alias = rhs if re.fullmatch(r"[\w.]+", rhs) and rhs != "{}" else ""
        api[name] = (rel(p), line_of(txt, m.start()), alias)

# ---------------------------------------------------------------- 2. compatibility aliases
aliases = {}  # alias name -> (relpath, line, target)
compat = os.path.join(SPARK, "compatibility.lua")
ctxt = read(compat)
depth, keys = 0, []  # keys: [(depth at which the key's table is open, key)]
for n, raw in enumerate(ctxt.splitlines(), 1):
    line = raw.split("--", 1)[0].rstrip()
    m = (re.match(r'^\s*setsharedglobal\(\s*"(\w+)"\s*,\s*\{', line)
         or re.match(r"^\s*(sw\.ai)\s*=\s*setmetatable\(\s*\{", line)
         or (keys and re.match(r"^\s*(\w+)\s*=\s*\{\s*$", line)))
    if m:
        keys.append((depth + 1, m.group(1)))
    elif keys:
        a = re.match(r"^\s*(\w+)\s*=\s*([\w.]+)\s*,?\s*$", line)
        if a and keys[-1][0] == depth:
            aliases[".".join(k for _, k in keys) + "." + a.group(1)] = (rel(compat), n, a.group(2))
    depth += line.count("{") - line.count("}")
    while keys and keys[-1][0] > depth:
        keys.pop()

# ---------------------------------------------------------------- 3. shared globals
glob = defaultdict(list)  # name -> [(relpath, line, how)]
SSG = re.compile(r'setsharedglobal\(\s*"(\w+)"\s*,')
for base in (SPARKLIB, CORE, os.path.join(dai, "src", "libraries", "plugins")):
    for p in sorted(files(base, (".lua",))):
        txt = read(p)
        for m in SSG.finditer(txt):
            glob[m.group(1)].append((rel(p), line_of(txt, m.start()), "Lua setsharedglobal"))
CPP = re.compile(r'lua_pushliteral\s*\(\s*[\w\->]+\s*,\s*"(\w+)"\s*\)\s*;\s*lua_pushcfunction', re.S)
for p in sorted(files(CORE, (".cpp",))):
    txt = read(p)
    for m in CPP.finditer(txt):
        if not m.group(1).startswith("__"):  # metamethods, not globals
            glob[m.group(1)].append((rel(p), line_of(txt, m.start()), "C++"))
DEFINER = re.compile(r'definer\(\s*"(\w+)"\s*,')
for p in sorted(files(os.path.join(SPARKLIB, "validation"), (".lua",))):
    txt = read(p)
    for m in DEFINER.finditer(txt):
        glob[m.group(1)].append((rel(p), line_of(txt, m.start()), "script env (definer)"))

# ---------------------------------------------------------------- usage counts
CHAIN = re.compile(r"(?<![\w.:])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)")


def scan(bases):
    c = Counter()
    for base in bases:
        if not os.path.isdir(base):
            continue
        for p in files(base, (".lua", ".bms")):
            try:
                txt = read(p)
            except OSError:
                continue
            txt = re.sub(r"--\[(=*)\[.*?\]\1\]", "", txt, flags=re.S)
            txt = re.sub(r"--[^\n]*", "", txt)
            for m in CHAIN.finditer(txt):
                parts = m.group(1).split(".")
                for i in range(1, len(parts) + 1):
                    c[".".join(parts[:i])] += 1
    return c


res = os.path.join(sword, "data", "app-data", "resources")
cnt = {
    "Int": scan([res]),
    "Models": scan([os.path.join(models, "data", "data", "models")]),
    "Soult": scan([os.path.join(soult, "data", "data", "models")]),
}


def counts(name):
    return " | ".join(str(cnt[k][name] or "") for k in ("Int", "Models", "Soult"))


# ---------------------------------------------------------------- 4. diagnostics
msgfile = os.path.join(SPARKLIB, "validation", "messages.lua")
mtxt = read(msgfile)
messages = {}
for m in re.finditer(r'\[\s*"([\w.-]+)"\s*\]\s*=\s*"((?:[^"\\]|\\.)*)"', mtxt):
    messages[m.group(1)] = (m.group(2), line_of(mtxt, m.start()))
RAISE = re.compile(r'[:.](error|warning|strictError)\s*\(\s*(?:\w+\s*,\s*)?"([\w.-]+)"')
raised = defaultdict(list)  # code -> [(level, rel:line)]
for base in (SPARKLIB, SPARK, SPARKCHECK):
    for p in sorted(files(base, (".lua",))):
        if p == msgfile:
            continue
        txt = read(p)
        for m in RAISE.finditer(txt):
            if m.group(2) in messages:
                raised[m.group(2)].append((m.group(1), f"{rel(p)}:{line_of(txt, m.start())}"))

# ---------------------------------------------------------------- output
branch = git(dai, "branch", "--show-current")
head = git(dai, "rev-parse", "--short", "HEAD")
bx = read(os.path.join(dai, "build", "build.xml"))
ver = ".".join(re.search(rf'name="dai\.version\.{k}" value="(\d+)"', bx).group(1) for k in ("major", "minor"))


def link(r, line):
    return f"[{r}:{line}](../../../directai/{r}#L{line})"


L = []
L.append("# DirectAI / Spark: Lua API and diagnostics index (generated)\n\n")
L.append(f"Generated by `forge/tools/build_dai_index.py` from directai `{branch}` @ `{head}` (DAI {ver}). "
         "Regenerate after pulling; line numbers drift. **Grep this file, never read it whole.**\n\n")
L.append("Usage columns are approximate token counts in Lua/.bms (comments stripped): **Int** = sw.sword "
         "`data/app-data/resources` (Integration.lua + integration/); **Models** = sw.models `data/data/models`; "
         "**Soult** = sw.soult `data/data/models`. Counts are for the branch checked out in each repo.\n")

L.append(f"\n## 1. `spark.*` API ({len(api)} names)\n\nDefined in `src/libraries/plugins/spark/*.lua`. "
         "\"Alias of\" = defined as `spark.X = Y`.\n\n| Name | Defined at | Alias of | Int | Models | Soult |\n|---|---|---|---|---|---|\n")
for name in sorted(api):
    r, ln, al = api[name]
    L.append(f"| `{name}` | {link(r, ln)} | {('`' + al + '`') if al else ''} | {counts(name)} |\n")

L.append(f"\n## 2. Compatibility aliases ({len(aliases)} names)\n\n`sw.ai.*` and `masalife.brain.*` are tables of "
         "references to `spark.*` functions built once in `compatibility.lua`; they are not separate implementations. "
         "Check the target: some names differ (e.g. `stopTasks` -> `spark.stopAll`).\n\n"
         "| Alias | Defined at | Target | Int | Models | Soult |\n|---|---|---|---|---|---|\n")
for name in sorted(aliases):
    r, ln, tgt = aliases[name]
    L.append(f"| `{name}` | {link(r, ln)} | `{tgt}` | {counts(name)} |\n")

L.append(f"\n## 3. Shared globals ({len(glob)} names)\n\nGlobals visible to every brain of a VM. \"C++\" names are "
         "registered by the core VM (some are removed after initialization); a name with several sites is defined more than once (e.g. `Activate`: C++, then re-registered by spark `skill.lua` "
         "as a wrapper that adds default parameters); \"definer\" names exist only while a skill "
         "or class Lua script is loaded/validated. Counts for short words (`node`, `brain`, `skill`) are noisy.\n\n"
         "| Name | Defined at | How | Int | Models | Soult |\n|---|---|---|---|---|---|\n")
for name in sorted(glob, key=str.lower):
    defs = glob[name]
    where = "<br>".join(link(r, ln) for r, ln, _ in defs)
    how = "<br>".join(sorted({h for _, _, h in defs}))
    L.append(f"| `{name}` | {where} | {how} | {counts(name)} |\n")

L.append(f"\n## 4. Validation diagnostics ({len(messages)} codes)\n\nTemplates from `src/libraries/spark/validation/messages.lua` "
         "(`%1`, `%2`… are arguments). Reported codes carry a `validator.` prefix. Level: `error`, `warning`, or "
         "`strictError` (error in strict mode, warning otherwise; sparkcheck is always strict). "
         "To map a printed message back to its code, grep a fixed fragment of it here.\n\n"
         "| Code | Message | Raised at (level) |\n|---|---|---|\n")
for code in sorted(messages):
    msg, _ = messages[code]
    sites = "<br>".join(f"{loc} ({lvl})" for lvl, loc in raised.get(code, [])) or "(not raised by a literal call; grep the code)"
    msg = msg.replace("|", "\\|")
    L.append(f"| `{code}` | {msg} | {sites} |\n")

os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", encoding="utf-8") as fh:
    fh.write("".join(L))
print(f"{len(api)} spark names, {len(aliases)} aliases, {len(glob)} globals, {len(messages)} diagnostics "
      f"({sum(1 for c in messages if c not in raised)} without literal raise site)")
