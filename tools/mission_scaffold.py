"""Scaffold a unit mission in a sw.models model from a JSON spec: normalized insertions into the physical files.

Usage (from the forge root):
    python -I tools/mission_scaffold.py SPEC.json|- [--root C:/MASARepo/sw.models] [--model ada]
                                       [--sword C:/MASARepo/sw.sword] [--write [--allow-master]]
    python -I tools/mission_scaffold.py --example [fragorder]   # print a commented example spec

Default: print a unified diff of every insertion; nothing is written. --write applies it (refused on master/main
unless --allow-master; a --root outside git is always allowed). Then run tools/mission_check.py --mission NAME.

What it writes (rules and anchors: docs/sw.models/MISSIONS.md §1):
  per target DB  Missions.xml   <units> entry appended: alphabetical attributes, the 4 context parameters, then the
                                spec parameters (choice / objects / units / value children), id = max over every DB + 1
                 Models.xml     <mission name/> in each listed unit model (sorted position if the list is sorted)
                 sheets         Mission sheets/units/<name>.xml + .html (html = missionSheets.xslt output, byte-exact)
                 per language   (DBs with Locales/) Missions_<lang>.ts: unit-missions, "unit - <name>",
                                mission-categories; fr sheets Locales/<lang>/<translated name>.xml + .html
  every DB       Stages.xml + Stages_<lang>.ts: the stage labels (checkstages wants them in both DBs)
  dia5           the task XML skeleton when the task does not exist (types from the model's missions, as in
                 mission_index.md §1); its behaviors are left as TODO comments for the designer
Refuses (exit 1): name / dia-type / id already used, unknown model, category or frag order, unknown type or type
with no Spark converter, missing translations, context dia-names reused. Unknown spec keys are reported as notes.

Frag-order mode (spec key "fragorder": true; rules and anchors: docs/sw.models/FRAGORDERS.md; --example fragorder):
  per target DB  Missions.xml   <fragorders> entry appended: alphabetical attributes, no context block, the spec
                                parameters (+ transmitters / equipments children), id = max over every DB + 1
                 Models.xml     models_level {db: [model]} -> model-level <fragorders>; missions {db: {model:
                                [mission]}} -> <fragorder> under that mission (unit, automat, crowd models; a name
                                shared by two kinds takes a "unit:" / "automat:" / "crowd:" prefix)
                 sheets         Mission sheets/fragmentary orders/<name>.xml + .html (+ fr, images under the en name)
                 per language   Missions_<lang>.ts: frag-order, "frag-order - <name>" (parameter and value names,
                                deduplicated), mission-categories
  every DB       Stages.xml + Stages_<lang>.ts for a new task's stage labels
  dia5           FragOrder.lua (CRLF kept; entries inserted, never reordered), by "kind":
                   kernel     no entry: the dia-type is the task name, dia-names = the task's parameter names
                   task       an initializer reading each dia-name into mission.<task_param> (getter and ontology
                              wrapper measured on the existing initializers), returning true, "<task>"
                   read       an empty initializer (a reader must consume the order: TODO)
                   immediate  an initializer stub reading the parameters, a TODO effect, then cleanFragOrder
                   automat    commander only: no initializer, an executeAutomatFragOrder branch (automat_branch is
                              implied), automat models only
                 FRAGORDERS.md §2 kinds -> "kind": (c) kernel, (a) task, (b) read, (f) immediate, (d) automat (or
                 propagate / automat_branch on top of a unit kind), (e) task / immediate on a crowd task + crowd models
                 "after": the initializer key to insert after (default: the end of fragOrderTaskInitializers);
                 "propagate": true -> fragOrdersToBePropagated; "automat_branch": true -> a TODO branch in
                 executeAutomatFragOrder (for a propagated order it neither cleans nor returns true: propagation
                 follows and cleans the commander's copy); a new task (kinds kernel / task) -> a frago task
                 skeleton, one stage, disable-when-done unless "ends": false (an order ended by an opposite order),
                 its behavior ("behavior", "priority", default 100000.0)
  Frag-order spec keys: fragorder, kind, name, dia-type, task, dbs, category (+ new_category, fr_category), id,
  without_mission, models_level, missions, after, propagate, automat_branch, description, stages, task_skeleton,
  ends, behavior, priority, fr / sheet / sheet_fr, params (as above, plus task_param = the mission.<field> the
  initializer fills, getter / wrap = integration.get<getter>Parameter and its ontology key, for a type no
  initializer reads yet); "propagate" may name an existing entry to insert after (else appended: the last entry
  gains its comma). {"fragorder": true, "existing": true, "name", "dbs", "models_level", "missions"} adds an
  existing frag order to more Models.xml lists and writes nothing else.

Spec keys (--example shows them; keys starting with _ are ignored):
  name, task                      required
  dia-type                        default = task (must be unique among unit missions: reusing a task needs another)
  dbs                             default = every DB of the model
  category                        DBs with <categories> (mildb); new one: new_category true + fr_category
  id                              default = max id over every DB's Missions.xml + 1 (same id in each DB)
  models                          {db: [unit model names]}
  fragorders                      [names] for every model, or {db: [names]}: <fragorder> children of the mission
  fr                              {"name": French mission name} (needed for every DB with Locales/fr)
  description                     task <description> (skeleton only)
  stages                          [{"label", "fr"}]; default = the existing task's labels
  task_skeleton                   default true: write the task skeleton when the task does not exist
  ends                            default true: skeleton task is disable-when-done; false = a continuous mission
  sheet, sheet_fr                 {section: [line | {"ul": [line | [line, {"ul": ...}]]}]}, sections context,
                                  behavior, specific-cases, comments, mission-end
  params                          [{name, dia-name, type, optional, list | max-occurs + min-occurs, min-value,
                                  max-value, choices, objects ("all" | "none" | [types] | {db: ...}), units,
                                  values [{id, name, fr}], attrs {other ADN attributes}, fr, sheet, sheet_fr,
                                  default (skeleton <default>), task_type (skeleton type: class | number | boolean |
                                  string | any), description}]
"""
import argparse
import difflib
import html.entities
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import missionlib as ml  # noqa: E402

EXAMPLE = {
    "_comment": "A valid spec for model ada. Keys starting with _ are ignored; see the module docstring for every key.",
    "name": "COMBAT - Hold a crossing point",
    "task": "military.agent.tasks.HoldCrossingPoint",
    "_dia-type": "military.agent.tasks.HoldCrossingPoint (default = task)",
    "dbs": ["mildb", "worldwide-tests"],
    "category": "Defense",
    "_new_category": False,
    "_fr_category": "",
    "_id": 0,
    "models": {"mildb": ["Infantry platoon"], "worldwide-tests": ["Infantry platoon"]},
    "fragorders": ["Pause movement", "Resume movement"],
    "fr": {"name": "COMBAT - Tenir un point de passage"},
    "description": "For the agent, to move to a crossing point and hold it.",
    "params": [
        {"name": "Crossing point", "dia-name": "crossingPoint", "type": "LocationComposite", "optional": False,
         "choices": ["Point", "ObjectKnowledge"], "objects": {"mildb": "all", "worldwide-tests": "all"},
         "task_type": "common.agent.world.interfaces.Destination",
         "fr": "Point de passage", "sheet": "The point to hold.", "sheet_fr": "Le point à tenir."},
        {"name": "Mode", "dia-name": "mode", "type": "Enumeration", "optional": True, "default": 0,
         "values": [{"id": 0, "name": "Static", "fr": "Statique"}, {"id": 1, "name": "Mobile", "fr": "Mobile"}],
         "fr": "Mode", "sheet": "How to hold.", "sheet_fr": "Comment tenir."},
        {"name": "Reinforcements", "dia-name": "reinforcements", "type": "AgentKnowledge", "optional": True,
         "list": True, "fr": "Renforts"},
    ],
    "stages": [{"label": "moving to crossing point", "fr": "Déplacement vers le point de passage"},
               {"label": "holding crossing point", "fr": "Tenue du point de passage"}],
    "sheet": {"context": ["One line per entry."], "behavior": [{"ul": ["A bullet.", "Another."]}],
              "specific-cases": [], "comments": [], "mission-end": ["The mission ends when ..."]},
    "sheet_fr": {"context": ["Une ligne par entrée."], "behavior": [], "specific-cases": [], "comments": [],
                 "mission-end": []},
}
TOP_KEYS = {"name", "task", "dia-type", "dbs", "category", "new_category", "id", "models", "fragorders",
            "description", "stages", "task_skeleton", "ends", "params"}
PARAM_KEYS = {"name", "dia-name", "type", "optional", "list", "max-occurs", "min-occurs", "min-value", "max-value",
              "choices", "objects", "units", "values", "attrs", "default", "task_type", "description", "sheet",
              "transmitters", "equipments", "task_param", "getter", "wrap"}

SECTIONS = ["context", "behavior", "specific-cases", "comments", "mission-end"]
# The context parameters' sheet texts, as written in 237 of the mildb sheets (en) and 236 of the fr ones.
CONTEXT_SHEET = {
    "en": {"Danger direction": "Expected enemy direction.",
           "Phase lines": "Lines used by units to adapt their behavior when reached or crossed.",
           "Limit 1": "One of two geographical boundaries within which the entity performs the mission.",
           "Limit 2": "One of two geographical boundaries within which the entity performs the mission."},
    "fr": {"Danger direction": "Direction supposée de la menace.",
           "Phase lines": "Les unités adaptent leur comportement au franchissement de ces lignes.",
           "Limit 1": "Limite gauche ou droite permettant de délimiter le fuseau de l'unité dans le cadre de sa mission.",
           "Limit 2": "Limite gauche ou droite permettant de délimiter le fuseau de l'unité dans le cadre de sa mission."},
}
SCALAR_TASK_TYPE = {"boolean": "<boolean/>", "number": "<number/>", "string": "<string/>"}


