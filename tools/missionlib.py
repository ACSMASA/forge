"""Shared parsing for the unit-mission tools (mission_check, mission_scaffold, checkpoint_lint, mission index).

Reads the WORKING TREE of a sw.models checkout (so uncommitted edits are seen) and, for parameter types, the sw.sword
sources. Stdlib only. Rules implemented here are documented in docs/sw.models/MISSIONS.md.

    import missionlib as ml
    m = ml.load_model(root="C:/MASARepo/sw.models", model="ada", sword="C:/MASARepo/sw.sword")
    db = m.dbs["mildb"]; mission = db.unit_missions_by_name["COMBAT - Attack objective"]
    task = m.task(mission.attrs["dia-behavior"])
"""
import os
import re
import xml.etree.ElementTree as ET
from collections import defaultdict

MASA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_ROOT = os.path.join(MASA, "sw.models")
DEFAULT_SWORD = os.path.join(MASA, "sw.sword")
SPARK_NS = "{http://www.masagroup.net/directai/spark/lang/1.5}"

# The 4 context parameters, read by position by the kernel (MIL_PionMissionType: contextLength 4).
CONTEXT = [
    {"dia-name": "dangerDirection_", "type": "Heading", "name": "Danger direction", "optional": "false"},
    {"dia-name": "phaseLines_", "type": "PhaseLine", "name": "Phase lines", "optional": "true",
     "max-occurs": "unbounded", "min-occurs": "1"},
    {"dia-name": "boundaryLimit1_", "type": "Limit", "name": "Limit 1", "optional": "true"},
    {"dia-name": "boundaryLimit2_", "type": "Limit", "name": "Limit 2", "optional": "true"},
]
CONTEXT_XML = """      <parameter dia-name="dangerDirection_" is-context="true" name="Danger direction" optional="false" type="Heading"/>
      <parameter dia-name="phaseLines_" is-context="true" max-occurs="unbounded" min-occurs="1" name="Phase lines" optional="true" type="PhaseLine">
        <objects/>
      </parameter>
      <parameter dia-name="boundaryLimit1_" is-context="true" name="Limit 1" optional="true" type="Limit"/>
      <parameter dia-name="boundaryLimit2_" is-context="true" name="Limit 2" optional="true" type="Limit"/>
"""

# Spark value shape expected for a kernel type (canonical registry name), measured on model ada (both DBs).
# 'obj' = an <object> type; lists wrap it. 'any' in the task is always accepted.
SCALAR_SHAPES = {
    "Boolean": {"boolean"},
    "Distance": {"number"}, "Enumeration": {"number"}, "Integer": {"number"}, "Numeric": {"number"},
    "Speed": {"number"}, "DateTime": {"number", "string"},
    "String": {"string"},
    "Path": {"list(obj)"}, "Itinerary": {"list(obj)"},
}

TAG_RE = re.compile(r"<(mission|parameter)\b([^>]*?)(/?)>")
FO_TAG_RE = re.compile(r"<(fragorder|parameter)\b([^>]*?)(/?)>")
FO_ATTRS = ["available-without-mission", "category", "dia-type", "id", "name"]
# Models.xml sections -> the entity kind they hold
MODEL_SECTIONS = {"units": "unit", "automats": "automat", "crowd": "crowd", "task-groups": "task-group"}
ATTR_RE = re.compile(r'([\w:.-]+)="([^"]*)"')


def read(p):
    with open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def attr_names(tag_text):
    return [k for k, _ in ATTR_RE.findall(tag_text)]


# ---------------------------------------------------------------------------------------------------------------------
# Lua text helpers (shared with checkpoint_lint)

LUA_BLOCK_TOK = re.compile(r"\b(function|if|while|for|do|repeat|end|until)\b")
LUA_STRING = re.compile(r"""\[(=*)\[.*?\]\1\]|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'""", re.S)


