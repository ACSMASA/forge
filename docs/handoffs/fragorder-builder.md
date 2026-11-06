# Handoff: build the frag-order builder (shared frag-order kit, tools, agent)

> **How to use:** start a Claude Code session in `C:\MASARepo\forge` and say
> "Continue `docs/handoffs/fragorder-builder.md` from the first unchecked step". Effort: high. Small steps, each
> ending in a commit, so an interrupted run loses at most one step. Written 2026-11-05 from the approved plan of
> `docs/handoffs/fragorder-builder-planning.md`. Precedent and template: `docs/handoffs/mission-builder.md` (done).

## Goal
An agent that **creates frag orders** (fragmentary orders) in `C:\MASARepo\sw.models` for **units, automats and
crowds** and wires them (physical files, `FragOrder.lua`, tasks, skills). The behaviors must be **checkpoint-resilient**
where the model allows it, and the builder must say plainly where it does not. Its knowledge is **readable by any agent**
(sw-models-navigator explaining "what happens when frag order X reaches unit Y", the main session, `/mission-check`)
**without spawning the builder**. The knowledge goes into kit files and deterministic tools; the agent prompt is a
procedure that points at them.

## Shape (decided; do not re-litigate)
1. **Shared knowledge**:
   - `docs/sw.models/FRAGORDERS.md` (new): the anatomy, the **kinds** of frag order and how each is wired, delivery and
     availability, frago task rules, automat propagation, crowds, checks, build order.
   - `docs/sw.models/CHECKPOINTS.md` (extended): frag-order rows, hazards, sim-side gaps and **one reference fix pair**
     (a sim-side design and a Lua-side design) for "a frag order does not survive a save + resume".
   - `docs/sw.models/mission_index.md` (extended): a new **§6 Frag orders** section, plus frag-order anomalies in §5.
2. **Deterministic tools**. Extend the existing ones; do not fork them:
   - `tools/missionlib.py`: frag-order parsing plus a `FragOrder.lua` parser. Move `scan_fragorder_lua` there from
     `tools/build_models_index.py` and import it from both.
   - `tools/mission_check.py --fragorder NAME | --all-fragorders`: frag orders are also included in `--all`.
   - `tools/checkpoint_lint.py --fragorder NAME`: lints the task the order starts, and the skills that read it.
   - `tools/mission_scaffold.py`: a frag-order spec mode (the spec key `"fragorder": true`), which also inserts into
     `FragOrder.lua`.
3. **Thin agent** `sw-fragorder-builder` + `/fragorder` (fork). `/mission-check` also accepts a frag-order name. The
   agent shares the tools, `CHECKPOINTS.md` and the indexes with `sw-mission-builder`. A separate prompt is kept
   because the procedure branches on the frag-order kind and the agent descriptions must route cleanly.

Same house rules as the other kits: code and files are the only truth, kit files hold paths and `path::anchor` (no
line numbers, no behavior prose beyond one-line anchored rules), `tools/check_themes.py` checks every anchor, indexes
are grepped and never read whole.

## Decisions (from the user, 2026-11-05)
- **Scope:** unit, automat and crowd frag orders, with or without a mission:
  - **Unit:** all wiring kinds: task via an initializer, task named by the dia-type, read by skills, immediate effect.
  - **Automat:** `fragOrdersToBePropagated`, `executeAutomatFragOrder` branches, the commander `ManageFragOrder`
    filters, automat models.
  - **Crowd:** crowd initializers and frago tasks, crowd models.
  - **Out of scope:** task-group frag orders. Their tasks are not in sw.models (`taskgroup.tasks.*`; the submodule is
    empty locally). The builder says so and names the files.
- **Shape:** separate thin agent `sw-fragorder-builder` + `/fragorder`, new `FRAGORDERS.md`, shared tools,
  `CHECKPOINTS.md` and `mission_index.md`.
- **Write mode:** the same as the mission builder:
  - It writes into sw.models on the user's current branch, under `data/data/models/<model>/` only.
  - It never commits and never changes git state. On `master` / `main` it stops and asks for a branch.
  - sw.sword and directai changes are **proposals as diffs** only.
  - **In addition, it may edit `dia5/FragOrder.lua`**: initializer entries, the propagation list, automat branches.
- **Critic, not a scribe**, as in the mission builder:
  - It challenges the request: a duplicate (also across DBs under another name), an order nothing handles, one that
    never ends, one never available, a parameter never read.
  - It flags defects in what it reuses.
  - It ends with a **Critique** section.
- **Checkpoints: a reference fix pair, then a per-order classification:**
  - One step designs the two reference fixes once: a sim-side diff with a measured blast radius, and a Lua-side
    design. They are stored in `CHECKPOINTS.md` §6/§7.
  - For each new order, the builder classifies what survives:
    - **Immediate / ODB effect:** survives.
    - **Task:** lost unless one of the two fixes is applied; the builder cites the pair and recommends one, with the
      version-freeze check.
    - **Read by a running skill:** survives only if the skill persists the derived value.
- **Names:** `sw-fragorder-builder`, `/fragorder`, and the existing `/mission-check` (extended).
- **Validation scenarios** (step 12; the user may amend them): listed in that step.

## Context: facts from the planning session (re-verify each one at step 1; cite only what you read)
Read at: sw.models `master` @ `d2f78fafcd`, sw.sword `master` @ `51bdc0ec9e`, directai `master` @ `9629aaf1a1`.
Facts marked ✓ were read directly in the planning session. The others come from three exploration reports, with
their file:line: **to verify at step 1**.

