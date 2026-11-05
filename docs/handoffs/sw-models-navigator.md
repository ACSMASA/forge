# Handoff: build the sw.models navigator

> **How to use:** start a Claude Code session in `C:\MASARepo\forge` and say
> "Follow `docs/handoffs/sw-models-navigator.md`" (first time) or
> "Continue `docs/handoffs/sw-models-navigator.md` from the first unchecked step" (after an interruption).
> Effort: high is enough. The work is split into small steps, each ending in a commit, so a run that stops (for
> example on quota) loses at most one step. See **Steps** at the end.
> Written 2026-11-05. Sibling handoff: `docs/handoffs/directai-navigator.md` (done: `dai-navigator`, `docs/directai/`,
> generalized `tools/check_themes.py`).

## Goal
Build a lean, code-first navigator agent for `C:\MASARepo\sw.models`, the user's **own working repo** (DIA5 models
and physical DB), following the pattern already used for sw.sword in this repo.

## Why this shape (lessons from sw.sword)
- The first sw.sword build started with a long prose study (a hub plus five deep dives, ~3,000 lines). During
  validation the agents went straight to the code for every claim. Two prose claims turned out wrong. The prose
  is now frozen in `docs/sw.sword/archive/`.
- What kept its value: a **generated index**, **vocabulary**, a **theme → files map** (saves the first few searches),
  and a short list of **traps you wouldn't think to search for**, each anchored to code. A **checker** confirms every
  path and anchor still exists.
- The agent must stay **light on spawn**: nothing loaded up front that a question may not need.

## Copy the existing pattern
Read these first; they are the template:
- `.claude/agents/sw-sword-navigator.md`: the agent (code-first, read-only, answer format, "Map updates")
- `docs/sw.sword/THEMES.md`, `docs/sw.sword/README.md`: the knowledge-base shape (vocabulary, theme → files table,
  anchored gotchas, `path::text` anchors, no line numbers, no descriptions of behavior)
- `tools/check_themes.py`, `tools/build_dec_index.py`: the checker and the generated index
- `.claude/skills/sword/SKILL.md`: the skill that forks a question into the agent
Do not write long prose docs. The code is the only source of truth.

## How sw.models differs from sw.sword
- **It is where the user writes.** Questions will be "is there already a skill/task/query that does X", "which mission
  runs which task and skills", "how is this unit type composed", "where do I put a new skill", "what breaks if I
  change this", more than "how does the engine work".
- **Branches matter a lot.** Content differs per branch, and the user switches often:
  - client-project branches (e.g. `QQ_MCAST_UK_04_2026`, `USMC_June_2026`, `USMC_AIR_DRONE_June_2026`);
  - per-ticket branches (`AUL/SW-xxxxx`, about 146 remote branches in all);
  - test branches (the checkout was on `test_DrawingTemplates` when this was written).

  Client models may appear only on their branch, possibly under another name. The agent must always report the
  branch it read, never assume a model or branch exists, and use `git show <branch>:<path>` to compare without
  checking out. The generated index must record the branch and commit it was built from. The agent must notice
  when the index is stale and fall back to grep.
- **Boundary with sw-sword-navigator.** The integration layer (`integration.*`, `DEC_*`) belongs to sw.sword: the
  sw.models navigator should name the integration function a model calls and leave its semantics to
  sw-sword-navigator. Subagents can't call each other and the main session routes, so both agent descriptions must
  make the split clear.

## Two areas the navigator must master
Both span three repos: the meaning comes from directai (Spark language and runtime), the content lives in sw.models,
and the effect in the sim comes from sw.sword. Point questions are not enough here. The user will run studies on
these areas (e.g. an ontology refactoring project), which need whole-graph views. Hence a generated index for each,
on top of the decisional index.

### Ontology (world, roles, interfaces, classes)
- Written in the Spark language (`xmlns=".../directai/spark/lang/1.5"`). In ada, about 173 class files, 36 interfaces
  and 32 roles (counts surveyed 2026-11-05).
  - A class (e.g. `common/agent/world/Agent.xml`) declares `<extends>`, many `<implements>` and `<constructor>`;
    its `.lua` sibling holds the method bodies.
  - Interfaces (`*/world/interfaces/*.xml`) declare method signatures.
  - Roles (`*/roles/*.xml`) bind a `<body-type>` (e.g. `common.agent.world.MeKnowledge`) to skills and a default task.
- sw.sword creates and types world objects from the integration layer (`integration.ontology.types…`, `InitMe*`,
  `meKnowledge`). directai defines what `extends` / `implements` / `body-type` mean and what the validator enforces.