def lua_strip_comments(src):
    """Remove Lua comments, keep strings and line numbers."""
    src = re.sub(r"--\[(=*)\[.*?\]\1\]", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return re.sub(r"--[^\n]*", "", src)


def lua_mask_strings(code):
    """Same text with string contents blanked (same length, newlines kept): offsets stay valid."""
    return LUA_STRING.sub(lambda m: m.group(0)[0] + re.sub(r"[^\n]", " ", m.group(0)[1:-1]) + m.group(0)[-1], code)


def lua_block_end(text, start):
    """End offset of the Lua block opened by the `function` keyword at `start` (text from lua_mask_strings)."""
    depth, pend_do = 0, 0
    for m in LUA_BLOCK_TOK.finditer(text, start):
        t = m.group(1)
        if t in ("function", "if", "repeat"):
            depth += 1
        elif t in ("while", "for"):
            depth += 1
            pend_do += 1
        elif t == "do":
            if pend_do:
                pend_do -= 1
            else:
                depth += 1
        elif t in ("end", "until"):
            depth -= 1
            if depth == 0:
                return m.end()
    return len(text)


def lua_brace_end(masked, start):
    """Offset just after the `}` closing the `{` at `start` (text from lua_mask_strings)."""
    depth = 0
    for i in range(start, len(masked)):
        c = masked[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(masked)


# ---------------------------------------------------------------------------------------------------------------------
# sw.sword parameter types


class SwordTypes:
    """Missions.xml parameter type registry (case-insensitive) and the BM converters that pass values to Spark."""

    def __init__(self, sword):
        self.ok = False
        self.registry = {}  # lower name -> registered (canonical) name
        self.kind = {}  # registered name -> E_Type
        self.converters = set()  # functorsBM keys (exact)
        self.ptype_keys = {}  # functorsBM key -> integration.ontology.types keys (filled by callers if needed)
        reg = os.path.join(sword, "src", "libraries", "simulation", "Entities", "Orders", "MIL_ParameterType_ABC.cpp")
        dec = os.path.join(sword, "src", "libraries", "simulation", "Decision", "DEC_Decision.cpp")
        if not (os.path.isfile(reg) and os.path.isfile(dec)):
            return
        for name, kind in re.findall(r'RegisterParameterType\(\s*"(\w+)"\s*,\s*(\w+)\s*\)', read(reg)):
            self.registry[name.lower()] = name
            self.kind[name] = kind
        src = read(dec)
        fmap = dict(re.findall(r'functorsBM\[\s*"(\w+)"\s*\]\s*=\s*(\w+)', src))
        self.converters = set(fmap)
        # A converter that always returns true leaves an absent value unset (nil -> the task <default> applies);
        # one that returns false makes RegisterMissionParameterBMVisitor store the NIL sentinel (default skipped).
        always_true = set()
        for m in re.finditer(r"^bool\s+(\w+FunctionBM)\s*\([^)]*\)\s*\{", src, re.M):
            body = " ".join(src[m.end():src.find("\n}", m.end())].split())
            if re.fullmatch(r"\w+\( [^;]*\); return true;", body):
                always_true.add(m.group(1))
        self.absent_is_nil = {k for k, fn in fmap.items() if fn in always_true}
        self.ok = bool(self.registry and self.converters)

    def absent_value(self, t, is_list):
        """What the task receives when an optional parameter is not given: 'nil' (default applies), 'NIL', or None
        (no converter: never passed, nil)."""
        key = self.converter(t, is_list)
        if key is None:
            return None
        return "nil" if key in self.absent_is_nil else "NIL"

    def canonical(self, t):
        return self.registry.get((t or "").lower())

    def converter(self, t, is_list):
        """functorsBM key used for a Missions.xml type, or None when the value is silently not passed."""
        c = self.canonical(t)
        if c is None:
            return None
        key = c + ("List" if is_list else "")
        return key if key in self.converters else None


# ---------------------------------------------------------------------------------------------------------------------
# physical DB


class Param:
    def __init__(self, el, tag_text, line):
        self.attrs = dict(el.attrib)
        self.order = attr_names(tag_text)
        self.line = line
        self.name = el.get("name")
        self.dia_name = el.get("dia-name")
        self.type = el.get("type")
        self.optional = el.get("optional", "false") == "true"
        self.is_context = el.get("is-context") == "true"
        mx = el.get("max-occurs")
        self.is_list = mx is not None and mx != "1"
        self.values = [v.get("name") for v in el.iter("value") if v.get("name") is not None]
        ch = el.find("choice")
        self.choices = [c.get("type") for c in ch.findall("parameter")] if ch is not None else []


class Mission:
    def __init__(self, db, section, el, tag_text, line, param_tags):
        self.db, self.section, self.line = db, section, line
        self.attrs = dict(el.attrib)
        self.order = attr_names(tag_text)
        self.name = el.get("name")
        self.params = []
        for p, (ptxt, pline) in zip(el.findall("parameter"), param_tags):
            self.params.append(Param(p, ptxt, pline))

    @property
    def task_name(self):
        return self.attrs.get("dia-behavior")

    def own_params(self):
        return [p for p in self.params if not p.is_context]


class FragOrder:
    """One <fragorders><fragorder> entry of a DB's Missions.xml (no context block: every parameter is its own)."""

    def __init__(self, db, el, tag_text, line, param_tags):
        self.db, self.line = db, line
        self.attrs = dict(el.attrib)
        self.order = attr_names(tag_text)
        self.name = el.get("name")
        self.dia_type = el.get("dia-type")
        self.without_mission = el.get("available-without-mission", "false") == "true"
        self.params = [Param(p, ptxt, pline) for p, (ptxt, pline) in zip(el.findall("parameter"), param_tags)]

    def own_params(self):
        return self.params


class DB:
    def __init__(self, name, path):
        self.name, self.path = name, path
        self.files = {}
        px = os.path.join(path, "physical.xml")
        for el in ET.parse(px).getroot().iter():
            for k in ("file", "path"):
                if k in el.attrib:
                    self.files.setdefault(el.tag, el.attrib[k])
        self.missions_path = os.path.join(path, self.files.get("missions", "Missions.xml"))
        self.models_path = os.path.join(path, self.files.get("models", "Models.xml"))
        self.stages_path = os.path.join(path, self.files.get("stages", "Stages.xml"))
        self.sheets_dir = os.path.join(path, self.files.get("units-mission-sheets-directory", "Mission sheets/units"))
        self.locales = os.path.join(path, "Locales")
        self.has_locales = os.path.isdir(self.locales)
        self._parse_missions()
        self._parse_models()
        self.stages = set()
        if os.path.isfile(self.stages_path):
            self.stages = {e.get("key") for e in ET.parse(self.stages_path).getroot().iter("stage")}
        self.ts = {}  # lang -> file kind ('Missions', 'Stages') -> {context: {source: translation}}
        if self.has_locales:
            for lang in sorted(os.listdir(self.locales)):
                for kind in ("Missions", "Stages"):
                    p = os.path.join(self.locales, lang, f"{kind}_{lang}.ts")
                    if os.path.isfile(p):
                        self.ts.setdefault(lang, {})[kind] = parse_ts(p)
        self.sheets = set_of_sheets(self.sheets_dir)
        self.sheets_by_lang = {lang: set_of_sheets(os.path.join(self.sheets_dir, "Locales", lang)) for lang in self.ts}
        self.fo_sheets_dir = os.path.join(path, self.files.get("fragorders-mission-sheets-directory",
                                                               "Mission sheets/fragmentary orders"))
        self.fo_sheets = set_of_sheets(self.fo_sheets_dir)
        self.fo_sheets_by_lang = {lang: set_of_sheets(os.path.join(self.fo_sheets_dir, "Locales", lang)) for lang in self.ts}
        self._parse_fragorders()
        self._parse_model_lists()
        self._parse_automat_subordinates()

    def _parse_fragorders(self):
        """<fragorders> entries with raw tags and lines."""
        self.fragorders = []
        self.fragorders_by_name = {}
        self.fragorders_by_dia_type = defaultdict(list)
        ms, me = re.search(r"(?m)^  <fragorders>", self.raw), re.search(r"(?m)^  </fragorders>", self.raw)
        self.fragorders_span = (ms.start(), me.start()) if ms and me else None
        if not (ms and me):
            return
        start, end = ms.end(), me.start()
        blocks = []
        for m in re.finditer(r"<fragorder\b[^>]*?(/?)>", self.raw[start:end]):
            a = start + m.start()
            b = start + m.end() if m.group(1) else self.raw.find("</fragorder>", a)
            blocks.append((a, b))
        sec = ET.parse(self.missions_path).getroot().find("fragorders")
        for el, (a, b) in zip(sec.findall("fragorder"), blocks):
            tags = list(FO_TAG_RE.finditer(self.raw[a:b]))
            ptags = [(t.group(0), line_of(self.raw, a + t.start())) for t in tags[1:]
                     if t.group(1) == "parameter" and 'name="' in t.group(2)]
            self.fragorders.append(FragOrder(self.name, el, tags[0].group(0), line_of(self.raw, a), ptags))
        self.fragorders_by_name = {f.name: f for f in self.fragorders}
        self.fragorders_by_dia_type = defaultdict(list)
        for f in self.fragorders:
            self.fragorders_by_dia_type[f.dia_type].append(f)

    def _parse_model_lists(self):
        """Every Models.xml section: model -> {kind, role, level: [frag orders], missions: {mission: [frag orders]}};
        fo_listed: frag-order name -> [(kind, model, mission or None)]."""
        self.model_entries = {}  # (kind, model name) -> dict
        self.fo_listed = defaultdict(list)
        if not self.models_raw:
            return
        root = ET.parse(self.models_path).getroot()
        for sec, kind in MODEL_SECTIONS.items():
            s = root.find(sec)
            for mo in (s if s is not None else []):
                fr = mo.find("fragorders")
                ms = mo.find("missions")
                e = {"kind": kind, "name": mo.get("name"), "role": mo.get("dia-type"),
                     "level": [f.get("name") for f in (fr if fr is not None else [])],
                     "missions": {x.get("name"): [f.get("name") for f in x.findall("fragorder")]
                                  for x in (ms if ms is not None else [])}}
                self.model_entries[(kind, e["name"])] = e
                for f in e["level"]:
                    self.fo_listed[f].append((kind, e["name"], None))
                for mn, fs in e["missions"].items():
                    for f in fs:
                        self.fo_listed[f].append((kind, e["name"], mn))

    def _parse_automat_subordinates(self):
        """automat decisional model -> unit decisional models of its subordinates (Automats.xml + Units.xml)."""
        self.automat_subs = defaultdict(set)
        ap = os.path.join(self.path, self.files.get("automats", "Automats.xml"))
        up = os.path.join(self.path, self.files.get("units", "Units.xml"))
        if not (os.path.isfile(ap) and os.path.isfile(up)):
            return
        unit_model = {u.get("name"): u.get("decisional-model") for u in ET.parse(up).getroot().iter("unit")
                      if u.get("decisional-model")}
        for a in ET.parse(ap).getroot().iter("automat"):
            for u in a.findall("unit"):
                um = unit_model.get(u.get("type"))
                if a.get("decisional-model") and um:
                    self.automat_subs[a.get("decisional-model")].add(um)

    def mission_names(self, kind):
        """Mission names of the Missions.xml section a Models.xml model kind gives (unit, automat, crowd, task-group)."""
        sec = {"unit": "units", "automat": "automats", "crowd": "populations", "task-group": "task-groups"}[kind]
        return self.section_names.get(sec, set())

    def fo_translation(self, lang, name):
        return self.ts.get(lang, {}).get("Missions", {}).get("frag-order", {}).get(name)

    def _parse_missions(self):
        self.raw = read(self.missions_path)
        root = ET.parse(self.missions_path).getroot()
        self.sections = [s.tag for s in root]
        self.ids = defaultdict(list)  # id -> [(section, tag, name)]
        self.categories = []
        self.section_names = defaultdict(set)  # Missions.xml section -> entry names
        for s in root:
            if s.tag == "categories":
                self.categories = [c.get("name") for c in s]
                continue
            for el in s:
                self.section_names[s.tag].add(el.get("name"))
                if el.get("id") is not None:
                    self.ids[el.get("id")].append((s.tag, el.tag, el.get("name")))
        # unit missions with source lines and raw tags
        # top-level section only: <units> also occurs nested in UnitType parameters
        ms, me = re.search(r"(?m)^  <units>", self.raw), re.search(r"(?m)^  </units>", self.raw)
        start, end = (ms.start(), me.start()) if ms and me else (0, 0)
        blocks = []
        for m in re.finditer(r"<mission\b[^>]*?(/?)>", self.raw[start:end]):
            a = start + m.start()
            if m.group(1):
                b = start + m.end()
            else:
                b = self.raw.find("</mission>", a)
            blocks.append((a, b))
        units = root.find("units")
        self.unit_missions = []
        for el, (a, b) in zip(units.findall("mission") if units is not None else [], blocks):
            block = self.raw[a:b]
            tags = list(TAG_RE.finditer(block))
            mtag = tags[0].group(0)
            ptags = [(t.group(0), line_of(self.raw, a + t.start())) for t in tags[1:]
                     if t.group(1) == "parameter" and 'name="' in t.group(2)]
            self.unit_missions.append(Mission(self.name, "units", el, mtag, line_of(self.raw, a), ptags))
        self.unit_missions_by_name = {m.name: m for m in self.unit_missions}
        self.max_id = max((int(i) for i in self.ids if i.isdigit()), default=0)
        self.fragorder_names = {n for entries in self.ids.values() for sec, _, n in entries if sec == "fragorders"}

    def _parse_models(self):
        self.models = {}  # unit model name -> [mission names]
        self.mission_models = defaultdict(list)  # mission name -> [unit model names]
        self.mission_frags = defaultdict(set)  # mission name -> <fragorder name> listed under it in unit models
        self.models_raw = read(self.models_path) if os.path.isfile(self.models_path) else ""
        if not self.models_raw:
            return
        root = ET.parse(self.models_path).getroot()
        units = root.find("units")
        for u in (units if units is not None else []):
            ms = u.find("missions")
            names = [x.get("name") for x in (ms if ms is not None else [])]
            self.models[u.get("name")] = names
            for n in names:
                self.mission_models[n].append(u.get("name"))
            for x in (ms if ms is not None else []):
                self.mission_frags[x.get("name")] |= {f.get("name") for f in x.findall("fragorder")}
        self.model_roles = {u.get("name"): u.get("dia-type") for u in (units if units is not None else [])}

    def translation(self, lang, context, source):
        return self.ts.get(lang, {}).get("Missions", {}).get(context, {}).get(source)

    def sheet_name(self, lang, mission_name):
        """File stem of a mission's sheet in a language ('en' = the top dir), or None if untranslated."""
        if lang == "en":
            return mission_name
        return self.translation(lang, "unit-missions", mission_name)


def parse_ts(path):
    out = {}
    for c in ET.parse(path).getroot().findall("context"):
        name = c.findtext("name")
        out[name] = {m.findtext("source"): m.findtext("translation") for m in c.findall("message")}
    return out


def set_of_sheets(d):
    if not os.path.isdir(d):
        return set()
    return {f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))}


