# Frag-order builder: working notes (deleted at step 13)

Aliases as in `docs/handoffs/fragorder-builder.md` (`phy/ wwt/ dia5/ sim/ int/ spark/ sparkle/`).

## 1. Step 1: verified facts and open questions

Read at sw.models `master` @ `d2f78fafcd`, sw.sword `master` @ `51bdc0ec9e`, directai `master` @ `9629aaf1a1`, all clean.
Physical counts come from a stdlib script that parsed every entry; the code facts were read at the cited lines.

### 1.1 Context re-verification

**Confirmed as written:**
- **Missions.xml `<fragorders>`**
  - 99 / 69 entries; LF, no BOM. mildb lines 6938–7322, before `<categories>`; wwt has it as the last section.
  - Attributes are alphabetical on all 99 + 69 `<fragorder>` and 144 + 74 `<parameter>` tags. Two attribute sets in
    mildb (86 with `category`, 13 without); wwt has none with `category`; no unknown category.
  - `available-without-mission`: mildb 71 true / 28 false, wwt 43 / 26. With parameters: 61 / 34.
  - 0 context dia-names; no duplicate dia-type within a DB; ids decrease 5× (mildb) and 1× (wwt) in file order.
  - Max id: mildb 445949729, wwt 545949903, so the next id is **545949904**.
  - 38 shared names; 6 have a different id (Activate / Remove CBRN protection, Display fire info, Drop off captured
    unit, Lend / Retrieve lent equipment).
  - Parameter children: mildb `value` 29, `units` 6, `transmitters` 4, `objects` 3, `choice` 1, `equipments` 1.
- **Models.xml**
  - LF. Sections: units, automats, crowd, task-groups.
  - mildb models with a non-empty model-level list: 93/94 unit, 50/54 automat, 2/2 crowd, 2/2 task-group.
  - mildb missions with `<fragorder>` children: 1097 unit, 388 automat, 9 crowd, 1 task-group.
  - Indents: 8 for the model-level list, 10 under a mission.
  - Every name is known. wwt has 13 unreferenced frag orders.
- **Sheets:** 99 xml + 99 html (mildb), with fr `Locales/fr/` 99 + 99; 69 + 69 (wwt).
  `physical.xml`: `<fragorders-mission-sheets-directory path="Mission sheets/fragmentary orders"/>`.
- **`Missions_fr.ts`**
  - LF; 430 contexts, ordinal-sorted.
  - `frag-order` holds 99 messages, sorted, equal to the set of mildb names.
  - 61 `frag-order - <name>` contexts, one per frag order with parameters, contiguous.
- **Load** (`sim/Entities/Orders/MIL_FragOrderType.cpp:22-61`)
  - Frag-order-only maps. id → throw "Frag order already defined"; name → throw "Entity fragOrder name already
    defined"; dia-type → `MT_LOG_WARNING_MSG`, and the last one wins (`:47-50`).
  - `MIL_OrderType_ABC( nID, 0, xis )`: context length 0.
  - `available-for-all-mission` is read (`:59`) but only declared in `MIL_FragOrderType.h:29`, never defined or
    called: dead.
- **Models.xml load** (`sim/Decision/DEC_Model_ABC.cpp:80-98`): "Unknown orderConduite type", looked up by **name**,
  for both list levels. `IsFragOrderAvailableForMission` = model-level list **or** the mission's list (`:105-113`).
- **Order time**
  - Pion: `sim/Entities/Orders/MIL_PionOrderManager.cpp:102-120` (surrender, type, availability, queue, ack, `Send`).
  - Crowd: `MIL_PopulationOrderManager.cpp:58-72`, identical without the surrender check.
  - The Lua-side `DEC_IsFragOrderAvailable` (`sim/Decision/DEC_OrdersFunctions.cpp:395-411`) looks up by
    **dia-type**:
    - without-mission → `agent->IsFragOrderAvailable` (model-level list required);
    - else the current mission must allow it.
- **Parameter conversion:** `RegisterFragOrderParameters` always uses the BM visitor (`sim/Decision/DEC_Decision.cpp:1455-1460`).
  A type without a `functorsBM` entry is skipped; a functor returning false sets `NIL` (`:1425-1430`).
- **Kernel start:** `sim/Decision/DEC_Decision.h:666-675` `StartFragOrderBehavior` → `startTask_( dia-type, refFrago )`
  → `int/SimulationIntegration.lua:484-486` `StartTask` = `spark.start`.
  `int/CommunicationIntegration.lua:326` `integration.startFragOrder = DEC_StartFragOrder`.