Path aliases (as in `mission-builder.md`):
- `phy/` = `sw.models/data/data/models/ada/physical/mildb/`
- `wwt/` = `.../physical/worldwide-tests/`
- `dia5/` = `.../ada/decisional/dia5/`
- `sim/` = `sw.sword/src/libraries/simulation/`
- `int/` = `sw.sword/data/app-data/resources/integration/`
- `spark/` = `directai/src/libraries/plugins/spark/`
- `sparkle/` = `directai/data/sparkle/src/sparkle/`

### Physical files (measured over every entry)
- **`Missions.xml <fragorders>`**
  - mildb has 99 entries (`phy/Missions.xml` 6938–7322, before `<categories>`). wwt has 69 (the last section).
  - Files are LF, no BOM. Indents: 4 for `<fragorder>`, 6 for `<parameter>`, 8 for its children.
  - **Attributes are always alphabetical:** `available-without-mission category dia-type id name`. `category` is
    mildb only and missing on 13 entries. It is a `<categories>` name shared with missions; 5 categories are used
    only by frag orders.
  - **Entries are appended**, not sorted. Ids interleave with mission ids. The next id follows the scaffold rule: max
    over both DBs + 1, which is **545949904** today.
  - **Ids are not consistent across DBs:** 92 ids name different entries (corrected at step 1). 38 names are shared; 6 of them have
    different ids and 6 have different parameters. 9 dia-types are shared under different names (`Infiltrate` =
    "Use infiltration path" in mildb).
  - Precedent commit `39dacf24df` added frag orders by hand. `27d2c6ee6e` fixed it: duplicate id, `.ts` order, LF
    `.html`, missing fr sheets.
- **No context block** (0 frag orders have the 4 context parameters). Kernel: `contextLength` 0,
  `sim/Entities/Orders/MIL_FragOrderType.cpp:54`.
  - Parameters: mildb has 61 entries with parameters (1 to 6 each). Types: Integer, AgentKnowledge, Point,
    Enumeration, ResourceType, EquipmentType, Path, UnitType, LiaisonInstance….
  - Extra ADN attributes: `owned-transmitters-only`, `network-instance`, `direct-liaison-instance`,
    `consistent-unit-type`, `unit-type-display-range`.
  - Child elements `<transmitters>` and `<equipments>` are **not written** by `mission_scaffold.param_xml` today.
- **Models.xml**
  - Each model has `<missions>` then `<fragorders>`. Unit, automat, crowd and task-group sections all carry them.
  - Model-level lists sit at indent 8 and mission-level lists at indent 10. Empty forms are `<fragorders/>` and
    `<missions/>`.
  - **Lists are append-order**, not sorted. Mission-level lists are mostly the fixed pattern ("Pause movement",
    "Resume movement"), plus "End mission" in wwt.
  - Every name exists in Missions.xml. 13 wwt frag orders are referenced by no model.
- **Sheets**
  - Folder `Mission sheets/fragmentary orders/` (`physical.xml` `<fragorders-mission-sheets-directory>`). fr sheets
    are in `Locales/fr/` (mildb only), named by the translation.
  - The structure is the same as unit sheets. A frag order without parameters has `<parameters/>`, but
    `mission_scaffold.sheet_xml` always writes the open form.
  - **`render_sheet_html` is byte-exact on 99 + 99 fr + 69** frag-order sheets. The fr image dir uses the **English**
    name (`./../../Images/<English name>/`).
- **`phy/Locales/fr/Missions_fr.ts`**
  - Context `frag-order`: all 99 names, ordinal-sorted.
  - One context `frag-order - <name>` per frag order with parameters. It holds the parameter names and the enum
    value names, **deduplicated** and ordinal-sorted. The contexts are contiguous right after `frag-order`.
  - A new category also needs a `mission-categories` message. The existing `ts_*` helpers fit unchanged.
- **Other files:** none to write. `mappings/hla.xml` holds only the fixed pause / resume / cancel names; Reports.xml
  does not list frag orders. Stages.xml has no frag-order **names**, but it holds **frago task stage labels** (e.g.
  "destroying the target"). checkstages covers every dia5 label, so a new frago task needs Stages.xml (both DBs) +
  `Stages_fr.ts`, like a mission task (corrected at step 1).
- **Checks**
  - **Fatal:** duplicate ids within a DB (`src/models/phydb.go`; it never reads `<task-groups>`), and en sheet pairs
    and name ↔ sheet matching (`checkdec sheets`).
  - **Nothing checks:**
    - Models.xml frag-order names (the sim does, at load);
    - `.ts` entries, fr sheets, attribute order, category;
    - whether a dia-type is handled (no initializer and no task);
    - cross-DB consistency.

### Sim side (sw.sword)
- **Load**
  - `MIL_FragOrderType.cpp` reads `id`, `name`, `dia-type`, `available-without-mission` (default false) and
    `<parameter>`s.
  - Duplicates use **frag-order-only maps**:
    - id → throws;
    - name → throws;
    - **dia-type → warning only, the last one wins** (and lookups go by dia-type in `FindByDiaType`).
  - `available-for-all-mission` is read but dead.
  - Parameter types use the same registry, and the same `functorsBM` converters. Frag orders always use the BM
    visitor (`DEC_Decision.cpp` ~1455). A type without a converter is skipped.
  - The `FragOrder` parameter type has no converter.