# ---------------------------------------------------------------------------------------------------------------------
# tasks


def type_shape(t):
    """<type> element -> 'obj', 'list(obj)', 'boolean', 'obj,obj' (union)…"""
    if t is None:
        return "?"

    def shape(e):
        tag = e.tag.replace(SPARK_NS, "")
        if tag == "object":
            return "obj"
        kids = list(e)
        return tag + ("(" + ",".join(shape(k) for k in kids) + ")" if kids else "")
    return ",".join(shape(k) for k in t)


def object_types(t):
    return [o.get("type") for o in t.iter(SPARK_NS + "object")] if t is not None else []


class TaskParam:
    def __init__(self, el):
        self.name = el.get("name")
        self.nullable = el.get("nullable") == "true"
        t = el.find(SPARK_NS + "type")
        self.shape = type_shape(t)
        self.object_types = object_types(t)
        d = el.find(SPARK_NS + "default")
        self.default = None if d is None else (",".join(k.tag.replace(SPARK_NS, "") for k in d) or (d.text or "").strip())


class Stage:
    def __init__(self, el, parallel=False):
        self.label = el.get("label")
        self.parallel = parallel
        self.behaviors = []  # (name, attrs)
        bs = el.find(SPARK_NS + "behaviors")
        for b in (bs if bs is not None else []):
            if b.tag == SPARK_NS + "behavior":
                self.behaviors.append((b.get("name"), dict(b.attrib)))


