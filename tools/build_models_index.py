"""Build the sw.models navigator indexes from a sw.models checkout (the branch currently checked out).

Usage (from the forge root):
    python -I tools/build_models_index.py <sw.models root> <out dir> [--model ada] [--only decisional,ontology,actions]
                                          [--sword C:/MASARepo/sw.sword]

Outputs (in <out dir>):
  decisional_index.md  entities (skills, tasks, queries, roles, resources, classes, interfaces) with their wiring,
                       missions per physical DB, decisional models, frag orders, unresolved references
  ontology_index.md    world classes, methods (declared / implemented / callers by name), interfaces and their
                       implementers, task-metadata classes, roles and the ontology type map, anomaly report
                       (reads sw.sword integration Lua and DEC_Decision.cpp when --sword exists)
  action_index.md      resources and who acquires them, action skills (and every skill with <resources>) with their
                       pattern (spark.action / startStopAction / hand-written / instantaneous), spark.startStopAction
                       world methods and their handlers, integration functions that start kernel actions, anomalies
                       (reads sw.sword integration Lua and the C++ StartAction< registrations when --sword exists)

Each output header records the repo branch and commit it was built from.
"""
import argparse
import datetime
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

SPARK_NS = "http://www.masagroup.net/directai/spark/lang/1.5"
ROOT_RE = re.compile(r"<([A-Za-z][\w.-]*)\b([^>]*)>")
NAME_RE = re.compile(r'\bname="([^"]+)"')
DOTTED = re.compile(r"\b[a-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+){2,}\b")
INTEG = re.compile(r"\bintegration\.([A-Za-z_][A-Za-z0-9_]*)")
REPORT = re.compile(r"\beRC_[A-Za-z0-9_]+")
LUA_BLOCK_COMMENT = re.compile(r"--\[(=*)\[.*?\]\1\]", re.S)
LUA_LINE_COMMENT = re.compile(r"--[^\n]*")
XML_COMMENT = re.compile(r"<\?.*?\?>|<!--.*?-->", re.S)

# XML reference patterns: via -> regex (group 1 = target name)
XML_REFS = {
    "behavior": re.compile(r'<behavior\s[^>]*?name="([^"]+)"'),
    "link": re.compile(r'<link\s[^>]*?target="([^"]+)"'),
    "query": re.compile(r'<call\s[^>]*?query="([^"]+)"'),
    "resource": re.compile(r'<resource\s[^>]*?name="([^"]+)"'),
    "metadata": re.compile(r"<entity>\s*([^<\s]+)\s*</entity>"),
    "extends": re.compile(r"<extends>\s*([^<\s]+)\s*</extends>"),
    "implements": re.compile(r"<implements>\s*([^<\s]+)\s*</implements>"),
    "type": re.compile(r'<object\s[^>]*?type="([^"]+)"'),
}
ROLE_SKILL = re.compile(r'<skill\s[^>]*?name="([^"]+)"')
STRING_VAL = re.compile(r"<string>([^<]*)</string>")
BODY_TYPE = re.compile(r"<body-type>\s*<object\s[^>]*?type=\"([^\"]+)\"", re.S)
FEEDBACK = re.compile(r'<feedback\s[^>]*?name="([^"]+)"')
STAGE = re.compile(r"<stage\b([^>]*)>")
LABEL = re.compile(r'label="([^"]*)"')
DESC = re.compile(r"<description>(.*?)</description>", re.S)
EXTERNAL_PREFIXES = ("sparkle.",)  # directai built-ins
USED_BY_CAP = 40  # "used by" lists are capped; the full reverse edges are on the users' own rows


def read(p):
    with open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def git(repo, *args):
    try:
        return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "?"


def strip_lua(txt):
    return LUA_LINE_COMMENT.sub("", LUA_BLOCK_COMMENT.sub("", txt))


def first_sentence(text, limit=140):
    t = " ".join(text.replace("&#xD;", " ").replace("&#xA;", " ").split())
    t = t.replace("|", "/")
    m = re.search(r"^(.+?[.!?])(\s|$)", t)
    if m and len(m.group(1)) <= limit:
        return m.group(1)
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def code(names, limit=None):
    names = sorted(names)
    extra = ""
    if limit and len(names) > limit:
        extra = f" … +{len(names) - limit}"
        names = names[:limit]
    return ", ".join(f"`{n}`" for n in names) + extra


class Entity:
    def __init__(self, name, kind, path, text):
        self.name, self.kind, self.path, self.text = name, kind, path, text
        self.lua = os.path.exists(path[:-4] + ".lua")
        self.lua_text = strip_lua(read(path[:-4] + ".lua")) if self.lua else ""
        self.desc = ""
        body = XML_COMMENT.sub("", text)
        m = DESC.search(body)
        if m and len(re.findall(r"<[A-Za-z]", body[: m.start()])) == 1:  # the root's own description only
            self.desc = first_sentence(m.group(1))
        self.uses = defaultdict(set)  # via -> targets
        self.integration = set(INTEG.findall(self.lua_text))
        self.reports = set(REPORT.findall(self.lua_text))


# ---------------------------------------------------------------------------------------------------------------------
# decisional scan


def scan_decisional(dia5):
    ents = {}
    for root, _, fs in os.walk(dia5):
        for f in fs:
            if not f.endswith(".xml"):
                continue
            p = os.path.join(root, f)
            text = read(p)
            m = ROOT_RE.search(XML_COMMENT.sub("", text))
            if not m or m.group(1) == "category":
                continue
            n = NAME_RE.search(m.group(2))
            if not n:
                continue
            ents[n.group(1)] = Entity(n.group(1), m.group(1), p, text)
    return ents


def link_refs(ents, extra_lua):
    """Fill Entity.uses; return (unresolved refs, lua refs from top-level files)."""
    names = set(ents)
    unresolved = []  # (source, via, target)
    for e in ents.values():
        body = XML_COMMENT.sub("", e.text)
        for via, rx in XML_REFS.items():
            if via == "resource" and e.kind != "skill":
                continue
            for t in rx.findall(body):
                if t == e.name:
                    continue
                if t in names:
                    e.uses[via].add(t)
                elif not t.startswith(EXTERNAL_PREFIXES):
                    unresolved.append((e.name, via, t))
        if e.kind == "role":
            skills = re.search(r"<skills>(.*?)</skills>", body, re.S)
            for t in ROLE_SKILL.findall(skills.group(1) if skills else ""):
                if t in names:
                    e.uses["role-skill"].add(t)
                else:
                    unresolved.append((e.name, "role-skill", t))
        for val in STRING_VAL.findall(body):
            for t in re.split(r"[;,\s]+", val):
                if t in names and t != e.name:
                    e.uses["string"].add(t)
        for t in DOTTED.findall(e.lua_text):
            if t in names and t != e.name:
                e.uses["lua"].add(t)
    top = {}
    for label, p in extra_lua:
        txt = strip_lua(read(p))
        top[label] = ({t for t in DOTTED.findall(txt) if t in names}, set(INTEG.findall(txt)))
    return unresolved, top


def users_of(ents, top):
    """target -> via -> set(source)"""
    users = defaultdict(lambda: defaultdict(set))
    for e in ents.values():
        for via, ts in e.uses.items():
            for t in ts:
                users[t][via].add(e.name)
    for label, (ts, _) in top.items():
        for t in ts:
            users[t]["lua"].add(label)
    return users


# ---------------------------------------------------------------------------------------------------------------------
# physical scan


def xml_root(p):
    return ET.parse(p).getroot()


def scan_physical(model_dir):
    """Return list of dicts, one per physical DB."""
    dbs = []
    phys = os.path.join(model_dir, "physical")
    if not os.path.isdir(phys):
        return dbs
    for db in sorted(os.listdir(phys)):
        pxml = os.path.join(phys, db, "physical.xml")
        if not os.path.isfile(pxml):
            continue
        files = {}
        for el in xml_root(pxml).iter():
            if "file" in el.attrib:
                files.setdefault(el.tag, el.attrib["file"])
        d = {"name": db, "missions": [], "fragorders": [], "models": [], "units_per_model": Counter(),
             "automats_per_model": Counter()}
        mp = os.path.join(phys, db, files.get("missions", "Missions.xml"))
        if os.path.isfile(mp):
            r = xml_root(mp)
            for section in r:
                if section.tag == "fragorders":
                    for fo in section:
                        d["fragorders"].append(dict(fo.attrib))
                    continue
                for m in section.findall("mission"):
                    a = dict(m.attrib)
                    a["section"] = section.tag
                    a["params"] = [p.get("dia-name", "") for p in m.findall("parameter") if p.get("is-context") != "true"]
                    a["ptypes"] = [p.get("type", "") for p in m.findall("parameter")]
                    d["missions"].append(a)
        mo = os.path.join(phys, db, files.get("models", "Models.xml"))
        if os.path.isfile(mo):
            for section in xml_root(mo):
                for m in section:
                    missions = m.find("missions")
                    frags = m.find("fragorders")
                    d["models"].append({
                        "section": section.tag, "name": m.get("name"), "role": m.get("dia-type"), "file": m.get("file"),
                        "missions": [x.get("name") for x in (missions if missions is not None else [])],
                        "mission_frags": {x.get("name"): [f.get("name") for f in x.findall("fragorder")]
                                          for x in (missions if missions is not None else [])},
                        "fragorders": [x.get("name") for x in (frags if frags is not None else [])],
                    })
        for key, counter, tag in (("units", "units_per_model", "unit"), ("automats", "automats_per_model", "automat")):
            up = os.path.join(phys, db, files.get(key, key.capitalize() + ".xml"))
            if os.path.isfile(up):
                for u in xml_root(up).iter(tag):
                    if u.get("decisional-model"):
                        d[counter][u.get("decisional-model")] += 1
        dbs.append(d)
    return dbs


