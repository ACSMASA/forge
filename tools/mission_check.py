"""Lint unit missions and frag orders of a sw.models model in the WORKING TREE (uncommitted edits included).

Usage (from the forge root):
    python -I tools/mission_check.py (--mission NAME | --task TASK | --fragorder NAME | --all-fragorders | --all)
                                     [--db mildb,worldwide-tests] [--model ada] [--root C:/MASARepo/sw.models]
                                     [--sword C:/MASARepo/sw.sword] [-v]

Unit missions (rules and anchors: docs/sw.models/MISSIONS.md):
  context block (first 4 parameters) · id / name / dia-type unique (ids across all sections) · type known and passed
  to Spark (BM converter, list form) · dia-name <-> task parameter, value shape · optional <-> nullable (absent object value =
  NIL, default skipped) · task parameters without a mission value · task exists, disable-when-done · stage labels in
  Stages.xml and the .ts files · listed in a decisional model · Missions_<lang>.ts entries · sheet pairs (en + langs)
  · category (DBs with <categories>) · attribute order.
Frag orders (--fragorder takes a name or a dia-type; rules: docs/sw.models/FRAGORDERS.md):
  attribute order · id / name / dia-type unique · no context parameters · types and converters · category ·
  kind resolution through dia5/FragOrder.lua (initializer -> task exists and the dia-names it reads exist; no
  initializer -> a task named by the dia-type, bound like a mission; empty initializer -> a reader; else "handled by
  nothing") · parameters never read · frago task ends (disable-when-done or a stopTask) and sets priorities · stage
  labels · availability (never available; automat / crowd models; propagated orders listed by the subordinate models;
  unit task run on a commander) · Missions_<lang>.ts "frag-order" + "frag-order - <name>" · sheet pairs.
--all checks every mission and frag order and adds global checks: orphan sheets, Stages.xml vs every dia5 label,
Models.xml names, FragOrder.lua propagation names / initializer keys / integration aliases, frago tasks never started.
Cross-DB divergences (same name with another id, same id with another name) are notes, shown with -v.
Exit code 1 when there is at least one error; warnings never fail.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import missionlib as ml  # noqa: E402

MISSION_ATTRS = {"category", "dia-behavior", "dia-type", "id", "name"}
CTX_NAMES = {c["dia-name"] for c in ml.CONTEXT}
# (verb, opposite verb) for the frago task name <-> behavior check; the longer verb is tested first
OPPOSITES = [("Mount", "Dismount"), ("Activate", "Deactivate"), ("Embark", "Disembark"), ("Load", "Unload"),
             ("Resume", "Pause")]


class Report:
    def __init__(self, model, verbose):
        self.model, self.verbose = model, verbose
        self.items = []  # (level, db, subject, msg, where)

    def add(self, level, db, subject, msg, path=None, line=None):
        where = f"{self.model.rel(path)}:{line}" if path and line else (self.model.rel(path) if path else "")
        self.items.append((level, db, subject, msg, where))

    def error(self, *a, **k):
        self.add("ERROR", *a, **k)

    def warn(self, *a, **k):
        self.add("WARN", *a, **k)

    def note(self, *a, **k):
        self.add("NOTE", *a, **k)

    def dump(self):
        shown = [i for i in self.items if i[0] != "NOTE" or self.verbose]
        for level, db, subject, msg, where in shown:
            loc = f"  [{where}]" if where else ""
            print(f"{level:5} {db}: {subject}: {msg}{loc}")
        n = {lvl: sum(1 for i in self.items if i[0] == lvl) for lvl in ("ERROR", "WARN", "NOTE")}
        print(f"\n{n['ERROR']} error(s), {n['WARN']} warning(s), {n['NOTE']} note(s)"
              + ("" if self.verbose or not n["NOTE"] else " (notes: -v)"))
        return n["ERROR"]


def norm_shape(s):
    return re.sub(r"obj(,obj)+", "obj", s)


def expected_shapes(canonical, is_list, choices=()):
    base = set(ml.SCALAR_SHAPES.get(canonical, {"obj"}))
    for c in choices:  # LocationComposite: the value takes the shape of the chosen type (a Path is a list)
        base |= ml.SCALAR_SHAPES.get(c, {"obj"})
    return {f"list({b})" for b in base} if is_list else set(base)


# ---------------------------------------------------------------------------------------------------------------------
# shared by missions and frag orders


def check_types(params, db, subj, path, model, rep):
    types = model.types
    for p in params:
        if not types.ok:
            break
        c = types.canonical(p.type)
        if c is None:
            rep.error(db.name, subj, f"parameter \"{p.name}\": unknown type {p.type} (the sim throws at load)", path, p.line)
            continue
        if types.converter(p.type, p.is_list) is None:
            form = c + ("List" if p.is_list else "")
            rep.error(db.name, subj, f"parameter \"{p.name}\": type {form} has no BM converter: the value is silently not passed to the task", path, p.line)
        elif c != p.type:
            rep.warn(db.name, subj, f"parameter \"{p.name}\": type spelled {p.type}, registered as {c}", path, p.line)


def check_binding(params, task, db, subj, model, rep, path, what="mission"):
    """Kernel binding by dia-name (missions, and frag orders started by the kernel path): MISSIONS.md §2."""
    types = model.types
    tpath = task.path
    dia_names = set()
    for p in params:
        if not p.dia_name:
            rep.error(db.name, subj, f"parameter \"{p.name}\" has no dia-name", path, p.line)
            continue
        dia_names.add(p.dia_name)
        tp = task.params.get(p.dia_name)
        if tp is None:
            rep.error(db.name, subj, f"parameter \"{p.name}\": dia-name {p.dia_name} is not a parameter of {task.name}: the value is dropped", path, p.line)
            continue
        c = types.canonical(p.type) if types.ok else None
        if c and "any" not in tp.shape:
            exp = expected_shapes(c, p.is_list, p.choices)
            got = norm_shape(tp.shape)
            if got not in exp:
                rep.warn(db.name, subj, f"parameter \"{p.name}\" ({p.type}{' list' if p.is_list else ''}) -> task parameter {p.dia_name} has type {tp.shape}, expected {' or '.join(sorted(exp))}", tpath, task.line_of(f'name="{p.dia_name}"'))
        absent = types.absent_value(p.type, p.is_list) if types.ok else None
        if p.optional and absent == "NIL" and not tp.nullable:
            rep.warn(db.name, subj, f"optional parameter \"{p.name}\" ({p.type}): an absent value arrives as NIL (the task <default> is skipped), but task parameter {p.dia_name} is not nullable", tpath, task.line_of(f'name="{p.dia_name}"'))
        elif p.optional and absent == "nil" and tp.default is None and not tp.nullable:
            rep.warn(db.name, subj, f"optional parameter \"{p.name}\" ({p.type}): an absent value arrives as nil, and task parameter {p.dia_name} has no <default> and is not nullable", tpath, task.line_of(f'name="{p.dia_name}"'))
    for tp in task.params.values():
        if tp.name in dia_names or tp.name in CTX_NAMES:
            continue
        if tp.default is None and not tp.nullable:
            rep.error(db.name, subj, f"task parameter {tp.name} gets no {what} value, has no <default> and is not nullable (receives nil)", tpath, task.line_of(f'name="{tp.name}"'))


def check_task_shape(task, db, subj, model, rep):
    """Stage advance, referenced entities, stage labels in Stages.xml / Stages_<lang>.ts."""
    tpath = task.path
    for st in task.stages[:-1]:
        if not task.terminators(st):
            rep.error(db.name, subj, f"stage \"{st.label or '?'}\" of {task.name} has no next-stage-when-done behavior: the task never leaves it (validator task.stage.non-final-but-not-terminating)", tpath, task.line_of(f'label="{st.label}"') if st.label else 2)
    for kind, ref in task.references():
        if not os.path.isfile(model.entity_path(ref)):
            rep.error(db.name, subj, f"task {task.name} references {kind} {ref}: no such entity XML", tpath, task.line_of(f'"{ref}"'))
    # checkstage ignores dia5/test/: its labels need not be in Stages.xml
    for lab in ([] if model.is_test(tpath) else task.labels()):
        if lab not in db.stages:
            rep.error(db.name, subj, f"stage label \"{lab}\" missing from Stages.xml (checkstages; fix: ant generate-stages)", db.stages_path)
        for lang, kinds in db.ts.items():
            st = kinds.get("Stages", {}).get("stage", {})
            if "Stages" in kinds and lab not in st:
                rep.error(db.name, subj, f"stage label \"{lab}\" missing from Stages_{lang}.ts (checktranslations)", os.path.join(db.locales, lang, f"Stages_{lang}.ts"))


# ---------------------------------------------------------------------------------------------------------------------
# unit missions


def check_mission(m, db, model, rep, others):
    path = db.missions_path
    subj = f'"{m.name}"'
    # attributes
    if m.order != sorted(m.order):
        rep.warn(db.name, subj, f"mission attributes not in alphabetical order {m.order} (ADN normalization)", path, m.line)
    extra = set(m.order) - MISSION_ATTRS
    if extra:
        rep.warn(db.name, subj, f"unexpected mission attribute(s) {sorted(extra)}", path, m.line)
    for p in m.params:
        if p.order != sorted(p.order):
            rep.warn(db.name, subj, f"parameter \"{p.name}\" attributes not in alphabetical order", path, p.line)
    # context block
    for i, ctx in enumerate(ml.CONTEXT):
        if i >= len(m.params):
            rep.error(db.name, subj, f"context parameter #{i + 1} ({ctx['dia-name']}) missing: the first 4 parameters are the context, read by position", path, m.line)
            break
        p = m.params[i]
        if p.dia_name != ctx["dia-name"] or (p.type or "").lower() != ctx["type"].lower() or not p.is_context:
            rep.error(db.name, subj, f"parameter #{i + 1} must be the context parameter {ctx['dia-name']} ({ctx['type']}, is-context=\"true\"), got {p.dia_name} ({p.type})", path, p.line)
    for p in m.params[4:]:
        if p.is_context:
            rep.error(db.name, subj, f"is-context parameter \"{p.name}\" after position 4", path, p.line)
    # ids and unique names
    mid = m.attrs.get("id")
    if not mid:
        rep.error(db.name, subj, "no id", path, m.line)
    elif len(db.ids.get(mid, [])) > 1:
        rep.error(db.name, subj, f"id {mid} used {len(db.ids[mid])}x in Missions.xml: {db.ids[mid]}", path, m.line)
    same_name = [x for x in db.unit_missions if x.name == m.name]
    if len(same_name) > 1:
        rep.error(db.name, subj, f"name defined {len(same_name)}x among unit missions", path, m.line)
    dt = m.attrs.get("dia-type")
    if not dt:
        rep.error(db.name, subj, "no dia-type", path, m.line)
    else:
        dups = [x.name for x in db.unit_missions if x.attrs.get("dia-type") == dt and x is not m]
        if dups:
            rep.error(db.name, subj, f"dia-type {dt} also used by {dups}", path, m.line)
    for odb in others:
        o = odb.unit_missions_by_name.get(m.name)
        if o is not None and o.attrs.get("id") != mid:
            rep.note(db.name, subj, f"id {mid} here, {o.attrs.get('id')} in {odb.name}", path, m.line)
        if o is not None and o.task_name != m.task_name:
            rep.warn(db.name, subj, f"dia-behavior {m.task_name} here, {o.task_name} in {odb.name}", path, m.line)
        same_id = [x.name for x in odb.unit_missions if x.attrs.get("id") == mid and x.name != m.name]
        if same_id:
            rep.note(db.name, subj, f"id {mid} is \"{same_id[0]}\" in {odb.name}", path, m.line)
    # category
    if db.categories:
        cat = m.attrs.get("category")
        if cat is None:
            rep.warn(db.name, subj, "no category (this DB has <categories>)", path, m.line)
        elif cat not in db.categories:
            rep.error(db.name, subj, f"category \"{cat}\" is not in <categories>", path, m.line)
    # types
    check_types(m.own_params(), db, subj, path, model, rep)
    # task and binding
    task = model.task(m.task_name)
    if task is None:
        rep.error(db.name, subj, f"dia-behavior {m.task_name}: no such task XML (start fails: unknown behavior)", path, m.line)
        return
    if task.kind != "task":
        rep.error(db.name, subj, f"dia-behavior {m.task_name} is a {task.kind}, not a task", path, m.line)
        return
    tpath = task.path
    last_term = task.terminators(task.stages[-1]) if task.stages else []
    if not task.disable_when_done:
        how = ("its last stage's done only reports TaskDone" if last_term else
               "its last stage has no next-stage-when-done behavior: no done, no TaskDone; it runs until replaced or failed")
        rep.note(db.name, subj, f"continuous mission: task {task.name} has no disable-when-done=\"true\", so it never ends by itself ({how})", tpath, 2)
    elif task.stages and not last_term:
        rep.warn(db.name, subj, f"task {task.name} is disable-when-done but its last stage has no next-stage-when-done behavior: the mission never ends by itself", tpath, task.line_of(f'label="{task.stages[-1].label}"') if task.stages[-1].label else 2)
    for f in sorted(db.mission_frags.get(m.name, ())):
        if f not in db.fragorder_names:
            rep.error(db.name, subj, f"Models.xml lists frag order \"{f}\" under this mission, but Missions.xml has no such frag order", db.models_path)
    check_binding(m.own_params(), task, db, subj, model, rep, path)
    check_task_shape(task, db, subj, model, rep)
    # models
    if not db.mission_models.get(m.name):
        rep.warn(db.name, subj, "listed in no decisional model (Models.xml): no unit can receive it", db.models_path)
    # translations
    for lang, kinds in db.ts.items():
        if "Missions" not in kinds:
            continue
        tsp = os.path.join(db.locales, lang, f"Missions_{lang}.ts")
        tr = kinds["Missions"]
        if m.name not in tr.get("unit-missions", {}):
            rep.warn(db.name, subj, f"no translation in context unit-missions of Missions_{lang}.ts", tsp)
        need = [p.name for p in m.own_params()] + [v for p in m.own_params() for v in p.values]
        have = tr.get(f"unit - {m.name}", {})
        missing = [n for n in dict.fromkeys(need) if n not in have]
        if missing:
            rep.warn(db.name, subj, f"Missions_{lang}.ts context \"unit - {m.name}\" lacks {missing}", tsp)
        cat = m.attrs.get("category")
        if cat and cat not in tr.get("mission-categories", {}):
            rep.warn(db.name, subj, f"category \"{cat}\" not in context mission-categories of Missions_{lang}.ts", tsp)
    # sheets
    for ext in ("xml", "html"):
        if f"{m.name}.{ext}" not in db.sheets:
            rep.error(db.name, subj, f"mission sheet \"{m.name}.{ext}\" missing (checkdec sheets)", db.sheets_dir)
    for lang in db.ts:
        stem = db.sheet_name(lang, m.name)
        if stem is None:
            continue
        for ext in ("xml", "html"):
            if f"{stem}.{ext}" not in db.sheets_by_lang.get(lang, set()):
                rep.warn(db.name, subj, f"{lang} mission sheet \"{stem}.{ext}\" missing", os.path.join(db.sheets_dir, "Locales", lang))


# ---------------------------------------------------------------------------------------------------------------------
# frag orders


def _short(items, n=5):
    items = list(items)
    return ", ".join(items[:n]) + (f" … (+{len(items) - n})" if len(items) > n else "")


def check_frago_task(task, info, db, subj, model, rep, reg, strs):
    """A task started by a frag order: shape, labels, how it ends, priorities (FRAGORDERS.md §5)."""
    check_task_shape(task, db, subj, model, rep)
    tpath = task.path
    stoppers = [(model.rel(reg.path), i.line) for i in reg.init.values() if task.name in i.stops]
    stoppers += strs.stoppers.get(task.name, [])
    last_term = task.terminators(task.stages[-1]) if task.stages else []
    if not task.disable_when_done:
        mission_only = not info.fo.without_mission and info.listed and all(
            mi is not None for lst in info.listed.values() for _, mi in lst)
        if not stoppers:
            msg = (f"frago task {task.name} has no disable-when-done and no stopTask( \"{task.name}\" ) anywhere: it runs "
                   "until the next mission stop (FRAGORDERS.md §5)")
            if mission_only:
                rep.note(db.name, subj, msg + "; the order is available under missions only, so the mission stop is a defined end", tpath, 2)
            else:
                rep.warn(db.name, subj, msg, tpath, 2)
    elif task.stages and not last_term:
        rep.warn(db.name, subj, f"frago task {task.name} is disable-when-done but its last stage has no next-stage-when-done behavior: it never ends by itself", tpath, task.line_of(f'label="{task.stages[-1].label}"') if task.stages[-1].label else 2)
    leaf = task.name.rsplit(".", 1)[-1]
    beh_leaves = [b.rsplit(".", 1)[-1] for b in task.behaviors()]
    for pos, neg in OPPOSITES:
        for verb, other in ((neg, pos), (pos, neg)):
            if leaf.startswith(verb) and not any(x.startswith(verb) for x in beh_leaves) \
                    and any(x.startswith(other) and not x.startswith(verb) for x in beh_leaves):
                rep.warn(db.name, subj, f"frago task {task.name} is named {verb}… but runs {', '.join(x for x in beh_leaves if x.startswith(other))}: opposite action?", tpath, 2)
    for st in ([task.parallel] if task.parallel else []) + task.stages:
        for b, a in st.behaviors:
            if "priority" in a:
                continue
            res = sorted(r.rsplit(".", 1)[-1] for r in ml.skill_resources(model, b))
            if res:
                rep.warn(db.name, subj, f"frago task {task.name}: behavior {b} has no priority but acquires {', '.join(res)}: it competes with the running mission's skills at the default priority and may lose (FRAGORDERS.md §5)", tpath, task.line_of(f'name="{b}"'))


def check_fragorder(fo, db, model, rep, others):
    path = db.missions_path
    subj = f'frag order "{fo.name}"'
    reg, strs = model.fragorder_lua(), model.dia5_strings()
    fol = reg.path
    info = model.fragorder_info(db, fo)
    dt = fo.dia_type
    # attributes
    if fo.order != sorted(fo.order):
        rep.warn(db.name, subj, f"attributes not in alphabetical order {fo.order} (ADN normalization)", path, fo.line)
    extra = set(fo.order) - set(ml.FO_ATTRS)
    if extra:
        rep.warn(db.name, subj, f"unexpected attribute(s) {sorted(extra)} (available-for-all-mission is read but dead)", path, fo.line)
    for p in fo.params:
        if p.order != sorted(p.order):
            rep.warn(db.name, subj, f"parameter \"{p.name}\" attributes not in alphabetical order", path, p.line)
    # ids, names, dia-type
    fid = fo.attrs.get("id")
    if not fid:
        rep.error(db.name, subj, "no id", path, fo.line)
    elif len(db.ids.get(fid, [])) > 1:
        rep.error(db.name, subj, f"id {fid} used {len(db.ids[fid])}x in Missions.xml: {db.ids[fid]}", path, fo.line)
    if sum(1 for x in db.fragorders if x.name == fo.name) > 1:
        rep.error(db.name, subj, "name defined more than once among frag orders (the sim throws at load)", path, fo.line)
    if not dt:
        rep.error(db.name, subj, "no dia-type", path, fo.line)
        return
    dups = [x.name for x in db.fragorders_by_dia_type.get(dt, []) if x is not fo]
    if dups:
        rep.error(db.name, subj, f"dia-type {dt} also used by {dups}: the sim only warns and keeps the last one", path, fo.line)
    # no context block
    for p in fo.params:
        if p.is_context or p.dia_name in CTX_NAMES:
            rep.error(db.name, subj, f"parameter \"{p.name}\" is a mission context parameter: frag orders have no context block (contextLength 0), it is passed as a plain parameter", path, p.line)
    # category
    if db.categories:
        cat = fo.attrs.get("category")
        if cat is None:
            rep.note(db.name, subj, "no category (13 mildb frag orders have none)", path, fo.line)
        elif cat not in db.categories:
            rep.error(db.name, subj, f"category \"{cat}\" is not in <categories>", path, fo.line)
    # types
    check_types(fo.params, db, subj, path, model, rep)
    for p in fo.params:
        if not p.dia_name:
            rep.error(db.name, subj, f"parameter \"{p.name}\" has no dia-name", path, p.line)
    # cross-DB
    for odb in others:
        o = odb.fragorders_by_name.get(fo.name)
        if o is not None:
            if o.attrs.get("id") != fid:
                rep.note(db.name, subj, f"id {fid} here, {o.attrs.get('id')} in {odb.name}", path, fo.line)
            if o.dia_type != dt:
                rep.warn(db.name, subj, f"dia-type {dt} here, {o.dia_type} in {odb.name}", path, fo.line)
            sig = sorted((p.dia_name, (p.type or "").lower(), p.is_list) for p in fo.params)
            osig = sorted((p.dia_name, (p.type or "").lower(), p.is_list) for p in o.params)
            if sig != osig:
                rep.note(db.name, subj, f"parameters differ from {odb.name}: {[s[0] for s in sig]} here, {[s[0] for s in osig]} there", path, fo.line)
        same_id = [x.name for x in odb.fragorders if x.attrs.get("id") == fid and x.name != fo.name]
        if same_id:
            rep.note(db.name, subj, f"id {fid} is frag order \"{same_id[0]}\" in {odb.name}", path, fo.line)
        same_dt = [x.name for x in odb.fragorders_by_dia_type.get(dt, []) if x.name != fo.name]
        if same_dt:
            rep.note(db.name, subj, f"same dia-type {dt} is named \"{same_dt[0]}\" in {odb.name}", path, fo.line)
    # Models.xml lists
    listed = info.listed
    for kind, mo, mi in db.fo_listed.get(fo.name, []):
        if mi is not None and mi not in db.mission_names(kind):
            rep.warn(db.name, subj, f"Models.xml {kind} model \"{mo}\" lists it under mission \"{mi}\", which is not a {kind} mission of Missions.xml", db.models_path)
    if info.kind == "c2":
        rep.note(db.name, subj, f"task-group frag order (dia-type {dt}): out of scope, its taskgroup.tasks.* live outside sw.models (dec/taskgroups submodule)", path, fo.line)
    else:
        resolve(fo, info, db, subj, model, rep, reg, strs, path, fol)
    # availability
    if not fo.without_mission and not any(listed.values()):
        rep.error(db.name, subj, "never available: available-without-mission is false and no model of this DB lists it (refused at order time)", db.models_path)
    elif not any(listed.values()):
        rep.note(db.name, subj, "listed by no model: accepted anyway (available-without-mission), but integration.isFragOrderAvailable (commander propagation filter) is false for every entity", db.models_path)
    availability(fo, info, db, subj, model, rep, reg, fol)
    # translations
    for lang, kinds in db.ts.items():
        if "Missions" not in kinds:
            continue
        tsp = os.path.join(db.locales, lang, f"Missions_{lang}.ts")
        tr = kinds["Missions"]
        if fo.name not in tr.get("frag-order", {}):
            rep.warn(db.name, subj, f"no translation in context frag-order of Missions_{lang}.ts", tsp)
        need = [p.name for p in fo.params] + [v for p in fo.params for v in p.values]
        have = tr.get(f"frag-order - {fo.name}", {})
        missing = [n for n in dict.fromkeys(need) if n not in have]
        if missing:
            rep.warn(db.name, subj, f"Missions_{lang}.ts context \"frag-order - {fo.name}\" lacks {missing}", tsp)
        cat = fo.attrs.get("category")
        if cat and cat not in tr.get("mission-categories", {}):
            rep.warn(db.name, subj, f"category \"{cat}\" not in context mission-categories of Missions_{lang}.ts", tsp)
    # sheets
    for ext in ("xml", "html"):
        if f"{fo.name}.{ext}" not in db.fo_sheets:
            rep.error(db.name, subj, f"sheet \"{fo.name}.{ext}\" missing (checkdec sheets)", db.fo_sheets_dir)
    for lang in db.ts:
        stem = db.fo_translation(lang, fo.name)
        if stem is None:
            continue
        for ext in ("xml", "html"):
            if f"{stem}.{ext}" not in db.fo_sheets_by_lang.get(lang, set()):
                rep.warn(db.name, subj, f"{lang} sheet \"{stem}.{ext}\" missing", os.path.join(db.fo_sheets_dir, "Locales", lang))


def resolve(fo, info, db, subj, model, rep, reg, strs, path, fol):
    """Kind resolution (FRAGORDERS.md §2) and what each kind requires."""
    init = info.init
    dt = fo.dia_type
    dia_names = {p.dia_name for p in fo.params if p.dia_name}
    k = info.kind
    if init is not None:
        for g, dn in init.reads:
            if dn not in dia_names:
                rep.error(db.name, subj, f"initializer reads dia-name \"{dn}\" (get{g}Parameter), which is not a parameter of this frag order in {db.name}: the getter returns nil silently", fol, init.line)
    if init is not None:
        numeric = {p.dia_name for p in fo.params if (model.types.canonical(p.type) or p.type) == "Numeric"}
        for g, dn in init.reads:
            if dn in numeric and g == "Integer":
                rep.warn(db.name, subj, f"initializer reads the Numeric parameter \"{dn}\" with getIntegerParameter, which truncates it (1.5 -> 1; no float getter exists): a float needs kind (c), the kernel path (FRAGORDERS.md §4)", fol, init.line)
    if k == "b" and (info.readers or info.helpers):
        reader_ents = {ml.entity_of_rel("/" + r) for r, _ in info.readers} | {ml.entity_of_rel("/" + r) for r in info.helper_callers}
        reader_ents.discard(None)
        # a query reader counts through the skills that call it; a world-class reader (PhaseLine, MeKnowledge) can be
        # reached from any skill: then the check cannot be bounded and is skipped
        kinds = {}
        for e in reader_ents:
            pth = model.entity_path(e)
            head = ml.read(pth)[:400] if os.path.isfile(pth) else ""
            kinds[e] = next((k for k in ("skill", "query", "task") if f"<{k}" in head), "other")
        for q in [e for e, k in kinds.items() if k == "query"]:
            reader_ents |= {x for x in (ml.entity_of_rel("/" + r) for r in strs.lua_callers(q)) if x}
        dead = None if "other" in kinds.values() else []
        for mo, mi in ([] if dead is None else info.listed.get("unit", []) + info.listed.get("crowd", [])):
            if mi is None:
                continue
            msn = next((x for x in db.unit_missions if x.name == mi), None)
            if msn is None or not model.task(msn.task_name):
                continue
            role = (db.model_entries.get(("unit", mo)) or db.model_entries.get(("crowd", mo)) or {}).get("role")
            runs = ml.entity_closure(model, msn.task_name) | (ml.entity_closure(model, role) if role else set())
            if not (runs & reader_ents):
                dead.append(f"{mo} / {mi}")
        if dead:
            rep.warn(db.name, subj, f"listed under {len(dead)} mission(s) whose task (and role default task) runs none of its readers ({_short(sorted(r.rsplit('.', 1)[-1] for r in reader_ents), 4)}): there it is never read, never cleaned, and a later mission's reader consumes it: {_short(sorted(dead), 4)}", db.models_path)
    if k == "a":
        for tn in info.missing_tasks:
            rep.error(db.name, subj, f"initializer starts task {tn}: no such task (\"No behavior associated to DIA type\" error at order time)", fol, init.line)
        for v in init.computed:
            rep.note(db.name, subj, f"initializer returns a task chosen at run time ({v}): not checked", fol, init.line)
        for tn in info.tasks:
            task = model.task(tn)
            if task is None or task.kind != "task":
                continue
            for s in init.sets:
                if s not in task.params:
                    rep.warn(db.name, subj, f"initializer sets mission.{s}, which is not a parameter of {tn}: dropped", fol, init.line)
            for tp in task.params.values():
                if tp.name not in init.sets and tp.default is None and not tp.nullable:
                    rep.warn(db.name, subj, f"task parameter {tp.name} of {tn} is not set by the initializer, has no <default> and is not nullable (receives nil)", task.path, task.line_of(f'name="{tp.name}"'))
            check_frago_task(task, info, db, subj, model, rep, reg, strs)
    elif k == "c":
        task = model.task(dt)
        check_binding(fo.params, task, db, subj, model, rep, path, what="frag-order")
        check_frago_task(task, info, db, subj, model, rep, reg, strs)
    elif k == "b":
        if not (info.readers or info.helpers) and (info.listed.get("unit") or info.listed.get("crowd")):
            rep.warn(db.name, subj, f"only commander code names the dia-type {dt} ({_short(sorted({u[0].rsplit('/', 1)[-1] for u in info.commander_uses}), 3)}), but unit / crowd models list it: on a unit or crowd it is never read, never cleaned", fol, init.line)
    elif k == "b?":
        rep.warn(db.name, subj, f"empty initializer and nothing in dia5 names the dia-type {dt}: never read, never cleaned (the order stays queued and its initializer reruns every tick)", fol, init.line)
    elif k == "f?":
        rep.warn(db.name, subj, f"initializer only cleans the order and nothing in dia5 names the dia-type {dt}: the order has no effect", fol, init.line)
    elif k == "f":
        if not init.cleans and not info.readers:
            rep.warn(db.name, subj, "initializer neither cleans the order nor starts a task: it reruns every tick while the order stays queued", fol, init.line)
    elif k == "d":
        if info.listed.get("unit") or info.listed.get("crowd"):
            rep.warn(db.name, subj, f"handled only by the commander, but unit / crowd models list it: a unit or crowd that receives it reaches the kernel path with no task named {dt} (\"unknown behavior\")", db.models_path)
    elif k == "none":
        rep.error(db.name, subj, f"handled by nothing: no FragOrder.lua initializer, no task named {dt}, no automat branch and no reader in dia5", path, fo.line)
    # parameters never read (kinds read by Lua code; kind (c) is checked by the binding)
    if k in ("a", "b", "f") and init is not None:
        read = {dn for _, dn in init.reads}
        for p in fo.params:
            q = f'"{p.dia_name}"'
            # the integration layer may read it through a registered getter (GetorderConduiteChangerReglesEngagement_)
            in_layer = p.dia_name and re.search(rf"{re.escape(p.dia_name)}\b", model.integration_text())
            if p.dia_name and p.dia_name not in read and not strs.where.get(p.dia_name) \
                    and q not in reg.code.replace(init.code, "") and not in_layer:
                rep.warn(db.name, subj, f"parameter \"{p.name}\" (dia-name {p.dia_name}) is never read: neither by the initializer, nor anywhere in dia5, nor by the integration layer", path, p.line)


def availability(fo, info, db, subj, model, rep, reg, fol):
    """Automat, propagation and crowd availability (FRAGORDERS.md §3, §6)."""
    listed = info.listed
    automats = sorted({mo for mo, _ in listed.get("automat", [])})
    dt = fo.dia_type
    task_named = model.task(dt) is not None and model.task(dt).kind == "task"
    handled_by_commander = bool(info.automat_branch or info.propagated or info.commander_uses or info.helpers)
    if automats and not handled_by_commander and not task_named and info.kind not in ("c2",):
        rep.warn(db.name, subj, f"listed by automat model(s) {_short(automats, 3)}, but the commander does nothing with it: no automat branch, not propagated, no commander test, no task named {dt}", db.models_path)
    if (info.automat_branch or info.commander_uses) and not automats and not fo.without_mission:
        rep.warn(db.name, subj, "handled by the commander, but no automat model lists it and it needs a mission: no automat can receive it", db.models_path)
    # unit task started on the commander: executeAutomatFragOrder fallback (military) / startTask (security)
    if not info.propagated and not info.automat_branch and task_named:
        msg = (f"not propagated and a task is named by its dia-type: an automat that receives it runs {dt} in the "
               "commander's brain (executeAutomatFragOrder fallback; FRAGORDERS.md §6)")
        if automats:
            rep.warn(db.name, subj, msg + f"; automat models listing it: {_short(automats, 3)}", db.models_path)
        elif fo.without_mission:
            rep.note(db.name, subj, msg + " (reachable through available-without-mission)", db.models_path)
    security = [mo for mo in automats if (db.model_entries.get(("automat", mo)) or {}).get("role", "").startswith("security.")]
    if security and not info.propagated and not info.automat_branch and info.kind in ("d", "none"):
        rep.warn(db.name, subj, f"security commander model(s) {_short(security, 3)} list it: their ManageFragOrder calls fragOrder:startTask() on a non-propagated order, which reaches the kernel path with no task named {dt}", db.models_path)
    # propagation: the subordinate models must allow it (DEC_IsFragOrderAvailable)
    # (an automat branch that forwards the order itself bypasses this filter)
    if info.propagated and automats and not info.automat_branch:
        missing, none_ok = set(), []
        for a in automats:
            subs = [s for s in db.automat_subs.get(a, ()) if ("unit", s) in db.model_entries]
            bad = []
            for sub in subs:
                e = db.model_entries[("unit", sub)]
                ok = fo.name in e["level"] or (not fo.without_mission and any(fo.name in fs for fs in e["missions"].values()))
                if not ok:
                    bad.append(sub)
            missing |= set(bad)
            if subs and len(bad) == len(subs):
                none_ok.append(a)
        need = "model-level list (available-without-mission)" if fo.without_mission else "model-level or mission list"
        if none_ok:
            rep.warn(db.name, subj, f"propagated, but no subordinate unit model of automat model(s) {_short(none_ok, 3)} has it in its {need}: SendFragOrder silently sends it to nobody (DEC_IsFragOrderAvailable)", db.models_path)
        elif missing:
            rep.note(db.name, subj, f"propagated, but {len(missing)} subordinate unit model(s) lack it in their {need}, so SendFragOrder skips them (DEC_IsFragOrderAvailable): {_short(sorted(missing))}", db.models_path)
    # crowd
    if info.family == "crowd" and not listed.get("crowd") and not fo.without_mission:
        rep.warn(db.name, subj, "crowd frag order (crowd task) but no crowd model lists it: no crowd can receive it", db.models_path)


def fragorder_summary(fo, db, model, receiver=None):
    """Lines printed for --fragorder: the resolved wiring and where the order is available."""
    info = model.fragorder_info(db, fo)
    out = [f"{db.name}: \"{fo.name}\" dia-type {fo.dia_type}, kind {info.kind}"
           + (f", starts {', '.join(info.tasks)}" if info.tasks else "")
           + (", propagated" if info.propagated else "") + (f", automat branch (FragOrder.lua:{info.automat_branch})" if info.automat_branch else "")
           + (", available without mission" if fo.without_mission else "") + f"; checkpoint: {info.checkpoint_class}"]
    rd = sorted({ml.entity_of_rel("/" + r) or r for r, _ in info.readers} | {ml.entity_of_rel("/" + r) or r for r in info.helper_callers})
    if rd:
        out.append(f"  readers: {_short(rd, 8)}")
    for kind in ml.MODEL_SECTIONS.values():
        lst = info.listed.get(kind, [])
        by = {}
        for mo, mi in lst:
            by.setdefault(mo, []).append(mi)
        for mo in sorted(by):
            lvl = None in by[mo]
            ms = sorted(x for x in by[mo] if x)
            out.append(f"  {kind} \"{mo}\": " + ("model level" if lvl else "") + (" + " if lvl and ms else "")
                       + (f"under {len(ms)} mission(s): {_short(ms, 6)}" if ms else ""))
    if receiver:
        hits = [k for k in db.model_entries if k[1] == receiver.split(":", 1)[-1] and (":" not in receiver or k[0] == receiver.split(":", 1)[0])]
        for k in hits:
            e = db.model_entries[k]
            if fo.name in e["level"]:
                out.append(f"  receiver {k[0]} \"{k[1]}\": model level, so every mission")
                continue
            missing = sorted(m for m, fs in e["missions"].items() if fo.name not in fs)
            out.append(f"  receiver {k[0]} \"{k[1]}\": listed under {len(e['missions']) - len(missing)}/{len(e['missions'])} missions; "
                       f"missing under: {_short(missing, 12) or 'none'}")
        if not hits:
            out.append(f"  receiver \"{receiver}\": no such model in {db.name}")
    return out


def check_fragorders_global(model, dbs, rep):
    reg, strs = model.fragorder_lua(), model.dia5_strings()
    fol = reg.path
    all_dt = {f.dia_type for db in model.dbs.values() for f in db.fragorders}
    for db in dbs:
        names = set(db.fragorders_by_name)
        for f in sorted(db.fo_sheets):
            stem, ext = os.path.splitext(f)
            if ext in (".xml", ".html") and stem not in names:
                rep.error(db.name, f'sheet "{f}"', "no frag order of that name (checkdec sheets)", db.fo_sheets_dir)
        for n, where in sorted(db.fo_listed.items()):
            if n not in names:
                kinds = sorted({k for k, _, _ in where})
                rep.error(db.name, f'Models.xml frag order "{n}"', f"listed by {len(where)} {'/'.join(kinds)} model entr(ies) but not in Missions.xml <fragorders> (the sim throws \"Unknown orderConduite type\")", db.models_path)
    if not reg.ok:
        rep.warn("-", "FragOrder.lua", f"{model.rel(fol)} not found: frag-order wiring not checked")
        return
    # propagation names
    low = {}
    for d in sorted(all_dt) + sorted(set(reg.init) - all_dt):  # DB dia-types first, then client initializer keys
        low.setdefault(d.lower(), d)
        low.setdefault(d.rsplit(".", 1)[-1].lower(), d)
    for n, line in reg.propagated:
        if n in all_dt:
            continue
        stem = re.sub(r"^Rep_OrderConduite_(Pion_)?", "", n).lower()
        near = [low[c] for c in (n.lower(), n.rsplit(".", 1)[-1].lower(), stem, stem + "s", stem.rstrip("s")) if c in low]
        near = [x for x in dict.fromkeys(near) if x != n]
        if near and near[0] in all_dt | set(reg.init):
            rep.warn("-", f'propagation name "{n}"', f"matches no frag-order dia-type of any DB; near match {near[0]}: a misspelled name is silently never propagated", fol, line)
        else:
            rep.note("-", f'propagation name "{n}"', "matches no frag-order dia-type of any DB (client or legacy name?)", fol, line)
    # initializer keys for dia-types in no DB
    for k, i in sorted(reg.init.items()):
        if k not in all_dt:
            rep.note("-", f'initializer "{k}"', "serves a dia-type that no DB of this model defines (client extension or dead code)", fol, i.line)
    # automat branches for unknown dia-types
    for k, line in sorted(reg.automat.items()):
        if k not in all_dt:
            rep.note("-", f'automat branch "{k}"', "tests a dia-type that no DB of this model defines", fol, line)
    # integration aliases pointing at undefined fragOrder.* functions
    for x in re.finditer(r"^integration\.(\w+)\s*=\s*fragOrder\.(\w+)\s*$", reg.code, re.M):
        if not re.search(rf"\bfragOrder\.{re.escape(x.group(2))}\s*=\s*function", reg.code):
            rep.warn("-", f"integration.{x.group(1)}", f"= fragOrder.{x.group(2)}, which FragOrder.lua never defines: the alias is nil", fol, ml.line_of(reg.code, x.start()))
    # polling queries that merge the kernel queue with myself.fragOrders: an index never advanced by the first loop
    for root, _, fs in os.walk(model.dia5):
        for f in fs:
            if not f.endswith(".lua"):
                continue
            p = os.path.join(root, f)
            code = ml.lua_strip_comments(ml.read(p))
            if "getOrdersCategory" not in code or "myself.fragOrders" not in code:
                continue
            for v in re.findall(r"\blocal\s+(\w+)\s*=\s*0\b", code):
                inc = re.search(rf"\b{v}\s*=\s*{v}\s*\+\s*1", code)
                q = code.find("getOrdersCategory")
                mf = code.find("myself.fragOrders")
                if inc and q < mf < inc.start() and re.search(rf"\[\s*{v}\s*\]", code[mf:]):
                    rep.warn("-", model.rel(p), f"merges the kernel queue and myself.fragOrders, but {v} is not advanced while the queue is copied: the commander's orders overwrite queued ones", p, ml.line_of(code, inc.start()))
    # frago tasks never started
    started = {t for i in reg.init.values() for t in i.starts} | all_dt
    for root, _, fs in os.walk(model.dia5):
        if not re.search(r"[/\\]frago$", root):
            continue
        for f in fs:
            if not f.endswith(".xml"):
                continue
            name = model.rel(os.path.join(root, f))
            ent = ml.entity_of_rel("/" + name)
            if ent and ent not in started and not strs.uses(ent, exclude={name}):
                rep.note("-", f"frago task {ent}", "started by no initializer, named by no frag-order dia-type and by no string in dia5", os.path.join(root, f))


def check_global(model, dbs, rep):
    labels = ml.all_stage_labels(model.decisional)
    tg = os.path.join(model.decisional, "taskgroups")
    tg_missing = os.path.isdir(tg) and not os.listdir(tg)
    if tg_missing:
        rep.warn("-", "Stages.xml", f"{model.rel(tg)} is empty (submodule not initialised): labels used only by task groups cannot be checked")
    for db in dbs:
        names = {m.name for m in db.unit_missions}
        for f in sorted(db.sheets):
            stem, ext = os.path.splitext(f)
            if ext in (".xml", ".html") and stem not in names:
                rep.error(db.name, f'sheet "{f}"', "no unit mission of that name (checkdec sheets)", db.sheets_dir)
        for lab in sorted(set(labels) - db.stages):
            rep.error(db.name, f'stage "{lab}"', f"label used in {model.rel(labels[lab])} is missing from Stages.xml (checkstages)", db.stages_path)
        for lab in ([] if tg_missing else sorted(db.stages - set(labels))):
            rep.error(db.name, f'stage "{lab}"', "in Stages.xml but used by no dia5 task outside test/ (checkstages)", db.stages_path)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--mission", help="unit mission name (Missions.xml name)")
    g.add_argument("--task", help="task name: every unit mission whose dia-behavior is this task")
    g.add_argument("--fragorder", help="frag-order name or dia-type")
    g.add_argument("--all-fragorders", action="store_true")
    g.add_argument("--all", action="store_true", help="every unit mission and frag order, plus the global checks")
    ap.add_argument("--db", help="comma-separated physical DBs (default: all)")
    ap.add_argument("--model", default="ada")
    ap.add_argument("--root", default=ml.DEFAULT_ROOT, help="sw.models root (or a scratch copy)")
    ap.add_argument("--sword", default=ml.DEFAULT_SWORD)
    ap.add_argument("-v", "--verbose", action="store_true", help="show notes")
    ap.add_argument("--receiver", help="with --fragorder: a decisional model (\"unit:Name\" on a clash): the missions of it that list / lack the order")
    a = ap.parse_args()
    model = ml.load_model(a.root, a.model, a.sword)
    wanted = a.db.split(",") if a.db else list(model.dbs)
    unknown = [d for d in wanted if d not in model.dbs]
    if unknown:
        sys.exit(f"unknown DB(s) {unknown}; have {list(model.dbs)}")
    dbs = [model.dbs[d] for d in wanted]
    rep = Report(model, a.verbose)
    if not model.types.ok:
        rep.warn("-", "sw.sword", f"parameter-type tables not found under {a.sword}: type and converter checks skipped")
    do_missions = a.all or a.mission or a.task
    do_frags = a.all or a.all_fragorders or a.fragorder
    found, where = 0, []
    for db in dbs:
        others = [o for o in model.dbs.values() if o is not db]
        n = nf = 0
        if do_missions:
            for m in db.unit_missions:
                if a.all or (a.mission and m.name == a.mission) or (a.task and m.task_name == a.task):
                    n += 1
                    check_mission(m, db, model, rep, others)
        if do_frags:
            for fo in db.fragorders:
                if a.all or a.all_fragorders or a.fragorder in (fo.name, fo.dia_type):
                    nf += 1
                    check_fragorder(fo, db, model, rep, others)
        found += n + nf
        where.append(f"{db.name} {n}" + (f" + {nf} frag orders" if do_frags else ""))
    if a.all:
        check_global(model, dbs, rep)
    if a.all or a.all_fragorders:
        check_fragorders_global(model, dbs, rep)
    if a.fragorder:
        for db in dbs:
            for fo in db.fragorders:
                if a.fragorder in (fo.name, fo.dia_type):
                    print("\n".join(fragorder_summary(fo, db, model, a.receiver)))
        print()
    if not found:
        what = (f'mission "{a.mission}"' if a.mission else f"a mission with dia-behavior {a.task}" if a.task
                else f'frag order "{a.fragorder}"' if a.fragorder else "missions")
        rep.error("-", "target", f"no unit {what} in {', '.join(d.name for d in dbs)}")
    print(f"sw.models root {a.root}, model {a.model}; entries checked per DB: {', '.join(where)}\n")
    sys.exit(1 if rep.dump() else 0)


if __name__ == "__main__":
    main()