- **Models.xml loading:** an unknown frag-order name throws "Unknown orderConduite type" (`DEC_Model_ABC.cpp` 80–98).
- **Order-time check, identical for pion, automat, crowd and task group**
  (`MIL_PionOrderManager.cpp` 106–110 and the 3 siblings):
  - not `available-without-mission` and (no current mission, or not `IsFragOrderAvailable`) → refused;
  - availability = model-level `<fragorders>` **or** the current mission's `<fragorder>` list (`DEC_Model_ABC.cpp` 105–113);
  - `available-without-mission=true` skips Models.xml **entirely** at order time;
  - the Lua-side `DEC_IsFragOrderAvailable`, used by the commander propagation filter, **does** require the
    model-level list (`DEC_OrdersFunctions.cpp` 399–403).
- **Delivery**
  - Unit, automat and crowd: `AddToOrdersCategory` (a plain vector, `DEC_Representations`). Lua polls it with
    `_DEC_GetOrdersCategory`.
  - Removal: Lua removes the order, or `ClearAllCategories` runs on brain reload. **A new mission does not clear it.**
  - Task groups get the order pushed (`StartFragOrderBehavior`).
  - Report: only `eRC_FragOrderReceived` on receipt (`MIL_FragOrder.cpp` 633–657). There are no start / end reports.
    `CRTaskListener` reports only for `myself.currentMission`, but `willStart` / `willStop` still overwrite
    `meKnowledge.currentTask` and run `Cleanup` (resets `myself.blocked`, `safetyAttitude`).
- **Kernel start path**
  - `integration.startFragOrder` = `DEC_StartFragOrder` (`int/CommunicationIntegration.lua:326`), then
    `StartFragOrderBehavior` (`DEC_Decision.h` 666–675), then `StartTask(dia-type)`, which runs `spark.start`.
  - Parameters bind by `dia-name` = task parameter name; Spark copies only declared names.
- **Lifetime of a started task:** a mission stop runs `integration.stopTasks` → `spark.stopAll()`, which also stops
  frago tasks (`int/CRTaskListener.lua` 540–546).

### Decisional side (dia5)
- **`dia5/FragOrder.lua` is the only registry**, `include`d from `dia5/Decisional.lua`. It holds:
  - `fragOrdersToBePropagated` (67 names);
  - `executeAutomatFragOrder` (20 automat branches, then `integration.startFragOrder` if a behavior exists);
  - `fragOrderTaskInitializers` (141 keys; 2 use a named function, `ChangeAttitude` returns a computed task);
  - `extensionsFragosInit` (merged, the base wins).
- **`FragOrder.lua` format** (step 1):
  - **CRLF**, UTF-8, no BOM, 4-space indent.
  - Initializers are **grouped by theme**, neither sorted nor appended.
  - The propagation list is unsorted, with no trailing comma on the last entry.

  Details are in `_fragorder_notes.md` §1.2 Q1.
- **House style: kernel path (c) first.** `6db5a91523` (a review fix) removed the initializers and propagation
  entries that `39dacf24df` had added: dia-type = the task name and dia-names = the task parameter names suffice. Use an
  initializer only to convert or validate, stop the opposite task, or pick the task at run time.
- **Every brain is `dia5/BMAgent.bms`** (a Spark brain over all of dia5), so an automat brain knows unit tasks. The
  `executeAutomatFragOrder` fallback therefore starts a unit task **on the commander** when the order is not
  propagated.
- ✓ **`fragOrder.startFragOrderTask`** (`dia5/FragOrder.lua` 1311–1333):
  - **No initializer** → `integration.startFragOrder` + clean.
  - **Initializer:** `init(mission, fo)` returns `true[, taskName]` → clean, then `spark.getBehavior(...)`
    (`error` if missing), then `behavior:start(mission)`. Any other return starts nothing.
- **Polling (units):** the role's default task runs `Default`, which activates `common.agent.behaviors.skills.ManageFragOrder` on
  `getFragOrders()`. That merges the kernel queue with `myself.fragOrders` (orders from the commander, Lua only), then
  each order runs `fo:startTask()` → `startFragOrderTask`.
  - Military roles: `dia5/military/agent/roles/Agent.xml`, `dia5/military/agent/skills/Default.lua` 111–114.
  - Navy, security, crowd and test roles wire it the same way.
- **Cleaning is deferred:** `integration.cleanFragOrder` queues the order, and `OnEndTick` removes it
  (`int/CRTaskListener.lua` 216–229). Within the tick, other skills still see it.
  - `int/ToolsQueries.lua` `getFirstFragOrderFromType` removes **immediately**.
- **Kinds** (mildb / wwt counts):

  | Kind | mildb | wwt | Example |
  |---|---|---|---|
  | (a) initializer starts a task | 24 | 19 (+5 whose task does not exist) | `PauseMovement` → `common.agent.tasks.frago.WaitThere` |
  | (c) no initializer, kernel starts task = dia-type | 14 | 0 (+6 not handled anywhere) | navy `SetSpeedLimit` |
  | (b) empty / clean-only initializer, read by skills of the running mission | 15 | 11 (+4 read by nothing) | `Infiltrate` |
  | (f) immediate effect in the initializer, then clean | 34 | 20 | `SetSpeedLimit` |
  | (d) automat / commander only | 3 | 1 | — |
  | (e) crowd | 2 | 3 | `CrowdWait` → `crowd.behaviors.tasks.frago.Wait` |
  | (c2) task group | 7 | 0 | out of scope |

  - Kind (b) **needs an empty initializer**: without one, the kernel tries to start a task named by the dia-type.
  - Reader APIs: `getFirstFragOrderFromType`, `meKnowledge:getFragOrder`, the `get*FragOrder` queries,
    `PhaseLine:needToWaitForCoordination`, and the poll helpers inside `FragOrder.lua` itself. The table is in
    `_fragorder_notes.md` §1.2 Q5.