def scan_fragorder_lua(path):
    """Parse FragOrder.lua: initializers (dia-type -> started tasks), propagated list, automat-handled dia-types."""
    res = {"init": {}, "propagated": set(), "automat": set()}
    if not os.path.isfile(path):
        return res
    txt = strip_lua(read(path))
    m = re.search(r"fragOrdersToBePropagated\s*=\s*\{(.*?)\}", txt, re.S)
    if m:
        res["propagated"] = set(re.findall(r'"([^"]+)"', m.group(1)))
    m = re.search(r"fragOrder\.executeAutomatFragOrder\s*=\s*function(.*?)\nend", txt, re.S)
    if m:
        res["automat"] = set(re.findall(r'orderType\s*==\s*"([^"]+)"', m.group(1)))
    start = txt.find("fragOrderTaskInitializers")
    if start >= 0:
        body = txt[start:]
        keys = list(re.finditer(r'\[\s*"([^"]+)"\s*\]\s*=\s*function', body))
        for i, k in enumerate(keys):
            block = body[k.end(): keys[i + 1].start() if i + 1 < len(keys) else len(body)]
            if i + 1 == len(keys):
                block = block.split("\n}\n", 1)[0]
            starts = set(re.findall(r'return\s+true\s*,\s*"([^"]+)"', block))
            if re.search(r"return\s+true\s*(?:$|\n|end|\))", block):
                starts.add(k.group(1))
            res["init"][k.group(1)] = starts
    return res


# ---------------------------------------------------------------------------------------------------------------------
# decisional index


