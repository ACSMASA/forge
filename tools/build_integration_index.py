"""Build an index of the sw.sword Lua integration layer: every `integration.*` name it defines.

Usage (from the forge root):
    python -I tools/build_integration_index.py <sw.sword root> <sw.models root> <sw.soult root> <out.md>

For each name: definition site(s), kind, LDoc summary, brain functions called, kernel action starters reached (same
closure as action_index.md section 4), other integration.* it calls, uses inside the layer, callers in sw.models and
sw.soult Lua, model-side overrides, references from kernel C++. Then the model-side definitions and an anomaly report.
Reads the branches currently checked out; the header records them.
"""
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir
sys.dont_write_bytecode = True  # no tools/__pycache__
import build_models_index as bmi  # noqa: E402

DEF = re.compile(r"^([ \t]*)(?:function\s+(integration\.[\w.]+)\s*\(|(integration\.[\w.]+)\s*=(?!=)\s*"
                 r"(function\b|spark\.startStopAction\b|\{|[\w.]+\s+or\s+\{|[A-Za-z_][\w.]*[ \t]*$)?)", re.M)
CACHED = re.compile(r"^[ \t]*local\s+(\w+)\s*=\s*integration\.([\w.]+)[ \t]*$", re.M)
IDENT = re.compile(r"(?<![.:\w])([A-Za-z_]\w*)\b")
REG = re.compile(r'\bRegister(Function|Method)\s*(?:<[^;"]*?>)?\s*\(\s*"(\w+)"')
CPP_INTEG = re.compile(r"\bintegration\.([A-Za-z_][\w.]*)")
USERS_SHOWN = 5
CALLS_SHOWN = 8
DEFS_SHOWN = 3


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def ldoc_summary(lines, def_line):
    """First sentence of the LDoc block (`--- text`, then `--` lines) just above line def_line (1-based)."""
    i = def_line - 2
    block = []
    while i >= 0 and lines[i].lstrip().startswith("--"):
        block.append(lines[i].strip())
        i -= 1
    block.reverse()
    for k, l in enumerate(block):
        if l.startswith("---") and not l.startswith("----"):
            text = [l[3:].strip()]
            for nxt in block[k + 1:]:
                t = nxt.lstrip("-").strip()
                if not t or t.startswith("@"):
                    break
                text.append(t)
            s = " ".join(t for t in text if t)
            if s and not s.startswith("@"):
                return bmi.first_sentence(s)
    return ""


def lua_files(base):
    for r, _, fs in os.walk(base):
        for f in sorted(fs):
            if f.endswith((".lua", ".bms")):
                yield os.path.join(r, f)


def function_body(t, m):
    """Body text of a `function` definition matched at m (indentation-aware), else the rest of its line."""
    eol = t.find("\n", m.end())
    eol = len(t) if eol < 0 else eol
    if re.search(r"\bend\s*[,)]?\s*$", t[m.end():eol]):
        return t[m.end():eol]
    e = re.compile(r"^" + re.escape(m.group(1)) + r"end\b", re.M).search(t, m.end())
    return t[m.end():e.end() if e else len(t)]


def scan_defs(path, label):
    """integration.* definitions in one Lua file: [(name, label, line, kind, body, summary)]."""
    raw = bmi.read(path)
    t = bmi.blank_lua(raw, keep_lines=True)
    lines = raw.splitlines()
    out = []
    for m in DEF.finditer(t):
        name = m.group(2) or m.group(3)
        rhs = (m.group(4) or "").strip()
        if m.group(2) or rhs == "function":
            kind, body = "function", function_body(t, m)
        elif rhs.startswith("spark.startStopAction"):
            i = t.find("(", m.start(4))
            kind, body = "startStopAction", t[m.end():bmi.match_close(t, i) + 1]
        elif rhs.startswith("{") or rhs.endswith("{"):
            kind, body = "table", ""
        elif rhs:
            kind, body = "alias", rhs
        else:
            eol = t.find("\n", m.end())
            kind, body = "value", t[m.end():len(t) if eol < 0 else eol].strip()
        ln = line_of(t, m.start())
        out.append((name[len("integration."):], label, ln, kind, body, ldoc_summary(lines, ln)))
    return out