- **Ontology index** (generated): for every class / interface / role:
  - extends, implements, body-type
  - methods declared vs implemented in the `.lua`
  - call sites (skills, tasks, queries, the integration layer, the other model repo)
  - an anomaly report: interfaces with 0 or 1 implementers, declared-but-unimplemented or never-called methods,
    duplicated methods across classes or across sw.models / sw.soult, inheritance depth

  Lua calls like `self:method()` can't be resolved to a type statically, so callers are matched by method name and
  are upper bounds. The index must say so.

### Action skills, resources and the action frameworks
- **Resources**: `<resource>` declarations in `common/agent/behaviors/resources/` (22) and `military/agent/resources/`
  (4). A skill acquires them through `<resources><resource name=… variable=…/>` (65 skill XMLs do). directai uses them
  to decide which skills can run at the same time; that arbitration lives in directai.
- **Action skills**: `*/skills/actions/` (about 135 skills: `common/agent` ~92, `military/agent` ~28, `crowd`,
  `common/commander`, `navy`), with `<feedbacks>` (e.g. `activationActionCompleted`) and a `body-type`.
- **Lua action frameworks**, all to be understood and told apart:
  - `spark.startStopAction` (directai `src/libraries/plugins/spark/integration.lua`): used in the six
    `MeKnowledge*.lua` world classes (e.g. `activateAirFiltrationSystem`) and in the integration layer. Its doc
    comment marks it **deprecated** in favor of DAI action nodes (`spark.action`).
  - `spark.action` / action nodes, the newer way.
  - start / started / stop functions written directly in skill `.lua` files.
  - underneath them, the sw.sword kernel's asynchronous actions: `_DEC__Start*` returns an id, state comes back
    through `CallbackAction` / `actionCallbacks`, `_DEC__StopAction` returns 0 (truthy in Lua), and the kernel does
    not stop Lua-started actions on mission change.
- **Action index** (generated):
  - for every action skill: resources acquired, feedbacks, body type, framework used, the `integration.*`
    functions it calls, and the kernel `_DEC__Start*` behind them (via sw.sword `dec_function_index.md`)
  - for every resource: which skills acquire it (this shows which skills are mutually exclusive)
  - for every `startStopAction` method on a world class: its start / started / stop handlers and the integration calls

## Context (surveyed 2026-11-05; re-check)
- About 12.3k tracked files, almost all under `data/data/models/ada` (the standing model). Mostly out of scope:
  - ~5.9k `.svg` symbols
  - ~1.4k `.html` mission/analytics sheets
  - `data/exercises/` (example exercises, only useful as test material)
- The decisional model is `ada/decisional/`:
  - `decisional.xml`; `dia5/BMAgent.bms` (loads `Integration.lua`); `dia5/Decisional.lua`, `dia5/FragOrder.lua`
  - families `common`, `military`, `mildb`, `security`, `navy`, `crowd`, `test`, each with `agent` / `commander` /
    `queries` / `world` / `roles` / `behaviors`
  - about 1.5k `.xml` + 900 `.lua`
  - `extensions/`, `taskgroups/`
- The physical DB is `ada/physical/mildb/*.xml` (Units, Equipments, Missions, Sensors, Objects, Reports, Models…).
  The files are large: grep them, never read them whole.
  - `Missions.xml` links each mission to its DIA5 task through `@dia-behavior` / `@dia-type`
    (e.g. `security.agent.behaviors.tasks.Follow`).
  - `Missions.xml` also carries `model-version` and an XSD path with the sw.sword version.
- `data/data/models/decfunctions.txt` lists the DEC functions models use (checked by sw.sword `ant checkdec`).
- Go tools in `src/`: `checkdec`, `gendec`, `checkstage`, `checktranslations`, `checkhla`, `models` (`decdb.go`,
  `phydb.go`: existing parsers of the decisional and physical DBs, worth reading before writing a generator).
- The repo has its own `README.md` (227 lines) and `ARCHITECTURE.md` (263 lines): use them for orientation,
  check them against the files, and link to them instead of copying them.
- The user's earlier working notes live in `C:\MASARepo\markdown_files`, for example:
  - `skill_architecture_overview.md`, `mission_architecture_overview.md`
  - `models_soult_sync_reference.md`, `lua_type_system_notes.md`
  - `physical_db_custom_build_guide.md`, the USMC notes, and a sw.models `CLAUDE.md`

  They are good sources of vocabulary and gotchas, but treat every claim as unverified until you check it in the files.
- `C:\MASARepo\sw.soult` is the French-named sister repo with similar structure.

## Working rules (apply to every step)
- **On start or resume:** run `git -C C:/MASARepo/forge status --short`, read **Decisions** and **Progress log** below,
  and continue at the first unchecked step. If a step was interrupted (outputs half-written, nothing committed),
  redo it from the beginning: every step is safe to repeat, and the generators overwrite their outputs.