class Task:
    def __init__(self, name, path):
        self.name, self.path = name, path
        root = ET.parse(path).getroot()
        self.kind = root.tag.replace(SPARK_NS, "")
        self.disable_when_done = root.get("disable-when-done") == "true"
        ps = root.find(SPARK_NS + "parameters")
        self.params = {p.get("name"): TaskParam(p) for p in (ps if ps is not None else []) if p.tag == SPARK_NS + "parameter"}
        par = root.find(SPARK_NS + "parallel")
        self.parallel = Stage(par, True) if par is not None else None
        st = root.find(SPARK_NS + "stages")
        self.stages = [Stage(s) for s in (st if st is not None else []) if s.tag == SPARK_NS + "stage"]
        self.next_task = root.find(SPARK_NS + "next-task") is not None
        self.metadata_class = None
        for a in root.iter(SPARK_NS + "annotation"):
            if a.get("key") == "spark.metadata-class":
                self.metadata_class = (a.findtext(SPARK_NS + "entity") or "").strip()
        self.raw = read(path)

    def labels(self):
        """Labels of the sequential <stage>s: the ones reported (eRC_BM_DebutPhase) and required in Stages.xml.
        A <parallel label> is neither (checkstage greps 'stage label=', CRTaskListener skips stage id 0)."""
        return [s.label for s in self.stages if s.label]

    def terminators(self, stage):
        """Behaviors of a sequential stage whose 'done' ends it: explicit next-stage-when-done (or 1.1 'main'), or the
        single behavior of a stage the validator makes terminating (val/parsers/task.lua deduceMainBehaviors)."""
        explicit = [b for b, a in stage.behaviors if (a.get("next-stage-when-done") or a.get("main")) == "true"]
        if explicit or len(stage.behaviors) != 1:
            return explicit
        if any((a.get("next-stage-when-done") or a.get("main")) == "false" for _, a in stage.behaviors):
            return []
        last = stage is self.stages[-1]
        if not last or (self.parallel is None and (self.next_task or self.disable_when_done)):
            return [stage.behaviors[0][0]]
        return []

    def references(self):
        """Entity names the task XML references: (kind, name) for every <behavior name> and <call query>."""
        out = [("behavior", n) for n in re.findall(r'<behavior\b[^>]*?\bname="([^"]+)"', self.raw)]
        out += [("query", n) for n in re.findall(r'<call\b[^>]*?\bquery="([^"]+)"', self.raw)]
        return list(dict.fromkeys(out))

    def behaviors(self):
        out = []
        for s in ([self.parallel] if self.parallel else []) + self.stages:
            out += [b for b, _ in s.behaviors]
        return out

    def line_of(self, needle):
        i = self.raw.find(needle)
        return line_of(self.raw, i) if i >= 0 else 1