def constructor_keys(path, label):
    """Top-level keys of an `integration = { key = … }` or `dai.share( "integration", { key = … } )` constructor
    (e.g. `ontology` in Integration.lua)."""
    raw = bmi.read(path)
    t = bmi.blank_lua(raw, keep_lines=True)
    lines = raw.splitlines()
    m = next((m for m in re.finditer(r'^(?:integration\s*=\s*|dai\.share\(\s*""\s*,\s*)\{', t, re.M)
              if re.search(r'^\s*(integration\s*=|dai\.share\(\s*"integration")', lines[line_of(t, m.start()) - 1])),
             None)
    if not m:
        return []
    start, end = m.end() - 1, bmi.match_close(t, m.end() - 1, "{", "}")
    out, depth, i = [], 0, start
    while i < end:
        c = t[i]
        if c in "{(":
            depth += 1
        elif c in "})":
            depth -= 1
        elif depth == 1 and (i == 0 or t[i - 1] == "\n"):
            k = re.match(r"[ \t]*([A-Za-z_]\w*)\s*=(?!=)", t[i:])
            if k:
                ln = line_of(t, i)
                out.append((k.group(1), label, ln, "constructor key", "", ldoc_summary(lines, ln)))
        i += 1
    return out


def model_label(models_dir, path):
    """dia5 entity name for files under <model>/decisional/dia5/ (prefixed by the model unless ada), else the path."""
    rel = os.path.relpath(path, models_dir).replace("\\", "/")
    parts = rel.split("/")
    if len(parts) > 3 and parts[1:3] == ["decisional", "dia5"]:
        inner = parts[3:]
        name = ".".join(inner)[:-4] if len(inner) > 1 else inner[0]
        return name if parts[0] == "ada" else f"{parts[0]}:{name}"
    return rel