- **On finishing a step:**
  1. Tick its box.
  2. Add 1–3 lines to the **Progress log**: what was produced, key numbers, anything the next step needs.
  3. Commit **only the paths you touched** (`git add <paths>`; other sessions may be working in forge), with the
     message `sw.models navigator: step N <title>`.
- **Write findings to files, never only in the conversation.** Survey findings go to `docs/sw.models/_build_notes.md`
  (working notes, one section per survey step, deleted in the last step).
- **Use the existing navigators** for anything outside sw.models: `dai-navigator` for Spark semantics, the type
  system, resource arbitration and `spark.action` / `startStopAction`; `sw-sword-navigator` for `integration.*`,
  `DEC_*` and kernel actions. Ask them precise questions.
- **Validation runs** always use a general-purpose agent told to read `.claude/agents/sw-models-navigator.md` and
  follow it, because the session's loaded copy of the agent may be older than the file.
- **Usable early:** after step 7 the navigator works for everyday questions. Phases B and C add the ontology and
  action indexes.

## Decisions
_(filled in at step 1; a resumed session must not ask again)_
- Scope: **sw.models only** (user chose this over "soult-ready"). Generators need not run on sw.soult; drop the
  sw.models / sw.soult duplicate-method check from the ontology anomaly report. sw.soult may still be named as
  "out of scope, not indexed".