def all_stage_labels(decisional, ignored=("dia5/test",)):
    """Every 'stage label="…"' under the model's decisional dir (dia5, taskgroups, extensions), minus dia5/test/:
    the set checkstage compares Stages.xml with (sw.models src/checkstage). label -> first file."""
    out = {}
    rx = re.compile(r'stage label="([^"]+)"')
    for root, dirs, fs in os.walk(decisional):
        rel = os.path.relpath(root, decisional).replace("\\", "/")
        if any(rel == i or rel.startswith(i + "/") for i in ignored):
            continue
        for f in fs:
            if f.endswith(".xml"):
                p = os.path.join(root, f)
                for lab in rx.findall(read(p)):
                    out.setdefault(lab, p)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# frag orders: dia5/FragOrder.lua and the dia5 strings that name dia-types (rules: docs/sw.models/FRAGORDERS.md)

INIT_KEY = re.compile(r'\[\s*"([^"]+)"\s*\]\s*=\s*(?:(function)\b|([A-Za-z_]\w*)\s*,)')
GETTER = re.compile(r'integration\.get(\w+?)Parameter\s*\(\s*[\w.]+\s*,\s*"([^"]+)"|\w+:get(\w+?)Parameter\s*\(\s*"([^"]+)"')
STOPPER = re.compile(r'(?:\bstopTask|spark\.stop)\s*\(\s*"([^"]+)"')
CLEAN = re.compile(r"integration\.cleanFragOrder\s*\(\s*[\w.]+\s*\)")