class SpecError(Exception):
    pass


def xa(s):
    """XML attribute value (double-quoted)."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def xt(s):
    """XML text."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------------------------------------------------
# mission sheet .html: phy/missionSheets.xslt as ADN applies it (libxslt, HTML output, written with \r\r\n line ends).
# Byte-exact on the 780 sheets of model ada (see validate_html).

C2N = html.entities.codepoint2name
INLINE = {"b", "i", "u", "br", "img"}
EMPTY = {"br", "link", "META", "img"}
FMT_NL, RAW_NL = "\r\r\n", "\r\n"


def html_escape(s):
    out = []
    for ch in s:
        o = ord(ch)
        if ch == "&":
            out.append("&amp;")
        elif ch == "<":
            out.append("&lt;")
        elif ch == ">":
            out.append("&gt;")
        elif o > 127:
            out.append(f"&{C2N[o]};" if o in C2N else f"&#{o};")
        else:
            out.append(ch)
    return "".join(out)


class H:
    def __init__(self, tag, attrs="", kids=None):
        self.tag, self.attrs, self.kids = tag, attrs, kids if kids is not None else []


def _apply(el, imgdir):
    out = []
    for c in el:
        out += _template(c, imgdir)
    return out


def _template(c, imgdir):
    t = c.tag
    if t == "line":
        return _apply(c, imgdir) + [H("br")]
    if t in ("ul", "li"):
        return [H(t, "", _apply(c, imgdir))]
    if t in ("bold", "italic", "underlined"):
        return [H({"bold": "b", "italic": "i", "underlined": "u"}[t], "", _apply(c, imgdir))]
    if t == "link":
        name = c.findtext("text") or ""
        return [("raw", f'\n\n<img alt="{name}" src="{imgdir}{name}"/>')]
    text = "".join(c.itertext())  # <text> and the built-in rule for other elements
    return [("text", text)] if text else []