- **Cleaning:** `int/FragOrderIntegration.lua:39-42` queues the order; `int/CRTaskListener.lua:216-229` `OnEndTick`
  removes it from `myself.fragOrders`, the queue and the representations. `integration.stopTasks` → `spark.stopAll()`
  (`int/CRTaskListener.lua:540-546`).
- **Reports:** `eRC_FragOrderReceived` is posted by the 4 `MIL_FragOrder::Send` overloads (`MIL_FragOrder.cpp`
  ~631-660). `CRTaskListener` `willStart` / `willStop` reset `meKnowledge.currentTask`; `Cleanup` resets
  `myself.blocked` and `myself.safetyAttitude`; stage reports go only to `myself.currentMission`.
- **Checkpoints**
  - `sim/CheckPoints/MIL_CheckPointManager.cpp:248-257` lists the files saved; none holds frag orders.
  - `DEC_Representations` has no Write / Read / serialize. `ClearAllCategories` is called only on brain reload
    (`MIL_AgentPion.cpp:1852`, `MIL_Automate.cpp:1605`, `MIL_Population.cpp:1682`).
  - `sim/MissionController.cpp:128-183` `ReadMissions` reads unit, crowd and task-group missions; `counter_` is
    rebuilt as the max id + 1.
  - Spark:
    - `spark/behavior.lua:20-29` keeps `activeBehaviors` in its own `agentState`;
    - `spark/persistence.lua:82-89` `saveState` serializes only the persistence `agentState`;
    - `sparkle/Behavior.lua:111-133`: `start` clears the persisted state; `stop` clears it only if the behavior was
      active;
    - `spark.resume` is called only from `int/SimulationIntegration.lua:472` (`startOrResume`, through
      `ResumeEvent`).
  - The ODB saves `roe` and `roe-crowd` (`DEC_RolePion_Decision.cpp:70-81`).
  - No `hash` in `dia5/common/agent/world/FragOrder.lua` or `dia5/crowd/world/classes/FragOrder.lua`;
    `int/SimulationIntegration.lua:768-777` errors.
- **dia5 polling**
  - `dia5/military/agent/skills/Default.lua:110-114`.
  - `dia5/common/agent/queries/getFragOrders.lua:1-18`. **Bug confirmed**: `resSize` stays 0 in the first loop, so
    the commander's orders overwrite user orders from index 1.
  - `dia5/common/agent/behaviors/skills/ManageFragOrder.lua:9-13` → `dia5/common/agent/world/FragOrder.lua:18-20`.
  - Crowd: `dia5/crowd/behaviors/roles/Crowd.xml:15` runs the same unit skill; `Crowd.lua:12` maps `fragOrder` to
    `common.agent.world.FragOrder`.
- **Frago tasks:** common 18 (12 dwd), military 11 (10), security 4 (1), crowd 1 (0): 34 tasks, 23
  `disable-when-done`.