- Change proposals: **proposals only** (diffs + placement/impact checklist), never edits.
- Agent / skill names: `sw-models-navigator` + `/models`.
- Validation questions (drafted by Claude at the user's request; user may amend before step 7):
  1. "Is there already a skill that makes a unit mount onto / dismount from its transport, and which tasks use it?"
  2. "When an agent gets the mission whose `dia-behavior` is `military.agent.tasks.AttackObjective`, which task and
     which skills run, which roles allow it, and does it react to frag orders?"
  3. "How is a mechanized infantry unit type composed in the physical DB (equipments, sensors, resources) and which
     decisional model / role does it get?"
  4. "What breaks if I rename the `military.agent.tasks.Reconnoitre` task: callers, `Missions.xml` wiring,
     translations, repo checks?"

## Steps

### Phase A — usable navigator
- [x] **0. Preflight.**
  - Read the template files (see **Copy the existing pattern**).
  - Note the sw.models branch and commit, the model dirs (`ls data/data/models`), and whether `C:\MASARepo\sw.soult` exists.
  - If `git status` shows someone else's uncommitted work in forge (e.g. the directai kit), ask the user before going on.
  - *Done when:* the log has the branch, commit and model dirs.
- [x] **1. Decisions.** Ask the user, in one AskUserQuestion:
  - **Scope:** sw.models only, or sw.models + sw.soult? Recommended: sw.models now, with a generator that also runs on sw.soult.
  - **Change proposals:** may the agent draft model changes as proposals? Recommended: proposals only, never edits.
  - **Names:** proposed `sw-models-navigator` and `/models`.
  - **Validation:** 3–4 real questions to validate with.

  *Done when:* **Decisions** is filled in.
- [ ] **2. Survey: decisional and physical.**
  - Survey the decisional tree:
    - how skills, tasks, queries, roles, world objects and behaviors are declared (xml + lua pairs) and named
    - how a mission reaches its task (`Missions.xml` `@dia-behavior`) and its skills
    - frag orders
    - where `integration.*` is called
  - Survey the physical DB layout, the repo checks (`src/checkdec`, `checkstage`, …), and the branch conventions.
  - Read `src/models/decdb.go`, the repo's README and ARCHITECTURE, and the user's notes (verify against the files).
  - Output: `_build_notes.md` § A.
  - *Done when:* the notes give the exact file patterns and regexes the generator needs.
- [ ] **3. Generator: decisional index.**
  - Write `tools/build_models_index.py`, structured for three outputs (one per index), and implement the decisional one → `docs/sw.models/decisional_index.md`. Its header gives branch and commit.
  - *Done when:*
    - it runs on ada in about a minute or less
    - its counts match a grep (e.g. the number of task XMLs)
    - it runs on sw.soult without crashing, if sw.soult is in scope
- [ ] **4. THEMES, README and checker kit.**
  - Write `docs/sw.models/THEMES.md` with the vocabulary, the theme rows and the gotchas. The ontology and action rows come later, in steps 10 and 13.
  - Write `docs/sw.models/README.md` with the commands to regenerate the indexes and run the checker.
  - Add a `sw.models` entry to `KITS` in `tools/check_themes.py`.
  - *Done when:* `python -I tools/check_themes.py` (all kits) passes.
- [ ] **5. Checker branch mode.**
  - Add an option to check a kit against another branch (`git ls-tree` / `git show`, no checkout).
  - *Done when:* it runs on the sw.models kit against one project branch, and every THEMES path missing there is fixed or marked branch-specific.
- [ ] **6. Agent and skill.**
  - Write `.claude/agents/sw-models-navigator.md`, adapted from the sw.sword one, with:
    - branch detection and an index-staleness check (index header commit vs HEAD; fall back to grep)
    - a model-existence check (`ls data/data/models`)
    - when to use which index: decisional (wiring), ontology (types and methods), actions (resources and frameworks).
      Phase B and C indexes are described as "if present".
    - the boundary with `sw-sword-navigator` (integration layer, kernel) and `dai-navigator` (Spark language and
      runtime), in the description too
    - if proposals are allowed: a checklist covering placement, xml + lua pair, `Missions.xml` wiring, callers,
      translations, `checkdec` / `checkstage`
    - the answer format with "Map updates"
  - Add the skill.
  - *Done when:* the checker passes on the agent file.
- [ ] **7. Validate A.**
  - Run each of the user's questions in turn. **Log each result** before starting the next: verdict, tool calls, and citations checked.
  - Apply verified map updates and re-run the checker.
  - *Done when:* all questions are logged and the checker passes. The navigator is usable once Claude Code restarts.

### Phase B — ontology
- [ ] **8. Survey: ontology.**
  - Cover:
    - how classes, interfaces and roles are declared, and how a `.lua` implements methods
    - `body-type`
    - where world objects are created (ask `sw-sword-navigator`)
    - which type rules directai enforces (ask `dai-navigator`)
  - Output: `_build_notes.md` § B.
- [ ] **9. Generator: ontology index.**
  - Output `docs/sw.models/ontology_index.md`, with the anomaly report and the note that callers are upper bounds.
  - *Done when:* the class, interface and role counts match a grep (about 173 / 36 / 32 in ada on 2026-11-05).
- [ ] **10. Ontology in THEMES and the agent.**
  - Add the THEMES row and its gotchas, and tell the agent when to use the ontology index.
  - Validate with one ontology question (e.g. "which interfaces have a single implementer, and could they be merged into their class?").
  - *Done when:* the result is logged and the checker passes.

### Phase C — action skills and resources
- [ ] **11. Survey: the action stack.**
  - Trace 2–3 real examples end to end: skill XML (resources, feedbacks) → Lua framework → `integration.*` → kernel `_DEC__Start*` → callback.
  - Use one `startStopAction` on `MeKnowledge`, one `skills/actions/` skill, and one `spark.action` if any exists.
  - Ask `dai-navigator` how resources are arbitrated.
  - Output: `_build_notes.md` § C.
- [ ] **12. Generator: action index.**
  - Output `docs/sw.models/action_index.md`.
  - *Done when:*
    - every `skills/actions/` skill appears, with its framework
    - every resource lists the skills that acquire it
    - every `startStopAction` method lists its handlers
- [ ] **13. Actions in THEMES and the agent.**
  - Add the "Resources & actions" row and its gotchas (deprecated `startStopAction`, truthy `_DEC__StopAction`, actions not stopped on mission change).
  - Add the agent rule to trace the whole stack and name the framework.
  - Validate with one action question (e.g. "which skills compete for the same resource as `Activate`, and which framework does each use?").
  - *Done when:* the result is logged and the checker passes.

### Phase D — cross-link and close
- [ ] **14. Cross-link.**
  - In `sw-sword-navigator.md` and `docs/sw.sword/THEMES.md`, point model-side questions to the new navigator.
  - Check that `docs/directai/THEMES.md` covers the Spark type system (class / interface / role / body-type, validation rules) and resources and actions (arbitration, `spark.action`, `startStopAction`). Add verified, anchored rows there if it doesn't.
  - *Done when:* the checker passes on all kits.
- [ ] **15. Close.**
  - Update the forge memory.
  - Move anything worth keeping from `_build_notes.md` into THEMES gotchas, then delete the notes.
  - Mark this handoff **DONE** at its top.
  - Final commit. Tell the user to restart Claude Code.

## Progress log
_(one entry per finished step: date, step, what was produced, what the next step needs)_
- 2026-11-05, step 0: sw.models on `test_DrawingTemplates` @ `e10c2a1336` (2026-11-03, clean tree); `data/data/models` =
  `ada/` + `decfunctions.txt` only. sw.soult exists, on `NavySmallUpdates`, model dir `sws/`. Forge clean apart from
  this handoff. Templates read (sw-sword-navigator agent, sw.sword THEMES/README, check_themes.py, build_dec_index.py, /sword skill).
- 2026-11-05, step 1: Decisions filled (sw.models only, proposals only, `sw-models-navigator` + `/models`, 4 drafted
  validation questions). Note: action dirs are `common/agent/behaviors/skills/actions`, `military/agent/skills/actions`,
  `navy/agent/skills/actions`, `crowd/behaviors/skills/actions`, `common/commander/behaviors/skills/actions`; 372 `dia-behavior` in Missions.xml.