def build_decisional(repo, model, out):
    model_dir = os.path.join(repo, "data", "data", "models", model)
    dia5 = os.path.join(model_dir, "decisional", "dia5")
    if not os.path.isdir(dia5):
        sys.exit(f"{dia5}: not found (model dirs: {', '.join(os.listdir(os.path.join(repo, 'data', 'data', 'models')))})")
    ents = scan_decisional(dia5)
    extra = []
    for f in ("FragOrder.lua", "Decisional.lua"):
        extra.append((f, os.path.join(dia5, f)))
    sd = os.path.join(dia5, "_scripts")
    if os.path.isdir(sd):
        extra += [(f"_scripts/{f}", os.path.join(sd, f)) for f in sorted(os.listdir(sd)) if f.endswith(".lua")]
    extra = [(label, p) for label, p in extra if os.path.isfile(p)]
    unresolved, top = link_refs(ents, extra)
    users = users_of(ents, top)
    dbs = scan_physical(model_dir)
    frag = scan_fragorder_lua(os.path.join(dia5, "FragOrder.lua"))

    # mission wiring: task -> [(db, section, mission name)]; role -> models
    task_missions = defaultdict(list)
    for d in dbs:
        for m in d["missions"]:
            for attr in ("dia-behavior", "mrt-dia-behavior", "cdt-dia-behavior"):
                if m.get(attr):
                    task_missions[m[attr]].append((d["name"], m["section"], m["name"], attr))
    mission_roles = defaultdict(lambda: defaultdict(set))  # db -> mission name -> roles
    mission_models = defaultdict(Counter)  # db -> mission name -> #models
    mission_frags = defaultdict(lambda: defaultdict(Counter))  # db -> mission name -> frag order name -> #models
    role_models = defaultdict(list)  # role -> [(db, model name)]
    for d in dbs:
        for mo in d["models"]:
            role_models[mo["role"]].append((d["name"], mo["name"]))
            for mn in mo["missions"]:
                mission_roles[d["name"]][mn].add(mo["role"])
                mission_models[d["name"]][mn] += 1
                for fname in mo["mission_frags"].get(mn, ()):
                    mission_frags[d["name"]][mn][fname] += 1
    frag_starts = defaultdict(set)  # task -> frag dia-types that start it
    for dt, ts in frag["init"].items():
        for t in ts:
            frag_starts[t].add(dt)

    branch = git(repo, "branch", "--show-current") or "(detached)"
    head = git(repo, "rev-parse", "--short=10", "HEAD")
    date = git(repo, "log", "-1", "--format=%cs", "HEAD")
    dirty = git(repo, "status", "--porcelain", "--", f"data/data/models/{model}")
    mroot = os.path.join(repo, "data", "data", "models")
    models_list = sorted(x for x in os.listdir(mroot) if os.path.isdir(os.path.join(mroot, x)))
    by_kind = defaultdict(list)
    for e in ents.values():
        by_kind[e.kind].append(e)

    L = []
    w = L.append
    w("# sw.models decisional index (generated)\n")
    w(f"Generated by `forge/tools/build_models_index.py` from sw.models **`{branch}` @ `{head}`** ({date}), model "
      f"`{model}`{' — **working tree had uncommitted changes in this model**' if dirty and dirty != '?' else ''}. "
      f"Built {datetime.date.today()}. Model dirs on that branch: {', '.join(f'`{x}`' for x in models_list)}. "
      f"Physical DBs: {', '.join(f'`{d['name']}`' for d in dbs) or 'none'}.\n")
    w("**Stale check:** if `git -C C:/MASARepo/sw.models rev-parse --short=10 HEAD` differs from the commit above, "
      "treat this file as a hint only and confirm with grep. **Grep this file, never read it whole.**\n")
    w(f"Conventions: an entity `a.b.C` lives at `data/data/models/{model}/decisional/dia5/a/b/C.xml` (+ `C.lua` when "
      "Lua = yes). Tasks never have Lua. `via` tags: **beh** = task/role stage `<behavior>`, **link** = skill `<link "
      "target>`, **call** = `<call query>`, **rsk** = role `<skills>`, **res** = skill `<resources>`, **meta** = task "
      "`spark.metadata-class`, **str** = XML `<string>` value (commander mission lists: these hold mission dia-types, matched here only when equal to a task name),**lua** = dotted name in Lua "
      "(string or bare; comments stripped), **ext/impl/type** = `<extends>` / `<implements>` / `<object type>`. "
      "Lua references are textual: a name built at runtime is not found.\n")

    counts = Counter(e.kind for e in ents.values())
    w("## Counts\n")
    w("| Kind | Count | With Lua |\n|---|---|---|")
    for k, c in counts.most_common():
        w(f"| {k} | {c} | {sum(1 for e in by_kind[k] if e.lua)} |")
    for d in dbs:
        w(f"\nPhysical DB `{d['name']}`: {len(d['missions'])} missions "
          f"({', '.join(f'{s} {c}' for s, c in Counter(m['section'] for m in d['missions']).items())}), "
          f"{len(d['fragorders'])} frag orders, {len(d['models'])} decisional models.")
    w("")

    VIA = {"behavior": "beh", "link": "link", "query": "call", "role-skill": "rsk", "resource": "res",
           "metadata": "meta", "string": "str", "lua": "lua", "extends": "ext", "implements": "impl", "type": "type"}

    def used_by(name, skip=("type",)):
        parts = []
        for via, srcs in sorted(users[name].items()):
            if via in skip:
                continue
            parts.append(f"{VIA[via]}: {code(srcs, USED_BY_CAP)}")
        return "; ".join(parts)

    def uses(e, vias, kinds=None):
        parts = []
        for via in vias:
            ts = [t for t in e.uses.get(via, ()) if kinds is None or ents[t].kind in kinds]
            if ts:
                parts.append(f"{VIA[via]}: {code(ts)}")
        return "; ".join(parts)

    def integ(e, limit=None):
        return f"{len(e.integration)}: " + ", ".join(sorted(e.integration)[:limit] if limit else sorted(e.integration)) \
            if e.integration else ""

    def missions_of(name):
        out = []
        for db, sec, mn, attr in task_missions.get(name, []):
            tag = {"mrt-dia-behavior": " (MRT)", "cdt-dia-behavior": " (CDT)"}.get(attr, "")
            out.append(f"{db}/{sec}: \"{mn}\"{tag}")
        return "; ".join(sorted(set(out)))

    w("## 1. Tasks\n")
    w("Missions = `Missions.xml` entries whose `dia-behavior` (automats: `mrt-`/`cdt-dia-behavior`) is this task, per "
      "physical DB. Frago = frag-order dia-types whose `FragOrder.lua` initializer starts it.\n")
    w("| Task | Stages | Skills (beh) | Queries (call) | Other uses | Missions | Frago | Used by | Description |")
    w("|---|---|---|---|---|---|---|---|---|")
    for e in sorted(by_kind["task"], key=lambda x: x.name):
        stages = STAGE.findall(e.text)
        labels = [LABEL.search(s).group(1) for s in stages if LABEL.search(s)]
        st = f"{len(stages)}" + (f" ({'; '.join(labels)})" if labels else "")
        w(f"| `{e.name}` | {st} | {code(e.uses.get('behavior', ()))} | {code(e.uses.get('query', ()))} | "
          f"{uses(e, ('metadata', 'string', 'link'))} | {missions_of(e.name)} | {code(frag_starts.get(e.name, ()))} | "
          f"{used_by(e.name)} | {e.desc} |")
    w("")

    w("## 2. Skills\n")
    w("| Skill | Links (skills) | Queries / tasks | Resources | Feedbacks | Body type | Used by | integration.* | Description |")
    w("|---|---|---|---|---|---|---|---|---|")
    for e in sorted(by_kind["skill"], key=lambda x: x.name):
        bt = BODY_TYPE.search(e.text)
        w(f"| `{e.name}` | {code(e.uses.get('link', ()))} | {uses(e, ('query', 'lua', 'string'), ('query', 'task', 'skill'))} | "
          f"{code(e.uses.get('resource', ()))} | {', '.join(sorted(set(FEEDBACK.findall(e.text))))} | "
          f"{('`' + bt.group(1) + '`') if bt else ''} | {used_by(e.name)} | {integ(e)} | {e.desc} |")
    w("")

    w("## 3. Queries\n")
    w("| Query | Uses | Used by | integration.* | Description |\n|---|---|---|---|---|")
    for e in sorted(by_kind["query"], key=lambda x: x.name):
        w(f"| `{e.name}` | {uses(e, ('query', 'lua'), ('query', 'task', 'skill'))} | {used_by(e.name)} | {integ(e)} | {e.desc} |")
    w("")

    w("## 4. Roles\n")
    w("Models = decisional models in `Models.xml` whose `dia-type` is this role, per DB (count of `Units.xml` / "
      "`Automats.xml` types using each model in brackets).\n")
    w("| Role | Extends | Body type | Skills (rsk, with modifiers) | Default task behaviors | Lua | Models | Description |")
    w("|---|---|---|---|---|---|---|---|")
    for e in sorted(by_kind["role"], key=lambda x: x.name):
        bt = BODY_TYPE.search(e.text)
        dt = re.search(r"<default-task>(.*?)</default-task>", e.text, re.S)
        dbeh = set(XML_REFS["behavior"].findall(dt.group(1))) if dt else set()
        mods = []
        for d in dbs:
            ms = [n for db, n in role_models.get(e.name, []) if db == d["name"]]
            if ms:
                cnt = d["units_per_model"] | d["automats_per_model"]
                mods.append(f"{d['name']} ({len(ms)}): " + ", ".join(f"\"{n}\" [{cnt.get(n, 0)}]" for n in sorted(ms)))
        w(f"| `{e.name}` | {code(e.uses.get('extends', ()))} | {('`' + bt.group(1) + '`') if bt else ''} | "
          f"{code(e.uses.get('role-skill', ()))} | {code(dbeh)} | {'yes' if e.lua else ''} | {'; '.join(mods)} | {e.desc} |")
    w("")

    w("## 5. Resources\n")
    w("| Resource | Acquired by (skills) | Description |\n|---|---|---|")
    for e in sorted(by_kind["resource"], key=lambda x: x.name):
        w(f"| `{e.name}` | {code(users[e.name].get('resource', ()))} | {e.desc} |")
    w("")

    w("## 6. Classes and interfaces (summary; details in `ontology_index.md` when present)\n")
    w("| Name | Kind | Extends / implements | Lua | Referenced as type (count) | Other users | Description |")
    w("|---|---|---|---|---|---|---|")
    for e in sorted(by_kind["class"] + by_kind["interface"], key=lambda x: x.name):
        w(f"| `{e.name}` | {e.kind} | {uses(e, ('extends', 'implements'))} | {'yes' if e.lua else ''} | "
          f"{len(users[e.name].get('type', ()))} | {used_by(e.name, skip=('type',))} | {e.desc} |")
    w("")

    known_tasks = {e.name for e in by_kind["task"]}
    for d in dbs:
        w(f"## 7.{d['name']} Missions in `physical/{d['name']}`\n")
        w("Task ✗ = no task with that name in this model's dia5 (e.g. `taskgroup.*` lives in the `taskgroups` submodule). "
          "Params = non-context `dia-name`s. Roles = `dia-type` of the `Models.xml` models offering the mission. "
          "Frag orders = `<fragorder>` children of this mission in those models, with the number of models listing "
          "each (model-level frag orders available without a mission are not included). Many frag orders start no task "
          "and are polled by skills instead (see THEMES gotchas).\n")
        w("| DB | Mission | Section | id | Behavior → task | dia-type (if different) | Params | Models | Roles | Frag orders |")
        w("|---|---|---|---|---|---|---|---|---|---|")
        for m in sorted(d["missions"], key=lambda x: (x["section"], x["name"])):
            beh = []
            for attr, tag in (("dia-behavior", ""), ("mrt-dia-behavior", "MRT "), ("cdt-dia-behavior", "CDT ")):
                if m.get(attr):
                    beh.append(f"{tag}`{m[attr]}`{'' if m[attr] in known_tasks else ' ✗'}")
            if len(beh) == 2 and m.get("mrt-dia-behavior") == m.get("cdt-dia-behavior"):
                beh = [beh[0].replace("MRT ", "MRT+CDT ")]
            dt = m.get("dia-type", "")
            same = dt in (m.get("dia-behavior"), m.get("mrt-dia-behavior"))
            w(f"| {d['name']} | \"{m['name']}\" | {m['section']} | {m.get('id', '')} | {'; '.join(beh)} | {'' if same else '`' + dt + '`'} | "
              f"{', '.join(m['params'])} | {mission_models[d['name']].get(m['name'], 0)} | "
              f"{code(mission_roles[d['name']].get(m['name'], ()))} | "
              + ", ".join(f"\"{f}\" ({c})" for f, c in sorted(mission_frags[d['name']].get(m['name'], {}).items()))
              + " |")
        w("")
        w(f"### Decisional models in `physical/{d['name']}/Models.xml`\n")
        w("| DB | Model | Section | Role (dia-type) | Brain file | Missions | Frag orders (model-level) | Unit/automat types using it |")
        w("|---|---|---|---|---|---|---|---|")
        cnt = d["units_per_model"] | d["automats_per_model"]
        for mo in sorted(d["models"], key=lambda x: (x["section"], x["name"])):
            role_ok = "" if mo["role"] in ents else " ✗"
            w(f"| {d['name']} | \"{mo['name']}\" | {mo['section']} | `{mo['role']}`{role_ok} | `{mo['file']}` | {len(mo['missions'])} | "
              f"{len(mo['fragorders'])} | {cnt.get(mo['name'], 0)} |")
        w("")
        w(f"### Frag orders in `physical/{d['name']}/Missions.xml`\n")
        w("Init = initializer in `FragOrder.lua` `fragOrderTaskInitializers` (no initializer → "
          "`integration.startFragOrder`, handled by the kernel / integration layer). Starts = task(s) the initializer "
          "starts (`return true, \"…\"`, or the dia-type itself on a bare `return true`). Prop = listed in "
          "`fragOrdersToBePropagated`. Auto = tested in `fragOrder.executeAutomatFragOrder`.\n")
        w("| DB | Frag order | dia-type | Without mission | Init | Starts | Prop | Auto |\n|---|---|---|---|---|---|---|---|")
        for fo in sorted(d["fragorders"], key=lambda x: x.get("name", "")):
            dt = fo.get("dia-type", "")
            starts = frag["init"].get(dt, set())
            if dt in known_tasks:
                starts = starts | {dt}
            w(f"| {d['name']} | \"{fo.get('name')}\" | `{dt}` | {fo.get('available-without-mission', '')} | "
              f"{'yes' if dt in frag['init'] else ''} | {code(starts)} | {'yes' if dt in frag['propagated'] else ''} | "
              f"{'yes' if dt in frag['automat'] else ''} |")
        w("")

    w("## 8. Top-level Lua\n")
    w("| File | Entities referenced | integration.* assigned or called |\n|---|---|---|")
    for label, (ts, ints) in top.items():
        w(f"| `{label}` | {code(ts)} | {len(ints)} |")
    w("")

    w("## 9. Unreferenced entities\n")
    w("Nothing in this model's XML, Lua (textual match) or physical DBs refers to them. Candidates for dead code, "
      "but a name built at runtime, a client `extensions/` model or another branch may still use them.\n")
    referenced = {t for t, v in users.items() if any(v.values())} | set(task_missions) | set(frag_starts)
    for d in dbs:
        referenced |= {mo["role"] for mo in d["models"]}
        referenced |= {fo.get("dia-type") for fo in d["fragorders"]}
    for kind in ("task", "skill", "query", "role", "resource"):
        orphans = sorted(e.name for e in by_kind[kind] if e.name not in referenced)
        w(f"- **{kind}** ({len(orphans)}): {code(orphans)}")
    w("")

    w("## 10. Unresolved references\n")
    w("XML references to names that are not entities of this model (directai `sparkle.*` built-ins excluded). "
      "Near match = same name ignoring case.\n")
    lower = defaultdict(list)
    for n in ents:
        lower[n.lower()].append(n)
    w("| From | via | Target | Near match |\n|---|---|---|---|")
    for src, via, t in sorted(set(unresolved)):
        w(f"| `{src}` | {VIA.get(via, via)} | `{t}` | {code(lower.get(t.lower(), ()))} |")
    w("")

    with open(os.path.join(out, "decisional_index.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(L))
    return counts, dbs


# ---------------------------------------------------------------------------------------------------------------------
# ontology


def blank_lua(src, keep_lines=False):
    """Drop Lua comments and blank string contents, keeping the code structure.
    keep_lines: keep the newlines of dropped block comments and long strings, so line numbers still hold."""
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("--", i):
            m = re.match(r"--\[(=*)\[", src[i:i + 20])
            if m:
                end = src.find("]" + m.group(1) + "]", i)
                j = n if end < 0 else end + len(m.group(1)) + 2
                if keep_lines:
                    out.append("\n" * src.count("\n", i, j))
                i = j
            else:
                j = src.find("\n", i)
                i = n if j < 0 else j
            continue
        m = re.match(r"\[(=*)\[", src[i:i + 20])
        if m:
            end = src.find("]" + m.group(1) + "]", i)
            j = n if end < 0 else end + len(m.group(1)) + 2
            out.append('""' + ("\n" * src.count("\n", i, j) if keep_lines else ""))
            i = j
            continue
        if c in "\"'":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            out.append('""')
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


LUA_TOK = re.compile(r"[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*\s*=(?!=)|[A-Za-z_]\w*|==|~=|<=|>=|\S")
METHOD_CALL = re.compile(r":\s*([A-Za-z_]\w*)\s*[\(\{\"]")
LUA_SPECIAL = {"__init", "__call", "__daihash", "__decode", "__eq", "__encode", "__gc", "__le", "__lt", "__tostring"}


def lua_top_keys(text):
    """Keys of a Lua table body, on text already passed through blank_lua: keys assigned at brace depth 1
    (`return { m = … }`, or a table text starting at its `{`) or `X.m = …` at depth 0, outside any function / control
    block and outside call parentheses. Returns [(name, form, value start, span end)]; a key's span ends where the
    next key starts (or at the end of the text)."""
    found = []
    brace = paren = pend_do = 0
    blocks = []
    for m in LUA_TOK.finditer(text):
        t = m.group(0)
        if t[-1] == "=" and (t[0].isalpha() or t[0] == "_"):
            lhs = re.sub(r"\s", "", t[:-1])
            if not blocks and not paren:
                rhs = text[m.end():m.end() + 40].lstrip()
                form = ("function" if rhs.startswith("function") else
                        "startStopAction" if rhs.startswith("spark.startStopAction") else
                        "spark.action" if rhs.startswith("spark.action") else "alias")
                if brace == 1 and "." not in lhs:
                    found.append([lhs, form, m.end()])
                elif brace == 0 and lhs.count(".") == 1:
                    found.append([lhs.split(".")[1], form, m.end()])
            continue
        if t == "{":
            brace += 1
        elif t == "}":
            brace -= 1
        elif t == "(":
            paren += 1
        elif t == ")":
            paren -= 1
        elif t in ("function", "if", "repeat"):
            blocks.append(t)
        elif t in ("while", "for"):
            pend_do += 1
            blocks.append(t)
        elif t == "do":
            if pend_do:
                pend_do -= 1
            else:
                blocks.append("do")
        elif t in ("end", "until") and blocks:
            blocks.pop()
    for i, k in enumerate(found):
        k.append(found[i + 1][2] if i + 1 < len(found) else len(text))
    return [tuple(k) for k in found]


def lua_class_methods(src):
    """Methods of a class .lua (see lua_top_keys). Returns {name: form}."""
    keys = {}
    for name, form, _, _ in lua_top_keys(blank_lua(src)):
        keys.setdefault(name, form)
    return keys


def repo_stamp(repo):
    branch = git(repo, "branch", "--show-current") or "(detached)"
    return branch, git(repo, "rev-parse", "--short=10", "HEAD"), git(repo, "log", "-1", "--format=%cs", "HEAD")


def scan_sword(sword):
    """sw.sword side: integration Lua files, ontologyKeys defaults, Missions.xml parameter type -> ontology key."""
    res = {"files": {}, "keys": [], "ptype_keys": {}, "ok": False}
    integ_dir = os.path.join(sword, "data", "app-data", "resources", "integration")
    top = os.path.join(sword, "data", "app-data", "resources", "Integration.lua")
    if not os.path.isdir(integ_dir):
        return res
    res["ok"] = True
    for f in sorted(os.listdir(integ_dir)):
        if f.endswith(".lua"):
            res["files"][f"integration/{f}"] = blank_lua(read(os.path.join(integ_dir, f)))
    if os.path.isfile(top):
        raw = read(top)
        res["files"]["Integration.lua"] = blank_lua(raw)
        m = re.search(r"local\s+ontologyKeys\s*=\s*\{(.*?)\}", raw, re.S)
        res["keys"] = re.findall(r'"(\w+)"', m.group(1)) if m else []
    cpp = os.path.join(sword, "src", "libraries", "simulation", "Decision", "DEC_Decision.cpp")
    if os.path.isfile(cpp):
        txt = read(cpp)
        bodies = {}
        for m in re.finditer(r"^bool\s+(\w+FunctionBM)\s*\(", txt, re.M):
            end = txt.find("\n}", m.end())
            bodies[m.group(1)] = txt[m.end():end]

        def keys_of(fn, seen=()):
            b = bodies.get(fn, "")
            ks = re.findall(r"integration\.ontology\.types\.(\w+)", b)
            for sub in re.findall(r"\b(\w+FunctionBM)\s*\(", b):
                if sub != fn and sub not in seen:
                    ks += keys_of(sub, seen + (fn,))
            return list(dict.fromkeys(ks))

        for t, fn in re.findall(r'functorsBM\[\s*"(\w+)"\s*\]\s*=\s*(\w+)', txt):
            res["ptype_keys"][t] = keys_of(fn)
    return res


def build_ontology(repo, model, out, sword):
    dia5 = os.path.join(repo, "data", "data", "models", model, "decisional", "dia5")
    if not os.path.isdir(dia5):
        sys.exit(f"{dia5}: not found")
    ents = scan_decisional(dia5)
    link_refs(ents, [])
    users = users_of(ents, {})
    classes = {n: e for n, e in ents.items() if e.kind == "class"}
    ifaces = {n: e for n, e in ents.items() if e.kind == "interface"}
    roles = {n: e for n, e in ents.items() if e.kind == "role"}

    # --- per class / interface XML data and Lua methods
    for e in list(classes.values()) + list(ifaces.values()) + list(roles.values()):
        body = XML_COMMENT.sub("", e.text)
        root = ROOT_RE.search(body)
        e.abstract = 'abstract="true"' in root.group(2)
        e.parent = (XML_REFS["extends"].findall(body) or [None])[0] if e.kind != "interface" else None
        e.ifs = XML_REFS["extends"].findall(body) if e.kind == "interface" else XML_REFS["implements"].findall(body)
        mm = re.search(r"<methods>(.*)</methods>", body, re.S)
        tags = re.findall(r"<method\s([^>]*)>", mm.group(1)) if mm else []
        e.declared = {NAME_RE.search(a).group(1): ('abstract="true"' in a or e.kind == "interface") for a in tags}
        e.ctor = "<constructor" in body
        e.methods = lua_class_methods(read(e.path[:-4] + ".lua")) if e.lua else {}

    def chain(n):
        out_ = []
        while n in classes and n not in out_:
            out_.append(n)
            n = classes[n].parent
        return out_, n  # n = first non-model ancestor (sparkle.*) or None

    def iface_closure(names):
        seen, todo = set(), list(names)
        while todo:
            i = todo.pop()
            if i in seen or i not in ifaces:
                continue
            seen.add(i)
            todo += ifaces[i].ifs
        return seen

    meta = {n for n in classes if chain(n)[1] == "sparkle.TaskMetadata"}
    world = {n: classes[n] for n in classes if n not in meta}
    subclasses = defaultdict(set)
    for n in classes:
        for anc in chain(n)[0][1:]:
            subclasses[anc].add(n)
    all_ifs = {n: iface_closure(i for c in chain(n)[0] for i in classes[c].ifs) for n in classes}
    direct_impl = defaultdict(set)
    trans_impl = defaultdict(set)
    for n in classes:
        for i in classes[n].ifs:
            direct_impl[i].add(n)
        for i in all_ifs[n]:
            trans_impl[i].add(n)
    iface_methods = {i: {m for j in iface_closure([i]) for m in ifaces[j].declared} for i in ifaces}

    # --- callers by method name (upper bounds)
    sw = scan_sword(sword) if sword else {"ok": False, "files": {}, "keys": [], "ptype_keys": {}}
    callers = defaultdict(lambda: defaultdict(set))  # method -> scope -> files/entities
    by_path = {os.path.normcase(e.path[:-4] + ".lua"): e.name for e in ents.values() if e.lua}
    for root_, _, fs in os.walk(dia5):
        for f in fs:
            if f.endswith(".lua"):
                p = os.path.join(root_, f)
                label = by_path.get(os.path.normcase(p), os.path.relpath(p, dia5).replace("\\", "/"))
                for m in METHOD_CALL.findall(blank_lua(read(p))):
                    callers[m]["model"].add(label)
    for label, txt in sw["files"].items():
        for m in METHOD_CALL.findall(txt):
            callers[m]["sword"].add(label)

    # --- roles: effective body type and ontology type map
    def role_chain(n):
        out_ = []
        while n in roles and n not in out_:
            out_.append(n)
            n = roles[n].parent
        return out_

    role_map, role_map_src, role_body, role_body_src = {}, {}, {}, {}
    for n, e in roles.items():
        own = re.search(r"setRoleProperties", e.lua_text or "")
        e.typemap = dict(re.findall(r"^\s*(\w+)\s*=\s*([a-z]\w*(?:\.\w+)+)\s*,?\s*$", e.lua_text, re.M)) if own else None
    for n in roles:
        for r in role_chain(n):
            if roles[r].typemap is not None:
                role_map[n], role_map_src[n] = roles[r].typemap, r
                break
        for r in role_chain(n):
            bt = BODY_TYPE.search(roles[r].text)
            if bt:
                role_body[n], role_body_src[n] = bt.group(1), r
                break
    key_classes = defaultdict(lambda: defaultdict(set))  # key -> class -> roles
    class_keys = defaultdict(set)  # class -> keys
    for n, mp in role_map.items():
        for k, c in mp.items():
            key_classes[k][c].add(n)
            class_keys[c].add(k)
    for n in roles:
        for k in sw["keys"]:
            if k not in role_map.get(n, {}):
                key_classes[k]["sparkle.Object (default)"].add(n)
    key_ptypes = defaultdict(list)
    for t, ks in sw["ptype_keys"].items():
        for k in ks:
            key_ptypes[k].append(t)
    used_keys = Counter()
    key_users = defaultdict(set)
    for root_, _, fs in os.walk(dia5):
        for f in fs:
            if f.endswith(".lua"):
                p = os.path.join(root_, f)
                for k in re.findall(r"integration\.ontology\.types\.(\w+)", blank_lua(read(p))):
                    used_keys[k] += 1
                    key_users[k].add(by_path.get(os.path.normcase(p), f))
    sword_key_files = defaultdict(set)
    for label, txt in sw["files"].items():
        for k in re.findall(r"integration\.ontology\.types\.(\w+)", txt):
            sword_key_files[k].add(label)

    # --- header
    branch, head, date = repo_stamp(repo)
    dirty = git(repo, "status", "--porcelain", "--", f"data/data/models/{model}")
    L = []
    w = L.append
    w("# sw.models ontology index (generated)\n")
    w(f"Generated by `forge/tools/build_models_index.py` from sw.models **`{branch}` @ `{head}`** ({date}), model "
      f"`{model}`{' — **working tree had uncommitted changes in this model**' if dirty and dirty != '?' else ''}. "
      f"Built {datetime.date.today()}." + (
          " sw.sword side (integration Lua callers, `ontologyKeys`, `DEC_Decision.cpp::functorsBM`) read from "
          "**`%s` @ `%s`** (%s).\n" % repo_stamp(sword) if sw["ok"] else
          " sw.sword not found: integration callers, default keys and parameter types are missing.\n"))
    w("**Stale check:** if `git -C C:/MASARepo/sw.models rev-parse --short=10 HEAD` differs from the commit above, "
      "treat this file as a hint only and confirm with grep. **Grep this file, never read it whole.**\n")
    w("**Callers are name matches, i.e. upper bounds.** A Lua call `x:m(` cannot be tied to a type statically, so every "
      "class with a method `m` gets the same caller list. \"model\" = this model's Lua (entity name, or file for "
      "top-level Lua), \"sword\" = sw.sword `data/app-data/resources/integration*`. A method called through a name built "
      "at runtime, or only by the kernel C++, shows no caller.\n")
    w("Rules checked (directai validator, see THEMES): **A** a concrete class must re-declare, non-abstract, in its own "
      "or an ancestor's XML every method of its interfaces (`class.method.unimplemented`); **B** a class `.lua` may only "
      "define methods declared in its own XML, and must define every own non-abstract one "
      "(`class.lua.method.missing-declaration` / `…unimplemented`); `<constructor>` ⇔ `__init`.\n")

    w("## Counts\n")
    w(f"| Kind | Count |\n|---|---|\n| class (world) | {len(world)} |\n| class (task metadata, chain to "
      f"`sparkle.TaskMetadata`) | {len(meta)} |\n| interface | {len(ifaces)} |\n| role | {len(roles)} |\n"
      f"| world-class methods implemented | {sum(len(e.methods) for e in world.values())} |\n")

    # --- 1. world classes
    w("## 1. World classes\n")
    w("Chain = `<extends>` up to the root (model classes implicitly extend `sparkle.Object`). Interfaces: own `<implements>`, "
      "then + inherited count (via ancestors and interface `<extends>`). Methods: declared in own XML / implemented in own "
      "`.lua` / inherited only / of which startStopAction. Keys = ontology keys mapping to the class in role type maps "
      "(roles in brackets); Param types = `Missions.xml` parameter types the kernel wraps with those keys.\n")
    w("| Class | Abstract | Chain | Interfaces | Ctor | Methods decl/impl/inh/ssa | Keys (roles) | Param types | "
      "Subclasses | Used as type | Description |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    for n, e in sorted(world.items()):
        ch, root_ = chain(n)
        inh = set()
        for c in ch[1:]:
            inh |= set(classes[c].methods)
        inh -= set(e.methods)
        keys = sorted(class_keys.get(n, ()))
        keys_s = ", ".join(f"`{k}` ({len(key_classes[k][n])})" for k in keys)
        pts = sorted({t for k in keys for t in key_ptypes.get(k, ())})
        extra_if = len(all_ifs[n] - set(e.ifs))
        w(f"| `{n}` | {'yes' if e.abstract else ''} | {' → '.join(f'`{c}`' for c in ch[1:]) or ''}"
          f"{' → `' + root_ + '`' if root_ else ''} | {code(e.ifs)}{f' +{extra_if}' if extra_if else ''} | "
          f"{'yes' if e.ctor else ''} | {len(e.declared)}/{len(e.methods)}/{len(inh)}/"
          f"{sum(1 for f in e.methods.values() if f == 'startStopAction')} | {keys_s} | {', '.join(pts)} | "
          f"{code(subclasses.get(n, ()), 12)} | {len(users[n].get('type', ()))} | {e.desc} |")
    w("")

    # --- 2. methods
    w("## 2. World-class methods\n")
    w("One row per method implemented in a class `.lua` or declared abstract. Form: function / startStopAction / alias / "
      "abstract. Overrides = nearest ancestor that also implements it. For = interfaces (of the class, transitively) "
      "declaring it. Callers: count of distinct entities/files, model then sword (names capped).\n")
    w("| Method | Form | Overrides | For interfaces | Callers model | Callers sword |")
    w("|---|---|---|---|---|---|")
    for n, e in sorted(world.items()):
        ch, _ = chain(n)
        names = set(e.methods) | {m for m, ab in e.declared.items() if ab}
        for m in sorted(names):
            if m in LUA_SPECIAL:
                continue
            form = e.methods.get(m, "abstract")
            ov = next((c for c in ch[1:] if m in classes[c].methods), "")
            fi = sorted(i for i in all_ifs[n] if m in ifaces[i].declared)
            cm, cs = callers[m].get("model", set()), callers[m].get("sword", set())
            w(f"| `{n}:{m}` | {form} | {('`' + ov + '`') if ov else ''} | {code(fi)} | "
              f"{len(cm)}{': ' + code(cm, 6) if cm else ''} | {len(cs)}{': ' + code(cs, 4) if cs else ''} |")
    w("")

    # --- 3. interfaces
    w("## 3. Interfaces\n")
    w("Implementers: direct (`<implements>`) and transitive (subclasses of implementers, and via interfaces extending this "
      "one). Used as type = model entities with an `<object type>` of this interface (skills/queries `<body-type>` included).\n")
    w("| Interface | Extends | Extended by | Methods (own) | Direct implementers | Transitive (count) | Used as type | Description |")
    w("|---|---|---|---|---|---|---|---|")
    ext_by = defaultdict(set)
    for i, e in ifaces.items():
        for x in e.ifs:
            ext_by[x].add(i)
    for i, e in sorted(ifaces.items()):
        w(f"| `{i}` | {code(e.ifs)} | {code(ext_by.get(i, ()))} | {len(e.declared)}: {', '.join(sorted(e.declared))} | "
          f"{code(direct_impl.get(i, ()))} | {len(trans_impl.get(i, ()))} | {len(users[i].get('type', ()))} | {e.desc} |")
    w("")

    # --- 4. task metadata classes
    w("## 4. Task-metadata classes\n")
    w("Classes whose chain reaches `sparkle.TaskMetadata` (base classes `common.agent.world.PlatoonTask`, "
      "`common.commander.behaviors.tasks.knowledges.CompanyTask`). Tasks = tasks citing it in "
      "`<annotation key=\"spark.metadata-class\">`. Methods = implemented in its `.lua`.\n")
    w("| Class | Parent | Tasks | Methods | integration.* |\n|---|---|---|---|---|")
    for n, e in sorted(classes.items()):
        if n in meta:
            w(f"| `{n}` | `{e.parent}` | {code(users[n].get('metadata', ()))} | "
              f"{', '.join(sorted(m for m in e.methods if m not in LUA_SPECIAL))} | {len(e.integration)} |")
    w("")

    # --- 5. roles and type map
    w("## 5. Roles\n")
    w("Body type = nearest `<body-type>` up the `<extends>` chain (directai inherits it); Type map = the role whose `.lua` "
      "`sw.ai.setRoleProperties` map applies (own or nearest parent: child roles get a copy of the parent's properties). "
      "Default keys = sw.sword `ontologyKeys` missing from that map, which fall back to `sparkle.Object`.\n")
    w("| Role | Extends | Body type (from) | Type map from | Default keys (sparkle.Object) |\n|---|---|---|---|---|")
    for n in sorted(roles):
        mp = role_map.get(n, {})
        miss = [k for k in sw["keys"] if k not in mp]
        w(f"| `{n}` | {('`' + roles[n].parent + '`') if roles[n].parent else ''} | "
          f"{('`' + role_body[n] + '`') if n in role_body else '✗ (sparkle.Object)'}"
          f"{'' if role_body_src.get(n) in (n, None) else ' (from `' + role_body_src[n] + '`)'} | "
          f"{('`' + role_map_src[n] + '`') if n in role_map_src else '✗'} | {', '.join(miss)} |")
    w("")
    w("## 6. Ontology type map (`integration.ontology.types.<key>`)\n")
    w("Per key: class per role (number of roles), the `Missions.xml` parameter types the kernel wraps with it "
      "(`DEC_Decision.cpp::functorsBM`), how many model Lua uses (`CreateKnowledge( integration.ontology.types.<key>, …)` "
      "and others), and which sw.sword integration files read it.\n")
    w("| Key | Class (roles) | Param types | Model Lua uses | sword files |\n|---|---|---|---|---|")
    all_keys = sorted(set(key_classes) | set(used_keys) | set(sw["keys"]) | set(sword_key_files))
    for k in all_keys:
        cl = "; ".join(f"`{c}` ({len(rs)})" for c, rs in sorted(key_classes[k].items(), key=lambda x: -len(x[1])))
        w(f"| `{k}` | {cl or '✗ none'} | {', '.join(sorted(key_ptypes.get(k, ())))} | {used_keys.get(k, 0)} | "
          f"{code(sword_key_files.get(k, ()), 6)} |")
    w("")

    # --- 7. anomalies
    w("## 7. Anomaly report\n")
    w("### 7.1 Interfaces with 0 or 1 implementers\n")
    w("| Interface | Direct | Transitive | Extended by |\n|---|---|---|---|")
    for i in sorted(ifaces):
        if len(direct_impl.get(i, ())) <= 1:
            w(f"| `{i}` | {code(direct_impl.get(i, ()))} | {code(trans_impl.get(i, ()), 8)} | {code(ext_by.get(i, ()))} |")
    w("")
    w("### 7.2 Validator rules A / B (expected empty: sparkcheck and the sim reject these)\n")
    viol = []
    for n, e in sorted(classes.items()):
        ch, _ = chain(n)
        if not e.abstract:
            decl = {}
            for c in reversed(ch):
                decl.update(classes[c].declared)
            for i in sorted(all_ifs[n]):
                for m in sorted(ifaces[i].declared):
                    if decl.get(m, True):
                        viol.append(f"A: `{n}` does not re-declare `{m}` from `{i}`")
        for m in sorted(set(e.methods) - set(e.declared) - LUA_SPECIAL):
            viol.append(f"B: `{n}.lua` defines undeclared `{m}`")
        for m, ab in sorted(e.declared.items()):
            if not ab and m not in e.methods:
                viol.append(f"B: `{n}` declares `{m}` but its `.lua` does not define it")
        if e.ctor != ("__init" in e.methods):
            viol.append(f"B: `{n}` `<constructor>` {'without' if e.ctor else 'missing for'} `__init`")
    w("\n".join(f"- {v}" for v in viol) if viol else "- none")
    w("")
    w("### 7.3 World-class methods never called by name\n")
    w("No `:name(` call in this model's Lua or sw.sword integration Lua. Kernel C++ or runtime-built names may still "
      "call them; hooks called by directai (`__init`, `__daihash`, …) are excluded.\n")
    nev = defaultdict(list)
    only_sword = defaultdict(list)
    for n, e in world.items():
        for m in e.methods:
            if m in LUA_SPECIAL:
                continue
            if not callers[m]:
                nev[m].append(n)
            elif not callers[m].get("model"):
                only_sword[m].append(n)
    w(f"{len(nev)} names: " + "; ".join(f"`{m}` ({code(v)})" for m, v in sorted(nev.items())) + "\n")
    w("### 7.4 World-class methods called only from sw.sword integration Lua\n")
    w((f"{len(only_sword)} names: " + "; ".join(f"`{m}` ({code(v)}; sword: {code(callers[m]['sword'], 3)})"
                                                for m, v in sorted(only_sword.items()))) + "\n")
    w("### 7.5 Same method in unrelated world classes\n")
    w("Same method name implemented in ≥ 2 world classes that share no ancestor class or interface declaring it "
      "(groups = classes that do share one). Candidates for an interface or a common base class, or plain name clashes.\n")
    impl_by = defaultdict(set)
    for n, e in world.items():
        for m in e.methods:
            if m not in LUA_SPECIAL:
                impl_by[m].add(n)
    rows = []
    for m, cs in sorted(impl_by.items()):
        if len(cs) < 2:
            continue
        origin = {}
        for c in cs:
            o = {a for a in chain(c)[0] if m in classes[a].declared} | {i for i in all_ifs[c] if m in ifaces[i].declared}
            origin[c] = o
        groups = []  # union classes sharing an origin
        for c in sorted(cs):
            hit = [g for g in groups if any(origin[c] & origin[x] for x in g)]
            merged = {c}.union(*hit) if hit else {c}
            groups = [g for g in groups if g not in hit] + [merged]
        if len(groups) > 1:
            rows.append((m, groups))
    w(f"{len(rows)} names.\n")
    w("| Method | Groups |\n|---|---|")
    for m, groups in rows:
        w(f"| `{m}` | " + " / ".join(code(g, 6) for g in sorted(groups, key=lambda g: (-len(g), sorted(g)))) + " |")
    w("")
    w("### 7.6 Ontology keys\n")
    for k in sorted(used_keys):
        cls = {c for c in key_classes.get(k, {}) if not c.startswith("sparkle.")}
        if not cls:
            where = "in sw.sword `ontologyKeys`, so `sparkle.Object` (no model methods)" if k in sw["keys"] else \
                "**in no role map and not in `ontologyKeys`: nil type**"
            w(f"- `{k}`: used {used_keys[k]}× in model Lua ({code(key_users[k], 5)}), {where}")
    for k in sorted(sword_key_files):
        if k not in sw["keys"] and not any(c for c in key_classes.get(k, {}) if not c.startswith("sparkle.")):
            w(f"- `{k}`: read by sw.sword {code(sword_key_files[k])}, in no role map and not in `ontologyKeys` (nil)")
    w("")
    w("### 7.7 Inheritance depth (world classes, depth ≥ 4)\n")
    w(", ".join(f"`{n}` ({len(chain(n)[0])})" for n in sorted(world, key=lambda x: (-len(chain(x)[0]), x))
                if len(chain(n)[0]) >= 4) + "\n")

    with open(os.path.join(out, "ontology_index.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(L))
    return {"class (world)": len(world), "class (metadata)": len(meta), "interface": len(ifaces), "role": len(roles),
            "rule violations": len(viol), "never called": len(nev)}


# ---------------------------------------------------------------------------------------------------------------------
# actions: resources, action skills, Lua action frameworks, kernel action starters

KERNEL_TOKEN = re.compile(r"\b_{0,2}DEC_\w+\b")
MODEL_INTEG = re.compile(r"\bintegration\.([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)")
CPP_DEF = re.compile(r"(?m)^[A-Za-z_][\w:<>,\s\*&]*?\b(\w+)\s*\(")
LUA_DEF = re.compile(r"^(local\s+)?(?:function\s+([\w.]+)\s*\(|([\w.]+)\s*=\s*(function\b|spark\.startStopAction\b|"
                     r"[A-Za-z_][\w.]*[ \t]*$))", re.M)
LIFECYCLE = ("create", "activate", "deactivate", "select", "deselect", "destroy", "start", "started", "stop")
ONCE_IDIOM = re.compile(r"Once")  # displayReportOnce, sendDynamicMessageOnce…: startStopAction used as "do once while called"


def strip_cpp(t):
    out, i, n = [], 0, len(t)
    while i < n:
        if t.startswith("//", i):
            j = t.find("\n", i)
            i = n if j < 0 else j
            continue
        if t.startswith("/*", i):
            j = t.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        c = t[i]
        if c in "\"'":
            j = i + 1
            while j < n and t[j] != c and t[j] != "\n":
                j += 2 if t[j] == "\\" else 1
            out.append(t[i:j + 1])
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def match_close(text, i, open_="(", close=")"):
    """Index of the bracket closing the one at text[i]."""
    d = 0
    for j in range(i, len(text)):
        if text[j] == open_:
            d += 1
        elif text[j] == close:
            d -= 1
            if d == 0:
                return j
    return len(text)


def kernel_starters(sword):
    """Brain function names that start a kernel action: RegisterFunction statements whose text calls StartAction<, or
    that register a C++ function whose body calls it. Returns {name: 'File.cpp' or 'File.cpp via Func'}."""
    sim = os.path.join(sword, "src", "libraries", "simulation")
    files = {}
    for r, _, fs in os.walk(sim):
        for f in fs:
            if f.endswith(".cpp"):
                files[f] = files.get(f, "") + strip_cpp(read(os.path.join(r, f)))
    funcs = set()
    for t in files.values():
        for m in re.finditer(r"\bStartAction\s*<", t):
            defs = list(CPP_DEF.finditer(t, 0, m.start()))
            if defs:
                funcs.add(defs[-1].group(1))
    funcs.discard("Register")
    names = {}
    for f, t in files.items():
        for m in re.finditer(r"\bRegister(?:Function|Method)\s*(?:<[^()]*?>\s*)?\(", t):
            stmt = t[m.end() - 1:match_close(t, m.end() - 1)]
            n = re.search(r'"(\w+)"', stmt)
            if not n:
                continue
            if re.search(r"\bStartAction\s*<", stmt):
                names[n.group(1)] = f
            else:
                via = [r for r in re.findall(r"&\s*(?:\w+::)*(\w+)", stmt) if r in funcs]
                if via:
                    names[n.group(1)] = f"{f} via {via[0]}"
    return names


def scan_integration_defs(sword):
    """integration.* definitions and Lua helpers in sw.sword integration Lua.
    Returns (defs {name without 'integration.': (file, kind, body)}, helpers {name: [(file, local?, body)]})."""
    res_dir = os.path.join(sword, "data", "app-data", "resources")
    paths = [os.path.join(res_dir, "Integration.lua")]
    idir = os.path.join(res_dir, "integration")
    paths += [os.path.join(idir, f) for f in sorted(os.listdir(idir)) if f.endswith(".lua")] if os.path.isdir(idir) else []
    defs, helpers = {}, defaultdict(list)
    for p in paths:
        if not os.path.isfile(p):
            continue
        label = os.path.relpath(p, res_dir).replace("\\", "/")
        t = blank_lua(read(p))
        for m in LUA_DEF.finditer(t):
            name = m.group(2) or m.group(3)
            rhs = m.group(4) or "function"
            if rhs.startswith("spark.startStopAction"):
                i = t.find("(", m.start(4))
                body, kind = t[m.end():match_close(t, i) + 1], "startStopAction"
            elif rhs == "function":
                line_end = t.find("\n", m.end())
                line_end = len(t) if line_end < 0 else line_end
                if re.search(r"\bend\s*,?\s*$", t[m.end():line_end]):
                    body = t[m.end():line_end]
                else:
                    e = re.compile(r"^end\b", re.M).search(t, m.end())
                    body = t[m.end():e.end() if e else len(t)]
                kind = "function"
            else:
                body, kind = rhs.strip(), "alias"
            if name.startswith("integration."):
                defs.setdefault(name[len("integration."):], (label, kind, body))
            elif "." not in name:
                helpers[name].append((label, bool(m.group(1)), body))
    return defs, helpers


def integration_closure(sword):
    """Kernel starters and what each integration.* function reaches.
    Returns (starters, idefs, helpers, resolve_integ, integ_starters, integ_direct, starters_of); empty when sword
    is None. starters_of(key, lua_body, file) gives the starters reachable from any Lua body."""
    have_sword = bool(sword) and os.path.isdir(os.path.join(sword, "src", "libraries", "simulation"))
    starters = kernel_starters(sword) if have_sword else {}
    idefs, helpers = scan_integration_defs(sword) if have_sword else ({}, {})

    def resolve_integ(name):
        parts = name.split(".")
        while parts:
            n = ".".join(parts)
            if n in idefs:
                return n
            parts.pop()
        return None

    memo = {}

    def starters_of(key, body, file, stack=()):
        """Kernel starters reachable from a Lua body: kernel tokens, helper calls, integration.* refs."""
        if key in memo:
            return memo[key]
        if key in stack:
            return set()
        stack = stack + (key,)
        found = set()
        for tok in set(KERNEL_TOKEN.findall(body)):
            if tok in starters:
                found.add(tok)
            elif tok in helpers:
                for f, _, b in helpers[tok]:
                    if b.strip() != tok:
                        found |= starters_of(("h", tok, f), b, f, stack)
        for call in set(re.findall(r"(?<![.:\w])([A-Za-z_]\w*)\s*\(", body)):
            if call in helpers and not KERNEL_TOKEN.fullmatch(call):
                for f, local, b in helpers[call]:
                    if not local or f == file:
                        found |= starters_of(("h", call, f), b, f, stack)
        for ref in set(MODEL_INTEG.findall(body)):
            r = resolve_integ(ref)
            if r:
                f, _, b = idefs[r]
                found |= starters_of(("i", r), b, f, stack)
        memo[key] = found
        return found

    integ_starters = {n: starters_of(("i", n), b, f) for n, (f, _, b) in idefs.items()}
    integ_direct = {n: {t for t in KERNEL_TOKEN.findall(b) if t in starters} for n, (f, _, b) in idefs.items()}
    return starters, idefs, helpers, resolve_integ, integ_starters, integ_direct, starters_of


def build_actions(repo, model, out, sword):
    dia5 = os.path.join(repo, "data", "data", "models", model, "decisional", "dia5")
    if not os.path.isdir(dia5):
        sys.exit(f"{dia5}: not found")
    ents = scan_decisional(dia5)
    rel = lambda e: os.path.relpath(e.path, dia5).replace("\\", "/")

    # --- sw.sword side: kernel starters and integration closure
    have_sword = bool(sword) and os.path.isdir(os.path.join(sword, "src", "libraries", "simulation"))
    starters, idefs, helpers, resolve_integ, integ_starters, integ_direct, _ = integration_closure(sword)

    def integ_names(text):
        outn = set()
        for ref in MODEL_INTEG.findall(text):
            if ref.startswith("ontology."):
                continue
            outn.add(resolve_integ(ref) or ref)
        return outn

    def kernel_of(inames):
        return set().union(*(integ_starters.get(n, set()) for n in inames)) if inames else set()

    # --- world classes: methods with bodies, startStopAction handlers
    classes = {n: e for n, e in ents.items() if e.kind == "class"}
    parent = {n: (XML_REFS["extends"].findall(XML_COMMENT.sub("", e.text)) or [None])[0] for n, e in classes.items()}

    def root_of(n):
        seen = set()
        while n in classes and n not in seen:
            seen.add(n)
            n = parent[n]
        return n

    world = {n: e for n, e in classes.items() if root_of(n) != "sparkle.TaskMetadata"}
    wm = defaultdict(list)  # method -> [dict]
    for n, e in sorted(world.items()):
        if not e.lua:
            continue
        t = blank_lua(read(e.path[:-4] + ".lua"))
        for name, form, vs, end in lua_top_keys(t):
            if name in LUA_SPECIAL:
                continue
            body = t[vs:end]
            d = {"cls": n, "form": form, "integ": integ_names(body), "calls": set(METHOD_CALL.findall(body)),
                 "handlers": {}}
            if form == "startStopAction":
                b = body.find("{")
                tbl = body[b:match_close(body, b, "{", "}") + 1] if b >= 0 else ""
                for h, _, hs, he in lua_top_keys(tbl):
                    if h in ("start", "started", "stop"):
                        d["handlers"][h] = integ_names(tbl[hs:he])
            wm[name].append(d)

    def is_ssa(m):
        return any(d["form"] == "startStopAction" for d in wm.get(m, []))

    cmemo = {}

    def method_closure(m, depth=2):
        """(integration names, startStopAction method names) reachable from method name m, by name (upper bound)."""
        k = (m, depth)
        if k in cmemo:
            return cmemo[k]
        cmemo[k] = (set(), set())
        integ, ssa = set(), set()
        for d in wm.get(m, []):
            integ |= d["integ"]
            if d["form"] == "startStopAction":
                ssa.add(m)
            if depth > 0:
                for c in d["calls"]:
                    if c != m and c in wm:
                        i2, s2 = method_closure(c, depth - 1)
                        integ |= i2
                        ssa |= s2
        cmemo[k] = (integ, ssa)
        return cmemo[k]

    # --- skills
    skills = {n: e for n, e in ents.items() if e.kind == "skill"}
    resources = {n: e for n, e in ents.items() if e.kind == "resource"}
    rows = {}
    for n, e in sorted(skills.items()):
        body = XML_COMMENT.sub("", e.text)
        rs = re.search(r"<resources>(.*?)</resources>", body, re.S)
        res = re.findall(r'<resource\s[^>]*?name="([^"]+)"\s+variable="([^"]+)"', rs.group(1)) if rs else []
        is_action = "/skills/actions/" in rel(e)
        if not (is_action or res):
            continue
        L = blank_lua(read(e.path[:-4] + ".lua")) if e.lua else ""
        nodes = []  # (wrapped by spark.action, {key: span text})
        for nm in re.finditer(r"\b(?:rootnode|node\s*\"\")\s*\)?\s*\{", L):
            b = nm.end() - 1
            tbl = L[b:match_close(L, b, "{", "}") + 1]
            wrapped = bool(re.search(r"\bspark\.action\s*\(\s*$", L[max(0, nm.start() - 40):nm.start()]))
            nodes.append((wrapped, {k: tbl[vs:ve] for k, _, vs, ve in lua_top_keys(tbl)}))
        node = bool(nodes)
        kd = {}
        for _, keys in nodes:
            for k, v in keys.items():
                kd[k] = kd.get(k, "") + v
        acquired = set(re.findall(r"skill\.resources\.(\w+)", kd.get("acquire", "")))
        spark_action = any(wr for wr, _ in nodes)
        idle_acquire = any("acquire" in k and "select" not in k and not wr for wr, k in nodes)
        callbacks = [k for k in LIFECYCLE if k in kd]
        calls = {c for c in METHOD_CALL.findall(L) if c in wm}
        direct_integ = integ_names(L)
        integ, ssa = set(direct_integ), set()
        for c in calls:
            i2, s2 = method_closure(c)
            integ |= i2
            ssa |= s2
        ssa_direct = {c for c in calls if is_ssa(c) and not ONCE_IDIOM.search(c)}
        ssa_all = {s for s in ssa if not ONCE_IDIOM.search(s)}
        pattern = []
        if spark_action:
            pattern.append("spark.action")
        if ssa_direct:
            pattern.append("startStopAction")
        elif ssa_all:
            pattern.append("startStopAction (deeper)")
        if not spark_action and ("deselect" in kd or "destroy" in kd):
            pattern.append("select+deselect/destroy")
        if not spark_action and "activate" in kd and "select" not in kd:
            pattern.append("activate-only")
        if not pattern:
            pattern.append("instantaneous" if "select" in kd else "no node" if not node else
                           "create-only" if callbacks == ["create"] else "other")
        start_k = kernel_of(integ_names(kd["start"])) if spark_action and "start" in kd else set()
        rows[n] = {"e": e, "action": is_action, "res": res, "acquired": acquired, "fb": FEEDBACK.findall(body),
                   "bt": (BODY_TYPE.findall(body) or [""])[0], "callbacks": callbacks, "pattern": pattern,
                   "ssa_direct": ssa_direct, "ssa_all": ssa_all, "direct_integ": direct_integ,
                   "kernel_direct": kernel_of(direct_integ) | set().union(
                       *(kernel_of(method_closure(c, 0)[0]) for c in calls)) if calls else kernel_of(direct_integ),
                   "kernel": kernel_of(integ), "spark_action": spark_action, "has_stop": "stop" in kd,
                   "start_kernel": start_k, "node": node, "idle_acquire": idle_acquire, "nodes": len(nodes)}

    res_acq = defaultdict(set)
    res_decl_only = defaultdict(set)
    for n, r in rows.items():
        for rn, var in r["res"]:
            (res_acq if var in r["acquired"] else res_decl_only)[rn].add(n)

    # --- callers of startStopAction methods, conditions using them, capacity overrides
    ssa_callers = defaultdict(set)
    cond_uses = defaultdict(set)
    capacity_hits = []
    by_path = {os.path.normcase(e.path[:-4] + ".lua"): e.name for e in ents.values() if e.lua}
    model_integ_users = defaultdict(set)
    for root_, _, fs in os.walk(dia5):
        for f in fs:
            p = os.path.join(root_, f)
            if f.endswith(".xml") and "initial-capacity" in read(p):
                capacity_hits.append(os.path.relpath(p, dia5).replace("\\", "/"))
            if not f.endswith(".lua"):
                continue
            label = by_path.get(os.path.normcase(p), os.path.relpath(p, dia5).replace("\\", "/"))
            t = blank_lua(read(p))
            if "setResourceCapacity" in t:
                capacity_hits.append(label)
            for c in set(METHOD_CALL.findall(t)):
                if is_ssa(c):
                    ssa_callers[c].add(label)
            for cond in re.findall(r"\b(?:if|elseif|while)\b(.*?)\b(?:then|do)\b", t):
                for c in METHOD_CALL.findall(cond):
                    if c in wm and all(d["form"] == "startStopAction" and "started" not in d["handlers"]
                                       for d in wm[c]):
                        cond_uses[c].add(label)
            for i in integ_names(t):
                model_integ_users[i].add(label)

    # --- header
    branch, head, date = repo_stamp(repo)
    dirty = git(repo, "status", "--porcelain", "--", f"data/data/models/{model}")
    L = []
    w = L.append
    w("# sw.models action index (generated)\n")
    w(f"Generated by `forge/tools/build_models_index.py` from sw.models **`{branch}` @ `{head}`** ({date}), model "
      f"`{model}`{' — **working tree had uncommitted changes in this model**' if dirty and dirty != '?' else ''}. "
      f"Built {datetime.date.today()}." + (
          " Kernel side (integration Lua, `StartAction<` registrations) read from sw.sword **`%s` @ `%s`** (%s).\n"
          % repo_stamp(sword) if have_sword else
          " **sw.sword not found: integration definitions and kernel starters are missing.**\n"))
    w("**Stale check:** if `git -C C:/MASARepo/sw.models rev-parse --short=10 HEAD` differs from the commit above, "
      "treat this file as a hint only and confirm with grep. **Grep this file, never read it whole.**\n")
    w("**Upper bounds.** A Lua call `x:m(` is resolved by method name to every world class defining `m`, two calls deep "
      "(skill → `meKnowledge:moveTo` → `destination:reachIt`), so \"via methods\" columns can list integration "
      "functions and kernel starters that a given skill never reaches. Direct columns (the skill's own Lua) are exact.\n")
    w("**Kernel starters** = brain functions registered (sw.sword `src/libraries/simulation`) with a statement that calls "
      "`StartAction<`, or with a C++ function whose body does. Not every `*Start*` name is one (`DEC_StartFragOrder`) "
      "and some have no `Start` (`DEC_ChangeEquipmentActivation`). An integration function reaches a starter through its "
      "body, Lua helpers (`DECFunctionWrappers.lua`, file locals) and other `integration.*` functions.\n")
    w("**Patterns** (a skill may show several): `spark.action` (node with start / started / stop) · `startStopAction` "
      "(calls a world method built with `spark.startStopAction`; `(deeper)` = only through another method; the "
      "`*Once*` report/message helpers are ignored) · `select+deselect/destroy` (hand-written stop in `deselect` / "
      "`destroy`) · `activate-only` (no `select`: consumes no resource) · `instantaneous` (`select` only). Semantics: "
      "THEMES \"Resources & actions\".\n")

    n_action = sum(1 for r in rows.values() if r["action"])
    n_ssa = sum(1 for v in wm.values() for d in v if d["form"] == "startStopAction")
    n_ssa_names = sum(1 for v in wm.values() if any(d["form"] == "startStopAction" for d in v))
    pat = Counter(p for r in rows.values() for p in r["pattern"])
    w("## Counts\n")
    w("| Item | Count |\n|---|---|")
    w(f"| resources | {len(resources)} |\n| action skills (`*/skills/actions/`) | {n_action} |\n"
      f"| other skills with `<resources>` | {len(rows) - n_action} |\n"
      f"| `startStopAction` world methods (definitions / names) | {n_ssa} / {n_ssa_names} |\n"
      f"| kernel action starters (sw.sword) | {len(starters)} |\n"
      f"| integration functions reaching a starter | {sum(1 for v in integ_starters.values() if v)} |")
    for p, c in sorted(pat.items()):
        w(f"| pattern `{p}` | {c} |")
    w("")

    # --- 1. resources
    w("## 1. Resources\n")
    w("Capacity: 1 for every resource (directai default), unless a role `<resource initial-capacity>` or "
      "`spark.setResourceCapacity` changes it. Found in this model: " +
      (code(capacity_hits) if capacity_hits else "**none**, so every resource is held by one skill at a time") +
      ". Skills acquiring the same resource cannot run (be selected) at the same time on one agent; the winner is "
      "re-decided each tick by activity.\n")
    w("| Resource | File | Acquired by (Lua `acquire`) | Declared in XML only |\n|---|---|---|---|")
    for n, e in sorted(resources.items()):
        w(f"| `{n}` | `{rel(e)}` | {code(res_acq[n]) or '—'} | {code(res_decl_only[n]) or '—'} |")
    w("")

    # --- 2. skills
    w("## 2. Action skills and skills with resources\n")
    w("Kind: `action` = under `*/skills/actions/`, `res` = elsewhere but declares `<resources>`. Resources: variable "
      "names, *italic* = declared in XML, never acquired. Callbacks: keys present in any node of the skill (`rootnode` and "
      "`node \"…\"`; the node count when more than one). startStopAction: world methods "
      "called directly (deeper ones in parentheses). Kernel: starters via the skill's own integration calls and the "
      "methods it calls directly; \"+ deeper\" = more via 2-level method resolution.\n")
    w("| Skill | Kind | Body type | Resources | Feedbacks | Pattern | Callbacks | startStopAction | Integration (own Lua) "
      "| Kernel starters |\n|---|---|---|---|---|---|---|---|---|---|")
    for n, r in sorted(rows.items()):
        resv = ", ".join(f"`{v}`" if v in r["acquired"] else f"*`{v}`*" for _, v in r["res"]) or "—"
        deeper = sorted(r["ssa_all"] - r["ssa_direct"])
        ssa_txt = (code(r["ssa_direct"], 8) or "—") + (f" ({code(deeper, 5)})" if deeper else "")
        kd = r["kernel_direct"]
        more = r["kernel"] - kd
        k_txt = (code(kd, 6) or "—") + (f" + deeper {code(more, 4)}" if more else "")
        nodes_txt = f" ({r['nodes']} nodes)" if r["nodes"] > 1 else ""
        w(f"| `{n}` | {'action' if r['action'] else 'res'} | `{r['bt'].split('.')[-1] or '—'}` | {resv} | "
          f"{', '.join(r['fb']) or '—'} | {' · '.join(r['pattern'])} | {' '.join(r['callbacks']) or '—'}{nodes_txt} | {ssa_txt} | "
          f"{code(r['direct_integ'], 8) or '—'} | {k_txt} |")
    w("")

    # --- 3. startStopAction methods
    w("## 3. `spark.startStopAction` world methods\n")
    w("Handlers present and the integration functions each calls (`started` may be a bare `integration.X`). Kernel = "
      "starters reached from the handlers. Callers = model Lua calling `:m(` (by name: upper bound; a name defined as a "
      "plain function in another class gets the same callers). `*Once*` = the \"do once while called\" idiom.\n")
    w("| Class | Method | start | started | stop | Kernel starters | Callers |\n|---|---|---|---|---|---|---|")
    for m in sorted(wm):
        for d in wm[m]:
            if d["form"] != "startStopAction":
                continue
            h = d["handlers"]
            cell = lambda k: (code(h[k], 5) or "(Lua only)") if k in h else "—"
            kern = kernel_of(set().union(*h.values())) if h else set()
            plain = [x["cls"] for x in wm[m] if x["form"] != "startStopAction"]
            w(f"| `{d['cls']}` | `{m}`{' (also plain in ' + code(plain, 3) + ')' if plain else ''} | {cell('start')} | "
              f"{cell('started')} | {cell('stop')} | {code(kern, 5) or '—'} | "
              f"{len(ssa_callers[m])}: {code(ssa_callers[m], 6) or '—'} |")
    w("")

    # --- 4. integration -> kernel
    w("## 4. Integration functions that start kernel actions (those the model calls)\n")
    w("Direct = a starter token in the function's own body (or an alias of one); otherwise reached through helpers or "
      "other `integration.*` functions. `update*` / `compute*` names that reach a starter (re)start an action when called.\n")
    w("| integration | Defined in | Kind | Kernel starters | Direct? | Model users |\n|---|---|---|---|---|---|")
    for i in sorted(model_integ_users):
        if not integ_starters.get(i):
            continue
        f, kind, _ = idefs[i]
        flag = " **(re)starts**" if re.match(r"(update|uptate|compute)", i.split(".")[-1]) else ""
        w(f"| `integration.{i}`{flag} | `{f}` | {kind} | {code(integ_starters[i], 5)} | "
          f"{'yes' if integ_direct[i] else 'via'} | {len(model_integ_users[i])}: {code(model_integ_users[i], 4)} |")
    w("")

    # --- 5. anomalies
    w("## 5. Anomalies\n")
    dno = sorted((n, v) for n, r in rows.items() for _, v in r["res"] if v not in r["acquired"])
    w(f"### 5.1 Resource declared in XML, never acquired in Lua ({len(dno)})\n")
    w("No directai diagnostic; the resource is still created. Often a parent whose child skill acquires it.\n")
    w("\n".join(f"- `{n}`: `{v}`" for n, v in dno) + "\n")
    nosel = sorted(n for n, r in rows.items() if r["idle_acquire"])
    w(f"### 5.2 `acquire` on a node without `select` ({len(nosel)})\n")
    w("Only selectable nodes consume resources, so this `acquire` does nothing.\n")
    w(("\n".join(f"- `{n}`" for n in nosel) or "None.") + "\n")
    w(f"### 5.3 startStopAction without `started` used as a condition ({len(cond_uses)})\n")
    w("Every definition of the method lacks `started`, so the call returns nothing and the condition is never true.\n")
    w(("\n".join(f"- `{m}` in {code(u, 6)}" for m, u in sorted(cond_uses.items())) or "None.") + "\n")
    nostop = sorted(n for n, r in rows.items() if r["spark_action"] and r["start_kernel"] and not r["has_stop"])
    w(f"### 5.4 `spark.action` whose `start` reaches a kernel starter but has no `stop` ({len(nostop)})\n")
    w("The kernel does not stop actions on mission change; without `stop` the action keeps running.\n")
    w(("\n".join(f"- `{n}`: {code(rows[n]['start_kernel'])}" for n in nostop) or "None.") + "\n")
    never = sorted(m for m in wm if is_ssa(m) and not ssa_callers[m])
    w(f"### 5.5 startStopAction methods never called as `:m(` in model Lua ({len(never)})\n")
    w((code(never) or "None.") + "\n")

    with open(os.path.join(out, "action_index.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(L))
    return {"resources": len(resources), "action skills": n_action, "other resource skills": len(rows) - n_action,
            "ssa defs/names": f"{n_ssa}/{n_ssa_names}", "spark.action": pat.get("spark.action", 0),
            "kernel starters": len(starters), "integ->starter": sum(1 for v in integ_starters.values() if v)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("out")
    ap.add_argument("--model", default="ada")
    ap.add_argument("--only", default="decisional,ontology,actions")
    ap.add_argument("--sword", default="C:/MASARepo/sw.sword", help="sw.sword checkout (integration Lua, functorsBM)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    only = set(a.only.split(","))
    if "decisional" in only:
        counts, dbs = build_decisional(a.repo, a.model, a.out)
        print("decisional:", dict(counts), "| DBs:", ", ".join(f"{d['name']} ({len(d['missions'])} missions)" for d in dbs))
    if "ontology" in only:
        print("ontology:", build_ontology(a.repo, a.model, a.out, a.sword if os.path.isdir(a.sword) else None))
    if "actions" in only:
        print("actions:", build_actions(a.repo, a.model, a.out, a.sword if os.path.isdir(a.sword) else None))


if __name__ == "__main__":
    main()