- **Frago tasks**
  - 34 tasks: `common/agent/tasks/frago/` 18, `military/agent/tasks/frago/` 11, `security/agent/behaviors/frago/` 4,
    `crowd/behaviors/tasks/frago/` 1. Frag orders also start navy and other tasks.
  - They have 1 stage (2 for GetDecontaminated), no Lua and no metadata class.
  - 23 are `disable-when-done`; the others are stopped by the opposite order (`stopTask`) or by a mission stop.
  - They run **beside** the mission and win shared resources by **priority** (1e2–1e6, e.g. `WaitThere` → `actions.Wait` at 1e5 on
    `motion`).
- **Automats**
  - `dia5/military/commander/skills/ManageFragOrder.lua` handles ROE, then `executeAutomatFragOrder`, then
    CancelMission, then propagation via `SendFragOrder`, filtered by availability and echelon, plus `Realign`.
  - `dia5/security/commander/behaviors/skills/ManageFragOrder.lua` is similar.
  - Subordinates receive orders through a `StartTask` or `FragOrder` message (`dia5/_scripts/MessageTreatments.lua`).
- **Crowds:** the crowd role maps the type to `common.agent.world.FragOrder` (`dia5/crowd/behaviors/roles/Crowd.lua:12`).
  `CrowdWait` / `CrowdResume` / `CrowdCancelMission` are handled by initializers.
- **Other hard-coded dia-type strings** (a rename / impact scan must cover them):
  - the commander `ManageFragOrder` skill;
  - `PhaseLine.lua`;
  - the `get*FragOrder` queries;
  - the `ManageDiving.xml` `<string>` defaults;
  - the `sendFragOrderFromType` callers;
  - `getNewNRBCProtectionFragOrder.lua`.
- **Anomalies to lint** (seed set):
  - wwt `SafetyPauseMovement` / `SafetyResumeMovement` are handled by nothing.
  - The security Commander's `Realign` has no task.
  - wwt VSRAM initializers start missing tasks.
  - 49 initializers serve dia-types that are in neither DB.
  - Empty initializers that nothing reads (the `GoTo*Area` ones, `SupportWithDirectFire`, `LendEquipment`) are never
    cleaned.
  - 26 propagation names match no dia-type of either DB (corrected at step 1). Most are client names (`france.*`,
    `bangladesh.*`) or legacy names. The defects are the misspellings of an existing dia-type: `GoTo`,
    `Rep_OrderConduite_MettreTenueNBC`, `ActivateNBCProtection`, `ProvideIndirectFire`.
  - Some frago tasks are never started.
  - `SafetyDismount` mounts the unit.
  - Bugs in `getFragOrders.lua:10` (`resSize`) and `integration.isTask = nil`.

### Checkpoints
- **The sim saves nothing about frag orders.** The checkpoint files (`sim/CheckPoints/MIL_CheckPointManager.cpp`
  248–257) do not include:
  - the orders queue, which has no serialization;
  - the frag-order action records (`ActionManager` saves only `orders_`);
  - frag orders in `MissionController::WriteMissions` / `ReadMissions`.
- **Correction for `CHECKPOINTS.md` §1:** crowd and task-group missions are also saved and resumed
  (`sim/MissionController.cpp` 128–183; the file is at the root of `sim/`).
- **Queued, not consumed** → lost. No report and no ack is sent again.
- **Frago task running** → the Spark store keeps `behaviors[<task>].seqStageIndex` (saved in the `state` string), but
  the list of active behaviors is not saved.
  - ✓ Nothing calls `spark.resume` except `ResumeEvent` (`int/SimulationIntegration.lua:472`), which runs only for the
    saved mission.
  - So **the frago task is not restarted**, and its saved stage is orphaned until a new start of that name.
  - ✓ `sparkle/Behavior.lua` `Behavior.start` clears the persisted state for that name. `stop` clears it only when
    the task was active.
- **Survives:** effects already in the ODB (unit and crowd ROE, `DEC_RolePion_Decision.cpp` 70–94) and world state.
- **Lost:** `myself.fragOrders` (orders from the commander), `myself.fragorderToClean`, `myself.ROEGivenByFragOrder`,
  `myself.blocked`…, and derived values a reading skill kept only in `self` (`MoveAlong.lua` Infiltrate → `self.pathType`).
  - Safe pattern: persist the derived value (`dia5/navy/agent/skills/ManageDiving.lua` `targetDepth`,
    `dia5/navy/agent/skills/Transit.lua` `maxSpeedOnWater`).
- **Ids can repeat after resume:** `MissionController::counter_` is rebuilt as the max saved mission id + 1.
- **`FragOrder` classes have no `hash`** (`dia5/common/agent/world/FragOrder.lua`, `dia5/crowd/world/classes/FragOrder.lua`).
  A skill with a FragOrder parameter that calls `getSkillState` errors. No frago skill uses skill state today.
- **Simtests:**
  - `sw.sword/src/masa/sword/simtests/fragorder_test.go` covers automat, crowd and unit (the crowd one is refused
    without a mission).
  - `dec_test.go` `TestFragOrderParameters`.
  - **No test checkpoints with a frag order active.** The global `Cfg.Checkpoint` mode compares client models only.