class Initializer:
    """One fragOrderTaskInitializers entry (code = comment-free body between the signature and its `end`)."""

    def __init__(self, key, line, end_line, code, named=None):
        self.key, self.line, self.end_line, self.code, self.named = key, line, end_line, code, named
        starts = re.findall(r'return\s+true\s*,\s*"([^"]+)"', code)
        self.computed = re.findall(r"return\s+true\s*,\s*([A-Za-z_][\w.]*)", code)
        if re.search(r"return\s+true\b(?!\s*,)", code):
            starts.append(key)  # startFragOrderTask keeps orderType = the dia-type
        # a task started directly by the initializer: common.agent.tasks.frago.DetachUnit:start( mission )
        self.direct = re.findall(r"\b([a-z]\w*(?:\.\w+){2,})\s*:\s*start\s*\(", code)
        starts += self.direct
        self.starts = list(dict.fromkeys(starts))
        self.cleans = bool(CLEAN.search(code))
        self.stops = list(dict.fromkeys(STOPPER.findall(code)))
        self.reads = list(dict.fromkeys((a or c, b or d) for a, b, c, d in GETTER.findall(code)))
        self.sets = list(dict.fromkeys(re.findall(r"\bmission\.(\w+)\s*=(?!=)", code)))
        rest = CLEAN.sub("", code).strip()
        self.empty = not code.strip()
        self.clean_only = not self.empty and not rest
        self.myself = sorted(set(re.findall(r"\bmyself\.(\w+)\s*=(?!=)", code)))

    @property
    def starts_task(self):
        return bool(self.starts or self.computed)


class FragOrderLua:
    """dia5/FragOrder.lua: initializers, propagation list, automat branches, poll helpers."""

    def __init__(self, path):
        self.path = path
        self.ok = os.path.isfile(path)
        self.init, self.propagated, self.automat, self.helpers = {}, [], {}, {}
        self.automat_fallback = False
        self.init_span = self.prop_span = self.automat_span = None
        self.crlf = False
        if not self.ok:
            return
        with open(path, "rb") as fh:
            rawb = fh.read()
        self.crlf = b"\r\n" in rawb
        text = rawb.decode("utf-8", errors="replace").replace("\r\n", "\n")
        code = lua_strip_comments(text)
        masked = lua_mask_strings(code)
        self.text, self.code = text, code
        # propagation list: [(name, line)]
        m = re.search(r"\bfragOrdersToBePropagated\s*=\s*\{", masked)
        if m:
            b = lua_brace_end(masked, m.end() - 1)
            self.prop_span = (m.start(), b)
            self.propagated = [(x.group(1), line_of(code, m.end() + x.start()))
                               for x in re.finditer(r'"([^"]+)"', code[m.end():b])]
        # automat branches: dia-type -> line
        m = re.search(r"\bfragOrder\.executeAutomatFragOrder\s*=\s*function", masked)
        if m:
            f = masked.find("function", m.start())
            b = lua_block_end(masked, f)
            self.automat_span = (m.start(), b)
            for x in re.finditer(r'orderType\s*==\s*"([^"]+)"', code[m.start():b]):
                self.automat.setdefault(x.group(1), line_of(code, m.start() + x.start()))
            self.automat_fallback = "spark.getBehavior( orderType )" in code[m.start():b] or \
                bool(re.search(r"spark\.getBehavior\s*\(\s*orderType\s*\)", code[m.start():b]))
        # local named functions used as initializer values
        local_fns = {}
        for x in re.finditer(r"\blocal\s+([A-Za-z_]\w*)\s*=\s*function\s*\([^)]*\)", masked):
            f = masked.find("function", x.start())
            local_fns[x.group(1)] = (x.end(), lua_block_end(masked, f))
        # initializers
        m = re.search(r"\bfragOrderTaskInitializers\s*=\s*\{", masked)
        if m:
            tb = lua_brace_end(masked, m.end() - 1)
            self.init_span = (m.start(), tb)
            pos = m.end()
            while True:
                k = INIT_KEY.search(masked, pos, tb)
                if k is None:
                    break
                key = code[k.start():k.end()]
                key = re.match(r'\[\s*"([^"]+)"', key).group(1)
                if k.group(2):  # inline function
                    f = k.start(2)
                    e = lua_block_end(masked, f)
                    sig = masked.find(")", f) + 1
                    body = code[sig:e - 3]
                    self.init[key] = Initializer(key, line_of(code, k.start()), line_of(code, e), body)
                    pos = e
                else:
                    fn = k.group(3)
                    a, e = local_fns.get(fn, (0, 0))
                    self.init[key] = Initializer(key, line_of(code, k.start()), line_of(code, k.end()),
                                                 code[a:max(a, e - 3)], named=fn)
                    pos = k.end()
        # poll helpers: other fragOrder.<f> = function bodies -> quoted strings
        skip = {"mustBePropagate", "executeAutomatFragOrder", "startFragOrderTask"}
        for x in re.finditer(r"\bfragOrder\.(\w+)\s*=\s*function", masked):
            if x.group(1) in skip:
                continue
            f = masked.find("function", x.start())
            e = lua_block_end(masked, f)
            for s in re.finditer(r'"([^"]+)"', code[x.start():e]):
                self.helpers.setdefault(s.group(1), []).append((x.group(1), line_of(code, x.start() + s.start())))

    def propagated_names(self):
        return {n for n, _ in self.propagated}

    def in_init_table(self, line):
        return self.init_span is not None and line_of(self.code, self.init_span[0]) <= line <= line_of(self.code, self.init_span[1])


