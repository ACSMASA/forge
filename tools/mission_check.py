"""Lint unit missions of a sw.models model in the WORKING TREE (uncommitted edits included).

Usage (from the forge root):
    python -I tools/mission_check.py (--mission NAME | --task TASK | --all) [--db mildb,worldwide-tests]
                                     [--model ada] [--root C:/MASARepo/sw.models] [--sword C:/MASARepo/sw.sword] [-v]

Checks (rules and anchors: docs/sw.models/MISSIONS.md):
  context block (first 4 parameters) · id / name / dia-type unique (ids across all sections) · type known and passed
  to Spark (BM converter, list form) · dia-name <-> task parameter, value shape · optional <-> nullable (absent object value =
  NIL, default skipped) · task parameters without a mission value · task exists, disable-when-done · stage labels in
  Stages.xml and the .ts files · listed in a decisional model · Missions_<lang>.ts entries · sheet pairs (en + langs)
  · category (DBs with <categories>) · attribute order.  --all adds: orphan sheets, Stages.xml vs every dia5 label.
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
    types = model.types
    for p in m.own_params():
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
    # task and binding
    task = model.task(m.task_name)
    if task is None:
        rep.error(db.name, subj, f"dia-behavior {m.task_name}: no such task XML (start fails: unknown behavior)", path, m.line)
        return
    if task.kind != "task":
        rep.error(db.name, subj, f"dia-behavior {m.task_name} is a {task.kind}, not a task", path, m.line)
        return
    tpath = task.path
    if not task.disable_when_done:
        rep.note(db.name, subj, f"continuous mission: task {task.name} has no disable-when-done=\"true\", so it never ends by itself (done -> TaskDone only)", tpath, 2)
    dia_names = set()
    for p in m.own_params():
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
        if tp.name in dia_names or tp.name in {c["dia-name"] for c in ml.CONTEXT}:
            continue
        if tp.default is None and not tp.nullable:
            rep.error(db.name, subj, f"task parameter {tp.name} gets no mission value, has no <default> and is not nullable (receives nil)", tpath, task.line_of(f'name="{tp.name}"'))
    # stages (checkstage ignores dia5/test/: its labels need not be in Stages.xml)
    for lab in ([] if model.is_test(tpath) else task.labels()):
        if lab not in db.stages:
            rep.error(db.name, subj, f"stage label \"{lab}\" missing from Stages.xml (checkstages; fix: ant generate-stages)", db.stages_path)
        for lang, kinds in db.ts.items():
            st = kinds.get("Stages", {}).get("stage", {})
            if "Stages" in kinds and lab not in st:
                rep.error(db.name, subj, f"stage label \"{lab}\" missing from Stages_{lang}.ts (checktranslations)", os.path.join(db.locales, lang, f"Stages_{lang}.ts"))
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
    g.add_argument("--all", action="store_true")
    ap.add_argument("--db", help="comma-separated physical DBs (default: all)")
    ap.add_argument("--model", default="ada")
    ap.add_argument("--root", default=ml.DEFAULT_ROOT, help="sw.models root (or a scratch copy)")
    ap.add_argument("--sword", default=ml.DEFAULT_SWORD)
    ap.add_argument("-v", "--verbose", action="store_true", help="show notes")
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
    found = 0
    for db in dbs:
        others = [o for o in model.dbs.values() if o is not db]
        for m in db.unit_missions:
            if a.all or (a.mission and m.name == a.mission) or (a.task and m.task_name == a.task):
                found += 1
                check_mission(m, db, model, rep, others)
    if a.all:
        check_global(model, dbs, rep)
    if not found:
        what = f'mission "{a.mission}"' if a.mission else f"a mission with dia-behavior {a.task}" if a.task else "missions"
        rep.error("-", "target", f"no unit {what} in {', '.join(d.name for d in dbs)}")
    print(f"sw.models root {a.root}, model {a.model}; {found} mission entr{'y' if found == 1 else 'ies'} checked "
          f"in {', '.join(d.name for d in dbs)}\n")
    sys.exit(1 if rep.dump() else 0)


if __name__ == "__main__":
    main()
