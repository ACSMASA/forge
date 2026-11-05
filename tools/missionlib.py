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
ATTR_RE = re.compile(r'([\w:.-]+)="([^"]*)"')


def read(p):
    with open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def attr_names(tag_text):
    return [k for k, _ in ATTR_RE.findall(tag_text)]


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

    def _parse_missions(self):
        self.raw = read(self.missions_path)
        root = ET.parse(self.missions_path).getroot()
        self.sections = [s.tag for s in root]
        self.ids = defaultdict(list)  # id -> [(section, tag, name)]
        self.categories = []
        for s in root:
            if s.tag == "categories":
                self.categories = [c.get("name") for c in s]
                continue
            for el in s:
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

    def rel(self, path):
        return os.path.relpath(path, self.root).replace("\\", "/")


def load_model(root=DEFAULT_ROOT, model="ada", sword=DEFAULT_SWORD, dbs=None):
    return Model(root, model, sword, dbs)
