"""Build the sw.models navigator indexes from a sw.models checkout (the branch currently checked out).

Usage (from the forge root):
    python -I tools/build_models_index.py <sw.models root> <out dir> [--model ada] [--only decisional,ontology,actions]

Outputs (in <out dir>):
  decisional_index.md  entities (skills, tasks, queries, roles, resources, classes, interfaces) with their wiring,
                       missions per physical DB, decisional models, frag orders, unresolved references
  ontology_index.md    (phase B, not yet implemented)
  action_index.md      (phase C, not yet implemented)

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
        m = DESC.search(text)
        if m and ("<parameter " not in text[: m.start()]):
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
      "`spark.metadata-class`, **str** = XML `<string>` value (commander task lists), **lua** = dotted name in Lua "
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("out")
    ap.add_argument("--model", default="ada")
    ap.add_argument("--only", default="decisional,ontology,actions")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    only = set(a.only.split(","))
    if "decisional" in only:
        counts, dbs = build_decisional(a.repo, a.model, a.out)
        print("decisional:", dict(counts), "| DBs:", ", ".join(f"{d['name']} ({len(d['missions'])} missions)" for d in dbs))
    for name in ("ontology", "actions"):
        if name in only:
            print(f"{name}: not implemented yet (phase {'B' if name == 'ontology' else 'C'})")


if __name__ == "__main__":
    main()