def main():
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    sword, models, soult, out = sys.argv[1:5]
    res = os.path.join(sword, "data", "app-data", "resources")
    idir = os.path.join(res, "integration")
    sim = os.path.join(sword, "src", "libraries", "simulation")
    if not os.path.isdir(idir):
        sys.exit(f"{idir}: not found")

    # --- layer definitions (every definition, with lines)
    layer_files = [os.path.join(res, "Integration.lua")] + [os.path.join(idir, f) for f in sorted(os.listdir(idir))
                                                             if f.endswith(".lua")]
    defs = defaultdict(list)  # name -> [(label, line, kind, body, summary)]
    layer_text = {}
    for p in layer_files:
        if not os.path.isfile(p):
            continue
        label = os.path.relpath(p, res).replace("\\", "/")
        layer_text[label] = bmi.blank_lua(bmi.read(p), keep_lines=True)
        for name, lab, ln, kind, body, summ in constructor_keys(p, label) + scan_defs(p, label):
            defs[name].append((lab, ln, kind, body, summ))

    def resolve(ref):
        parts = ref.split(".")
        while parts:
            n = ".".join(parts)
            if n in defs:
                return n
            parts.pop()
        return None

    # --- kernel side: registered brain functions, starters, closure, C++ references to integration.*
    registered = set()
    cpp_refs = Counter()
    for r, _, fs in os.walk(sim):
        for f in fs:
            if f.endswith((".cpp", ".h")):
                txt = bmi.read(os.path.join(r, f))
                registered |= {m.group(2) for m in REG.finditer(txt) if m.group(1) == "Function"}
                for ref in CPP_INTEG.findall(txt):
                    n = resolve(ref)
                    if n:
                        cpp_refs[n] += 1
    starters, _, _, _, _, _, starters_of = bmi.integration_closure(sword)

    def reached(name, label, ln, body):
        return starters_of(("x", name, label, ln), body, label)

    # --- uses inside the layer, cached locals
    layer_uses = Counter()
    cached = defaultdict(list)  # name -> ["repo:file:line"]
    for label, t in layer_text.items():
        for ref in bmi.MODEL_INTEG.findall(t):
            n = resolve(ref)
            if n:
                layer_uses[n] += 1
        for m in CACHED.finditer(t):
            n = resolve(m.group(2))
            if n:
                cached[n].append(f"sword:{label}:{line_of(t, m.start())}")
    for n, ds in defs.items():
        layer_uses[n] -= sum(1 for d in ds if d[2] != "constructor key")  # definition sites matched the pattern too

    # --- model repos: callers, model-side definitions, cached locals
    users = {"models": defaultdict(set), "soult": defaultdict(set)}
    model_defs = []  # (repo, label, line, name, override?)
    undefined = defaultdict(set)  # name -> users (sw.models)
    stamps = {}
    for key, repo in (("models", models), ("soult", soult)):
        mdir = os.path.join(repo, "data", "data", "models")
        if not os.path.isdir(mdir):
            stamps[key] = None
            continue
        stamps[key] = bmi.repo_stamp(repo)
        local_defs = set()
        files = list(lua_files(mdir))
        texts = {}
        for p in files:
            t = bmi.blank_lua(bmi.read(p), keep_lines=True)
            label = model_label(mdir, p)
            texts[label] = t
            for m in DEF.finditer(t):
                name = (m.group(2) or m.group(3))[len("integration."):]
                local_defs.add(name)
                model_defs.append((key, label, line_of(t, m.start()), name, resolve(name) == name))
            for m in CACHED.finditer(t):
                n = resolve(m.group(2))
                if n:
                    cached[n].append(f"{key}:{label}:{line_of(t, m.start())}")
        for label, t in texts.items():
            for ref in set(bmi.MODEL_INTEG.findall(t)):
                n = resolve(ref)
                if n:
                    users[key][n].add(label)
                elif key == "models" and not any(ref == d or ref.startswith(d + ".") for d in local_defs):
                    undefined[ref].add(label)
    overrides = {}
    for key, label, ln, name, is_override in model_defs:
        if is_override:
            overrides.setdefault(name, []).append(f"{key}:{label}:{ln}")

    # --- output
    sb, sh, sd = bmi.repo_stamp(sword)
    w_lines = []
    w = w_lines.append
    w("# sw.sword integration layer index (generated)\n")
    stamp_txt = "; ".join(f"{lab} `{s[0]}` @ `{s[1]}`" for lab, s in (("sw.models", stamps["models"]),
                                                                       ("sw.soult", stamps["soult"])) if s)
    w(f"Generated by `forge/tools/build_integration_index.py` from sw.sword **`{sb}` @ `{sh}`** ({sd}). "
      f"Callers read from {stamp_txt or 'no model repo'}.\n")
    w("**Stale check:** if `git -C C:/MASARepo/sw.sword rev-parse --short=10 HEAD` differs from the commit above, "
      "treat this file as a hint only and confirm with grep (line numbers drift first). "
      "**Grep this file, never read it whole.**\n")
    w("Scope: `data/app-data/resources/Integration.lua` + `integration/*.lua`. One row per `integration.X` name, "
      "grep `^| .integration.X. ` (the dots stand for the backticks); search words in the Summary column to find "
      "an existing function. **Kind**: function, alias (= another name), startStopAction, table, value, constructor "
      "key (a key of the `integration` table built in `Integration.lua`, e.g. `ontology`: "
      "`integration.ontology.types.<key>` is the current role's type map, see `docs/sw.models/ontology_index.md` "
      "section 6). "
      "**Summary**: first sentence of the LDoc `---` block above the definition (empty = undocumented). "
      "**Brain calls**: `DEC_*` tokens and registered brain functions in the body (direct only). "
      "**Kernel starters**: brain functions registered with `StartAction<` reached through the body, Lua helpers and "
      "other `integration.*` (same closure as `docs/sw.models/action_index.md` section 4). "
      "**Layer / C++**: references inside the layer (definitions excluded) / in kernel C++ (`src/libraries/simulation`). "
      "**sw.models users**: distinct files (dia5 entity names; `model:` prefix outside ada), first "
      f"{USERS_SHOWN} shown; **Soult**: distinct sw.soult files. **Overridden by**: model Lua assigning the same name "
      "(models load after the layer, so their definition wins, except where a load-time local cached the old one: "
      "section 3.2). Lua references are textual: names built at runtime are invisible.\n")

    n_defs = sum(len(v) for v in defs.values())
    kinds = Counter(v[0][2] for v in defs.values())
    w("## Counts\n")
    w("| Item | Count |\n|---|---|")
    w(f"| `integration.*` names defined in the layer | {len(defs)} |")
    w(f"| definitions (a name may be defined several times) | {n_defs} |")
    for k in ("function", "alias", "startStopAction", "table", "value", "constructor key"):
        w(f"| kind {k} | {kinds.get(k, 0)} |")
    w(f"| with an LDoc summary | {sum(1 for v in defs.values() if any(d[4] for d in v))} |")
    w(f"| reaching a kernel action starter | "
      f"{sum(1 for n, v in defs.items() if any(reached(n, d[0], d[1], d[3]) for d in v))} |")
    w(f"| used by sw.models Lua | {len(users['models'])} |")
    w(f"| overridden by model Lua (sw.models / sw.soult) | "
      f"{len({n for n, _ in ((x[3], 0) for x in model_defs if x[4] and x[0] == 'models')})} / "
      f"{len({x[3] for x in model_defs if x[4] and x[0] == 'soult'})} |")
    never = sorted(n for n in defs if layer_uses[n] <= 0 and not users["models"][n] and not users["soult"][n]
                   and not cpp_refs[n])
    w(f"| never referenced (layer, C++, sw.models, sw.soult) | {len(never)} |")
    w(f"| kernel action starters known (sw.sword) | {len(starters)} |\n")

    w("## 1. Integration functions\n")
    w("| Name | Defined at | Kind | Summary | Brain calls | Kernel starters | Calls integration.* | Layer / C++ "
      "| sw.models users | Soult | Overridden by |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    for name in sorted(defs, key=str.lower):
        ds = defs[name]
        lab, ln, kind, body, summ = ds[0]
        where = ", ".join(f"[{d[0].split('/')[-1]}:{d[1]}](../../../sw.sword/data/app-data/resources/{d[0]}#L{d[1]})"
                          for d in ds[:DEFS_SHOWN]) + (f" … +{len(ds) - DEFS_SHOWN}" if len(ds) > DEFS_SHOWN else "")
        if kind == "alias":
            kind = f"alias = `{body}`"
        summary = next((d[4] for d in ds if d[4]), "")
        bodies = " ".join(d[3] for d in ds)
        brain = {t for t in bmi.KERNEL_TOKEN.findall(bodies)} | {t for t in IDENT.findall(bodies) if t in registered}
        st = set().union(*(reached(name, d[0], d[1], d[3]) for d in ds))
        calls = {resolve(r) or r for r in bmi.MODEL_INTEG.findall(bodies)} - {name}
        mu = sorted(users["models"][name])
        mu_txt = f"{len(mu)}: " + bmi.code(mu, USERS_SHOWN) if mu else ""
        so = len(users["soult"][name]) or ""
        lc = f"{max(layer_uses[name], 0)} / {cpp_refs[name]}" if cpp_refs[name] else str(max(layer_uses[name], 0))
        w(f"| `integration.{name}` | {where} | {kind} | {summary} | {bmi.code(brain, CALLS_SHOWN)} | "
          f"{bmi.code(st, CALLS_SHOWN)} | {bmi.code(('integration.' + c for c in calls), CALLS_SHOWN)} | {lc} | "
          f"{mu_txt} | {so} | {', '.join(overrides.get(name, []))} |")

    w("\n## 2. Model-side definitions of `integration.*`\n")
    w("Assignments to `integration.X` in model Lua. **Override** = the layer defines the same name; "
      "**model-only** = the layer does not (the model extends the namespace).\n")
    w("| Repo | Name | File:line | Override? |\n|---|---|---|---|")
    for key, label, ln, name, is_override in sorted(model_defs, key=lambda x: (x[0], x[3].lower(), x[1])):
        w(f"| {'sw.models' if key == 'models' else 'sw.soult'} | `integration.{name}` | `{label}`:{ln} | "
          f"{'override' if is_override else 'model-only'} |")

    w("\n## 3. Anomalies\n")
    multi = {n: ds for n, ds in defs.items() if sum(1 for d in ds if d[2] in ("function", "alias", "startStopAction")) > 1}
    w(f"### 3.1 Defined more than once in the layer ({len(multi)})\n")
    w("Names with two or more function / alias / startStopAction definitions (plain value assignments are not "
      "counted). The definition that runs last wins (load order: `Integration.lua` includes; a redefinition inside a "
      "function applies only once that function runs).\n")
    w("| Name | Definitions |\n|---|---|")
    for n in sorted(multi, key=str.lower):
        w(f"| `integration.{n}` | " + ", ".join(f"`{d[0]}`:{d[1]} ({d[2]})" for d in multi[n]) + " |")
    risky = {n: cached[n] for n in overrides if cached.get(n)}
    w(f"\n### 3.2 Overridden by a model and cached as a load-time local ({len(risky)})\n")
    w("`local f = integration.f` keeps the value it had when that file loaded: a layer file caching it keeps the "
      "layer version even after the model override.\n")
    w("| Name | Overridden by | Cached in |\n|---|---|---|")
    for n in sorted(risky, key=str.lower):
        w(f"| `integration.{n}` | {', '.join(overrides[n])} | {', '.join(risky[n])} |")
    w(f"\n### 3.3 Referenced in sw.models Lua, defined nowhere ({len(undefined)})\n")
    w("Not defined in the layer nor by any sw.models Lua assignment found: dead code, a typo, or a name built at "
      "runtime / defined by another mechanism. Confirm by grep before acting.\n")
    w("| Name | Used in |\n|---|---|")
    for n in sorted(undefined, key=str.lower):
        w(f"| `integration.{n}` | {bmi.code(undefined[n], USERS_SHOWN)} |")
    w(f"\n### 3.4 Never referenced ({len(never)})\n")
    w("Defined in the layer, with no reference in the layer, kernel C++, sw.models or sw.soult Lua (on the branches "
      "read). Candidates for cleanup, or entry points called by name from elsewhere (exercise scripts, other models).\n")
    w(bmi.code(("integration." + n for n in never)) + "\n")

    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(w_lines))
    print(f"integration: {len(defs)} names, {n_defs} definitions, {len(model_defs)} model-side definitions, "
          f"{len(multi)} multi-defined, {len(risky)} override+cached, {len(undefined)} undefined refs, "
          f"{len(never)} never referenced")


if __name__ == "__main__":
    main()
