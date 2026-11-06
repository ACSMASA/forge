"""Static checkpoint (save / resume) lint for skill Lua in a sw.models model (WORKING TREE). Heuristic: every finding
is an upper bound to confirm by reading the code. Rules and fixes: docs/sw.models/CHECKPOINTS.md §3-§4.

Usage (from the forge root):
    python -I tools/checkpoint_lint.py (--skill NAME | --task TASK | --fragorder NAME | --all | --file PATH.lua)
                                       [--rules r1,r2] [--model ada] [--root C:/MASARepo/sw.models] [--summary]

--task walks the task's stage behaviors, then skill links, recursively (skills only; queries keep no state).
--fragorder (a frag-order name or dia-type, every DB) lints the task(s) the order starts, as --task, and the skills
that read it (kind (b) readers and the callers of its poll helpers: docs/sw.models/FRAGORDERS.md §2, §7).
Rules:
  lost-progress   self.x set to a flag / counter / timer in activate or a callback (not in create/start/select/started,
                  which rerun on resume), x never persisted in the skill state
  unhashable      getSkillState in a skill whose parameter types have no `hash` (runtime error)
  key-order       getSkillState keyed by >= 2 parameters (pairs order may differ after resume)
  or-true         getSkillState(...).x or true (a saved false becomes true)
  stored-object   a knowledge object, function, closure or action id written into the skill state
  no-clear        skill state written but never cleared in destroy (role default-task skills exempt)
  side-effect     issueMission / createEmptyUnit / create* / give / transfer… in create or start, no state read first
  re-report       displayReport* / reportFunction / sendMessage* in create or start, no state read first (duplicates)
  table-eq        x == {} / x ~= {} (always false / true)
  lost-timer      utilities.waitInMin / waitInSec keep their start time in a Lua table (knowledge[myself]): the full
                  delay restarts after resume
  order-effect-not-persisted  a function reads a frag order (get*FragOrder, getFirstFragOrderFromType,
                  meKnowledge:getFragOrder, needToWaitForCoordination) and sets self.x afterwards, x never persisted:
                  the order is consumed (and the queue is not saved), so the effect is lost on resume
  fragorder-param-state  getSkillState in a skill with a FragOrder parameter (FragOrder classes have no hash)
  frago-task-lost (--fragorder only, a NOTE)  the order starts a task: it is not resumed after a checkpoint unless
                  one of the CHECKPOINTS.md §7 fixes is applied
Out of scope: state kept in world objects (object[myself], startStopAction state) and in integration functions.
Exit code 0 (findings are warnings).
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import missionlib as ml  # noqa: E402
from build_models_index import lua_class_methods, scan_decisional, link_refs, read  # noqa: E402

RULES = ["lost-progress", "unhashable", "key-order", "or-true", "stored-object", "no-clear", "side-effect", "re-report",
         "table-eq", "lost-timer", "order-effect-not-persisted", "fragorder-param-state", "frago-task-lost"]
# Frag-order reader APIs (FRAGORDERS.md §7): their result is a consumed order, absent after resume.
READER = re.compile(r"\b(get\w*FragOrder\w*|getFirstFragOrderFromType|needToWaitForCoordination)\s*[({]")
# Timer helpers that keep their start time in Lua (int/ToolsFunctions.lua: knowledge[myself].tempsDebut), lost on resume.
LUA_TIMER = re.compile(r"\b(?:utilities\.)?(waitInMin|waitInSec)\s*\(")
# Functions that rerun after a resume (node recreated, action restarted): what they set is rebuilt, not lost.
RERUN_FN = {"create", "start", "started", "select", "deselect", "stop", "destroy"}
LIFECYCLE_FN = re.compile(r"\b([A-Za-z_]\w*)\s*=\s*function\s*\(")
STATE_CALL = re.compile(r"\b(?:scripts|integration)\.(getSkillState|setSkillState|setSkillStateElement|setSkillStatePointElement|"
                        r"getSkillStatePointElement|clearSkillState)\s*\(|\bgetPersistentState\s*\(")
TIME_EXPR = re.compile(r"getRealTime|getSimulationTime|DEC_TempsReel|DEC_TempsSim|\bgetTime\s*\(")
SIDE_EFFECT = re.compile(r"(?::|\.)\s*(issueMission\w*|createEmptyUnit|createObject\w*|createPlannedWork\w*|createUnit\w*|"
                         r"magicMove\w*|teleport\w*|giveEquipment\w*|transfer\w*|destroy\w*)\s*\(")
REPORT = re.compile(r"(?::|\.)\s*(displayReport\w*|sendMessage\w*|SendMessage\w*|sendReport\w*|send\w*MessageTo\w*)\s*\(|"
                    r"\b(reportFunction)\s*\(")


# Lua text helpers live in missionlib (shared with the frag-order parser); kept under their old names here.
strip_comments = ml.lua_strip_comments
mask_strings = ml.lua_mask_strings
block_end = ml.lua_block_end
STRING = ml.LUA_STRING


def functions(blank):
    """[(name, start, end)] of every `name = function(` in the file (any depth)."""
    out = []
    for m in LIFECYCLE_FN.finditer(blank):
        f = blank.find("function", m.start())
        out.append((m.group(1), f, block_end(blank, f)))
    return out


def lineno(text, pos):
    return text.count("\n", 0, pos) + 1


class Hashability:
    """Can a parameter of a given world type be keyed by getPersistentKey? The value at runtime is an instance of a
    concrete class: the type itself if it is a non-abstract class, its subclasses, or an interface's implementers (and
    their subclasses). Hashable when every such concrete class has a `hash` method (own or inherited)."""

    def __init__(self, ents):
        self.ents = ents
        self.own, self.abstract, self.subs, self.impl = {}, {}, {}, {}
        for e in ents.values():
            if e.kind == "class":
                lua = e.path[:-4] + ".lua"
                self.own[e.name] = os.path.isfile(lua) and "hash" in lua_class_methods(read(lua))
                root = re.search(r"<class\b[^>]*>", e.text)
                self.abstract[e.name] = bool(root and 'abstract="true"' in root.group(0))
                for sup in e.uses.get("extends", ()):
                    self.subs.setdefault(sup, set()).add(e.name)
                for i in e.uses.get("implements", ()):
                    self.impl.setdefault(i, set()).add(e.name)
            elif e.kind == "interface":
                for sup in e.uses.get("extends", ()):
                    self.subs.setdefault(sup, set()).add(e.name)
        self.cache = {}

    def has_hash(self, cls, seen=()):
        if self.own.get(cls):
            return True
        e = self.ents.get(cls)
        return bool(e) and any(self.has_hash(s, seen + (cls,)) for s in e.uses.get("extends", ()) if s not in seen)

    def concrete(self, name, seen=()):
        if name in seen:
            return set()
        e = self.ents.get(name)
        if e is None:
            return set()
        out = set()
        if e.kind == "class" and not self.abstract.get(name):
            out.add(name)
        nxt = set(self.subs.get(name, ()))
        if e.kind == "interface":
            nxt |= self.impl.get(name, set())
        for n in nxt:
            out |= self.concrete(n, seen + (name,))
        return out

    def missing(self, name):
        """Concrete classes a value of this type can be that have no hash."""
        return {c for c in self.concrete(name) if not self.has_hash(c)}

    def of(self, name):
        """True (hashable), False (some concrete class has no hash), None (unknown type / no concrete class)."""
        if name not in self.cache:
            cs = self.concrete(name)
            self.cache[name] = None if not cs else not any(not self.has_hash(c) for c in cs)
        return self.cache[name]


def skill_param_types(xml_text):
    """{param name: [object types]} from a skill XML (scalar params map to [])."""
    out = {}
    body = re.search(r"<parameters>(.*?)</parameters>", xml_text, re.S)
    for m in re.finditer(r'<parameter\s+name="([^"]+)"[^>]*>(.*?)</parameter>', body.group(1) if body else "", re.S):
        out[m.group(1)] = re.findall(r'<object\s[^>]*?type="([^"]+)"', m.group(2))
    return out


def persisted_keys(code):
    """(uses skill state?, keys read or written in it, aliases of getSkillState( self )) from comment-free Lua."""
    uses_state = bool(STATE_CALL.search(code))
    aliases = set(re.findall(r"\b(\w+)\s*=\s*(?:scripts|integration)\.getSkillState\s*\(\s*self\s*\)", code))
    keys = set(re.findall(r"(?:setSkillStateElement|setSkillStatePointElement|getSkillStatePointElement)\s*\(\s*self\s*,\s*[\"'](\w+)[\"']", code))
    keys |= set(re.findall(r"getSkillState\s*\(\s*self\s*\)\s*\.\s*(\w+)", code))
    for a in aliases:
        keys |= set(re.findall(rf"\b{re.escape(a)}\s*\.\s*(\w+)", code))
    return uses_state, keys, aliases


def lint_skill(path, xml_text, hashes, rules, model_root, keep_state=frozenset()):
    """Return [(rule, file, line, message)] for one skill Lua file."""
    src = read(path)
    code = strip_comments(src)
    blank = mask_strings(code)
    rel = os.path.relpath(path, model_root).replace("\\", "/")
    out = []

    def add(rule, pos_text, pos, msg):
        if rule in rules:
            out.append((rule, rel, lineno(pos_text, pos), msg))

    uses_state, keys, aliases = persisted_keys(code)
    writes_state = bool(re.search(r"setSkillState(?:Element|PointElement)?\s*\(", code)) or any(
        re.search(rf"\b{re.escape(a)}\s*\.\s*\w+\s*=(?!=)", code) for a in aliases) or bool(
        re.search(r"getSkillState\s*\(\s*self\s*\)\s*\.\s*\w+\s*=(?!=)", code))
    fns = functions(blank)

    # lost-progress (one finding per field; a flag set both true and false in activate is recomputed every tick)
    fields = {}  # field -> [kind, first pos, {functions}, {literal values set in activate}]
    for name, a, b in fns:
        if name in RERUN_FN:
            continue
        for m in re.finditer(r"\bself\.(\w+)\s*=(?!=)\s*([^\n]*)", code[a:b]):
            field, rhs = m.group(1), m.group(2).strip()
            if field in keys or field in ("params",):
                continue
            kind = ("flag" if re.match(r"(true|false)\b", rhs) else
                    "counter" if re.match(rf"self\.{field}\s*[+-]", rhs) else
                    "timer" if TIME_EXPR.search(rhs) else None)
            if not kind:
                continue
            f = fields.setdefault(field, [kind, a + m.start(), set(), set()])
            f[2].add(name)
            if name == "activate" and kind == "flag":
                f[3].add(rhs.split()[0].rstrip(",;"))
    rerun_vals = {}  # field -> (function, value) set by a function that reruns on resume (create, start...)
    for name, a, b in fns:
        if name in RERUN_FN:
            for m in re.finditer(r"\bself\.(\w+)\s*=(?!=)\s*([^\n]*)", code[a:b]):
                rerun_vals.setdefault(m.group(1), (name, m.group(2).strip().rstrip(",;")[:40]))
    for field, (kind, pos, where, act_vals) in fields.items():
        if {"true", "false"} <= act_vals:
            continue
        hint = (f"; {rerun_vals[field][0]} resets it to {rerun_vals[field][1]} on resume: fine if that initial value is "
                f"right after resume" if field in rerun_vals else "")
        add("lost-progress", code, pos, f"self.{field} ({kind}, set in {', '.join(sorted(where))}) is not persisted: lost on resume{hint}")
    # unhashable / key-order
    if uses_state and xml_text is not None:
        ptypes = skill_param_types(xml_text)
        m0 = STATE_CALL.search(code)
        for p, types in ptypes.items():
            for ty in types:
                h = hashes.of(ty)
                if h is False:
                    missing = hashes.missing(ty)
                    detail = (f"implementers without hash: {', '.join(sorted(missing)[:6])}" + (" …" if len(missing) > 6 else "")
                              if missing else "no hash method")
                    add("unhashable", code, m0.start(), f"skill state keyed by parameter '{p}' of type {ty} ({detail}): getPersistentKey errors when such a value is passed")
                elif h is None:
                    add("unhashable", code, m0.start(), f"skill state keyed by parameter '{p}' of type {ty}: hash unknown (not a model class, or an interface without implementers)")
        if len(ptypes) >= 2:
            add("key-order", code, m0.start(), f"skill state keyed by {len(ptypes)} parameters {sorted(ptypes)}: the key follows pairs order, which may differ after resume (stock Lua 5.2 hash seed; CHECKPOINTS.md §3)")
    # or-true
    for m in re.finditer(r"getSkillState\s*\([^)]*\)\s*\.\s*\w+\s+or\s+true\b", code):
        add("or-true", code, m.start(), "`or true` on a persisted value: a saved false becomes true (test ~= nil)")
    for al in aliases:
        for m in re.finditer(rf"\b{re.escape(al)}\s*\.\s*\w+\s+or\s+true\b", code):
            add("or-true", code, m.start(), "`or true` on a persisted value: a saved false becomes true (test ~= nil)")
    # stored-object
    bad_val = re.compile(r"^\s*(self\.params\.\w+|meKnowledge|myself|function\b|CreateKnowledge|[\w.]*[Aa]ction[Ii][Dd]\w*|"
                         r"self\.\w*[Aa]ction\w*|\w+:\w+\(\s*\)\s*$)")
    for m in re.finditer(r"setSkillStateElement\s*\(\s*self\s*,\s*[\"'](\w+)[\"']\s*,([^\n]*)", code):
        key, val = m.group(1), m.group(2).rstrip().rstrip(")").strip()
        if bad_val.search(val) and not re.search(r":get(ID|Id|Position|Name)\w*\(", val) or re.search(r"[Aa]ction[Ii][Dd]", key):
            add("stored-object", code, m.start(), f"skill state '{key}' = {val[:60]}: store plain data (ids, XY, numbers), not objects, closures or action ids")
    targets = [r"getSkillState\s*\(\s*self\s*\)"] + [re.escape(al) for al in aliases]
    for tg in targets:
        for m in re.finditer(rf"\b{tg}\s*\.\s*(\w+)\s*=(?!=)\s*([^\n]*)", code):
            key, val = m.group(1), m.group(2).strip()
            if (bad_val.search(val) and not re.search(r":get(ID|Id|Position|Name)\w*\(", val)) or re.search(r"[Aa]ction[Ii][Dd]", key):
                add("stored-object", code, m.start(), f"skill state '{key}' = {val[:60]}: store plain data (ids, XY, numbers), not objects, closures or action ids")
    # no-clear
    skill_name = re.search(r'<skill\s[^>]*?name="([^"]+)"', xml_text or "")
    if writes_state and not (skill_name and skill_name.group(1) in keep_state):
        destroys = [(a, b) for n, a, b in fns if n == "destroy"]
        if not any(re.search(r"clearSkillState\s*\(", code[a:b]) for a, b in destroys):
            m0 = re.search(r"setSkillState(?:Element|PointElement)?\s*\(|getSkillState", code)
            add("no-clear", code, m0.start() if m0 else 0, "skill state written but not cleared in destroy: the next run with equal parameters starts from old data")
    # side-effect
    for name, a, b in fns:
        if name not in ("create", "start"):
            continue
        body = code[a:b]
        seen = set()
        for rule, rx, what in (("side-effect", SIDE_EFFECT, "repeated on resume"),
                               ("re-report", REPORT, "sent again on resume")):
            for se in rx.finditer(body):
                call = se.group(1) or se.group(se.lastindex)
                if call in seen or STATE_CALL.search(body[:se.start()]):
                    continue
                seen.add(call)
                add(rule, code, a + se.start(), f"{call} in {name} with no persisted guard read before it: {what}")
    # lost-timer
    for m in LUA_TIMER.finditer(code):
        add("lost-timer", code, m.start(), f"{m.group(1)} keeps its start time in a Lua table (int/ToolsFunctions.lua): the whole delay restarts after resume; persist an absolute utilities.getRealTime() deadline instead")
    # order-effect-not-persisted (one finding per field)
    seen_fields = set()
    for name, a, b in fns:
        for rm in READER.finditer(code, a, b):
            # the value the reader returns: `local v = …reader(` / `self.v = …reader(`; the effect is what is set in the
            # `if v …` / `if …reader(…)` block that follows, or computed from v
            ls = code.rfind("\n", a, rm.start()) + 1
            lhs = re.match(r"\s*(?:local\s+)?((?:self\.)?\w+)\s*=", code[ls:rm.start()])
            var = lhs.group(1) if lhs else None
            spans = []
            if var:
                for im in re.finditer(rf"\bif\b[^\n]*\b{re.escape(var)}\b", blank[rm.end():b]):
                    s0 = rm.end() + im.start()
                    spans.append((s0, min(block_end(blank, s0), b)))
            else:
                im = re.search(r"\bif\b", blank[ls:rm.start()])
                if im:
                    s0 = ls + im.start()
                    spans.append((s0, min(block_end(blank, s0), b)))
            cands = []
            for s0, s1 in spans:
                cands += [(m, s0) for m in re.finditer(r"\bself\.(\w+)\s*=(?!=)", code[s0:s1])]
            if var:
                cands += [(m, rm.end()) for m in re.finditer(rf"\bself\.(\w+)\s*=(?!=)[^\n]*\b{re.escape(var)}\b", code[rm.end():b])]
            for m, base in cands:
                field = m.group(1)
                if field in keys or field in seen_fields or field == "params" or f"self.{field}" == var:
                    continue
                seen_fields.add(field)
                add("order-effect-not-persisted", code, base + m.start(),
                    f"self.{field} is set after reading a frag order ({rm.group(1)}, line {lineno(code, rm.start())}) but "
                    "not persisted: the order is consumed and the queue is not saved, so the effect is lost on resume; "
                    "persist the derived value (CHECKPOINTS.md §4, ManageDiving targetDepth)")
    # fragorder-param-state
    if uses_state and xml_text is not None:
        fparams = [p for p, tys in skill_param_types(xml_text).items() if any(ty.rsplit(".", 1)[-1] == "FragOrder" for ty in tys)]
        m0 = re.search(r"getSkillState\s*\(", code)
        if fparams and m0:
            add("fragorder-param-state", code, m0.start(), f"getSkillState in a skill with FragOrder parameter(s) {fparams}: FragOrder classes have no hash, getPersistentKey errors (CHECKPOINTS.md §3)")
    # table-eq
    for m in re.finditer(r"[=~]=\s*\{\s*\}", code):
        add("table-eq", code, m.start(), "comparison with a new table {} is always false (==) / true (~=): use next( x ) == nil")
    return out


def closure(ents, start):
    """Skills reachable from an entity through behavior / link edges (tasks included in the walk)."""
    seen, stack = set(), [start]
    while stack:
        n = stack.pop()
        if n in seen or n not in ents:
            continue
        seen.add(n)
        e = ents[n]
        for via in ("behavior", "link"):
            stack.extend(e.uses.get(via, ()))
    return [ents[n] for n in sorted(seen) if ents[n].kind == "skill"]


def fragorder_targets(a, ents, notes):
    """Skills to lint for a frag order (the started tasks' closure + the readers), and frago-task-lost notes."""
    model = ml.load_model(a.root, a.model)
    skills, found = {}, 0
    own = fragorder_targets.own = set()  # files that name this order (its readers): findings elsewhere are reused skills
    for db in model.dbs.values():
        for fo in db.fragorders:
            if a.fragorder not in (fo.name, fo.dia_type):
                continue
            found += 1
            info = model.fragorder_info(db, fo)
            where = (model.rel(db.missions_path), fo.line)
            notes.append((*where, f"{db.name} \"{fo.name}\": kind {info.kind}, checkpoint class: {info.checkpoint_class} (CHECKPOINTS.md §7.4)"))
            if info.init is not None and info.init.myself:
                notes.append((model.rel(model.fragorder_lua().path), info.init.line,
                              f"the {fo.dia_type} initializer writes myself.{', myself.'.join(info.init.myself)}: Lua-only, lost on resume (mirror it in the ODB / world or accept the loss)"))
            own.update(r for r, _ in info.readers + info.commander_uses)
            own.update(info.helper_callers)
            for tn in info.tasks:
                if tn in ents:
                    for e in closure(ents, tn):
                        skills[e.name] = e
                    notes.append((*where, f"{db.name} \"{fo.name}\" (kind {info.kind}) starts {tn}: not resumed after a checkpoint (the queue and the running frago tasks are not saved); fix pair and per-kind recommendation: CHECKPOINTS.md §7"))
            # kind (b): the skills that read it (other kinds' dia-type strings are senders, stoppers, filters)
            readers = [r for r, _ in info.readers + info.commander_uses] + info.helper_callers if info.kind == "b" else []
            for r in dict.fromkeys(readers):
                ent = ml.entity_of_rel("/" + r)
                if ent in ents and ents[ent].kind == "skill":
                    skills[ent] = ents[ent]
    if not found:
        sys.exit(f"no frag order named or with dia-type {a.fragorder} in {', '.join(model.dbs)}")
    return [skills[k] for k in sorted(skills)], found


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--skill")
    g.add_argument("--task")
    g.add_argument("--fragorder", help="frag-order name or dia-type")
    g.add_argument("--all", action="store_true")
    g.add_argument("--file")
    ap.add_argument("--rules", default=",".join(RULES))
    ap.add_argument("--model", default="ada")
    ap.add_argument("--root", default=ml.DEFAULT_ROOT)
    ap.add_argument("--summary", action="store_true", help="counts per rule only")
    ap.add_argument("-v", "--verbose", action="store_true", help="accepted for symmetry with mission_check (no effect)")
    a = ap.parse_args()
    rules = set(a.rules.split(","))
    model_dir = os.path.join(a.root, "data", "data", "models", a.model)
    dia5 = os.path.join(model_dir, "decisional", "dia5")
    ents = scan_decisional(dia5)
    link_refs(ents, [])
    hashes = Hashability(ents)
    keep_state = set()  # skills run by a role's <default-task>: their state is meant to live as long as the agent
    for e in ents.values():
        if e.kind == "role":
            dt = re.search(r"<default-task>(.*?)</default-task>", e.text, re.S)
            keep_state |= set(re.findall(r'<behavior\s[^>]*?name="([^"]+)"', dt.group(1) if dt else ""))
    notes = []
    if a.fragorder:
        skills, n = fragorder_targets(a, ents, notes)
        targets = [(e.path[:-4] + ".lua", e.text) for e in skills if e.lua]
    elif a.file:
        p = os.path.abspath(a.file)
        x = p[:-4] + ".xml"
        targets = [(p, read(x) if os.path.isfile(x) else None)]
    else:
        if a.skill:
            if a.skill not in ents:
                sys.exit(f"unknown entity {a.skill}")
            skills = [ents[a.skill]]
        elif a.task:
            if a.task not in ents:
                sys.exit(f"unknown entity {a.task}")
            skills = closure(ents, a.task)
        else:
            skills = [e for e in ents.values() if e.kind == "skill"]
        targets = [(e.path[:-4] + ".lua", e.text) for e in skills if e.lua]
    findings = []
    if "frago-task-lost" in rules:
        for f, line, msg in notes:
            tag = "frago-task-lost" if "not resumed" in msg else "fragorder"
            print(f"NOTE {tag:16} {f}:{line}: {msg}")
    for lua, xml_text in targets:
        findings += lint_skill(lua, xml_text, hashes, rules, a.root, keep_state)
    if a.summary:
        from collections import Counter
        c = Counter(f[0] for f in findings)
        c["frago-task-lost"] = sum(1 for n in notes if "not resumed" in n[2]) if "frago-task-lost" in rules else 0
        for r in RULES:
            print(f"{r:26} {c.get(r, 0)}")
    else:
        own = getattr(fragorder_targets, "own", None) if a.fragorder else None
        for rule, f, line, msg in findings:
            tag = " [reused skill: it reads another order, not this one]" if (
                own is not None and rule == "order-effect-not-persisted" and f not in own) else ""
            print(f"WARN {rule:13} {f}:{line}: {msg}{tag}")
    print(f"\n{len(findings)} finding(s) in {len(targets)} skill Lua file(s) (upper bounds: confirm in the code; "
          "rules in docs/sw.models/CHECKPOINTS.md §3)")


if __name__ == "__main__":
    main()