def _sheet_tree(root, css, imgdir):
    titles = [("context", "Context", 'border="thin solid #6495ed" padding="2px 10px" text-align="left" width="100"'),
              ("parameters", "Parameters", 'width="200"'), ("behavior", "Behavior", 'width="200"'),
              ("specific-cases", "Specific cases", 'width="200"'), ("comments", "Comments", 'width="200"'),
              ("mission-end", "End of mission", 'width="200"')]
    trs = []
    for tag, title, tdattrs in titles:
        tr = H("tr")
        for sec in root.findall(tag):
            if tag == "parameters":
                body = [H("h2", "", [("text", "Mandatory")])]
                for p in sec.findall("parameter"):
                    if p.get("optional") in (None, "false"):
                        body += [H("h3", "", [("text", p.get("name"))])] + _apply(p, imgdir)
                body.append(H("h2", "", [("text", "Optional")]))
                for p in sec.findall("parameter"):
                    if p.get("optional") == "true":
                        body += [H("h3", "", [("text", p.get("name"))])] + _apply(p, imgdir)
            else:
                body = _apply(sec, imgdir)
            tr.kids += [H("td", " " + tdattrs, [H("h2", "", [("text", title)])]), H("td", "", body)]
        trs.append(tr)
    name = root.get("name")
    head = H("head", "", [H("META", ' http-equiv="Content-Type" content="text/html; charset=utf-8"'),
                          H("title", "", [("text", name)]), H("link", f' href="{css}" rel="stylesheet"'),
                          H("h1", ' align="center"', [("text", "Mission : \n                  " + name)])])
    body = H("body", "", [H("table", ' align="center" border="1" cellpadding="5" width="100%"', trs)])
    return H("html", ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"', [head, body])


def _dump(n, parent, nxt, out):
    if isinstance(n, tuple):
        out.append(html_escape(n[1]).replace("\n", FMT_NL) if n[0] == "text" else n[1].replace("\n", RAW_NL))
        return
    kids = []
    for k in n.kids:  # adjacent text nodes merge, empty ones vanish
        if isinstance(k, tuple) and k[0] == "text":
            if not k[1]:
                continue
            if kids and isinstance(kids[-1], tuple) and kids[-1][0] == "text":
                kids[-1] = ("text", kids[-1][1] + k[1])
                continue
        kids.append(k)
    out.append(f"<{n.tag}{n.attrs}>")
    if kids:
        # measured: a newline after the start tag when the first child is an element, except <td> + inline child
        if not isinstance(kids[0], tuple) and not (n.tag == "td" and kids[0].tag in INLINE)                 and not n.tag.startswith("p"):
            out.append(FMT_NL)
        for i, k in enumerate(kids):
            _dump(k, n, kids[i + 1] if i + 1 < len(kids) else None, out)
        if not isinstance(kids[-1], tuple) and not n.tag.startswith("p"):
            out.append(FMT_NL)
    if n.tag not in EMPTY:
        out.append(f"</{n.tag}>")
    if nxt is not None and not isinstance(nxt, tuple) and parent is not None and not parent.tag.startswith("p") \
            and not (n.tag == "td" and nxt.tag == "td"):
        out.append(FMT_NL)


def render_sheet_html(xml_text, lang_dir_depth, imgdir=None):
    """Sheet XML text -> .html bytes. lang_dir_depth = 0 for Mission sheets/units/, 2 for .../Locales/<lang>/.
    imgdir = ADN's imageDirectory for <link> images (default ./Images/<name>/; existing sheets keep historical ones)."""
    root = ET.fromstring(xml_text.encode("utf-8"))
    css = "./" + "../" * (1 + lang_dir_depth) + "missionSheets.css"
    if imgdir is None:
        imgdir = "./" + "../" * lang_dir_depth + f"Images/{root.get('name')}/"
    out = []
    _dump(_sheet_tree(root, css, imgdir), None, None, out)
    out.append(FMT_NL)
    return "".join(out).encode("utf-8")


def validate_html(model):
    """Re-render every sheet of the model and compare with the .html on disk: (identical, total, first mismatches)."""
    same, tot, bad = 0, 0, []
    for db in model.dbs.values():
        dirs = []
        for top in (db.sheets_dir, db.fo_sheets_dir):
            dirs += [(top, 0)] + [(os.path.join(top, "Locales", lang), 2) for lang in db.ts]
        for d, depth in dirs:
            if not os.path.isdir(d):
                continue
            for f in sorted(os.listdir(d)):
                if not f.endswith(".xml") or not os.path.isfile(os.path.join(d, f[:-4] + ".html")):
                    continue
                tot += 1
                with open(os.path.join(d, f[:-4] + ".html"), "rb") as fh:
                    exp = fh.read()
                img = re.search(rb'<img alt="[^"]*" src="([^"]*/)', exp)
                img = img.group(1).decode("utf-8") if img else None
                if render_sheet_html(ml.read(os.path.join(d, f)), depth, img) == exp:
                    same += 1
                else:
                    bad.append(os.path.join(d, f))
    return same, tot, bad


# ---------------------------------------------------------------------------------------------------------------------
# file edits


class Edits:
    def __init__(self, model):
        self.model = model
        self.files = {}  # path -> [old text or None, new text]
        self.notes = []

    def get(self, path):
        if path not in self.files:
            old = None
            if os.path.isfile(path):
                with open(path, encoding="utf-8", newline="") as fh:
                    old = fh.read()
            self.files[path] = [old, old]
        return self.files[path][1]

    def set(self, path, text):
        self.get(path)
        self.files[path][1] = text

    def new(self, path, content):
        if os.path.exists(path):
            raise SpecError(f"{self.model.rel(path)} already exists")
        self.files[path] = [None, content]

    def diff(self):
        out = []
        for path, (old, new) in self.files.items():
            if old == new:
                continue
            rel = self.model.rel(path)
            if isinstance(new, bytes):
                out.append(f"--- /dev/null\n+++ b/{rel}\n(binary-exact .html, {len(new)} bytes, rendered from the sheet "
                           f".xml)\n")
                continue
            a = (old or "").splitlines(keepends=True)
            b = new.splitlines(keepends=True)
            out += difflib.unified_diff(a, b, "/dev/null" if old is None else f"a/{rel}", f"b/{rel}", n=2)
        return "".join(x if x.endswith("\n") else x + "\n" for x in out)

    def write(self):
        n = 0
        for path, (old, new) in self.files.items():
            if old == new:
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if isinstance(new, bytes):
                with open(path, "wb") as fh:
                    fh.write(new)
            else:
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(new)
            n += 1
        return n

    def changed(self):
        return [self.model.rel(p) for p, (o, n) in self.files.items() if o != n]


def insert_sorted(text, items, new_key, new_block, end_pos):
    """items = [(key, start offset)] in file order; insert new_block before the first item whose key > new_key if
    the keys are sorted (ordinal), else at end_pos."""
    keys = [k for k, _ in items]
    if keys == sorted(keys):
        for k, pos in items:
            if k > new_key:
                return text[:pos] + new_block + text[pos:]
    return text[:end_pos] + new_block + text[end_pos:]


# ---------------------------------------------------------------------------------------------------------------------
# builders


def param_xml(p, canonical, db):
    attrs = {"dia-name": p["dia-name"], "name": p["name"], "optional": "true" if p.get("optional") else "false",
             "type": canonical}
    if p.get("list") or p.get("max-occurs"):
        attrs["max-occurs"] = str(p.get("max-occurs", "unbounded"))
        attrs["min-occurs"] = str(p.get("min-occurs", "1"))
    for k in ("min-value", "max-value"):
        if k in p:
            attrs[k] = str(p[k])
    for k, v in (p.get("attrs") or {}).items():  # other ADN extras, e.g. indirect-fire-only
        attrs[k] = str(v)
    head = "      <parameter " + " ".join(f'{k}="{xa(v)}"' for k, v in sorted(attrs.items()))
    kids = []
    if p.get("choices"):
        kids.append("        <choice>")
        kids += [f'          <parameter type="{xa(c)}"/>' for c in p["choices"]]
        kids.append("        </choice>")
    obj = p.get("objects", "all" if canonical in ("LocationComposite", "ObjectKnowledge", "PlannedWork") else None)
    if isinstance(obj, dict):  # per DB
        obj = obj.get(db)
    if obj == "all":
        kids.append('        <objects all="true"/>')
    elif obj == "none":
        kids.append("        <objects/>")
    elif isinstance(obj, list):
        kids.append("        <objects>")
        kids += [f'          <parameter type="{xa(o)}"/>' for o in obj]
        kids.append("        </objects>")
    for tag in ("units", "transmitters", "equipments"):
        if p.get(tag):
            kids.append(f"        <{tag}>")
            kids += [f'          <parameter type="{xa(u)}"/>' for u in p[tag]]
            kids.append(f"        </{tag}>")
    for v in p.get("values") or []:
        kids.append(f'        <value id="{xa(v["id"])}" name="{xa(v["name"])}"/>')
    if not kids:
        return head + "/>\n"
    return head + ">\n" + "\n".join(kids) + "\n      </parameter>\n"


def sheet_lines(entries, indent):
    """Spec section entries (str = a line, {"ul": [str | {"ul": …}]} = a bullet list) -> sheet XML lines."""
    sp = " " * indent
    out = []
    for e in entries or []:
        if isinstance(e, dict) and "ul" in e:
            out.append(f"{sp}<ul>")
            for li in e["ul"]:
                out.append(f"{sp}  <li>")
                out += sheet_lines(li if isinstance(li, list) else [li], indent + 4)
                out.append(f"{sp}  </li>")
            out.append(f"{sp}</ul>")
        else:
            out += [f"{sp}<line>", f"{sp}  <text>{xt(e)}</text>", f"{sp}</line>"]
    return out


def sheet_xml(name, version_line, params, sections):
    """params = [(name, optional, text)]. version_line = the attributes after name= on <mission-sheet>."""
    L = ['<?xml version="1.0" encoding="UTF-8" standalone="no" ?>',
         f'<mission-sheet model-version="{version_line[0]}" name="{xa(name)}" '
         f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="{version_line[1]}">']

    def section(tag, entries):
        body = sheet_lines(entries, 4)
        if body:
            L.extend([f"  <{tag}>"] + body + [f"  </{tag}>"])
        else:
            L.append(f"  <{tag}/>")
    section("context", sections.get("context"))
    L.append("  <parameters>" if params else "  <parameters/>")
    for pname, opt, text in params:
        head = f'    <parameter name="{xa(pname)}" optional="{"true" if opt else "false"}"'
        body = sheet_lines([text] if text else [], 6)
        L.extend([head + ">"] + body + ["    </parameter>"] if body else [head + "/>"])
    if params:
        L.append("  </parameters>")
    for tag in SECTIONS[1:]:
        section(tag, sections.get(tag))
    L.append("  <attachments/>")
    L.append("</mission-sheet>")
    return "\n".join(L) + "\n"


def task_type_xml(model, canonical, is_list, override, catalog):
    """Inner XML of a task <type> for a Missions.xml type: the override, or the commonest task type the model's
    missions bind to that Missions.xml type (single / list)."""
    if override:
        inner = SCALAR_TASK_TYPE.get(override) or ("<any/>" if override == "any" else f'<object type="{xa(override)}"/>')
        return f"<list>{inner}</list>" if is_list and override != "any" else inner
    seen = catalog.get((canonical, is_list))
    if seen:
        return seen.most_common(1)[0][0]
    return None


def type_catalog(model):
    """(canonical type, is_list) -> Counter of task <type> inner XML, over every unit mission of every DB."""
    cat = defaultdict(Counter)
    for db in model.dbs.values():
        for m in db.unit_missions:
            task = model.task(m.task_name)
            if task is None:
                continue
            root = ET.fromstring(task.raw.encode("utf-8"))
            ps = root.find(ml.SPARK_NS + "parameters")
            types = {}
            for p in (ps if ps is not None else []):
                t = p.find(ml.SPARK_NS + "type")
                if t is not None:
                    types[p.get("name")] = "".join(
                        re.sub(r"\s+", "", ET.tostring(k, encoding="unicode").replace(f' xmlns:ns0="{ml.SPARK_NS[1:-1]}"', ""))
                        .replace("ns0:", "").replace("<objecttype=", '<object type=') for k in t)
            for p in m.own_params():
                c = model.types.canonical(p.type)
                if c and p.dia_name in types:
                    cat[(c, p.is_list)][types[p.dia_name]] += 1
    return cat


def default_xml(p, shape):
    d = p.get("default")
    if d is None:
        return None
    if isinstance(d, bool):
        return f"<boolean>{'true' if d else 'false'}</boolean>"
    if isinstance(d, (int, float)):
        return f"<number>{d!r}</number>"  # dia5 writes both 30 and 1.0: keep the spec's form
    return f"<string>{xt(d)}</string>"


def pretty_xml(fragment, indent):
    """'<list><object type="x"/></list>' -> indented lines, one element per line (the dia5 task style)."""
    out = []

    def walk(e, depth):
        sp = " " * (indent + 4 * depth)
        attrs = "".join(f' {k}="{xa(v)}"' for k, v in e.attrib.items())
        text = (e.text or "").strip()
        if len(e):
            out.append(f"{sp}<{e.tag}{attrs}>")
            for k in e:
                walk(k, depth + 1)
            out.append(f"{sp}</{e.tag}>")
        elif text:
            out.append(f"{sp}<{e.tag}{attrs}>{xt(text)}</{e.tag}>")
        else:
            out.append(f"{sp}<{e.tag}{attrs}/>")
    for e in ET.fromstring(f"<r>{fragment}</r>"):
        walk(e, 0)
    return out


def task_skeleton(spec, params, labels, frago=False):
    L = ['<?xml version="1.0" encoding="UTF-8"?>',
         f'<task xmlns="http://www.masagroup.net/directai/spark/lang/1.5" name="{xa(spec["task"])}"'
         + (' disable-when-done="true">' if spec.get("ends", True) else ">"),
         f'    <description>{xt(spec.get("description", "TODO"))}</description>']
    if params:
        L.append("    <parameters>")
    for p, inner, nullable, dflt in params:
        L.append(f'        <parameter name="{xa(p["dia-name"])}"{" nullable=" + chr(34) + "true" + chr(34) if nullable else ""}>')
        L.append(f'            <description>{xt(p.get("description") or p.get("sheet") or p["name"])}</description>')
        L.append("            <type>")
        L += pretty_xml(inner, 16)
        L.append("            </type>")
        if dflt:
            L += ["            <default>"] + pretty_xml(dflt, 16) + ["            </default>"]
        L.append("        </parameter>")
    if params:
        L.append("    </parameters>")
    L.append("    <stages>")
    for i, lab in enumerate(labels):
        last_open = i == len(labels) - 1 and not spec.get("ends", True)
        todo = ("runs until the mission is stopped: no next-stage-when-done" if last_open else
                "one with next-stage-when-done")
        L += [f'        <stage label="{xa(lab)}">', "            <behaviors>"]
        if frago and spec.get("behavior") and i == len(labels) - 1:
            nswd = ' next-stage-when-done="true"' if spec.get("ends", True) else ""
            L.append(f'                <behavior name="{xa(spec["behavior"])}"{nswd} priority="{xa(spec.get("priority", "100000.0"))}">')
            bound = [p["dia-name"] for p, _, _, _ in params if p["dia-name"] in spec.get("_skill_params", ())]
            for n in bound:
                L += [f'                    <parameter name="{xa(n)}">', f"                        <variable>{xa(n)}</variable>",
                      "                    </parameter>"]
            rest = [n for n in spec.get("_skill_params", ()) if n not in bound]
            if rest or not spec.get("_skill_params"):
                L.append("                    <!-- TODO: bind the skill parameters"
                         + (f" {', '.join(rest)}" if rest else "") + ": <parameter name=\"…\"><variable>…</variable></parameter> -->")
            L.append("                </behavior>")
        elif frago:
            L.append(f"                <!-- TODO: behaviors of stage \"{xt(lab)}\" ({todo}), each with a priority "
                     f"(e.g. 100000.0) over the mission's skills on shared resources: FRAGORDERS.md §5 -->")
        else:
            L.append(f"                <!-- TODO: behaviors of stage \"{xt(lab)}\" ({todo}) -->")
        L += ["            </behaviors>", "        </stage>"]
    L.append("    </stages>")
    L.append("</task>")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------------------------------------------------
# scaffold


def ts_message(source, translation, indent=4):
    sp = " " * indent
    return (f"{sp}<message>\n{sp}  <source>{xt(source)}</source>\n{sp}  <translation>{xt(translation)}</translation>\n"
            f"{sp}</message>\n")


def ts_add_message(text, context, source, translation):
    """Insert a message in an existing context at its sorted position (no-op if the source is there)."""
    m = re.search(rf"(?m)^  <context>\n    <name>{re.escape(xt(context))}</name>\n", text)
    if m is None:
        raise SpecError(f"context {context} not found")
    end = text.find("  </context>", m.end())
    block = text[m.end():end]
    items = [(ET.fromstring(f"<s>{s}</s>").text or "", m.end() + mm.start())
             for mm, s in ((x, x.group(1)) for x in re.finditer(r"(?m)^    <message>\n      <source>(.*?)</source>", block))]
    if source in {k for k, _ in items}:
        return text
    return insert_sorted(text, items, source, ts_message(source, translation), end)


def ts_add_context(text, context, messages):
    if re.search(rf"(?m)^    <name>{re.escape(xt(context))}</name>$", text):
        raise SpecError(f"context {context} already exists")
    items = [(ET.fromstring(f"<s>{x.group(1)}</s>").text or "", x.start())
             for x in re.finditer(r"(?m)^  <context>\n    <name>(.*?)</name>", text)]
    block = f"  <context>\n    <name>{xt(context)}</name>\n" + "".join(
        ts_message(s, t) for s, t in sorted(messages.items())) + "  </context>\n"
    return insert_sorted(text, items, context, block, text.rfind("</TS>"))


def scaffold(model, spec, sword_types):
    ed = Edits(model)
    errors = []

    def need(cond, msg):
        if not cond:
            errors.append(msg)
        return cond

    name, task = spec.get("name"), spec.get("task")
    need(name and task, "spec needs name and task")
    if errors:
        raise SpecError("\n".join(errors))
    dia_type = spec.get("dia-type", task)
    dbs = spec.get("dbs") or list(model.dbs)
    for d in dbs:
        need(d in model.dbs, f"unknown DB {d}; have {list(model.dbs)}")
    dbs = [model.dbs[d] for d in dbs if d in model.dbs]
    mid = str(spec.get("id") or model.max_id() + 1)
    for db in model.dbs.values():
        need(mid not in db.ids, f"id {mid} already used in {db.name}: {db.ids.get(mid)}")
    for db in dbs:
        need(name not in db.unit_missions_by_name, f"{db.name}: a unit mission is already named \"{name}\"")
        same_dt = [m.name for m in db.unit_missions if m.attrs.get("dia-type") == dia_type]
        need(not same_dt, f"{db.name}: dia-type {dia_type} already used by {same_dt} (set another \"dia-type\")")
    # params
    ctx_names = {c["dia-name"] for c in ml.CONTEXT} | {c["name"] for c in ml.CONTEXT}
    params = spec.get("params") or []
    seen = set()
    canon = {}
    for p in params:
        need(p.get("name") and p.get("dia-name") and p.get("type"), f"parameter {p} needs name, dia-name and type")
        if not (p.get("name") and p.get("dia-name") and p.get("type")):
            continue
        need(p["dia-name"] not in ctx_names and p["name"] not in ctx_names,
             f"parameter {p['name']}: the context parameters are added automatically")
        need(p["dia-name"] not in seen, f"dia-name {p['dia-name']} twice")
        seen.add(p["dia-name"])
        is_list = bool(p.get("list") or p.get("max-occurs"))
        if sword_types.ok:
            c = sword_types.canonical(p["type"])
            if need(c, f"parameter {p['name']}: unknown type {p['type']}"):
                need(sword_types.converter(c, is_list),
                     f"parameter {p['name']}: {c}{'List' if is_list else ''} has no BM converter (value never passed)")
            canon[p["dia-name"]] = c or p["type"]
        else:
            canon[p["dia-name"]] = p["type"]
        if canon[p["dia-name"]] == "Enumeration":
            need(p.get("values"), f"parameter {p['name']}: Enumeration needs values")
    # translations needed
    langs = sorted({lang for db in dbs for lang, kinds in db.ts.items() if "Missions" in kinds})
    for lang in langs:
        tr = spec.get(lang) or {}
        need(tr.get("name"), f"spec.{lang}.name (translated mission name) missing")
        for p in params:
            need(p.get(lang), f"parameter {p.get('name')}: \"{lang}\" translation missing")
            for v in p.get("values") or []:
                need(v.get(lang), f"parameter {p.get('name')} value {v.get('name')}: \"{lang}\" translation missing")
    # stages
    existing_task = model.task(task)
    if spec.get("stages"):
        stages = [(s["label"], s) for s in spec["stages"]]
    elif existing_task is not None:
        stages = [(lab, {}) for lab in existing_task.labels()]
    else:
        stages = []
        need(False, f"task {task} does not exist and the spec gives no stages")
    if existing_task is not None and spec.get("stages"):
        need([lab for lab, _ in stages] == existing_task.labels(),
             f"spec stages {[lab for lab, _ in stages]} differ from the existing task's {existing_task.labels()}")
    for lab, s in stages:
        for db in model.dbs.values():
            for lang, kinds in db.ts.items():
                if "Stages" in kinds and lab not in kinds["Stages"].get("stage", {}):
                    need(s.get(lang), f"stage \"{lab}\": \"{lang}\" translation missing (Stages_{lang}.ts)")
    # categories
    cat = spec.get("category")
    for db in dbs:
        if db.categories:
            if need(cat, f"{db.name}: category required (this DB has <categories>: {db.categories})"):
                need(cat in db.categories or spec.get("new_category"),
                     f"{db.name}: category \"{cat}\" not in {db.categories} (or set new_category + fr_category)")
    # models
    models = spec.get("models") or {}
    for db in dbs:
        need(models.get(db.name), f"{db.name}: models lists no unit model (no unit could receive the mission)")
        for um in models.get(db.name) or []:
            need(um in db.models, f"{db.name}: unknown unit model \"{um}\"")
    # frag orders (Models.xml <mission><fragorder name/>): names from the DB's Missions.xml <fragorders>
    fo_spec = spec.get("fragorders") or []
    frags = {}
    for db in dbs:
        frags[db.name] = list(fo_spec.get(db.name) or []) if isinstance(fo_spec, dict) else list(fo_spec)
        known = {n for entries in db.ids.values() for sec, _, n in entries if sec == "fragorders"}
        for f in frags[db.name]:
            need(f in known, f"{db.name}: unknown frag order \"{f}\" (not in Missions.xml <fragorders>)")
    # unknown keys: reported, not fatal (a typo would otherwise be silently ignored)
    lang_keys = {k for lang in langs for k in (lang, f"{lang}_category", f"sheet_{lang}")}
    for k in set(spec) - TOP_KEYS - lang_keys - {"sheet", "fr_category", "sheet_fr", "fr"}:
        ed.notes.append(f"unknown spec key \"{k}\" ignored")
    for p in params:
        for k in set(p) - PARAM_KEYS - {lang for lang in langs} - {f"sheet_{lang}" for lang in langs} - {"fr", "sheet_fr"}:
            if not k.startswith("_"):
                ed.notes.append(f"parameter {p.get('name')}: unknown key \"{k}\" ignored")
    if errors:
        raise SpecError("\n".join(errors))

    # ---- Missions.xml
    for db in dbs:
        text = ed.get(db.missions_path)
        attrs = {"dia-behavior": task, "dia-type": dia_type, "id": mid, "name": name}
        if db.categories:
            attrs["category"] = cat
        block = ("    <mission " + " ".join(f'{k}="{xa(v)}"' for k, v in sorted(attrs.items())) + ">\n"
                 + ml.CONTEXT_XML + "".join(param_xml(p, canon[p["dia-name"]], db.name) for p in params) + "    </mission>\n")
        end = re.search(r"(?m)^  </units>", text).start()
        text = text[:end] + block + text[end:]
        if db.categories and cat not in db.categories:
            cend = re.search(r"(?m)^  </categories>", text).start()
            text = text[:cend] + f'    <category name="{xa(cat)}"/>\n' + text[cend:]
        ed.set(db.missions_path, text)

    # ---- Models.xml
    for db in dbs:
        text = ed.get(db.models_path)
        for um in models[db.name]:
            if name in db.models.get(um, []):
                continue
            u = re.search(rf'(?m)^    <unit [^>]*\bname="{re.escape(xa(um))}"[^>]*>\n', text)
            if u is None:
                raise SpecError(f"{db.name}: unit model {um} not found in {db.models_path}")
            uend = text.find("    </unit>", u.end())
            ms = re.compile(r"(?m)^      <missions/>\n").search(text, u.end(), uend)
            if frags[db.name]:
                line = (f'        <mission name="{xa(name)}">\n'
                        + "".join(f'          <fragorder name="{xa(f)}"/>\n' for f in frags[db.name])
                        + "        </mission>\n")
            else:
                line = f'        <mission name="{xa(name)}"/>\n'
            if ms:
                text = text[:ms.start()] + "      <missions>\n" + line + "      </missions>\n" + text[ms.end():]
                continue
            mstart = re.compile(r"(?m)^      <missions>\n").search(text, u.end(), uend)
            if mstart is None:
                raise SpecError(f"{db.name}: unit model {um} has no <missions> element")
            mend = text.find("      </missions>", mstart.end())
            items = [(ET.fromstring(f"<s>{x.group(1)}</s>").text, mstart.end() + x.start())
                     for x in re.finditer(r'(?m)^        <mission name="([^"]*)"', text[mstart.end():mend])]
            text = insert_sorted(text, items, name, line, mend)
        ed.set(db.models_path, text)

    # ---- Stages.xml (every DB) and Stages_<lang>.ts
    for db in model.dbs.values():
        if os.path.isfile(db.stages_path):
            text = ed.get(db.stages_path)
            for lab, _ in stages:
                if lab in db.stages:
                    continue
                items = [(ET.fromstring(f'<s k="{x.group(1)}"/>').get("k"), x.start())
                         for x in re.finditer(r'(?m)^  <stage key="([^"]*)"', text)]
                text = insert_sorted(text, items, lab, f'  <stage key="{xa(lab)}" value="{xa(lab)}"/>\n',
                                     text.rfind("</stages>"))
            ed.set(db.stages_path, text)
        for lang, kinds in db.ts.items():
            if "Stages" not in kinds:
                continue
            p = os.path.join(db.locales, lang, f"Stages_{lang}.ts")
            text = ed.get(p)
            for lab, s in stages:
                if lab not in kinds["Stages"].get("stage", {}):
                    text = ts_add_message(text, "stage", lab, s[lang])
            ed.set(p, text)

    # ---- Missions_<lang>.ts
    for db in dbs:
        for lang in langs:
            if "Missions" not in db.ts.get(lang, {}):
                continue
            p = os.path.join(db.locales, lang, f"Missions_{lang}.ts")
            text = ed.get(p)
            tr = spec[lang]
            text = ts_add_message(text, "unit-missions", name, tr["name"])
            msgs = {}
            for prm in params:
                msgs[prm["name"]] = prm[lang]
                for v in prm.get("values") or []:
                    msgs[v["name"]] = v[lang]
            if msgs:
                text = ts_add_context(text, f"unit - {name}", msgs)
            if db.categories and cat not in db.categories:
                if not spec.get(f"{lang}_category"):
                    raise SpecError(f"new category \"{cat}\": {lang}_category missing")
                text = ts_add_message(text, "mission-categories", cat, spec[f"{lang}_category"])
            ed.set(p, text)

    # ---- sheets
    for db in dbs:
        head = re.search(r'<missions model-version="([^"]*)"[^>]*xsi:noNamespaceSchemaLocation="([^"]*)/Missions\.xsd"',
                         ed.get(db.missions_path))
        version = (head.group(1), head.group(2) + "/missionSheet.xsd") if head else ("4.8.2", "schemas/physical/missionSheet.xsd")
        sp = [(c["name"], c["optional"] == "true", CONTEXT_SHEET["en"][c["name"]]) for c in ml.CONTEXT]
        sp += [(prm["name"], bool(prm.get("optional")), prm.get("sheet")) for prm in params]
        x = sheet_xml(name, version, sp, spec.get("sheet") or {})
        ed.new(os.path.join(db.sheets_dir, f"{name}.xml"), x)
        ed.new(os.path.join(db.sheets_dir, f"{name}.html"), render_sheet_html(x, 0))
        for lang in langs:
            if "Missions" not in db.ts.get(lang, {}):
                continue
            ctx_tr = db.ts[lang]["Missions"].get("unit-context", {})
            sp = [(ctx_tr.get(c["name"]) or c["name"], c["optional"] == "true", CONTEXT_SHEET.get(lang, {}).get(c["name"]))
                  for c in ml.CONTEXT]
            sp += [(prm[lang], bool(prm.get("optional")), prm.get(f"sheet_{lang}")) for prm in params]
            stem = spec[lang]["name"]
            d = os.path.join(db.sheets_dir, "Locales", lang)
            x = sheet_xml(stem, version, sp, spec.get(f"sheet_{lang}") or {})
            ed.new(os.path.join(d, f"{stem}.xml"), x)
            ed.new(os.path.join(d, f"{stem}.html"), render_sheet_html(x, 2))

    # ---- task skeleton
    if existing_task is None and spec.get("task_skeleton", True):
        catalog = type_catalog(model)
        tparams = []
        for prm in params:
            c = canon[prm["dia-name"]]
            is_list = bool(prm.get("list") or prm.get("max-occurs"))
            inner = task_type_xml(model, c, is_list, prm.get("task_type"), catalog)
            if inner is None:
                inner = "<any/>"
                ed.notes.append(f"task parameter {prm['dia-name']}: no mission binds a {c}{' list' if is_list else ''} "
                                f"yet, typed <any/>; set task_type")
            absent = sword_types.absent_value(c, is_list) if sword_types.ok else None
            dflt = default_xml(prm, inner)
            nullable = bool(prm.get("optional")) and (absent == "NIL" or dflt is None)
            if prm.get("optional") and absent == "nil" and dflt is None:
                ed.notes.append(f"task parameter {prm['dia-name']}: optional scalar without \"default\": made nullable "
                                f"(it arrives as nil)")
            tparams.append((prm, inner, nullable, dflt))
        ed.new(model.entity_path(task), task_skeleton(spec, tparams, [lab for lab, _ in stages]))
        ed.notes.append(f"task skeleton {model.rel(model.entity_path(task))}: fill each stage's behaviors (TODO)")
    elif existing_task is None:
        ed.notes.append(f"task {task} does not exist and task_skeleton is false: write it before mission_check")
    return ed

# ---------------------------------------------------------------------------------------------------------------------
# frag orders (rules and anchors: docs/sw.models/FRAGORDERS.md)

EXAMPLE_FO = {
    "_comment": "A valid frag-order spec for model ada (kinds: kernel | task | read | immediate; FRAGORDERS.md §2).",
    "fragorder": True,
    "kind": "task",
    "name": "Wait at a point",
    "dia-type": "WaitAtPoint",
    "task": "common.agent.tasks.frago.WaitThere",
    "_task": "kernel / task kinds; a new task gets a skeleton (stages, behavior, priority, ends)",
    "dbs": ["mildb", "worldwide-tests"],
    "category": "Coordination",
    "without_mission": False,
    "models_level": {"mildb": [], "worldwide-tests": []},
    "missions": {"mildb": {"Infantry platoon": ["COMMON - Move"]},
                 "worldwide-tests": {"Infantry platoon": ["COMMON - Move"]}},
    "after": "PauseMovement",
    "_after": "insert the initializer after this related key (default: end of the table; FRAGORDERS.md §1 prefers next to a related entry)",
    "propagate": False,
    "automat_branch": False,
    "ends": True,
    "_ends": "new task skeleton only: false = ended by an opposite order or the mission stop",
    "_new_task_keys": "stages [{label, fr}], behavior, priority, description, task_skeleton (default true)",
    "fr": {"name": "Attendre en un point"},
    "params": [
        {"name": "Delay", "dia-name": "delay", "type": "Integer", "optional": True, "task_param": "delay",
         "fr": "Délai", "sheet": "Waiting time in seconds.", "sheet_fr": "Durée d'attente en secondes.",
         "_getter": "only for a type no initializer reads yet: \"getter\": \"Point\", \"wrap\": \"point\""},
    ],
    "sheet": {"context": ["Stops the agent's movement for a given time."], "behavior": [], "specific-cases": [],
              "comments": [], "mission-end": ["The order ends when the delay is over."]},
    "sheet_fr": {"context": ["Arrête le mouvement de l'agent pendant une durée donnée."], "behavior": [],
                 "specific-cases": [], "comments": [], "mission-end": []},
}
FO_TOP_KEYS = {"fragorder", "kind", "name", "dia-type", "task", "dbs", "category", "new_category", "id",
               "without_mission", "models_level", "missions", "after", "propagate", "automat_branch", "description",
               "stages", "task_skeleton", "ends", "behavior", "priority", "params"}
FO_KINDS = ("kernel", "task", "read", "immediate", "automat")
MODEL_KINDS = ("unit", "automat", "crowd", "task-group")


def getter_catalog(model):
    """(canonical type, is_list) -> Counter of (getter stem, ontology key or None) over the FragOrder.lua
    initializers that read a parameter of that type (e.g. Point -> ("Point", "point"))."""
    reg = model.fragorder_lua()
    cat = defaultdict(Counter)
    for db in model.dbs.values():
        for fo in db.fragorders:
            init = reg.init.get(fo.dia_type)
            if init is None:
                continue
            pt = {q.dia_name: (model.types.canonical(q.type) or q.type, q.is_list) for q in fo.params}
            for line in init.code.splitlines():
                g = re.search(r'integration\.get(\w+?)Parameter\s*\(\s*fragOrder\s*,\s*"([^"]+)"', line)
                if g and g.group(2) in pt:
                    w = re.search(r"CreateKnowledge\(\s*integration\.ontology\.types\.(\w+)", line)
                    cat[pt[g.group(2)]][(g.group(1), w.group(1) if w else None)] += 1
    return cat


def resolve_model(db, ref):
    """'Name' or 'kind:Name' -> (kind, name) of a Models.xml model, or an error string."""
    if ":" in ref and ref.split(":", 1)[0] in MODEL_KINDS:
        k, n = ref.split(":", 1)
        return (k, n) if (k, n) in db.model_entries else f"{db.name}: no {k} model \"{n}\""
    hits = [k for k in db.model_entries if k[1] == ref]
    if not hits:
        return f"{db.name}: unknown model \"{ref}\""
    if len(hits) > 1:
        return f"{db.name}: model name \"{ref}\" is used by {[k for k, _ in hits]} models: write \"{hits[0][0]}:{ref}\""
    return hits[0]


def models_add(text, kind, model_name, fo_name, mission=None):
    """Append <fragorder name/> to a model's model-level list, or under one of its missions (Models.xml)."""
    u = re.search(rf'(?m)^    <{kind} [^>]*\bname="{re.escape(xa(model_name))}"[^>]*>\n', text)
    if u is None:
        raise SpecError(f"{kind} model {model_name} not found in Models.xml")
    uend = text.find(f"    </{kind}>", u.end())
    if mission is None:
        line = f'        <fragorder name="{xa(fo_name)}"/>\n'
        empty = re.compile(r"(?m)^      <fragorders/>\n").search(text, u.end(), uend)
        if empty:
            return text[:empty.start()] + "      <fragorders>\n" + line + "      </fragorders>\n" + text[empty.end():]
        op = re.compile(r"(?m)^      <fragorders>\n").search(text, u.end(), uend)
        if op:
            close = text.find("      </fragorders>", op.end())
            if line in text[op.end():close]:
                return text
            return text[:close] + line + text[close:]
        ms = re.compile(r"(?m)^      (?:</missions>|<missions/>)\n").search(text, u.end(), uend)
        if ms is None:
            raise SpecError(f"{kind} model {model_name}: neither <fragorders> nor <missions> found")
        return text[:ms.end()] + "      <fragorders>\n" + line + "      </fragorders>\n" + text[ms.end():]
    line = f'          <fragorder name="{xa(fo_name)}"/>\n'
    sc = re.compile(rf'(?m)^        <mission name="{re.escape(xa(mission))}"/>\n').search(text, u.end(), uend)
    if sc:
        return (text[:sc.start()] + f'        <mission name="{xa(mission)}">\n' + line + "        </mission>\n"
                + text[sc.end():])
    op = re.compile(rf'(?m)^        <mission name="{re.escape(xa(mission))}">\n').search(text, u.end(), uend)
    if op is None:
        raise SpecError(f"{kind} model {model_name} has no mission {mission}")
    close = text.find("        </mission>", op.end())
    if line in text[op.end():close]:
        return text
    return text[:close] + line + text[close:]


def initializer_lines(kind, dt, task, params, canon, catalog):
    """FragOrder.lua initializer for kinds task / read / immediate (LF; the caller restores CRLF)."""
    L = [f'    [ "{dt}" ] = function( mission, fragOrder )']
    reads = []
    for prm in params:
        c, is_list = canon[prm["dia-name"]], bool(prm.get("list") or prm.get("max-occurs"))
        if prm.get("getter"):
            g, w = prm["getter"], prm.get("wrap")
        else:
            g, w = catalog[(c, is_list)].most_common(1)[0][0]
        reads.append((prm, f'integration.get{g}Parameter( fragOrder, "{prm["dia-name"]}" )', w))
    if kind == "task":
        for prm, call, w in reads:
            field = prm.get("task_param", prm["dia-name"])
            if w and prm.get("optional"):
                L += [f"        local {prm['dia-name']} = {call}", f"        if {prm['dia-name']} ~= nil then",
                      f"            mission.{field} = CreateKnowledge( integration.ontology.types.{w}, {prm['dia-name']} )",
                      "        end"]
            elif w:
                L.append(f"        mission.{field} = CreateKnowledge( integration.ontology.types.{w}, {call} )")
            else:
                L.append(f"        mission.{field} = {call}")
        L.append(f'        return true, "{task}"')
    elif kind == "read":
        L.append(f'        -- read by skills of the running mission (TODO: the reader consumes the order with '
                 f'integration.query.getFirstFragOrderFromType( "{dt}" ) and persists what it derives; FRAGORDERS.md §2, '
                 'CHECKPOINTS.md §4)')
    else:  # immediate
        for prm, call, w in reads:
            L.append(f"        local {prm['dia-name']} = {call}")
        L += ["        -- TODO: the immediate effect; keep it in the ODB / world, not in myself.* (lost on resume: "
              "CHECKPOINTS.md §7.4)", "        integration.cleanFragOrder( fragOrder )"]
    L.append("    end,")
    return L


def edit_fragorder_lua(raw, reg, dt, init_lines, after, propagate, automat_branch):
    """(propagate + automat_branch: the branch must not clean the order nor return true; FragOrder.lua's own
    comment: the order is reused for the units, and propagation cleans the commander's copy.)"""
    """Insert entries into FragOrder.lua text (bottom to top, so the parser's line numbers stay valid)."""
    crlf = "\r\n" in raw
    lines = raw.replace("\r\n", "\n").split("\n")
    code = reg.code
    if init_lines:
        if after:
            at = reg.init[after].end_line  # insert after the entry's `end,` line
            lines[at:at] = [""] + init_lines
        else:
            close = ml.line_of(code, reg.init_span[1] - 1)  # the line holding the table's `}`
            lines[close - 1:close - 1] = [""] + init_lines
    if automat_branch:
        a0, a1 = (ml.line_of(code, x) for x in reg.automat_span)
        idx = next((i for i in range(a0 - 1, a1) if "integration.mustBePropagate" in lines[i]), None)
        j = idx - 1 if idx is not None else None
        while j is not None and j > a0 and not lines[j].strip():
            j -= 1
        if j is None or lines[j] != "    end":
            raise SpecError("FragOrder.lua: executeAutomatFragOrder does not end its orderType chain with `    end` "
                            "before the mustBePropagate fallback: add the branch by hand")
        if propagate:
            lines[j:j] = [f'    elseif orderType == "{dt}" then',
                          "        -- TODO: what the commander does before propagation (do not clean the order nor "
                          "return true: it is propagated to the subordinates next, FRAGORDERS.md §6)"]
        else:
            lines[j:j] = [f'    elseif orderType == "{dt}" then',
                          "        -- TODO: what the commander does: act, or forward to the subordinates with "
                          "integration.SendMessage( \"StartTask\", … ) / meKnowledge:sendDynamicMessage( \"FragOrder\", … )",
                          "        integration.cleanFragOrder( fragOrder )", "        return true"]
    if propagate and isinstance(propagate, str):
        hit = [ln for n, ln in reg.propagated if n == propagate]
        if not hit:
            raise SpecError(f"\"propagate\": {propagate} is not in fragOrdersToBePropagated")
        if hit[0] == reg.propagated[-1][1]:
            propagate = True  # the anchor is the last entry: append
        else:
            lines[hit[0]:hit[0]] = [f'    "{dt}",']
            propagate = None
    if propagate:
        name, ln = reg.propagated[-1]
        m = re.match(r'^(\s*"[^"]*")(\s*,)?(.*)$', lines[ln - 1])
        if m is None:
            raise SpecError("FragOrder.lua: unexpected last line of fragOrdersToBePropagated")
        if not m.group(2):
            lines[ln - 1] = m.group(1) + "," + m.group(3)
        lines[ln:ln] = [f'    "{dt}"']
    out = "\n".join(lines)
    return out.replace("\n", "\r\n") if crlf else out


def add_existing(model, spec, ed):
    """"existing": true: add an existing frag order to more Models.xml lists (models_level / missions), per DB."""
    name = spec.get("name")
    errors = []
    dbs = [model.dbs[d] for d in (spec.get("dbs") or list(model.dbs)) if d in model.dbs]
    lvl_spec, msn_spec = spec.get("models_level") or {}, spec.get("missions") or {}
    todo = defaultdict(list)
    for db in dbs:
        if name not in db.fragorders_by_name:
            errors.append(f"{db.name}: no frag order named \"{name}\" (\"existing\" only adds Models.xml entries)")
            continue
        for ref in lvl_spec.get(db.name) or []:
            r = resolve_model(db, ref)
            if isinstance(r, str):
                errors.append(r)
            elif name in db.model_entries[r]["level"]:
                ed.notes.append(f"{db.name}: {r[0]} \"{r[1]}\" already lists it at model level")
            else:
                todo[db.name].append((r, None))
        for ref, missions in (msn_spec.get(db.name) or {}).items():
            r = resolve_model(db, ref)
            if isinstance(r, str):
                errors.append(r)
                continue
            for mi in missions:
                fs = db.model_entries[r]["missions"].get(mi)
                if fs is None:
                    errors.append(f"{db.name}: {r[0]} model \"{r[1]}\" has no mission \"{mi}\"")
                elif name in fs:
                    ed.notes.append(f"{db.name}: {r[0]} \"{r[1]}\" already lists it under \"{mi}\"")
                else:
                    todo[db.name].append((r, mi))
    for k in set(spec) - {"fragorder", "existing", "name", "dbs", "models_level", "missions"}:
        if not k.startswith("_"):
            ed.notes.append(f"\"existing\" mode ignores key \"{k}\"")
    if errors:
        raise SpecError("\n".join(errors))
    for db in dbs:
        if todo[db.name]:
            text = ed.get(db.models_path)
            for (k, mo), mi in todo[db.name]:
                text = models_add(text, k, mo, name, mi)
            ed.set(db.models_path, text)
    return ed


def scaffold_fragorder(model, spec, sword_types):
    ed = Edits(model)
    errors = []

    def need(cond, msg):
        if not cond:
            errors.append(msg)
        return cond

    reg = model.fragorder_lua()
    if spec.get("existing"):
        return add_existing(model, spec, ed)
    kind = spec.get("kind", "kernel")
    name, task = spec.get("name"), spec.get("task")
    need(kind in FO_KINDS, f"kind {kind} not in {FO_KINDS}")
    need(name, "spec needs name")
    if kind == "kernel":
        dt = spec.get("dia-type", task)
        need(task, "kind kernel needs task (the task the kernel starts)")
        need(dt == task, "kind kernel: the dia-type must be the task name (the kernel starts the task named by it)")
    else:
        dt = spec.get("dia-type")
        need(dt, f"kind {kind} needs a dia-type")
        if kind == "task":
            need(task, "kind task needs task")
        elif task:
            need(False, f"kind {kind} starts no task: drop \"task\"")
        if kind == "automat":
            spec = dict(spec, automat_branch=True)
    if errors:
        raise SpecError("\n".join(errors))
    dbs = spec.get("dbs") or list(model.dbs)
    for d in dbs:
        need(d in model.dbs, f"unknown DB {d}; have {list(model.dbs)}")
    dbs = [model.dbs[d] for d in dbs if d in model.dbs]
    fid = str(spec.get("id") or model.max_id() + 1)
    for db in model.dbs.values():
        need(fid not in db.ids, f"id {fid} already used in {db.name}: {db.ids.get(fid)}")
    for db in dbs:
        need(name not in db.fragorders_by_name, f"{db.name}: a frag order is already named \"{name}\"")
        same = [f.name for f in db.fragorders_by_dia_type.get(dt, [])]
        need(not same, f"{db.name}: dia-type {dt} already used by frag order(s) {same} (the sim keeps the last one)")
        need(db.fragorders_span is not None, f"{db.name}: Missions.xml has no <fragorders> section")
    need(reg.ok, f"{model.rel(reg.path)} not found")
    need(dt not in reg.init, f"FragOrder.lua already has an initializer for {dt}"
         + (": the kernel path would never run" if kind == "kernel" else ""))
    if spec.get("propagate"):
        need(dt not in reg.propagated_names(), f"{dt} is already in fragOrdersToBePropagated")
    if spec.get("automat_branch"):
        need(dt not in reg.automat, f"executeAutomatFragOrder already tests {dt}")
    after = spec.get("after")
    if after:
        need(kind not in ("kernel", "automat"), f"\"after\" places an initializer: kind {kind} writes none")
        need(after in reg.init, f"\"after\": no initializer {after} in FragOrder.lua")
    # params
    params = spec.get("params") or []
    seen, canon = set(), {}
    ctx = {c["dia-name"] for c in ml.CONTEXT} | {c["name"] for c in ml.CONTEXT}
    for prm in params:
        if not need(prm.get("name") and prm.get("dia-name") and prm.get("type"), f"parameter {prm} needs name, dia-name and type"):
            continue
        need(prm["dia-name"] not in ctx, f"parameter {prm['name']}: frag orders have no context block")
        need(prm["dia-name"] not in seen, f"dia-name {prm['dia-name']} twice")
        seen.add(prm["dia-name"])
        is_list = bool(prm.get("list") or prm.get("max-occurs"))
        c = sword_types.canonical(prm["type"]) if sword_types.ok else prm["type"]
        if sword_types.ok and need(c, f"parameter {prm['name']}: unknown type {prm['type']}"):
            need(sword_types.converter(c, is_list), f"parameter {prm['name']}: {c}{'List' if is_list else ''} has no BM converter (value never passed)")
        canon[prm["dia-name"]] = c or prm["type"]
        if canon[prm["dia-name"]] == "Enumeration":
            need(prm.get("values"), f"parameter {prm['name']}: Enumeration needs values")
        if canon[prm["dia-name"]] == "Numeric" and kind != "kernel":
            need(False, f"parameter {prm['name']}: a Numeric read in Lua is truncated (MIL_FragOrder GetIntegerParameter; no "
                        "float getter): use kind kernel, or an Integer (e.g. a percentage) (FRAGORDERS.md §4)")
    catalog = getter_catalog(model)
    if kind in ("task", "immediate"):
        for prm in params:
            if prm.get("dia-name") in canon and not prm.get("getter"):
                key = (canon[prm["dia-name"]], bool(prm.get("list") or prm.get("max-occurs")))
                need(catalog.get(key), f"parameter {prm['name']}: no initializer reads a {key[0]}{' list' if key[1] else ''} "
                                       "today; give \"getter\" (integration.get<getter>Parameter) and \"wrap\" (ontology key or null)")
    # translations
    langs = sorted({lang for db in dbs for lang, kinds in db.ts.items() if "Missions" in kinds})
    for lang in langs:
        need((spec.get(lang) or {}).get("name"), f"spec.{lang}.name (translated frag-order name) missing")
        for prm in params:
            need(prm.get(lang), f"parameter {prm.get('name')}: \"{lang}\" translation missing")
            for v in prm.get("values") or []:
                need(v.get(lang), f"parameter {prm.get('name')} value {v.get('name')}: \"{lang}\" translation missing")
    # task and stages
    existing_task = model.task(task) if task else None
    stages = []
    if task:
        if existing_task is not None and existing_task.kind != "task":
            need(False, f"{task} is a {existing_task.kind}, not a task")
        if spec.get("stages"):
            stages = [(s["label"], s) for s in spec["stages"]]
            if existing_task is not None:
                need([lab for lab, _ in stages] == existing_task.labels(),
                     f"spec stages differ from the existing task's {existing_task.labels()}")
        elif existing_task is not None:
            stages = [(lab, {}) for lab in existing_task.labels()]
        else:
            need(False, f"task {task} does not exist and the spec gives no stages")
        for lab, s in stages:
            for db in model.dbs.values():
                for lang, kinds in db.ts.items():
                    if "Stages" in kinds and lab not in kinds["Stages"].get("stage", {}):
                        need(s.get(lang), f"stage \"{lab}\": \"{lang}\" translation missing (Stages_{lang}.ts)")
        if kind == "kernel" and existing_task is not None:
            for prm in params:
                if prm.get("dia-name"):
                    need(prm["dia-name"] in existing_task.params,
                         f"kind kernel: dia-name {prm['dia-name']} is not a parameter of {task} (the kernel binds by dia-name)")
        if kind == "task" and existing_task is not None:
            for prm in params:
                f_ = prm.get("task_param", prm.get("dia-name"))
                need(f_ in existing_task.params, f"parameter {prm.get('name')}: mission.{f_} is not a parameter of {task} (set task_param)")
    # category
    cat = spec.get("category")
    for db in dbs:
        if db.categories and cat is not None:
            need(cat in db.categories or spec.get("new_category"),
                 f"{db.name}: category \"{cat}\" not in {db.categories} (or set new_category + fr_category)")
    # models
    wm = bool(spec.get("without_mission"))
    lvl_spec, msn_spec = spec.get("models_level") or {}, spec.get("missions") or {}
    lvl, msn = defaultdict(list), defaultdict(list)
    for db in dbs:
        for ref in lvl_spec.get(db.name) or []:
            r = resolve_model(db, ref)
            if need(not isinstance(r, str), r):
                lvl[db.name].append(r)
        for ref, missions in (msn_spec.get(db.name) or {}).items():
            r = resolve_model(db, ref)
            if not need(not isinstance(r, str), r):
                continue
            for mi in missions:
                if need(mi in db.model_entries[r]["missions"], f"{db.name}: {r[0]} model \"{r[1]}\" has no mission \"{mi}\""):
                    msn[db.name].append((r, mi))
        if not wm:
            need(lvl[db.name] or msn[db.name], f"{db.name}: never available: without_mission is false and neither "
                                               "models_level nor missions lists a model")
        if kind == "automat":
            bad = [r[1] for r in lvl[db.name] + [x[0] for x in msn[db.name]] if r[0] != "automat"]
            need(not bad, f"{db.name}: kind automat (no unit initializer): list it in automat models only, not {bad}")
            if wm:
                ed.notes.append(f"{db.name}: kind automat with without_mission: a unit or crowd that receives it reaches "
                                "the kernel path with no task (FRAGORDERS.md §6)")
    # unknown keys
    lang_keys = {k for lang in langs for k in (lang, f"{lang}_category", f"sheet_{lang}")}
    for k in set(spec) - FO_TOP_KEYS - lang_keys - {"sheet", "fr_category", "sheet_fr", "fr"}:
        ed.notes.append(f"unknown spec key \"{k}\" ignored")
    for prm in params:
        for k in set(prm) - PARAM_KEYS - set(langs) - {f"sheet_{lang}" for lang in langs} - {"fr", "sheet_fr"}:
            if not k.startswith("_"):
                ed.notes.append(f"parameter {prm.get('name')}: unknown key \"{k}\" ignored")
    if errors:
        raise SpecError("\n".join(errors))

    # ---- Missions.xml
    for db in dbs:
        text = ed.get(db.missions_path)
        attrs = {"available-without-mission": "true" if wm else "false", "dia-type": dt, "id": fid, "name": name}
        if db.categories and cat is not None:
            attrs["category"] = cat
        head = "    <fragorder " + " ".join(f'{k}="{xa(v)}"' for k, v in sorted(attrs.items()))
        block = (head + ">\n" + "".join(param_xml(q, canon[q["dia-name"]], db.name) for q in params)
                 + "    </fragorder>\n") if params else head + "/>\n"
        end = re.search(r"(?m)^  </fragorders>", text).start()
        text = text[:end] + block + text[end:]
        if db.categories and cat is not None and cat not in db.categories:
            cend = re.search(r"(?m)^  </categories>", text).start()
            text = text[:cend] + f'    <category name="{xa(cat)}"/>\n' + text[cend:]
        ed.set(db.missions_path, text)
    # ---- Models.xml
    for db in dbs:
        text = ed.get(db.models_path)
        for k, mo in lvl[db.name]:
            text = models_add(text, k, mo, name)
        for (k, mo), mi in msn[db.name]:
            text = models_add(text, k, mo, name, mi)
        ed.set(db.models_path, text)
    # ---- Stages.xml (every DB) and Stages_<lang>.ts
    for db in model.dbs.values():
        if os.path.isfile(db.stages_path):
            text = ed.get(db.stages_path)
            for lab, _ in stages:
                if lab in db.stages:
                    continue
                items = [(ET.fromstring(f'<s k="{x.group(1)}"/>').get("k"), x.start())
                         for x in re.finditer(r'(?m)^  <stage key="([^"]*)"', text)]
                text = insert_sorted(text, items, lab, f'  <stage key="{xa(lab)}" value="{xa(lab)}"/>\n',
                                     text.rfind("</stages>"))
            ed.set(db.stages_path, text)
        for lang, kinds in db.ts.items():
            if "Stages" not in kinds:
                continue
            pth = os.path.join(db.locales, lang, f"Stages_{lang}.ts")
            text = ed.get(pth)
            for lab, s in stages:
                if lab not in kinds["Stages"].get("stage", {}):
                    text = ts_add_message(text, "stage", lab, s[lang])
            ed.set(pth, text)
    # ---- Missions_<lang>.ts
    for db in dbs:
        for lang in langs:
            if "Missions" not in db.ts.get(lang, {}):
                continue
            pth = os.path.join(db.locales, lang, f"Missions_{lang}.ts")
            text = ed.get(pth)
            text = ts_add_message(text, "frag-order", name, spec[lang]["name"])
            msgs = {}
            for prm in params:
                msgs[prm["name"]] = prm[lang]
                for v in prm.get("values") or []:
                    msgs[v["name"]] = v[lang]
            if msgs:
                text = ts_add_context(text, f"frag-order - {name}", msgs)
            if db.categories and cat is not None and cat not in db.categories:
                if not spec.get(f"{lang}_category"):
                    raise SpecError(f"new category \"{cat}\": {lang}_category missing")
                text = ts_add_message(text, "mission-categories", cat, spec[f"{lang}_category"])
            ed.set(pth, text)
    # ---- sheets
    for db in dbs:
        hd = re.search(r'<missions model-version="([^"]*)"[^>]*xsi:noNamespaceSchemaLocation="([^"]*)/Missions\.xsd"',
                       ed.get(db.missions_path))
        version = (hd.group(1), hd.group(2) + "/missionSheet.xsd") if hd else ("4.8.2", "schemas/physical/missionSheet.xsd")
        sp = [(q["name"], bool(q.get("optional")), q.get("sheet")) for q in params]
        x = sheet_xml(name, version, sp, spec.get("sheet") or {})
        ed.new(os.path.join(db.fo_sheets_dir, f"{name}.xml"), x)
        ed.new(os.path.join(db.fo_sheets_dir, f"{name}.html"), render_sheet_html(x, 0))
        for lang in langs:
            if "Missions" not in db.ts.get(lang, {}):
                continue
            sp = [(q[lang], bool(q.get("optional")), q.get(f"sheet_{lang}")) for q in params]
            stem = spec[lang]["name"]
            d = os.path.join(db.fo_sheets_dir, "Locales", lang)
            x = sheet_xml(stem, version, sp, spec.get(f"sheet_{lang}") or {})
            ed.new(os.path.join(d, f"{stem}.xml"), x)
            # images stay under the English name (measured on every fr frag-order sheet)
            ed.new(os.path.join(d, f"{stem}.html"), render_sheet_html(x, 2, f"./../../Images/{name}/"))
    # ---- FragOrder.lua
    init_lines = initializer_lines(kind, dt, task, params, canon, catalog) if kind not in ("kernel", "automat") else []
    if init_lines or spec.get("propagate") or spec.get("automat_branch"):
        ed.set(reg.path, edit_fragorder_lua(ed.get(reg.path), reg, dt, init_lines, after, spec.get("propagate"),
                                            spec.get("automat_branch")))
    if kind == "read":
        ed.notes.append(f"kind read: write the reader in the running mission's skill: integration.query."
                        f"getFirstFragOrderFromType( \"{dt}\" ), and persist what it derives (CHECKPOINTS.md §4)")
    if kind == "immediate":
        ed.notes.append(f"kind immediate: write the effect in the {dt} initializer (TODO), before cleanFragOrder")
    if spec.get("automat_branch"):
        ed.notes.append(f"automat branch for {dt}: write the commander's action (TODO in executeAutomatFragOrder)")
    # ---- task skeleton
    if task and existing_task is None and spec.get("task_skeleton", True):
        tp = model.entity_path(task)
        if "/frago/" not in model.rel(tp):
            ed.notes.append(f"new task {task} is not under a frago/ folder (FRAGORDERS.md §1)")
        cat_t = type_catalog(model)
        tparams = []
        for prm in params:
            c = canon[prm["dia-name"]]
            is_list = bool(prm.get("list") or prm.get("max-occurs"))
            inner = task_type_xml(model, c, is_list, prm.get("task_type"), cat_t) or "<any/>"
            absent = sword_types.absent_value(c, is_list) if sword_types.ok else None
            dflt = default_xml(prm, inner)
            nullable = bool(prm.get("optional")) and (absent == "NIL" or dflt is None)
            q = dict(prm)
            q["dia-name"] = prm.get("task_param", prm["dia-name"]) if kind == "task" else prm["dia-name"]
            tparams.append((q, inner, nullable, dflt))
        sk = spec.get("behavior")
        skp = model.entity_path(sk) if sk else ""
        skill_params = re.findall(r'<parameter\s+name="([^"]+)"', ml.read(skp).split("<parameters>", 1)[-1].split("</parameters>", 1)[0]) \
            if skp and os.path.isfile(skp) and "<parameters>" in ml.read(skp) else []
        if sk and not os.path.isfile(skp):
            ed.notes.append(f"behavior {sk} does not exist yet: write the skill (mission_check reports the missing entity)")
        xml = task_skeleton(dict(spec, task=task, _skill_params=skill_params), tparams, [lab for lab, _ in stages], frago=True)
        # line endings of the folder's existing tasks (dia5 is mostly CRLF; measure, do not assume)
        folder = os.path.dirname(tp)
        crlf = lf = 0
        for f in (os.listdir(folder) if os.path.isdir(folder) else []):
            if f.endswith(".xml"):
                with open(os.path.join(folder, f), "rb") as fh:
                    b = fh.read()
                crlf += b.count(b"\r\n") > 0
                lf += b.count(b"\r\n") == 0
        if crlf > lf:
            xml = xml.replace("\n", "\r\n")
        ed.new(tp, xml)
        ed.notes.append(f"task skeleton {model.rel(tp)}: check its behavior and bind its parameters (TODO)")
    elif task and existing_task is None:
        ed.notes.append(f"task {task} does not exist and task_skeleton is false: write it before mission_check")
    return ed


def git_branch(path):
    try:
        r = subprocess.run(["git", "-C", path, "branch", "--show-current"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", nargs="?", help="JSON spec file, or - for stdin")
    ap.add_argument("--root", default=ml.DEFAULT_ROOT, help="sw.models root (or a scratch copy)")
    ap.add_argument("--model", default="ada")
    ap.add_argument("--sword", default=ml.DEFAULT_SWORD)
    ap.add_argument("--write", action="store_true", help="apply the insertions (default: print the diff)")
    ap.add_argument("--allow-master", action="store_true", help="allow --write on a master/main checkout")
    ap.add_argument("--example", nargs="?", const="mission", choices=["mission", "fragorder"],
                    help="print an example spec (unit mission, or frag order) and exit")
    ap.add_argument("--check-html", action="store_true",
                    help="re-render every existing sheet .html of the model and compare (renderer self-test)")
    a = ap.parse_args()
    if a.example:
        print(json.dumps(EXAMPLE_FO if a.example == "fragorder" else EXAMPLE, indent=2, ensure_ascii=False))
        return
    model = ml.load_model(a.root, a.model, a.sword)
    if a.check_html:
        same, tot, bad = validate_html(model)
        print(f"{same}/{tot} sheet .html identical to the rendering of their .xml")
        for b in bad[:20]:
            print("  differs:", model.rel(b))
        sys.exit(0 if same == tot else 1)
    if not a.spec:
        ap.error("a spec file is required (or --example / --check-html)")
    if a.spec == "-":
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8")
        raw = json.load(sys.stdin)
    else:
        with open(a.spec, encoding="utf-8") as fh:
            raw = json.load(fh)
    spec = {k: v for k, v in raw.items() if not k.startswith("_")}
    if not model.types.ok:
        print(f"WARN sw.sword type tables not found under {a.sword}: types not checked, skeleton types guessed")
    try:
        ed = (scaffold_fragorder if spec.get("fragorder") else scaffold)(model, spec, model.types)
    except SpecError as e:
        print("ERROR spec rejected:\n  " + str(e).replace("\n", "\n  "))
        sys.exit(1)
    if a.write:
        br = git_branch(a.root)
        if br in ("master", "main") and not a.allow_master:
            print(f"ERROR {a.root} is on {br}: create a branch first (or --allow-master)")
            sys.exit(1)
        n = ed.write()
        print(f"wrote {n} file(s) under {a.root}" + (f" (branch {br})" if br else ""))
        for f in ed.changed():
            print("  " + f)
    else:
        print(ed.diff())
        print(f"{len(ed.changed())} file(s) would change; rerun with --write to apply")
    for n in ed.notes:
        print("NOTE " + n)
    if spec.get("fragorder"):
        print(f'next: python -I tools/mission_check.py --fragorder "{spec["name"]}" -v --root {a.root} && '
              f'python -I tools/checkpoint_lint.py --fragorder "{spec["name"]}" --root {a.root}')
    else:
        print(f'next: python -I tools/mission_check.py --mission "{spec["name"]}" --root {a.root}')


if __name__ == "__main__":
    main()