## Working rules (apply to every step)
- **On start or resume:**
  - Run `git -C C:/MASARepo/forge status --short`, read **Decisions** and **Progress log**, and continue at the first
    unchecked step.
  - An interrupted step (outputs half-written, nothing committed) is redone from the beginning. Every step is safe to
    repeat.
- **Branches:** record the sw.models / sw.sword / directai branch and commit read at each step. Never change git
  state in the sibling repos.
- **Build and validation writes:** tools and validation runs write only to a scratch copy of the model dir (`--root`),
  never into sw.models.
- **On finishing a step:**
  1. Tick its box.
  2. Add 1–3 lines to the **Progress log**.
  3. Commit **only the paths touched**: `fragorder builder: step N <title>`.
- **Findings go to files**, never only in the conversation. Working notes: `docs/sw.models/_fragorder_notes.md`
  (deleted in the last step).
- **Questions outside sw.models:** precise questions to `sw-sword-navigator` (sim, integration) / `dai-navigator`
  (Spark), in parallel when independent.
- **Validation runs:** a general-purpose agent told to read and follow the agent file (agent files load at session
  start). After editing an agent file, tell the user to restart Claude Code.
- **`python -I tools/check_themes.py`** after every doc or agent step: it must pass. Register new kit files in it.
- **Lessons carried over from the mission builder:**
  - **Measure formats over every file**: insertion order, attribute order, line endings, `.ts` order. Reproduce ADN
    output byte-exact and reuse `mission_scaffold.render_sheet_html` (`.html` lines end `\r\r\n`).
  - **Python on Windows writes CRLF in text mode**: write with `newline=""` or in binary. Measure
    `FragOrder.lua`'s own line endings before inserting.
  - **Bash heredocs** mangle backslashes and stop at a line equal to the delimiter: put edit scripts in files (in the
    scratchpad).
  - **Duplicates:** distinguish exact from near duplicates. Search by dia-type, by name in **both** DBs (names
    differ), by the task the order starts and by the skill that task runs.
  - **Before calling a checkpoint hazard infrastructural**, read the `integration.*` starter: world state is saved and
    some starters recover from it.
  - **Budget a fix pass in validation:** in the mission builder, validation found most of the defects.

## Steps

### Phase 0 — handoff
- [x] **0. Handoff file.** This file. Commit it with `docs/handoffs/fragorder-builder-planning.md`:
  `fragorder builder: step 0 handoff`.

