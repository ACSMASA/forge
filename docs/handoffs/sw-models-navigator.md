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