**Corrected (the handoff's Context has been updated):**
- **Ids:** 92 ids name different entries in the two DBs, not "over 100".
- **Stages.xml:** frag-order **names** are not in it, but **frago task stage labels are**, in both DBs: e.g.
  "destroying the target" (`AirDestroyAircraft`), "waiting". checkstages covers every dia5 label, so a new frago task
  needs Stages.xml (both DBs) + `Stages_fr.ts`, exactly as a mission task does.
- **Path:** `MissionController.cpp` is at the root of `sim/` (`sim/MissionController.cpp`), not `sim/Entities/Orders/`.
- **Initializers:** 141 keys. 139 are inline `function( mission, fragOrder )`; 2 are a named function
  (`DetachUnit2/3 = detachUnit`, `dia5/FragOrder.lua` ~1170).
  - `ChangeAttitude` returns a **computed** task (`return true, orderType`), and must not be read as "starts a missing
    task".
- **Propagation:** **26** names in `fragOrdersToBePropagated` match no dia-type of either DB, not 4. Most are client
  (`france.*`, `bangladesh.*`) or legacy `Rep_OrderConduite_*` names that may exist on client branches. The real
  defects are those whose intended dia-type exists under another spelling: `GoTo` (→ `common.agent.tasks.frago.GoTo`),
  `Rep_OrderConduite_MettreTenueNBC` (initializer key `MettreTenueNBC`), `ActivateNBCProtection`,
  `ProvideIndirectFire`.
- **Kind (d):** `Realign` is read by the military commander skill directly
  (`dia5/military/commander/skills/ManageFragOrder.lua:91-101`), not by `executeAutomatFragOrder`. The security
  Commander (`dia5/security/commander/behaviors/skills/ManageFragOrder.lua`) calls `startTask` on it, which reaches
  the kernel path with no task: confirm in step 6 triage.
- **Kind counts:** re-derived by script (task existence, initializer body, readers incl. the `FragOrder.lua` poll
  helpers). They agree with the planning table:
  - mildb: (a) 24 incl. ChangeAttitude, (b) 15, (c) 14, (c2) 7, (d) 3 incl. Realign, (e) 2, (f) 34;
  - wwt: (a) 19 (+5 missing tasks), (b) 11 (+4 unread), (c!) 6 + Realign, (e) 3, (f) 20.

### 1.2 Answers to the open questions

**Q1 `FragOrder.lua` format.**
- The file is **CRLF**, UTF-8 (12 non-ASCII bytes, valid UTF-8), no BOM, 4-space indent, and ends with a newline.
  Read and write it in binary or with `newline=""`.
- **Initializers are grouped by theme, neither sorted nor appended.** The precedent inserted its entries next to
  related ones (`39dacf24df`: after the `ProvideIndirectFires` group, before `LendEquipment`).
  - Format: a blank line between entries, then `    [ "<dia-type>" ] = function( mission, fragOrder )` … `    end,`.
  - 3 keys have irregular spacing (`["GoToFireBaseArea" ]`, `[  "SafetyMount" ]`, `[  "SafetyDismount" ]`): the
    parser must accept `\[\s*"…"\s*\]`.
  - Scaffold rule: insert **after a given key** (spec `after`); by default, before the closing `}` of
    `fragOrderTaskInitializers` (line ~1299), with a blank line in front.
- **`fragOrdersToBePropagated`** (`dia5/FragOrder.lua:2-71`): 67 quoted names, one per line at 4 spaces, unsorted,
  some with `-- WW` / `-- BD` comments; the last entry has no trailing comma. Insert after a given entry or before the
  last line, adding the comma.
- **`executeAutomatFragOrder` branch shape** (`:82-231`): an `if/elseif orderType == "<dia-type>" then … return true`
  chain. Branches act directly (logistics, ROE) or forward with `integration.SendMessage( "StartTask", subordinates[i],
  { taskName, params } )` / `meKnowledge:sendDynamicMessage( "FragOrder", … )` and `integration.cleanFragOrder`.
  - The fallback (`:226-229`): not propagated and `spark.getBehavior( orderType )` → `integration.startFragOrder` on the
    automat itself.
- **Getters:** mostly `integration.get<Type>Parameter( fragOrder, "<dia-name>" )` (Integer 38, Point 26, AgentType 17,
  ResourceType 11, AgentKnowledgeType 10…), plus `fragOrder:get<Type>Parameter` (Liaison, Transmitter, Integer,
  String: the Trans* entries).
  - Objects are wrapped with `CreateKnowledge( integration.ontology.types.<t>, value )` (e.g. GoTo, `39dacf24df`
    Point / AgentKnowledge).
  - The scaffold needs a type → getter + ontology type table. Build it from the existing initializers in step 6.

**Q2 Binding per kind.**
- **Kind (c), kernel:** `RegisterFragOrderParameters` → the BM visitor by `dia-name`, then `spark.start(dia-type)`,
  which copies the declared names only. This is the **same rule as missions** (`MISSIONS.md` §2): an absent optional
  object → `NIL` (the task default is skipped); an absent scalar → nil → the task `<default>`.
- **Kind (a), initializer:** the `mission` table is built by hand. Getters return nil on any error
  (`int/FragOrderIntegration.lua:16-32` `pcall`); a nil field gets the task `<default>`. The Lua code decides the
  binding, so the check verifies that each dia-name an initializer reads exists in the DB.
- **House style:** `6db5a91523` ("remove useless code following NMI comments in PR") deleted the two initializers and
  propagation entries that `39dacf24df` had added. The orders kept dia-type = the task name and dia-names = the task
  parameter names, so the **kernel path (c) is the preferred wiring** for a task order. Add an initializer only to
  convert or validate parameters, stop the opposite task, or pick the task at run time.
  - Note: the kernel path has no `spark.getBehavior` guard; a missing task gives "unknown behavior" at order time.

**Q3 Automat.**
- The company default task runs `military.commander.skills.ManageFragOrder` on `getFragOrders`
  (`dia5/military/commander/roles/Company.xml:12-15`). Per order:
  1. ROE → `setCompanyROE`.
  2. `executeAutomatFragOrder`.
  3. CancelMission.
  4. If propagated: the `SendFragOrder` node per subordinate (`ManageFragOrder.lua:62-89`).
     - The echelon filter: `CrossDepartureLine` is not sent to scouts; `Rep_OrderConduite_AttendreSePoster` goes to
       the first echelon only. 4 types (radio silence, NBC) go to every entity of the automat.
     - `SendFragOrder` (`:150-158`) checks `fragOrder:isAvailableToAgent( entity )` → `DEC_IsFragOrderAvailable(
       subordinate, dia-type )` (Q1.1). So a propagated order must be in the **subordinate's model-level list**
       (without-mission) or allowed by the subordinate's current mission; otherwise it is silently not sent.
     - `meKnowledge:sendFragOrder` (`dia5/common/commander/world/MeKnowledge.lua:110-113`) sends a Lua "FragOrder"
       message (→ the subordinate's `myself.fragOrders`, `dia5/_scripts/MessageTreatments.lua:53-57`) and cleans the
       commander's order. The subordinate then runs the **unit** initializer.
  5. Not propagated: `Realign` handling.
- **An automat-level task can start.** Every brain is built from `dia5/BMAgent.bms` (`spark.createBrain` over all of
  dia5; Models.xml `file="BMAgent.bms"` for 150 models). So the `executeAutomatFragOrder` fallback starts a task named
  by the dia-type **in the commander's brain** whenever that task exists and the order is not propagated.
  - **Hazard:** a unit kind-(c) order given to a company (accepted when it is `available-without-mission`, or listed
    by the automat model) runs the unit task on the commander. The builder must either propagate it or keep it out of
    automat models.
- Automat Models.xml lists: 50/54 mildb automat models have a model-level list; 388 automat missions list frag
  orders.
- `MessageTreatments.lua:279-288` `StartTask` starts a named task on the subordinate if it is not already running.

**Q4 Crowd.**
- Crowd orders need a mission only when `available-without-mission="false"`, as for units: "Crowd - Pause movement"
  (`CrowdWait`) is false, hence `sw.sword/src/masa/sword/simtests/fragorder_test.go:77-79` refusing it before a
  mission.
- Crowd models (2 per DB) carry both list levels.
- The crowd frago task `dia5/crowd/behaviors/tasks/frago/Wait.xml`: 1 stage "waiting", one behavior at priority 1e6,
  no `disable-when-done`, stopped by `CrowdResume`'s initializer (`dia5/FragOrder.lua` ~363-366).

**Q5 Reader table, kind (b)** (dia-type → reader → file):

| Dia-type | Reader API | Reading code |
|---|---|---|
| Infiltrate | `fragOrder.getInfiltrateFragOrder` (`dia5/FragOrder.lua` poll helpers) via `common.agent.queries.getInfiltrateFragOrder` | MoveAlong, ReconnoiterRoute, ReconnoiterInOrder, MoveAndTakePosition |
| StopSafeguarding | `military.queries.getStopSafeguardingFragOrder` | AttackACrowd, SelfProtectFromEnies |
| Rep_OrderConduite_Pion_AppliquerFeux, Rep_OrderConduite_Pion_ApplyFireOnUnit | `getFireFragOrder` (FragOrder.lua) | DeployToProvideIndirectFires, SupportWithIndirectFires |
| Disengage (+ Rep_OrderConduite_Decrocher) | `getDisengageFragOrder` | Delay, Screen (commander missions) |
| CrossDepartureLine, ResumeMovement | `PhaseLine:needToWaitForCoordination` | `dia5/common/agent/world/PhaseLine.lua`, ReactToCoordinationLine; commander echelon filter |
| ContinueMission | `int/ToolsQueries.lua` `getFirstFragOrderFromType` | `military/commander/skills/WaitOrderToContinue.lua` |
| GoToNextObjective | `common.agent.queries.getGoToNextObjectiveFragOrder` | 10 engineer skills, e.g. `primitives/Build.lua` |
| Rep_OrderConduite_PoursuivreConvoi, SafetyChangeInterventionDuration | `getFirstFragOrderFromType` | `common/agent/world/MeKnowledge.lua` |
| ReachRecipient | `getFirstFragOrderFromType` | `military/agent/skills/Supply.lua` |
| Rep_OrderConduite_MiseAFeuIED, ChangeRoute | `meKnowledge:getFragOrder` | EmplaceAnIED, MoveDroneAlongRouteAndGoBackToSender |
| Dive, Resurface | `meKnowledge:getFragOrder`, dia-type in `<string>` defaults | `navy/agent/skills/ManageDiving.xml` / `.lua` |

Readers to detect in step 6: a quoted dia-type in any dia5 `.lua` / `.xml` other than the initializer itself,
**including the poll helpers in `FragOrder.lua`** (`:1335-1414`), and `>dia-type<` in XML `<string>` values.

**Q6 Tick order.** `sim/MIL_AgentServer.cpp:341-377` `MainSimLoop`: `pEntityManager_->Update()` (all decisions,
including each brain's Spark end-tick callbacks, `spark/core.lua:179-184`, where `OnEndTick` cleans) runs before
`pCheckPointManager_->Update()` (the last statement). So a checkpoint never catches a half-cleaned order. The point is
moot anyway, since the queue is not saved.

**Q7 Id convention.** It works as for missions: max id over every section of both DBs + 1, the same id in each DB
(next: 545949904). The sim checks ids only among frag orders; `src/models/phydb.go` checks them across all sections
within a DB. Cross-DB id consistency is already broken (92 ids), so the tool cannot enforce it, only avoid adding more.

### 1.3 New items for later steps
- **Step 2:**
  - The kinds decision table must state the house style (kernel path first).
  - The "frago task labels → Stages.xml" rule.
  - The automat hazard: a unit task started on the commander.
  - Propagation needs the subordinate's model-level list.
- **Step 6:**
  - Parse `\[\s*"…"\s*\]` keys, named-function values, computed `return true, <var>`.
  - Readers include the `FragOrder.lua` poll helpers.
  - The propagation check separates "intended dia-type exists under another spelling" (error) from client names
    (note).
- **Step 9:** the `FragOrder.lua` insertion (CRLF, `after` key, blank-line separation); the Stages entries for frago
  task labels (reuse the mission scaffold's stage code); the type → getter + ontology table.
- **Step 4:** the Lua-side design must work with `Behavior.start` clearing state: resume with `spark.resume`, not
  `start`.

## 6. Step 6: `mission_check --all-fragorders` triage

Read at sw.models `master` @ `d2f78fafcd`, sw.sword `master` @ `51bdc0ec9e`. Result: 23 errors, 69 warnings, 182 notes
(missions unchanged: the 297 findings of the previous `--all` are all still reported, byte-identical).

**Kinds found** (tool codes): mildb a 25, b 15, c 14, c2 7, d 3, f 35; wwt a 25, b 11, b? 4, d 1, f 22, none 6.
They agree with step 1 once crowd orders (e) are counted inside a / f and the direct starts are added.

**Seed anomalies, all reported:** wwt `SafetyPauseMovement` / `SafetyResumeMovement` handled by nothing (+4 other
wwt `Rep_OrderConduite_*`, also never available); security commanders + `Realign` (startTask → kernel path, no task);
5 wwt VSRAM initializers starting missing tasks; 49 initializer keys for dia-types in no DB (notes); the 4 unread
empty initializers in wwt (`GoTo*Area`, `LendEquipment`; `SupportWithDirectFire` is in no DB, so it is one of the 49);
propagation names `GoTo`, `Rep_OrderConduite_MettreTenueNBC`, `ActivateNBCProtection`, `ProvideIndirectFire` (+ new:
`DeactivateNBCProtection`); 5 frago tasks never started (GoToAndWait and security WaitThere are only stopped);
`SafetyDismount` runs Mount; `getFragOrders.lua` `resSize`; `integration.isTask = fragOrder.isTask` (nil).

**Parser facts found while triaging** (kept in the code):
- `detachUnit` (DetachUnit2/3) and others start their task **directly** (`common.agent.tasks.frago.DetachUnit:start(
  mission )`) and clean, instead of returning `true`: counted as kind (a). The decisional index now shows them.
- The ROE parameter `orderConduiteChangerReglesEngagement_` is read through a registered C++ getter
  (`int/FragOrderIntegration.lua` `GetorderConduiteChangerReglesEngagement_`), never as a quoted string: the
  "never read" rule greps the bare word in the integration layer.
- `CancelMission` is named by commander code only, but its unit initializer acts: kind (f), not (b).

**False positives removed:** priority warnings only when the behavior (or its links) acquires a resource (33 → 16);
propagation to subordinates is a WARN only when **every** subordinate model of an automat model lacks the order
(53 → 17), a note otherwise; skipped when an automat branch forwards the order itself.

**Deviation from the plan:** "Pause movement" is not clean. The 2 warnings are true positives: automat model
"Aviation bomber squadron" (mildb) lists it under `COMMON - Move`, but its only subordinate unit model (also named
"Aviation bomber squadron") lists it under none of its 6 missions; same for wwt "Test company". Its own wiring
(initializer → `common.agent.tasks.frago.WaitThere`, lists, sheets, `.ts`) is clean.

## 7. Step 7: `checkpoint_lint --fragorder`

- `order-effect-not-persisted`: 41 findings over `--all`. Validation: MoveAlong `self.pathType` (Infiltrate) flagged;
  ManageDiving (`targetDepth`) and Transit (`maxSpeedOnWater`) clean; `--fragorder Infiltrate` lints exactly the 4
  reader skills of the §1.2 Q5 table (MoveAlong, MoveAndTakePosition, ReconnoiterInOrder, ReconnoiterRoute).
- `fragorder-param-state`: 0 today (no skill keys state on a FragOrder parameter), as step 1 found.
- `--fragorder "Pause movement"`: the 2 `frago-task-lost` notes (one per DB) **plus** 1 `lost-timer` upper bound in
  `actions.Wait` (`waitInMin`). That one is unreachable for Pause movement (its initializer sets no `delay`), but real
  for other users of WaitThere; kept. Readers are linted only for kind (b): for the other kinds the dia-type strings
  are senders (Transport) or commander filters.
- The Lua helpers (`strip_comments`, `mask_strings`, `block_end`) now come from missionlib; `checkpoint_lint --all`
  output is byte-identical to before for the old rules.

## 9. Step 9: `mission_scaffold.py` frag-order mode

Validated on a scratch copy of model ada (from master @ `d2f78fafcd`):
- `--check-html`: 1047/1047 byte-exact (780 mission + 99 + 99 + 69 frag-order sheets); 1058/1058 after the 4 test
  orders were written (fr images under the English name).
- Specs written: `task` (reuses WaitThere, `after` PauseMovement), `read` (+ propagate), `kernel` (new frago task
  TestHold with a priority 1e5 `actions.Wait`), `immediate` (+ automat branch, mildb, an `automat:` model prefix).
  `mission_check --fragorder` clean for task / kernel / immediate; read is clean once a reader exists (one added to
  MoveAlong, persisting its value: `checkpoint_lint` does not flag it). TEST - Hold keeps the expected note "a task
  named by the dia-type runs on a commander" (available-without-mission, not propagated).
- `--all` on the copy = the real tree's findings (line numbers normalized) + those 2 notes.
- `FragOrder.lua` stays pure CRLF (1450 lines) and passes `directai/bin/luac.exe -p` (Lua 5.2.4).
- Refusals checked: existing name / dia-type, unknown model, a model name shared by two kinds (needs `unit:`),
  missing fr name, unknown `after` key, kind kernel with a dia-name that is no task parameter.
- Getter table measured from the initializers (`getter_catalog`): e.g. Point → `getPointParameter` wrapped in
  `CreateKnowledge( …point, … )`, Integer / Enumeration → `getIntegerParameter`; a type no initializer reads needs
  `getter` / `wrap` in the spec.

## 11. Step 11: dry review (general-purpose agent, read-only)

21 items (1 blocker, 13 should-fix, 7 nits); all applied:
- **Blocker**: the index grep recipes had unescaped pipes (`^| mildb |…` matches every line with rg / `grep -E`):
  escaped, plus a line on BRE vs ERE.
- Scaffold: new `kind: automat` (commander only, automat models only, no unit initializer); a branch on a
  **propagated** order neither cleans nor returns true (the military commander ignores the return value and
  propagates next; the security one stops on true; `FragOrder.lua` line 83 comment); `--help` lists every
  frag-order key, the example now carries `after` / `propagate` / `automat_branch` / `ends` as real keys.
- Prompt: scaffold kind ↔ FRAGORDERS kind mapping; a kind-(c) variant becomes kind (a); frozen branch → ask about
  kind (a); unit task orders without mission → wrong-brain rule; design B only for kind (c); the Lua-side helper is
  a proposal unless it exists (insert-only rule); client branches via the CHECKPOINTS §6 one-call recipe; adding an
  existing order to models = hand edits after agreement; `--model`; XML parse command; `ant check`; on master run
  clarify + critique, then stop.
