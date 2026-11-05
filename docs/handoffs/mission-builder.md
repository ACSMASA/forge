# Handoff: build the unit-mission builder (shared mission + checkpoint kit, tools, agent)

**DONE 2026-11-05.** Record only: the facts below were folded into `docs/sw.models/MISSIONS.md`, `CHECKPOINTS.md`
and the tools; the working notes (`_mission_notes.md`) are in git history (commit `8693981`).

> **How to use:** start a Claude Code session in `C:\MASARepo\forge` and say
> "Follow `docs/handoffs/mission-builder.md`" (first time) or
> "Continue `docs/handoffs/mission-builder.md` from the first unchecked step" (after an interruption).
> Effort: high. The work is split into small steps, each ending in a commit, so a run that stops (for example on
> quota) loses at most one step. Written 2026-11-05. Sibling records: `docs/handoffs/sw-models-navigator.md`,
> `docs/handoffs/directai-navigator.md` (done; same pattern).

## Goal
An agent that **creates unit (agent) missions** in `C:\MASARepo\sw.models` (no automat missions, no frag orders),
whose behaviors are **checkpoint-resilient** (survive a checkpoint save + resume of a game session).
Its knowledge must be **readable by any agent** (sw-models-navigator explaining a mission's flow or reviewing a skill
for checkpoint safety, the main session) **without spawning the builder**. So the knowledge goes into kit files and
deterministic tools, and the agent prompt is a procedure that points at them.

## Shape (decided; do not re-litigate)
1. **Shared knowledge**:
   - `docs/sw.models/MISSIONS.md`: anchored recipe and rules for unit missions.
   - `docs/sw.models/CHECKPOINTS.md`: anchored save/resume rules, conventions and "Sim-side gaps"; valid for any behavior.
   - `docs/sw.models/mission_index.md` (generated): parameter-type catalog, per-mission rows, skill/query signatures
     with a checkpoint column, anomalies.
2. **Deterministic tools** (stdlib, `python -I`):
   - `tools/missionlib.py` + `tools/mission_check.py`: lints unit missions in the **working tree**, so it sees
     uncommitted edits.
   - `tools/checkpoint_lint.py`: static Lua lint for checkpoint hazards.
   - `tools/mission_scaffold.py`: spec → normalized insertions into the physical files.
3. **Thin agent** `sw-mission-builder` + `/mission` (fork), plus an inline `/mission-check` skill.

There is no separate "task composer" or "checkpoint" agent. Subagents cannot call each other, so extra agents would
only duplicate the kit.

Same house rules as the other kits: code and files are the only truth, kit files hold paths and `path::anchor` (no
line numbers, no behavior prose beyond one-line anchored rules), `tools/check_themes.py` checks every anchor, indexes
are grepped and never read whole.

## Decisions (from the user, 2026-11-05)
- **Write mode:** the builder **writes** into sw.models on the user's current branch. It never commits and never
  changes git state (no checkout, stash, reset or pull). If the branch is `master`, it stops and asks the user to
  create a branch.
- **Compose and create:** reuse existing skills and queries first. When something is missing, draft new skills,
  queries or action skills, following sw-models-navigator step 7 and its "Change proposals" checklist.
- **The builder is a critic, not a scribe:**
  - It challenges the request: an existing mission or task already covers it, the design can never end, or a
    parameter will never be passed.
  - It flags defects in the skills it reuses.
  - It ends every answer with a "Critique" section.
- **Checkpoints, sim-side first:**
  - Checkpoint handling should ideally live in sw.sword. The builder does not hesitate to propose sw.sword
    infrastructure changes (integration Lua or C++). These are **proposals as diffs, never written**: the builder
    writes only in sw.models.
  - Each proposal states its blast radius, measured: callers and overrides (`docs/sw.sword/integration_index.md`),
    model callers, sw.soult, client branches, and checkpoint simtests to extend.
  - It **always also gives a Lua-side solution**, for when that radius is too large or sw.sword is version-frozen,
    and recommends one of the two.