class Dia5Strings:
    """Every quoted Lua / XML attribute string and every XML text value in dia5 (FragOrder.lua excluded):
    string -> [(rel path, line)]. Used to find the hard-coded readers of a frag-order dia-type, and stopTask callers."""

    def __init__(self, model):
        self.where = defaultdict(list)
        self.stoppers = defaultdict(list)  # task name -> [(rel, line)] of stopTask / spark.stop( "task" )
        self.calls = defaultdict(set)  # identifier -> rel paths that contain it (helper callers)
        dia5 = model.dia5
        skip = os.path.join(dia5, "FragOrder.lua")
        rx = re.compile(r'"([^"\n]{2,120})"|>\s*([^<>\n]{2,120}?)\s*<')
        for root, _, fs in os.walk(dia5):
            for f in fs:
                if not f.endswith((".lua", ".xml")):
                    continue
                p = os.path.join(root, f)
                if p == skip:
                    continue
                txt = read(p)
                if f.endswith(".lua"):
                    txt = lua_strip_comments(txt)
                else:
                    txt = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), txt, flags=re.S)
                rel = model.rel(p)
                for m in rx.finditer(txt):
                    s = m.group(1) or m.group(2)
                    self.where[s].append((rel, line_of(txt, m.start())))
                for m in STOPPER.finditer(txt):
                    self.stoppers[m.group(1)].append((rel, line_of(txt, m.start())))
                for ident in set(re.findall(r"\b(get\w*FragOrder\w*)\b", txt)):
                    self.calls[ident].add(rel)

    def uses(self, s, exclude=()):
        return [w for w in self.where.get(s, []) if w[0] not in exclude]


def skill_resources(model, name, depth=6, seen=None):
    """Resources a skill acquires, itself or through its <link target>s (XML only; upper bound)."""
    seen = set() if seen is None else seen
    if name in seen or depth < 0:
        return set()
    seen.add(name)
    p = model.entity_path(name)
    if not os.path.isfile(p):
        return set()
    txt = read(p)
    out = set(re.findall(r'<resource\s+name="([^"]+)"', txt))
    for t in re.findall(r'<link\b[^>]*?\btarget="([^"]+)"', txt):
        out |= skill_resources(model, t, depth - 1, seen)
    return out


def entity_of_rel(rel):
    """dia5 file rel path -> dotted entity name (None outside dia5)."""
    m = re.search(r"/decisional/dia5/(.+)\.(?:xml|lua)$", rel)
    return m.group(1).replace("/", ".") if m else None