### Phase A — shared knowledge
- [x] **1. Verify the Context, settle the open questions** (read-only; answers in `_fragorder_notes.md` §1, and in this
  file's Context when a fact changes):
  - Re-verify every unmarked Context fact. Correct or drop the ones that do not hold.
  - **`FragOrder.lua` format:**
    - line endings and indentation;
    - where a new initializer goes (grouped by theme or comment? appended before the closing `}`?);
    - the order and format of `fragOrdersToBePropagated`;
    - the shape of an `executeAutomatFragOrder` branch;
    - the parameter getters an initializer uses per type (`integration.get<Type>Parameter` vs `fo:get…`, and
      `CreateKnowledge` wrapping).
  - **Binding per kind:**
    - kind (c): kernel `InitTaskParameter` by dia-name;
    - kind (a): the hand-built `mission` table, whose getters return nil on error (`pcall`);
    - what an absent optional parameter gives in each case.
  - **Automat:**
    - which orders the commander propagates, and the echelon filter;
    - what the subordinate model must list for `DEC_IsFragOrderAvailable` to pass;
    - the `StartTask` / `FragOrder` messages to subordinates;
    - automat Models.xml lists;
    - whether an automat-level task is ever started for a frag order.
  - **Crowd:** crowd models' lists; whether crowd orders need a mission (`fragorder_test.go`); the shape of the crowd
    frago task.
  - **A reader table for kind (b):** dia-type → reader API → reading skill.
  - **Tick order:** where `SaveCheckPoint` runs relative to `OnEndTick` (whether a cleaned order can be saved half-done).
  - **Id convention:** do frag orders follow the scaffold's "max over both DBs + 1, same in both"?

  *Done when:* each question has an answer with a file:line, or is marked "unknown, ask the user".
- [x] **2. `docs/sw.models/FRAGORDERS.md`.**
  - Anatomy table: file → element → rule → `path::anchor`, per DB. It covers:
    - Missions.xml `<fragorders>` and Models.xml model-level / mission-level lists for unit, automat and crowd models;
    - sheets (en + fr), `.ts` contexts, `FragOrder.lua` (initializer, propagation, automat branch), frago task folders.
  - **Kinds**, as a decision table: what the designer wants → the kind → what to write → example → how it ends → its
    checkpoint class.
  - One-line anchored rules on:
    - availability (kernel vs `DEC_IsFragOrderAvailable`);
    - no context block; parameters and converters (link to `MISSIONS.md` §2);
    - polling and deferred cleaning;
    - frago task priority and resources, ending (`disable-when-done` / opposite order / mission stop), and the
      `currentTask` / `Cleanup` side effects;
    - automat propagation; crowds; reports (`eRC_FragOrderReceived` only); dia-type duplicates (last one wins).
  - Checks table: what each ant target and the sim load catch, and what nothing catches.
  - Build order.
  - Add to the sw.models kit in `tools/check_themes.py`. Update the THEMES sw.models "Frag orders" row to point to it.
  - The checker passes.
- [x] **3. `CHECKPOINTS.md`: frag-order rows.**
  - **§1:**
    - add the queue (lost), the frago task (not resumed, orphan `seqStageIndex`), `myself.fragOrders` /
      `fragorderToClean` (lost), ODB effects (saved) and the `counter_` id reuse;
    - correct the "missions saved" row (crowd and task-group missions are saved too).
  - **§2:** a note that only the saved mission is resumed.
  - **§3 hazards:**
    - a reading skill keeping a consumed order's effect only in `self`;
    - a FragOrder parameter with `getSkillState` (no `hash`);
    - an orphan stage reused by a later `spark.resume`.
  - **§4:** the "persist the derived value" convention, with `ManageDiving` / `Transit`.
  - **§6 gap rows:** queue not serialized; frago tasks not resumed; `myself.fragOrders` not persisted; ids reused.
  - The checker passes.
- [x] **4. Reference fix pair** (`CHECKPOINTS.md` §7, "Frag orders across a save + resume"). Design once; the builder
  cites it:
  - **Sim-side:** a unified diff design, **proposed only**: persist active frag orders (type + parameters) and resume
    their tasks, e.g.
    - serialize the orders queue, **or**
    - record the frag orders started via `StartFragOrderBehavior` in `MissionController` and replay them with a
      `resume` flag through a `spark.resume` path.

    Choose with `sw-sword-navigator`, then measure the blast radius:
    - `integration_index.md` rows;
    - model callers;
    - sw.soult;
    - client branches;
    - simtests to extend (`fragorder_test.go` + `checkpointAndRestart`);
    - the version-freeze rule.
  - **Lua-side:** a dia5 helper design, e.g.:
    - `startFragOrderTask` / a `willStart` listener records `{task, plain params}` in the Default skill's state
      (ids, points, numbers only);
    - `willStop` clears it;
    - Default `create` calls `spark.resume` on a non-empty record.

    State its limits: parameters that cannot become plain data, kind (c) userdata parameters, orders from the
    commander.
  - The recommendation and the reason.

  *Done when:* both designs are anchored, with `dai-navigator` confirmation of the `spark.resume` / `Behavior.start`
  semantics they rely on. The checker passes.
- [x] **5. Cross-links.**
  - CLAUDE.md pipeline row "Frag order: anatomy / build / check" pointing to `FRAGORDERS.md`, `CHECKPOINTS.md` §7 and
    (later) the tools.
  - sw-models-navigator: frag-order question shortcut, and the rename/impact checklist covers the hard-coded
    dia-type list.
  - One line in sw-sword-navigator and dai-navigator.
  - `sw-mission-builder`: "frag order → sw-fragorder-builder" in its description and hard rule.
  - sw.sword THEMES "Frag orders" row: the checkpoint gotcha (done at step 4).
  - The checker passes. Tell the user to restart.

### Phase B — tools
- [x] **6. `missionlib.py` + `mission_check.py` frag-order checks.**
  - **Parse:**
    - frag orders per DB (raw tags, lines, params);
    - Models.xml lists per section (unit, automat, crowd; model-level and mission-level);
    - the `FragOrder.lua` registry: move and extend `scan_fragorder_lua` (initializer bodies, the returned task, the
      dia-names they read, propagation, automat branches);
    - dia-type readers across dia5 Lua / XML.
  - **Checks:**
    - id / name unique across sections;
    - dia-type unique among frag orders (the sim keeps the last one);
    - attribute order; category (mildb); no context dia-names; types known + converter, list form included.
    - **Kind resolution:**
      - initializer → its task exists, and the dia-names it reads exist in this DB;
      - no initializer → a task named by the dia-type exists, with dia-names ↔ task params as for missions;
      - empty initializer → at least one reader, else "never read, never cleaned";
      - none of these → "handled by nothing".
    - Frago task: `disable-when-done` or an opposite-order stopper, else "never ends".
    - **Availability:**
      - `available-without-mission=false` → listed by at least 1 model (model-level or under a mission);
      - referenced by at least 1 model per DB;
      - a propagated order is listed in the subordinate models (the `DEC_IsFragOrderAvailable` filter);
      - automat-handled → automat models; crowd → crowd models.
    - Propagation names match a dia-type.
    - `.ts` `frag-order` + `frag-order - <name>` (parameter and value names, deduplicated); en / fr sheet pairs.
    - Cross-DB notes: same name, other id / dia-type / params.
  - **CLI:**
    - `--fragorder NAME`, `--all-fragorders`; `--all` includes both;
    - `--db`, `--root`;
    - exit 1 on errors.
  - Triage `--all-fragorders` until there are no false positives. The seed anomalies in Context must appear;
    "Pause movement" must be clean.
- [ ] **7. `checkpoint_lint.py --fragorder NAME`.**
  - Resolve the order's task through the step 6 parser and lint its closure.
  - For kind (b), lint the reading skills.
  - **New rules** (warnings are upper bounds, with file:line):
    - `order-effect-not-persisted`: a skill reads a frag order (reader APIs) and keeps the result only in `self.*`;
    - `fragorder-param-state`: `getSkillState` in a skill with a FragOrder parameter;
    - `frago-task-lost` (a note): every task-starting order. It cites `CHECKPOINTS.md` §7.
  - Validate: `MoveAlong` (flags Infiltrate), `ManageDiving` / `Transit` (clean on the derived value), Pause movement
    (`frago-task-lost` only).
- [ ] **8. `mission_index.md` §6 Frag orders** (`build_models_index.py --only missions`).
  - One row per frag order per DB: name, dia-type, id, without-mission, **kind**, initializer → task, params
    (dia-name:type), models (unit / automat / crowd, model-level / under missions), propagated, automat branch,
    readers, checkpoint class, `mission_check` status.
  - Frag-order anomalies go into §5.
  - In `decisional_index.md` §7, fix the "Starts" column wording: rows without an initializer show the kernel path.
  - Update `docs/sw.models/README.md`.
  - Rebuild; `kits.py` shows it fresh.
- [ ] **9. `mission_scaffold.py` frag-order mode** (`"fragorder": true` in the spec).
  - **Missions.xml:** append to `<fragorders>`, with no context block and the next id. Parameters gain
    `<transmitters>` / `<equipments>` passthrough.
  - **Models.xml**, per DB:
    - `models_level` {db: [unit / automat / crowd model names]} appended to model-level `<fragorders>` (expand
      `<fragorders/>` when empty);
    - `missions` {db: {model: [mission names]}} appended under the mission (expand a self-closed `<mission/>`).
  - **Sheets:** `fragmentary orders/` en + fr, with `<parameters/>` when there are none and the fr imgdir set to the
    English name.
  - **`.ts`:** `frag-order` (sorted) + `frag-order - <name>` (deduplicated, sorted), and `mission-categories` for a
    new category.
  - **`FragOrder.lua`**, by `kind`:
    - `task` → an initializer reading the dia-names into `mission.<param>` and returning `true, "<task>"`;
    - `kernel` → no entry (task = dia-type);
    - `read` → an empty initializer plus a TODO reader;
    - `immediate` → an initializer stub with `cleanFragOrder`.

    Plus `propagate: true` → the propagation list, and `automat_branch` → a TODO branch skeleton.
  - **Task skeleton:** in the right frago folder, one high-priority behavior, `disable-when-done` by default.
  - Refuse on: duplicate name / dia-type / id; an unknown model / mission / category / type; missing translations.
  - Validate:
    - `--check-html` covers the frag-order sheets (99 + 99 + 69 byte-exact);
    - on a scratch copy, scaffold specs for kinds task, read and kernel → `mission_check --fragorder` clean,
      `--all-fragorders` unchanged otherwise;
    - `FragOrder.lua` still loads (a Lua syntax check with the directai Lua if one is available, else a balanced-block
      check).

### Phase C — agent and skills
- [ ] **10. `.claude/agents/sw-fragorder-builder.md` + `.claude/skills/fragorder/SKILL.md`** (`/fragorder`,
  `context: fork`). Tools: Read, Grep, Glob, Bash, Edit, Write; model inherit.
  - **Description** in the navigators' style:
    - unit, automat and crowd frag orders;
    - NOT task-group frag orders, unit / automat missions (→ sw-mission-builder), or sim semantics (→ navigators).
  - **Procedure:**
    1. **Context check**: branch, commit, dirty state, version.major. On master, stop.
    2. **Clarify**: who receives it (unit / automat / crowd, which models); with or without a mission (which
       missions); parameters; the effect (immediate, a task beside the mission, or a change to how running skills
       behave); how it ends (on its own, an opposite order, a mission stop); propagation; French names.
    3. **Critique the request** (see Decisions): duplicate across both DBs and under other names; handled by
       nothing; never ends; never available; parameter never read; the automat propagation filter.
    4. **Choose the kind** from the `FRAGORDERS.md` decision table.
    5. **Reuse scan** (`mission_index.md` §6, §3–§4): lint the reused task / readers; report their defects.
    6. **Design**: initializer, task priority over shared resources, the opposite order, the automat branch /
       propagation.
    7. **Checkpoint design**: classify the order (`CHECKPOINTS.md` §7). For a task order, give the reference fix
       pair adapted to this order (the sim-side diff with a measured radius and the version freeze; the Lua-side
       helper) and recommend one. Readers persist derived values.
    8. **Missing entities**, checkpoint-safe.
    9. **Write**: the scaffold (`--write`), then Edit for the task / skills / initializer body.
    10. **Lint**: `mission_check --fragorder`, `checkpoint_lint --fragorder`.
    11. **Report**: Answer, Changes, Checks (the ant targets; the test recipe: give the order, save, resume,
        observe), sw.sword proposal, **Critique**, Evidence / Caveats / Map updates.
  - **Hard rules:** as for sw-mission-builder, plus: `FragOrder.lua` may be edited by inserting entries only (never
    reorder or reformat it).
  - **`/mission-check`:** a frag-order name or dia-type → `mission_check --fragorder` + `checkpoint_lint --fragorder`,
    explained from `FRAGORDERS.md` / `CHECKPOINTS.md`.
  - Add to the checker. Tell the user to restart.
- [ ] **11. Dry review of the agent file** by a general-purpose agent: it reads the file and the kit only, and lists
  contradictions, missing anchors and steps the tools cannot do. Fix them. (Cheap; it catches prompt gaps before
  validation.)
- [ ] **12. Validation + fix pass.** Run each scenario with a general-purpose agent that reads and follows the agent
  file, on a scratch copy (`--root`). Record PASS / FAIL in the notes §12, then fix the kit, tools and prompt and rerun
  the failures.
  - (a) **Unit, kind (a), reuse:** a new frag order that reuses an existing frago task with another parameter set
    (e.g. "go to a point and wait N minutes" on top of `GoToAndWait`). Expected: a near-duplicate is flagged; the
    initializer + physical files are written; lint-clean; the checkpoint class is "task lost", citing §7.
  - (b) **Unit, new task + stateful skill:** a new frago task with a new skill that keeps state, checkpoint-safe and
    lint-clean. Expected: a recommended sim-side vs Lua-side fix with a measured radius.
  - (c) **Mission-specific, kind (b):** an order available only under given missions and read by a skill of those
    missions (e.g. "speed up" read by a movement skill). Expected: an empty initializer, the reader persists the
    derived value, and Models.xml lists the order under those missions only.
  - (d) **Automat:** a frag order given to a company and propagated to its platoons. Expected: the propagation entry
    or automat branch; the subordinate models list it (the availability filter); the commander filters are checked.
  - (e) **Crowd:** a crowd frag order with a crowd frago task. Expected: crowd models, needs a mission, task in
    `crowd/behaviors/tasks/frago/`.
  - (f) **Duplicate:** "add a frag order that pauses a unit's movement". Expected: says Pause movement exists (both
    DBs, the WaitThere task) first; nothing is built.
  - (g) **Kit only, without the builder:** a general-purpose agent following sw-models-navigator answers "what
    happens when 'Use infiltration path' reaches an infantry platoon during a move mission" and "does 'Pause movement'
    survive a checkpoint?" from the kit.
- [ ] **13. Close.**
  - CLAUDE.md: a builder bullet (`sw-fragorder-builder`, `/fragorder`, `/mission-check` covers frag orders) and the
    tools in the pipeline row.
  - Delete `_fragorder_notes.md` and mark this file **DONE**.
  - Update the memory pointer (forge workspace purpose: the frag-order builder).
  - The checker passes.

## Progress log
- 2026-11-05 step 0: handoff written from the approved plan (planning exploration of the physical files, dia5, the
  sim side and checkpoints; facts in **Context**, ✓ = read directly in planning).
- 2026-11-05 step 1: Context re-verified (`_fragorder_notes.md` §1.1; counts by script, code at the cited lines).
  - **Corrected:** 92 cross-DB ids; frago task labels must be in Stages.xml; `sim/MissionController.cpp` path;
    26 dead propagation names.
  - **New:** `FragOrder.lua` is CRLF and grouped by theme; house style = kernel path (c) first (`6db5a91523`); all
    brains are `BMAgent.bms`, so a non-propagated unit task can start on the commander; the checkpoint is written after
    `OnEndTick`; ids follow the max + 1 rule.
- 2026-11-05 step 2: `FRAGORDERS.md` (anatomy, a kinds decision table with a survives-checkpoint column, delivery,
  binding, frago tasks, automats / crowds, readers, checks, build order); registered in the checker; THEMES "Frag
  orders" row points to it. New facts:
  - an initializer that returns without cleaning reruns every tick;
  - any task start / stop (frago included) resets `meKnowledge.currentTask`, and stop / finish runs `Cleanup`;
  - the precedent's frago priorities were forgotten and fixed later (`d87f8b86d9`).
- 2026-11-05 step 3: `CHECKPOINTS.md` frag-order rows:
  - §1: + queue lost, frago task not resumed (orphan stage), Lua-only commander orders and `myself` effects lost,
    id counter rebuilt; ROE in the ODB survives; crowd and task-group missions saved too (correction);
  - §2: hop 8;
  - §3: 4 new hazards (frag order lost, reader effect in `self`, initializer effect in `myself`, orphan stage) + 2
    rows widened;
  - §4: the reader convention (`ManageDiving` / `Transit`); §5: item 6; §6: 4 gap rows;
  - a §7 stub that step 4 fills.
- 2026-11-05 step 4: `CHECKPOINTS.md` §7, the reference fix pair (dai-navigator and sw-sword-navigator consulted):
  - **Sim-side (B)**: record kernel-started frag orders (incl. task groups) in `MissionController`, write them like
    missions (protobuf), and resume them through a new `ResumeTask` → `spark.resume` after `controller_.Start`
    (early-return path too). Mission resume never calls `StopEvents`; the version is 6.29.0 on both master branches,
    so it can land on master.
  - **Lua-side**: plain-parameter records under the fixed key `getPersistentState( "fragOrder", "tasks" )`, written
    where the model starts the task, cleared on `willStop`, resumed once per brain from `getFragOrders`.
  - **Recommendation per kind**: (a) / (e) Lua-side; (c) with parameters sim-side; (b) the reader persists.
  - New Spark facts: `seqStageIndex` = last completed stage; no API for a running task's parameters; call `resume`
    once.
  - Map updates applied: 2 directai THEMES gotchas, 2 sw.sword THEMES gotchas.
- 2026-11-06 step 5: cross-links: CLAUDE.md pipeline row "Frag order: anatomy / kinds / build / check";
  sw-models-navigator (FRAGORDERS.md bullet, frag-order shortcut step 10, dia-type strings in the rename scan);
  one line in sw-sword-navigator and dai-navigator; sw-mission-builder routes frag orders to sw-fragorder-builder;
  sw.sword THEMES "Frag orders" row widened (the checkpoint gotcha was already added at step 4).
- 2026-11-06 step 6: `missionlib.py` parses frag orders, every Models.xml section, automat → subordinate models and
  `FragOrder.lua` (initializers incl. named / direct starts, propagation, automat branches, poll helpers), plus the
  dia5 string index (readers, stoppers); `scan_fragorder_lua` moved there. `mission_check.py --fragorder` /
  `--all-fragorders` (`--all` covers both). Triage in notes §6: all seeds reported; "Pause movement" keeps 2 true
  positives (automat models whose subordinates cannot receive it).