- **Names:** `sw-mission-builder`, `/mission`, `/mission-check`.
- **Validation scenarios** (step 10; the user may amend them):
  - (a) A new mission reusing an existing task with another parameter set.
  - (b) A new mission needing a new task built from existing skills, plus one new query and one new stateful skill.
    The result must be checkpoint-safe and lint-clean.
  - (b') A request whose checkpoint hazard is infrastructural, e.g. a skill that must keep a kernel action across
    resume. Expected: a sw.sword proposal with measured blast radius, a Lua-side fallback, and a recommendation.
  - (b'') A request that duplicates an existing mission. Expected: the builder says so first.
  - (c) Without the builder, a general-purpose agent following sw-models-navigator answers "flow of mission X" and
    "is skill Y checkpoint-safe?" from the kit.

## Context: facts established 2026-11-05 (re-verify each one while writing; cite only what you read)
Read at: sw.models `master` @ `d2f78fafcd`, sw.sword `master` @ `51bdc0ec9e`, directai `master` @ `9629aaf1a`.

Path aliases:
- `phy/` = `sw.models/data/data/models/ada/physical/mildb/`
- `wwt/` = `.../physical/worldwide-tests/`
- `dia5/` = `.../ada/decisional/dia5/`
- `sim/` = `sw.sword/src/libraries/simulation/`
- `int/` = `sw.sword/data/app-data/resources/integration/`
- `spark/` = `directai/src/libraries/plugins/spark/`
- `val/` = `directai/src/libraries/spark/validation/`

### Missions.xml (physical, per DB)
- **Sections:** unit missions are the `<mission>` children of `<units>` (mildb 247, wwt 286). They are followed by
  `<automats>` (`mrt-dia-behavior` / `cdt-dia-behavior`), `<populations>`, `<task-groups>`, `<fragorders>`
  (`<fragorder available-without-mission>`) and, in mildb only, `<categories>`.
- **Attributes**, always in alphabetical order:
  - `category`: mildb only, a name from `<categories>`; missing on 2 NAVY.MCM missions.
  - `dia-behavior`: the Spark task that gets started, `sim/Entities/Orders/MIL_PionMissionType.cpp`.
  - `dia-type`: unique among unit missions. It differs from `dia-behavior` 19× in mildb and 47× in wwt. Commander
    skills name unit missions by it in `<string>` lists.
  - `id`
  - `name`: the key used by Models.xml, the .ts files and the mission sheets.
  - There is no `package` attribute.
- **Kernel throws on duplicates:** id, name and dia-type, `sim/Entities/Orders/MIL_MissionType_ABC.h`.
- **No existence check on the task:** the kernel never checks at load that the task exists. A missing task fails at
  start: `spark.start` gives "unknown behavior", which ends in `eRC_MissionImpossible`
  (`sim/Entities/Orders/MIL_OrderManager_ABC.cpp`, `sim/Entities/Agents/MIL_AgentPion.cpp`).
- **First 4 `<parameter>`s are the context, read BY POSITION** (`MIL_OrderType_ABC.cpp` `ReadParameter`,
  `contextLength=4` in `MIL_PionMissionType.cpp`, `MIL_OrderContext.cpp`):
  - `dangerDirection_` Heading
  - `phaseLines_` PhaseLine, unbounded, `<objects/>`
  - `boundaryLimit1_` / `boundaryLimit2_` Limit, optional

  Their type is not validated. A `Limit` in position 5 or later fails with "Unknown parameter type". Example:
  `phy/Missions.xml` first mission.
- **Parameter attributes read by the sim:** `name`, `dia-name`, `type`, `optional`, `min-occurs`, `max-occurs`
  (`unbounded` makes it a list), `sim/Entities/Orders/MIL_OrderTypeParameter.cpp`.
  - ADN-only extras: `is-context`, `min-value`, `max-value`, `indirect-fire-only`, `owned-equipments-only`,
    `all-dotation-types`.
  - ADN-only child structures: `<choice><parameter type/>` (LocationComposite), `<objects all|…>`, `<units>`
    (UnitType), and `<value id name>` (Enumeration).
- **Allowed types:** registry in `sim/Entities/Orders/MIL_ParameterType_ABC.cpp`, matched case-insensitively.
  **Spark conversion:** `sim/Decision/DEC_Decision.cpp` `functorsBM`, keyed by type name, plus `List` when
  `max-occurs>1`.
  - A type with no BM converter is **silently not passed**. Examples: `Location`, `Direction`, `DirectionBM`,
    `bool`, `Automate`, `DotationType`, `GenObject`, `UrbanBlock`, `IndirectFire`, `Duration`, `Stage`,
    `LimaFunction`, `Mission`, `FragOrder`, `TaskGroupKnowledge`, `List`.
  - Many list forms are also missing: `BooleanList`, `EnumerationList`, `DateTimeList`, `HeadingList`,
    `TransmitterTypeList`.
  - Use the canonical names: `Heading`, `Boolean`, `ResourceType`, `PlannedWork`, `UrbanKnowledge`, `Polygon`.
- **Binding is by name:** `dia-name` must equal the task's `<parameter name>`.
  - Extras are dropped (`spark/behavior.lua` schedule copies only the declared names).
  - A task parameter with no counterpart gets its `<default>`, otherwise nil.
  - Warnings only appear with `--dec-typecheck`.
  - An absent optional **object** parameter arrives as `NIL` (`DEC_Decision.cpp`), a shared sentinel table
    (`spark/compatibility.lua`), so the task `<default>` is skipped; an absent **scalar** (Boolean, numbers, String,
    Enumeration…) stays nil and gets the `<default>` (its converter always returns true).
  - Order-time validation (`sim/Entities/Orders/MIL_Mission_ABC.cpp` `FillParameters`, by position after the
    context): missing non-optional, min/max-occurs, `IsOfType`, invalid geometry.
- **Ids:** mildb max unit id 445949727, file max 445949729; wwt max 545949903. The 5459xxxxx range is wwt-only.
  - The same id may carry different names in the two DBs.
  - ADN allocates ids with `IdManager::GetNextId()` and renumbers duplicates on load
    (`sw.sword/src/applications/adaptation_app/ADN_Missions_*.cpp`).
  - `sw.models/src/models/phydb.go` fails on duplicate ids.

### Models.xml, Units.xml, roles
- **Unit model:** `<unit dia-type="<role>" file="BMAgent.bms" masalife="true" name>`, then
  `<missions><mission name>` with optional `<fragorder name>` children, then model-level `<fragorders>`.
  - An unknown mission name → "Invalid mission name" at load, `sim/Decision/DEC_Model_ABC.cpp`.
  - Units.xml `decisional-model` picks the model.
  - Order-time check `IsMissionAvailable`, `sim/Entities/Orders/MIL_PionOrderManager.cpp`.
  - One archetype brain per `file`: `sim/Decision/DEC_Decision.cpp` `CreateBrain`, then `sw.ai.setRole(dia-type)`.
- **Role `default-task`:** example `dia5/military/agent/roles/Platoon.xml`, running `military.agent.skills.Default`.
  - It runs **concurrently** with the mission: all active behaviors are activated every tick, both at priority 1
    (sw.sword starts missions without a priority, `int/SimulationIntegration.lua` `startOrResume`).
  - They compete for resources.
  - `spark.stopAll()` spares the default task (`spark/behavior.lua`, `int/CRTaskListener.lua` `integration.stopTasks`).
- **Modifiers:**
  - The skill declares `<modifiers><modifier name><type><value>` (boolean, number or string only).
  - A role overrides them in `<skills><skill name><modifier name>`.
  - Lua reads `skill.modifiers.x`, or `spark.getSkillModifier(skill, name)` from outside the skill. An undeclared
    name is a runtime error; sparkcheck passes an empty table.
  - Example: `dia5/military/agent/skills/SelfProtectFromEnies.xml`, where `importance` serves as an activation
    priority. 16 skills declare modifiers; 11 of 32 roles set them; tasks never do.

### Tasks (Spark 1.5; `directai/etc/schemas/spark/1.5/spark.xsd`, `val/parsers/task.lua`, `spark/task.lua`)
- **Shape:** `<task name disable-when-done?>` > `parameters` (`<parameter name nullable?><type>`, `<default>`?) >
  `let*` > `parallel?` > `stages` (`<stage label?>` > `let*` > `<behaviors><behavior name next-stage-when-done?
  priority? ignore-failure?>` with `<parameter name element-wise?>VALUE`) > `next-task?`.
- **VALUE** is one of: a literal, `<entity>`, `<variable>`, `<call query once?>`, `<list>`, `<map>`, or `<NIL/>`
  (deprecated).
- **Metadata class:** an annotation `spark.metadata-class` holding an `<entity>`. The class must subclass
  `sparkle.TaskMetadata` and not be abstract. Only commander code reads it, so it is needed only when an automat gives
  the mission.
- **Ending a mission:** only `disable-when-done="true"` (or a failure) ends it.
  - `int/CRTaskListener.lua` `willStop` sends `eRC_BM_FinMission` or `eRC_BM_EndMissionFailed`, then
    `_DEC_FinMission`.
  - Without it, `done` → `finished` (`TaskDone` goes to the superior) and **the task keeps running**.
  - Stage events become the reports `eRC_BM_DebutPhase` / `FinPhase`.
- **`next-stage-when-done`** defaults to true for a stage with a single behavior (`val/parsers/task.lua`).
- **Parallel stage:** its `done` is ignored unless the task is `disable-when-done`; its failure fails the task.
- **Failures:** any behavior's `failed` fails the task unless that behavior has `ignore-failure`.
- **Lets:** resolved lazily. Query results are recomputed **every tick** unless `once="true"`, which is the default
  for a top-level `<call>` in a parameter `<default>`.
- **`element-wise`:** one activation per element.
- **Validator codes:** `val/messages.lua` (`task.*`, `parameter.*`, `value.*`, `role.skill.modifier.*`,
  `behavior.graph-has-cycle`).
- **Census:** 523 tasks (none with Lua). 281 unit tasks outside test/frago/commander. 134 have a metadata class,
  13 a parallel stage, 28 use let.
- **Examples:**
  - small: `dia5/military/agent/tasks/GetTransported.xml`
  - lets, queries and a metadata class: `dia5/military/agent/tasks/AttackObjective.xml`
  - bigger: `dia5/mildb/agent/tasks/AirAttackGroundTargets.xml`
- **Queries:** `<query>` > `parameters` > `body-type?` > `<return><type>`. The Lua returns `function(params)`.
  Called from XML with `<call>` (raw function, no runtime type check) or from Lua with `x.y.query{…}`. Example:
  `dia5/military/queries/getEnemiesInRange.xml`.

### Other files a unit mission touches
- **Stages:** `phy/Stages.xml` + `wwt/Stages.xml`: `<stage key value>` for every stage label. Also
  `phy/Locales/fr/Stages_fr.ts`.
- **Mission translations:** `phy/Locales/fr/Missions_fr.ts` (wwt has no `Locales/`). The mission name goes in context
  `unit-missions`; parameter names and enum values go in context `unit - <mission name>`; a new category goes in
  `mission-categories`.
- **Mission sheets:** `Mission sheets/units/<name>.xml` + `.html` in each DB that has the mission (a mission may be
  in one DB only: 75 mildb-only, 114 wwt-only); French sheets **mildb only**, under
  `Mission sheets/units/Locales/fr/<French name>.xml/.html` (French name = the `unit-missions` translation).
  - The sheet XML is a `<mission-sheet>` with `context`, `parameters`, `behavior`, `specific-cases`, `comments`,
    `mission-end` and `attachments`.
  - The `.html` is rendered by ADN through `missionSheets.xslt` (`phy/physical.xml`).
  - Example: `phy/Mission sheets/units/COMBAT - Attack objective.xml`.
- **Reports:** `Reports.xml` only for a new `eRC_`.
- **Changelog:** `data/changelog.txt` only for renames and moves.
- **Checks** (`sw.models/build/build.xml`):
  - `checkdec`: no `DEC_` calls; dia-behavior → task, **warning only**; duplicate ids fatal; sheet pairs **fatal**.
  - `checkstages` (`generate-stages` fixes it).
  - `checktranslations`: stages only; **Missions_fr.ts is not checked**.
  - `checkpackages`, `checkreports`.
  - `checkxml`: a sw.tools binary, not in the repo.
  - `generate-database`: sparkcheck.
- **No XSD** for Missions/Models/Stages is available anywhere locally. **No template or generator exists**; missions
  are normally edited in SWORD Adaptation (ADN).

### Checkpoints (save / resume)
- **Saved per brain** (`decisionalState.xml`): one string from `int/SimulationIntegration.lua`
  `integration.savePersistentState`. It contains `persistentState[""]` (per-skill states:
  `[skillName][getPersistentKey(params)]`) and `spark.saveState()` (task `seqStageIndex`, per-stage tables
  `seqStage` / `parStage`).
  - Written by `sim/Decision/DEC_Decision.h` `WriteDecisionalState` and `sim/Decision/Brain.cpp`.
  - **One non-serializable value (userdata, function, knowledge object, cycle) drops the brain's whole state**, with
    only a log ("Error serializing persistent state"). The mission then restarts from stage 1.
- **Also saved:**
  - The current **unit** mission with its parameters (`sim/.../MissionController.cpp` `WriteMissions`). A mission
    with an invalid parameter is silently not saved; automat missions are not saved.
  - Knowledge with its ids.
  - The ODB decision attributes (`DEC_RolePion_Decision::WriteODB`).
- **Not saved:**
  - The Lua VM: node instances, `self.*`, `myself.*` (`currentMission`, `taskParams`, `previousTaskParams`,
    `leadData`), `meKnowledge` fields, knowledge caches, `startStopAction` state, pending messages.
  - **Pion kernel actions:** they are dropped and `start` runs again with new ids. A stale stored id can match an
    unrelated new action (`sim/Decision/DEC_ActionFunctions.cpp`, ids re-seeded on load).
- **Resume sequence:** fresh brain → `InitMePlatoon` (resets `persistentState`) → default task scheduled →
  `integration.loadPersistentState` (`spark.loadState` replaces the store) → missions read → first tick
  `MIL_PionMission::Start` with `toResume_` → `ResumeEvent` → `spark.resume`.
  - The task restarts **at the saved stage, from that stage's beginning**: `create` and `start` rerun, `willStart`
    and stage reports are re-sent, and the parent link is lost.
  - A saved stage that no longer exists → the task silently counts as finished.
  - There is **no resume hook**: a skill knows it resumed only because its state is non-empty.
- **`integration.getPersistentKey(params)`:**
  - It **errors on a param type without `hash`**: PhaseLine, AreaOfResponsibility, Direction, FragOrder,
    EquipmentType, CBRNProtection, HumanWound (MeKnowledge inherits `Agent:hash`, corrected at step 6). Classes with `hash` (commit `aa0b276a84`): Agent,
    AgentKnowledge, Area, UrbanBlock, UnitType, ResourceNode, ReachingArea, Point, PlannedObject, Object, Crowd,
    Company.
  - It iterates with `pairs`, so key order may vary across a reload: Lua 5.2 hash seed, to verify.
  - `Point:hash` uses `%g`.
  - Skill states are **never cleared automatically**.
- **Model helpers** (`dia5/_scripts/Scripts.lua`): `scripts.getSkillState`, `setSkillState` (0 callers),
  `setSkillStateElement`, `clearSkillState`, `set/getSkillStatePointElement` (XY → `CreateKnowledge`).
  - They are used by **18 skills only**: 12 navy, plus military Default, Delay, Screen and
    MoveUAVAlongRouteAndGoBackToTransporter, plus common MoveAlong and OpenRoute.
  - Spark also offers root-node `self:getPersistentState()` (`spark/skill.lua`), unused in dia5.
  - `scripts.registerActionCallback` (in `myself.actionsState`) is **not** persisted.
- **Conventions (from the model):**
  - Persist plain data only: ids via `getID` / `meKnowledge:getAgentFromID` / `integration.getAgentKnowledge(agentId,
    knowledgeId)`, XY points, indices, booleans, and `utilities.getRealTime()` deadlines.
  - Read in `create` with `~= nil`, not `or default`.
  - Write on every change, and clear in `destroy`.
  - Mirror `myself` state into the Default skill's state, as `dia5/military/agent/skills/Default.lua` does with
    `detacherID` / `transporterID`.
  - Never persist knowledge objects, action ids or closures.
  - Make `create` / `start` idempotent.
- **Known fragile cases** (lint test set):
  - `dia5/navy/agent/skills/EmbarkToLandingShipDock.lua` and `DisembarkFromLandingShipDock.lua`: `or true` turns a
    saved false into true.
  - `dia5/navy/agent/skills/LoadUponRequest.lua`: `== {}`.
  - `dia5/navy/agent/skills/Unmoor.lua`: an unpersisted timer.
  - `dia5/common/agent/behaviors/skills/actions/DetachUnit.lua`: `createEmptyUnit` reruns.
  - `dia5/common/commander/behaviors/skills/actions/IssueMission.lua`: re-issues missions in `create`.
  - `dia5/military/agent/skills/OperateACheckpoint.lua`: Lua-only "done" flags.
  - Known-safe: `dia5/common/agent/behaviors/skills/MoveAlong.lua`, `dia5/military/agent/skills/Delay.lua`.
- **Test harness** (for the user; the agent never runs the sim):
  - `simulation_app --savecheckpoint <n>` / `--checkpoint <n>` (`sw.sword/src/libraries/core/SessionConfig.cpp`,
    `sim/Tools/MIL_Config.cpp`).
  - Go simtests `sw.sword/src/masa/sword/simtests/checkpoint_test.go` `checkpointAndRestart`. No simtest checks the
    decisional state.

## Working rules (apply to every step)
- **On start or resume:** run `git -C C:/MASARepo/forge status --short`, read **Decisions** and **Progress log**, and
  continue at the first unchecked step. If a step was interrupted (outputs half-written, nothing committed), redo it
  from the beginning. Every step is safe to repeat.
- **Branches:** record the sw.models / sw.sword / directai branch and commit you read at each step. Never change git
  state in the sibling repos.
- **Build and validation writes:** tools and validation runs write only to the scratchpad (a copy of the model dir,
  passed with `--root`), never into sw.models, until the user uses the finished agent.
- **On finishing a step:**
  1. Tick its box.
  2. Add 1–3 lines to the **Progress log**.
  3. Commit **only the paths you touched**, with the message `mission builder: step N <title>`.
- **Write findings to files, never only in the conversation.** Working notes go to `docs/sw.models/_mission_notes.md`
  (deleted in the last step).
- **Questions outside sw.models:** use the existing navigators with precise questions: `dai-navigator` for Spark,
  `sw-sword-navigator` for sim and integration.
- **Validation runs** use a general-purpose agent told to read the agent file and follow it, because agent files load
  at session start. After editing an agent file, tell the user to restart Claude Code before using it directly.
- **Run** `python -I tools/check_themes.py` after every doc or agent step; it must pass.

## Steps

### Phase 0 — handoff
- [x] **0. Handoff file.** This file. Commit `mission builder: step 0 handoff`.

### Phase A — shared knowledge
- [x] **1. Settle open format questions** (read-only; answers go into `_mission_notes.md` § 1, and into this file's
  Context if they change a fact):
  - Insertion order in `Missions.xml` (by id?), in a model's `<missions>`, in `Stages.xml`, and in the `.ts` contexts.
  - How a sheet `.html` is produced. Stdlib has no XSLT, so mirror an existing simple sheet.
  - Id convention. Proposed: max over both DBs' files + 1, the same id in both.
  - Does an optional param that arrives as `NIL` get the task `<default>`?
  - Lua 5.2 string-hash randomization, which affects `getPersistentKey` order: check the directai Lua build config.
  - How to tell which sw.sword version a sw.models branch targets and whether it is frozen (`decisional.xml`
    `model-version`, sw.sword release branches, the directai pin). The builder needs this to choose between a
    sim-side and a Lua-side fix.

  *Done when:* each question has an answer with a file:line, or is marked "unknown, ask the user".
- [x] **2. `docs/sw.models/MISSIONS.md`.**
  - Anatomy table: file → element / field → rule → `path::anchor`, per DB.
  - One-line anchored rules: context block, types and converters, binding, optional ↔ nullable/default, ending,
    stages, parallel stage, lets / queries / `once`, default-task concurrency and resources, modifiers, metadata
    class, reports.
  - Checks table: what each ant target catches, and what nothing catches.
  - The designer's build order.
  - Add the file to the sw.models kit `files` in `tools/check_themes.py`. The checker passes.
- [x] **3. `docs/sw.models/CHECKPOINTS.md`.**
  - What is saved, lost and rerun (anchored), and the resume sequence as anchored hops.
  - Hazards: whole-state drop, unhashable params, `pairs` key order, `or default` booleans, stale action ids,
    non-idempotent `create` / `start`, re-sent reports, stage-shared `seqStage` tables, never-cleared states.
  - The conventions, with model examples, and the user's test recipe.
  - A **"Sim-side gaps"** table: gap → anchor → candidate sw.sword change → how to measure the blast radius. Seed
    rows: the serializer drops the whole state; `getPersistentKey` errors on unhashable params and depends on `pairs`
    order; skill states are never cleared on task stop; pion kernel actions are not saved and there is no stale-id
    guard; no resume hook or `resuming` flag; reports are re-sent and the parent link is lost on resume; `myself.*`
    mission state is not persisted.
  - Add a gotcha line pointing to the file in `docs/sw.sword/THEMES.md` and `docs/directai/THEMES.md`, and a THEMES
    sw.models row "Checkpoints (save / resume)".
  - Add the file to the checker. The checker passes.
- [x] **4. Cross-links.**
  - CLAUDE.md pipeline rows "Unit mission: anatomy / build / check" and "Checkpoint safety of a behavior", pointing to
    the two files (and, later, the tools).
  - sw-models-navigator: "Mission question", step 7 and "Change proposals" point to `MISSIONS.md` /
    `CHECKPOINTS.md`.
  - One line in the other two navigators' "Where things are".
  - The checker passes. Tell the user to restart before using the agents directly.

### Phase B — tools
- [x] **5. `tools/missionlib.py` + `tools/mission_check.py`.**
  - Parse, per DB: unit missions, models, stages, .ts, sheets, and the target task XML.
  - Checks:
    - context block
    - id / name / dia-type unique across all sections, and consistent across the DBs
    - type known and has a BM converter, list form included (read `functorsBM` from `sim/Decision/DEC_Decision.cpp`)
    - dia-names ↔ task params (a missing one must have a default)
    - optional ↔ nullable / default
    - task exists, and has `disable-when-done`
    - stage labels in both Stages.xml and Stages_fr.ts
    - listed in at least 1 model per DB
    - Missions_fr.ts entries
    - sheet pairs, en + fr
    - category (mildb)
    - attribute order
  - CLI: `--mission NAME | --task T | --all`, `--db`, `--model`, `--root`. Exit 1 on errors; warnings are separate.
  - Validate `--all` on the current branch and triage until there are no false positives.
    `--mission "COMBAT - Attack objective"` must be clean.
- [x] **6. `tools/checkpoint_lint.py`.** Heuristic: warnings are upper bounds, each with file:line. Per skill Lua it
  flags:
  - `self.*` fields assigned outside `create` with no skill-state mirror
  - `getSkillState` in a skill whose parameter types lack `hash`
  - `getSkillState(...).x or <literal>`
  - non-plain stored values: knowledge objects, action-id names, functions
  - no `clearSkillState` in `destroy`
  - absolute timers kept in `self`
  - side effects in `create` / `start` (`issueMission`, `createEmptyUnit`, `displayReport`) with no resume guard

  `--task T` walks stages → behaviors → links, reusing the link resolution in `tools/build_models_index.py`.
  Validate on the known fragile and known-safe cases (Context); tune false positives.
- [x] **7. `docs/sw.models/mission_index.md`.**
  - Built by `tools/build_models_index.py --only missions`, reusing `scan_decisional`, `scan_physical`,
    `repo_stamp` and `scan_sword`.
  - §1 Parameter-type catalog: Missions.xml type → converter and list support → ontology key / world class → task
    `<type>`s seen → hashable? → count and an example mission.
  - §2 One row per unit mission per DB.
  - §3 Skill signatures: params, feedbacks, modifiers, body type, resources, and a **checkpoint** column (persisted
    keys / lint warnings).
  - §4 Query signatures.
  - §5 Anomalies.
  - Register in `tools/kits.py` `INDEXES` (repos sw.models, sw.sword) and in `docs/sw.models/README.md`.
- [x] **8. `tools/mission_scaffold.py`.**
  - Input: a JSON spec with name, dia-type, task, category, params, DBs, models, stage labels + fr, and fr name.
  - Output, as normalized insertions:
    - the Missions.xml entry, with the context block and the next id
    - Models.xml entries
    - Stages.xml + Stages_fr.ts entries
    - Missions_fr.ts contexts
    - sheet `.xml` / `.html` pairs, en + fr
    - a task XML skeleton, typed from §1
  - Default: print a unified diff. `--write` applies it.
  - Validate on a scratch copy (`--root`): scaffold, then `mission_check` must be clean.

### Phase C — agent and skills
- [x] **9. `.claude/agents/sw-mission-builder.md`** (tools: Read, Grep, Glob, Bash, Edit, Write; model inherit) and
  `.claude/skills/mission/SKILL.md` (`/mission`, `context: fork`).
  - Description in the navigators' style: unit missions only, NOT automat missions, frag orders or sim-semantics
    questions.
  - Procedure:
    1. Context check: branch, commit, dirty state. If on `master`, stop and ask for a branch.
    2. Clarify intent: DBs, unit types / models, parameters.
    3. **Critique the request.**
    4. Reuse scan (`mission_index` §2–§4), reporting defects found in the reused skills.
    5. Design: stages, bindings, lets, queries, parallel stage, `disable-when-done`, default task and resources,
       modifiers.
    6. **Checkpoint design** per stage. For an infrastructural hazard, give **two options**: a sim-side sw.sword diff
       with measured blast radius and a version-freeze check, and a Lua-side solution; recommend one.
    7. Missing skills, queries and action skills, written checkpoint-safe.
    8. Write: `mission_scaffold.py --write` for the physical files, Edit / Write for the XML + Lua.
    9. Run `mission_check.py` and `checkpoint_lint.py --task`; both clean, or every warning justified.
    10. Report: diff stat, the ant checks to run, the checkpoint test recipe, the sw.sword proposal, a **Critique**
        section, and Evidence / Caveats / Map updates.
  - Hard rules: write only under `data/data/models/<model>/`. No git state changes, commits, ant or sim runs unless
    asked. sw.sword and directai changes are proposals only.
  - Add the file to the checker's sw.models kit.
- [x] **10. Validation.** Run the scenarios in **Decisions** with a general-purpose agent that reads and follows the
  agent file, working on a scratch copy. Fix the gaps found in the kit, tools and prompt.
- [x] **11. Close.**
  - `/mission-check` skill: model-invocable, inline. It runs both linters on a mission, task or skill and explains the
    findings from `MISSIONS.md` / `CHECKPOINTS.md`.
  - CLAUDE.md mention.
  - Delete `_mission_notes.md` and mark this file **DONE**.
  - Update the memory pointer.

## Progress log
- 2026-11-05 step 0: handoff written from the approved plan (exploration of sw.models, directai, sw.sword missions
  and checkpoints done in the planning session; facts in **Context**).
- 2026-11-05 step 1 (same 3 commits): answers in `_mission_notes.md` §1. Append missions at end of `<units>`; Stages,
  .ts sorted ordinal; ids = max over both DBs + 1; absent optional → `NIL` sentinel, default skipped (corrected at step 4); `getPersistentKey` order
  unstable (Lua 5.2.4 seed); version = `build/build.xml` `version.major`. fr sheets are mildb only (Context fixed).
- 2026-11-05 step 2: `MISSIONS.md` (anatomy, parameters, task rules, default task, checks, build order). Checker gets
  cross-repo aliases `sim/ int/ spark/ val/` for the sw.models kit. New facts: Stages.xml must hold every dia5 label in
  **both** DBs; a single-behavior last stage is not stage-ending unless the task is `disable-when-done`.
- 2026-11-05 step 3: `CHECKPOINTS.md` (saved/lost table, resume hops, 14 hazards, conventions, test recipe, 7 sim-side
  gaps with blast-radius recipe and version-freeze rule). Gotcha lines in sw.sword / directai THEMES, sw.models THEMES
  row. New: `seqStage` tables are per behavior index, shared across sequential stages; actions use the sim id space.
- 2026-11-05 step 4: CLAUDE.md rows, sw-models-navigator (shortcuts, step 3, new step 9 checkpoint safety, proposals
  step 4), one line in sw-sword / dai navigators, THEMES mission row. **Correction**: `NIL` is a shared sentinel
  (`spark/compatibility.lua`), so an absent optional parameter skips the task `<default>` (notes §1.4, MISSIONS §2).
- 2026-11-05 step 5 (same commits): `missionlib.py` + `mission_check.py`. `--all` triaged to 22 errors / 12 warnings,
  all real model defects (18 dia-names not in the task, EnumerationList without converter, 2 required task params
  unfed, …); reference mission clean. Refinement: absent optional **scalar** → nil → `<default>` applies; absent
  **object** → `NIL`, default skipped (per-converter, computed from DEC_Decision.cpp). Parallel labels and
  `dia5/test/` are outside checkstage; it scans all of `decisional/` (taskgroups submodule empty locally).
- 2026-11-05 step 6: `checkpoint_lint.py`, 9 rules. All known-fragile cases flagged (or-true ×2, table-eq, Unmoor
  timer, DetachUnit createEmptyUnit, IssueMission, OperateACheckpoint flags). Tuned: hashability over concrete
  classes (abstract LocalizedElement was a false positive), fields set in create/start/select/started not "lost",
  reports split from world side effects, default-task skills exempt from no-clear. `--all`: side-effect 7,
  key-order 16 (incl. MoveAlong and Default: the "known-safe" skills are exposed to the pairs-order hazard).
- 2026-11-05 step 7: `mission_index.md` (§1 53 types, §2 533 mission rows with mission_check status, §3 562 skills with
  state keys + lint counts, §4 queries, §5 33 anomalies), `build_models_index.py --only missions` (now in the default
  `--only`), registered in `kits.py` and the kit README; sw-models-navigator shortcut + context-check grep. Also
  committed: mission_check UTF-8 output, `.gitignore` for `__pycache__/`. Next: step 8 in a fresh session.
- 2026-11-05 step 8 (same 3 commits): `mission_scaffold.py` (JSON spec → Missions/Models/Stages/.ts/sheets/task skeleton;
  diff by default, `--write` refused on master). Sheet `.html` renderer byte-exact on 780/780 (`--check-html`; rules in
  notes §8). Scratch validation: two specs scaffolded, `mission_check` 0/0, `--all` unchanged (22/12).
- 2026-11-05 step 9: `sw-mission-builder` agent (10-step procedure, root override for scratch copies, master stop,
  sim-side diff + Lua-side option + recommendation, Critique section) and `/mission` (fork). Scaffold reads the spec
  from stdin (`-`). Agent added to the checker's sw.models kit. Restart Claude Code before using it directly.
- 2026-11-05 step 10: 5 runs on scratch copies, all PASS (notes §10): (a) Patrol once → new PatrolOnce task, near-dup
  flagged, getPersistentKey sort proposal; (b) Hold crossing site → new query + HoldForDuration (persisted deadline),
  lint-clean; (b') build + guard obstacle → construction already resumes from world state, sim-side identity diff +
  Lua wrapper; (b'') duplicate reported first, nothing built; (c) navigator answered both questions from the kit.
  Fixed: decisional_index §7 unit/automat models (rebuilt with mission_index), CHECKPOINTS (MoveAlong not safe, world
  state row, waitInMin, stale state, construction gap, branch grep), MISSIONS (terminators, inherited default task,
  frag orders), THEMES (escort, timers), agent (root override, exact/near duplicate, skill search, one-line gaps),
  scaffold (frag orders, per-DB objects, `ends`, int defaults, unknown keys), mission_check (targets, frag orders,
  terminators), checkpoint_lint (`lost-timer`, reset hint, `-v`).
- 2026-11-05 step 11: `/mission-check` skill (inline, model-invocable: both linters + rule explanations), CLAUDE.md
  (builder bullet, tools in the two pipeline rows), `_mission_notes.md` deleted, memory pointer updated. Restart
  Claude Code to load `sw-mission-builder`, `/mission` and `/mission-check`.