class FragOrderInfo:
    """How one frag order of one DB is wired (FRAGORDERS.md §2): kind, tasks, readers, lists, checkpoint class."""

    def __init__(self, model, db, fo):
        reg, strs = model.fragorder_lua(), model.dia5_strings()
        self.fo, self.db = fo, db
        dt = fo.dia_type or ""
        self.init = reg.init.get(dt)
        self.propagated = dt in reg.propagated_names()
        self.automat_branch = reg.automat.get(dt)
        self.helpers = sorted({h for h, _ in reg.helpers.get(dt, [])})
        own = model.rel(model.entity_path(dt)) if dt else ""
        uses = strs.uses(dt, exclude={own, own[:-4] + ".lua"})
        self.commander_uses = [u for u in uses if "/commander/" in u[0]]
        self.readers = [u for u in uses if "/commander/" not in u[0]]
        # helper callers: who calls the poll helper(s) that test this dia-type
        self.helper_callers = sorted({r for h in self.helpers for r in strs.calls.get(h, ())})
        listed = db.fo_listed.get(fo.name, [])
        self.listed = defaultdict(list)  # kind -> [(model, mission or None)]
        for kind, mo, mi in listed:
            self.listed[kind].append((mo, mi))
        self.task_group_only = bool(listed) and all(k == "task-group" for k, _, _ in listed)
        # kind
        self.tasks = []  # task names this order starts on a unit / crowd
        self.missing_tasks = []
        if self.init is not None:
            if self.init.starts_task:
                self.kind = "a"
                self.tasks = list(self.init.starts)
            elif not (self.init.empty or self.init.clean_only):
                self.kind = "f"
            elif self.readers or self.helpers or self.commander_uses:
                self.kind = "b"
            else:
                self.kind = "b?" if self.init.empty else "f?"
        else:
            t = model.task(dt)
            if t is not None and t.kind == "task":
                self.kind = "c"
                self.tasks = [dt]
            elif self.task_group_only or dt.startswith("taskgroup."):
                self.kind = "c2"
            elif self.automat_branch or self.commander_uses:
                self.kind = "d"
            else:
                self.kind = "none"
        for tn in self.tasks:
            t = model.task(tn)
            if t is None or t.kind != "task":
                self.missing_tasks.append(tn)
        if any(tn.startswith("crowd.") for tn in self.tasks) or (self.listed.get("crowd") and not self.listed.get("unit")):
            self.family = "crowd"
        else:
            self.family = "unit"

    @property
    def checkpoint_class(self):
        k = self.kind
        if k in ("a", "c"):
            return "task lost (CHECKPOINTS.md §7)"
        if k == "b":
            return "reader must persist"
        if k == "f":
            return "immediate" + (f"; myself.{', myself.'.join(self.init.myself)} lost" if self.init and self.init.myself else "")
        if k == "d":
            return "commander (Lua-only orders lost)"
        return "—"


# ---------------------------------------------------------------------------------------------------------------------
# model


class Model:
    def __init__(self, root=DEFAULT_ROOT, model="ada", sword=DEFAULT_SWORD, dbs=None):
        self.root, self.model = root, model
        self.model_dir = os.path.join(root, "data", "data", "models", model)
        self.decisional = os.path.join(self.model_dir, "decisional")
        self.dia5 = os.path.join(self.decisional, "dia5")
        phys = os.path.join(self.model_dir, "physical")
        if not os.path.isdir(phys):
            raise SystemExit(f"no physical dir: {phys}")
        self.dbs = {}
        for d in sorted(os.listdir(phys)):
            if dbs and d not in dbs:
                continue
            if os.path.isfile(os.path.join(phys, d, "physical.xml")):
                self.dbs[d] = DB(d, os.path.join(phys, d))
        self.types = SwordTypes(sword)
        self.sword = sword
        self._tasks = {}

    def is_test(self, path):
        return self.rel(path).startswith(self.rel(os.path.join(self.dia5, "test")) + "/")

    def entity_path(self, name):
        return os.path.join(self.dia5, *name.split(".")) + ".xml"

    def task(self, name):
        if name not in self._tasks:
            p = self.entity_path(name) if name else ""
            t = None
            if p and os.path.isfile(p):
                try:
                    t = Task(name, p)
                except ET.ParseError:
                    t = None
            self._tasks[name] = t
        return self._tasks[name]

    def max_id(self):
        return max((db.max_id for db in self.dbs.values()), default=0)

    def fragorder_lua(self):
        if not hasattr(self, "_fol"):
            self._fol = FragOrderLua(os.path.join(self.dia5, "FragOrder.lua"))
        return self._fol

    def integration_text(self):
        """All sw.sword integration Lua, comment-free (frag-order parameters read by the layer, e.g. ROE)."""
        if not hasattr(self, "_itext"):
            d = os.path.join(self.sword, "data", "app-data", "resources", "integration")
            parts = []
            if os.path.isdir(d):
                for f in sorted(os.listdir(d)):
                    if f.endswith(".lua"):
                        parts.append(lua_strip_comments(read(os.path.join(d, f))))
            self._itext = "\n".join(parts)
        return self._itext

    def dia5_strings(self):
        if not hasattr(self, "_strs"):
            self._strs = Dia5Strings(self)
        return self._strs

    def fragorder_info(self, db, fo):
        key = (db.name, fo.name)
        if not hasattr(self, "_foi"):
            self._foi = {}
        if key not in self._foi:
            self._foi[key] = FragOrderInfo(self, db, fo)
        return self._foi[key]

    def rel(self, path):
        return os.path.relpath(path, self.root).replace("\\", "/")


def load_model(root=DEFAULT_ROOT, model="ada", sword=DEFAULT_SWORD, dbs=None):
    return Model(root, model, sword, dbs)


def scan_fragorder_lua(path):
    """Summary of FragOrder.lua for the decisional index: {"init": {dia-type: set(started tasks)}, "propagated":
    set, "automat": set}. A bare `return true` starts the task named by the dia-type itself."""
    reg = FragOrderLua(path)
    return {"init": {k: set(i.starts) for k, i in reg.init.items()}, "propagated": reg.propagated_names(),
            "automat": set(reg.automat)}
