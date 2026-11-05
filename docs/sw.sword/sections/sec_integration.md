# The Lua integration layer (`data/app-data/resources/integration/`)

> Scope: the Lua "integration layer" that sw.sword ships, which sits between the C++ DEC functions and the DirectAI / Spark (DIA5) decisional models in `sw.models`. All paths are relative to the sw.sword root unless they start with `sw.models/`. Line numbers come from the tree as of 2026-11 (`master` HEAD `ffbf1a501a`). A `~` before a line number means the line is approximate (±3). "(unverified)" marks claims I inferred but did not confirm in code.

---

## 0. TL;DR for the navigator

* The layer consists of **one root loader**, `data/app-data/resources/Integration.lua`, plus **58 `.lua` files** in `integration/` (about 26.9k lines) and **one generated file**, `integration/Reports.lua`. Reports.lua is gitignored and does not appear in a fresh checkout.
* The model loads it. `sw.models/.../dia5/BMAgent.bms` calls `LoadResourcesFile( "Integration.lua" )`. That C++ function resolves `<integration-dir>/Integration.lua`, where `--integration-dir` defaults to `resources`. Integration.lua then `include`s every `integration/*.lua` in a fixed order. After that, the model's own `Decisional.lua` / `FragOrder.lua` run, then `spark.createBrain{ extensions, dia5 }`.
* Everything lands in **globals of the brain's Lua state**:
  * `integration.*` (and the sub-tables `integration.communication`, `integration.query`, `integration.taskgroup`, `integration.ontology`, `integration.taskKnowledge`)
  * `utilities.*`
  * about 500 `DEC_*` wrappers
  * enum constants (`eEtat…`, `eRC_…`)
  * the C++→Lua entry points (`StartEvent`, `CallbackAction`, `InitMePlatoon`, …)
* `myself` is the C++ `DEC_Decision_ABC*` of the entity, injected per brain. `meKnowledge` is the DirectAI "body" knowledge, created in `InitMe*`.
* Two kinds of C++ function exist. The `_DEC_X( myself, … )` C++ functions take the caller explicitly. `DECFunctionWrappers.lua` re-exposes them as `DEC_X( … )`, which injects `myself`. Integration files instead cache `local _DEC_X = _DEC_X` and pass `myself` themselves.
* Simulation actions use a start / update / stop triplet:
  * the start returns an **action id**;
  * results come back asynchronously through `actionCallbacks[id]` (set from C++ via `CallbackAction`);
  * per-objective state is stored on the knowledge, keyed by `myself`: `objective[myself].moveAction`.
* Safety nets when you change the layer:
  * `ant checkreports` checks that every `eRC_*` used in the integration layer exists in Reports.xml.
  * `ant checkdec` checks sw.models' `decfunctions.txt` against the C++ registrations.
  * `ant checkintegration` runs the lunit tests. They cover **only** `utilities.*` (TableIntegration + ToolsFunctions).
  * `ant integration-documentation` generates the LDoc reference.

---

## 1. Loading: how the integration layer gets into each brain

### 1.1 Configuration of the integration directory

| What | Where |
|---|---|
| `integrationDir_` defaults to `"resources"` | `src/libraries/simulation/Tools/MIL_Config.cpp:78` |
| CLI option `--integration-dir` ("set integration directory") | `MIL_Config.cpp:89` |
| Getter | `MIL_Config.cpp:259-262` |
| Self-training launcher passes `integration-dir` from the registry key `IntegrationLayerPath` (debug config panel) | `src/libraries/selftraining/ScenarioLauncherPage.cpp:206-207`, `DebugConfig.cpp:27,51`, `DebugConfigPanel.cpp:43-55` |
| Docker image copies `data/app-data/resources` to `/app/resources` and the generated `Reports.lua` to `/app/resources/integration/Reports.lua` | `Dockerfile:385-388` |

The integration directory can therefore be pointed at a working copy for development: launch with `--integration-dir=<path>`, or use the selftraining debug panel. The path that is passed is the directory that **contains** `Integration.lua`, not the `integration/` subfolder.

### 1.2 From models.xml to a brain (C++)

1. **Workspace loading**
   * `DEC_Workspace::InitializeModels` loads the physical file `models` (`src/libraries/simulation/Decision/DEC_Workspace.cpp:246-250`).
   * `LoadModels` iterates `units/unit`, `automats/automat`, `crowd/crowd` and optional `task-groups/task-group` (`:252-286`). Each entry goes through `ReadModel`.
2. **`ReadModel`** (`DEC_Workspace.cpp:288-312`)
   * It reads the attribute `masalife` (bool) into `isDAISpark`.
   * It picks the source directory by namespace key: `"net.masagroup"` for Spark, `"none"` for legacy.
   * It builds `DEC_Model( name, xis, sourcePath, missionTypes, isDAISpark, integrationDir )`.
   * The source directories come from `decisional.xml`. For example, `sw.models/data/data/models/ada/decisional/decisional.xml` maps `dia5` and `extensions` to `net.masagroup`, and `bms/sources` to `none`.
3. **`DEC_Model_ABC` constructor** (`DEC_Model_ABC.cpp:27-37`) stores `integrationDir_` and reads `dia-type` and `file` (the brain file, e.g. `BMAgent.bms`).
4. **`DEC_Decision<T>::InitBrain`** (`DEC_Decision.h:~302-330`)
   * It calls `DEC_DecisionImpl::CreateBrain(...)` (`:313`), then `RegisterSelf( *pBrain_ )` (`:315`), which sets the global `myself`.
   * It also applies the profiler, the timeout, `RegisterSpecific`, the `ScriptRefs` and the GC settings for new archetypes.
5. **`CreateBrain`** (`src/libraries/simulation/Decision/DEC_Decision.cpp:1494-1560`)
   * **Archetype cache.** `brainTable[brainFile]` holds one *archetype* brain per brain file. Each entity brain is a copy: `new sword::Brain( *pArchetypeBrain )` (`:1508-1514`, `:1546-1552`). So the integration layer is **loaded once per brain file per process**, and entities share the archetype's loaded code.
     * `reload=true` (debug reload) builds a fresh archetype and uses it directly.
     * The archetypes are intentionally leaked (long comment at `:1462-1485`).
   * **Plugins.**
     * Spark (`isDAISpark`): plugins `'spark','devtools'`, then `sw.ai.setCoreLibraryPath( integrationDir/"spark-core" )` (`:1515-1522`), with optional `sw.ai.setTypeChecking` and `sw.ai.setEntityCaching`.
     * Non-Spark (legacy DIA4 / "bms"): plugins `'eventmanager','motivation','devtools'` (`:1527-1532`).
     * **No `spark-core` directory exists under `data/app-data/resources`** in this checkout (unverified where it comes from; probably a package fetched at deploy).
   * **Order** (comment at `:1536-1538`):
     1. `RegisterCommonUserFunctions( brain, isDAISpark )` registers **all C++ DEC functions first** (`:1539`, definition at `:275-305`).
     2. It registers the Lua-callable `LoadResourcesFile( file )`, which does `include( integrationDir / file )` (`:1487-1490`, `:1540-1541`).
     3. It runs `include( brainFile, includePath, type )` (`:1542`).
     * Consequence: when integration files execute, every `DEC_*` / `_DEC_*` already exists. That is why `local _DEC_X = _DEC_X` at file top works.
   * `pBrain->GetScriptRef( "sw.ai.setRole" )( type )` selects the DIA role, for example `security.agent.Agent` from `dia-type` (`:1512`, `:1556`).

### 1.3 The brain file and the order of Lua loading

`sw.models/data/data/models/ada/decisional/dia5/BMAgent.bms` contains the whole bootstrap:

```lua
pragma "once"
LoadResourcesFile( "Integration.lua" )   -- 1. integration layer (sw.sword)
include "Decisional.lua"                 -- 2. model-level globals (Decisional.lua just includes FragOrder.lua)
local libPath = select( 1, ... )
spark.createBrain( { libPath .. "/../extensions", libPath } )  -- 3. Spark loads classes/skills/tasks; extensions dir FIRST
installErrorHandler()                    -- 4. defined in SimulationIntegration.lua:934
```

All 152 units in `sw.models/.../physical/mildb/Models.xml` are `masalife="true"` with `file="BMAgent.bms"`. **In practice DIA4 / the non-Spark path is dead for ADA models.** The `isDAISpark=false` branches in C++ (e.g. `RegisterMissionParameters` at `DEC_Decision.cpp:1440-1452`) are legacy.

`data/app-data/resources/Integration.lua` (144 lines) does the following, in order:

1. **`ontologyKeys`** (`:2-26`) lists the type keys that must exist in `integration.ontology.types`: `agent`, `agentKnowledge`, `area`, `automat`, `body`, `cbrnProtection`, `direction`, `equipmentType`, `fragOrder`, `fuseau`, `genObject`, `humanWound`, `itinerary`, `liaisonInstance`, `limaStatique`, `object`, `point`, `population`, `resourceNetwork`, `taskgroup`, `transmitterType`, `unitType`, `urbanBlock`.
2. **`dai.share( "integration", { ontology = <proxy> } )`** (`:31-51`).
   * `integration.ontology.types` is a **lazy proxy**. Each access calls `sw.ai.getRoleProperties().ontology.types`, which is the type map of the *current role*, and fills any missing key with `sparkle.Object`.
   * The role files in sw.models declare the map, e.g. `sw.models/.../dia5/military/agent/roles/Agent.lua` calls `sw.ai.setRoleProperties({ ontology = { types = { body = common.agent.world.MeKnowledge, point = common.agent.world.Point, automat = common.agent.world.Company, … }}})`.
   * Commit `59632d60e7` (2026-05) removed an ontology cache to fix memory leaks, so do not cache `integration.ontology.types` at file load time.
3. **`dai.share( "utilities", {} )`** (`:53`).
4. `require "debug"`, `"math"`, `"string"`, `"table"` (`~:56-59`).
5. **Includes in this exact order** (`:61-~133`):
   ```
   DECFunctionWrappers, Reports (generated), SimulationIntegration, KnowledgeManager, Types, BaseTaskKnowledge,
   ToolsFunctions, TableIntegration, GetPerception, RelievableIntegration, Element, KeyPoint, Unit,
   UndergroundNetworkIntegration, LeadIntegration, CRTaskListener,
   Activable, Animatable, Askable, Buildable, Bypassable, Observable, Identifiable, Reconnoitrable, Reachable,
   Relievable (AGAIN - included twice), Destroyable, Neutralizable, Illuminable, IndirectFireable, Improvable,
   Removable, Loadable, Positionable, Safeguardable, Safeguarding, Supporting, Reaching, Controlable, Sealable,
   Communication, Asymetric, Reinforcable, FragOrder, Equipment, CrowdBody, Crowd, Logistic,
   CommandingFilter, ToolsQueries, Geometry, Lima, Weather, TaskGroup, Transmissions,
   -- "for Security": UrbanBlock, Area, Fire, Object, Commander
   ```
6. `TraceGraph = function( trace ) DEC_Trace( trace ) end` for devtools, and `spark.setLogger( … DEC_Trace("[tag] msg") )` (`~:136-144`).

Things to know about this order:

* **The order matters only for load-time references.**
  * A file that does `local X = integration.someFn`, or `integration.a = integration.b`, needs `b` to be defined earlier.
  * Calls made inside function bodies resolve at call time, so they work regardless of order.
  * Example: SimulationIntegration's `startOrResume` calls `copyTable`, which TableIntegration defines later. That is fine because it runs at call time.
* **`RelievableIntegration.lua` is included twice.** This is harmless (pure function assignments) but sloppy.
* **There is no `require` / module mechanism between integration files.** Everything is global assignment into shared tables, and `include` is the DirectAI include (relative to the including file's directory, unverified). Each file starts with `--- @module integration` / `-- @section <File>` for LDoc only.

### 1.4 How the model and the integration layer combine, and overriding

* There is **one Lua state per brain**. The integration globals and the model's classes and skills live in the same global namespace.
* sw.models runs **after** the integration layer (step 2 and step 3 above). Any model file can therefore **redefine or extend** `integration.*`. Last assignment wins.
  * Example: `sw.models/.../dia5/FragOrder.lua:1421-1430` adds `integration.isTask`, `integration.mustBePropagate`, `integration.executeAutomatFragOrder`, `integration.startFragOrderTask`, `integration.getFireFragOrder`, … from its local `fragOrder` table.
  * A grep of `^\s*integration\.X\s*=` over dia5 `*.lua` finds exactly 10 such assignments, all in `FragOrder.lua`.
* **The override gotcha.** Integration files cache aliases at load time: `local integration = integration`, `local _DEC_X = _DEC_X`, and the many `integration.foo = DEC_Bar` direct aliases.
  * Overriding `integration.foo` from the model works for skills that call `integration.foo(...)`.
  * It does **not** change other integration functions that captured the old value in a local. Wrapper-to-wrapper calls inside the layer usually go through `integration.foo(...)` (a table lookup), so most still pick up the override.
  * Overriding a `_DEC_*` or `DEC_*` global after load has **no effect** on the files that cached it locally.
* The `extensions` directory (`sw.models/.../decisional/extensions/`, whose readme says "meant to contain client decisional models") is passed first to `spark.createBrain`. It holds client Spark classes and skills, not integration overrides (unverified whether a same-named class shadows dia5).
* `integration.ontology.types` is role-dependent. The same integration function creates different concrete knowledge classes in an agent brain, a company brain or a crowd brain.

### 1.5 C++ → Lua entry points defined by the integration layer

The C++ side binds these global names after loading, through `ScriptRefs` (`DEC_Decision.cpp:74-98`). **Renaming or removing any of these breaks the simulation.**

| Global | Defined at | Called by C++ when |
|---|---|---|
| `StartEvent(task, params)` / `ResumeEvent` | `SimulationIntegration.lua:476,480` | mission start / resume. It stores `myself.taskParams`, `myself.newTask=true`, `myself.currentMission`, `myself.taskParamsWithDefault` (a copy of `spark.start(...).parameters`), and skips `"BEH_Defaut"`. |
| `StartTask(task, params)` | `:484` | `spark.start` without the bookkeeping |
| `StartDefaultTask()` | `:489` | agent resurrected (`spark.getDefaultTask():start()`) |
| `StopEvents()` | `:496` | `integration.stopTasks()`, which runs `spark.stopAll()`, clears `currentMission`, saves `previousTaskParams` (`CRTaskListener.lua:540`) |
| `InitTaskParameter(params, type, name, source, list)` | `:589-625` | while filling mission params. It switches to `AppendTaskParameterFunction` between the `"beginlist"`/`"endlist"` markers for lists of lists. The C++ visitor passes `brain.GetScriptRef("integration.ontology.types.point")` etc. (`DEC_Decision.cpp:367-1245`). |
| `InitMePlatoon` / `InitMeAutomat` / `InitMeCrowd` / `InitMeTaskGroup` | `:692/716/705/727` | right after `RegisterSelf` (`Entities/Agents/Roles/Decision/DEC_RolePion_Decision.cpp:199-210`, `DEC_AutomateDecision.cpp:212-222`, `DEC_PopulationDecision.cpp:170-180`, `DEC_TaskGroupDecision.cpp:102-112`). Each creates `meKnowledge` with `CreateKnowledge(types.body, myself)`, runs `spark.me:setBody`, sets `reportFunction` (`integration.pionRC` for agents, `integration.crowdRC` for crowds, `integration.genericRC` for automat and task group), and calls `RegisterTaskListener()`, `InitKnowledgeManager()`, plus `RegisterDoneTaskListener()` for agents only. |
| `CallbackAction(actionId, value)` | `:861` | async action result (`DEC_Decision_ABC.h:209-213`) |
| `KnowledgeCallbackAction`, `RemoveAction`, `CallbackPerception` | `:867,879,873` | object-knowledge results, action deletion (clears both callback tables), perception (recce) callbacks |
| `UndergroundCallback`, `OperationalStateCallback`, `EchelonCallback`, `IntoxicatedCallback`, `ContaminatedCallback`, `SurrenderedCallback` | `:884-907` | set `myself.isUnderground`, `.operationalState`, `.echelon`, `.isIntoxicated`, `.isContaminated`, `.isSurrendered` |
| `DeadCallback()` | `:908` | sends `"CleanUp"` to the superior and releases `myself.currentItinerary` and the task params |
| `EndMissionCallback(entity)` | `:921` | `SendMessage("EndMissionAgent"/"EndMissionAutomat")` |
| `CallbackMovingOnPath(position)` | `:938` | forwards to `myself.callbackMovingOnPath` if set |
| `ActivateBrainDebug(b)` | `:946` | `spark.setTracing(b, "select")` |
| `installErrorHandler()` | `:934` | from BMAgent.bms. `dai.setErrorHandler` traces `err .. debug.traceback()` via DEC_Trace. |
| `integration.savePersistentState()` / `integration.loadPersistentState(s)` | `:679 / :665` | checkpoint save / load (`src/libraries/simulation/Decision/Brain.cpp:126-133`). Serializes `persistentState[""]` and `spark.saveState()`. |
| `spark.stopTasks`, `collectgarbage`, `step`, `SetStateVariable` | spark / Lua | also bound in ScriptRefs |

---

## 2. Globals and namespaces actually defined

| Global | What it is | Defined at |
|---|---|---|
| `myself` | C++ `DEC_Decision_ABC*` userdata of the entity: agent, automat, crowd or task group. It also accepts arbitrary Lua fields (`myself.moveAction = …`), which **persist across ticks**. This is the main per-entity scratch storage. | C++ `RegisterSelf`, e.g. `DEC_RolePion_Decision.cpp:201` |
| `meKnowledge` | DirectAI knowledge of type `integration.ontology.types.body` wrapping `myself`. Its class is defined in sw.models (`common.agent.world.MeKnowledge`, `…Company`, etc.). | `InitMe*` in `SimulationIntegration.lua:692-750` |
| `integration` | Shared table (`dai.share`). Sub-tables: `ontology` (lazy, see 1.3), `communication` (`CommunicationIntegration.lua:~76`), `taskKnowledge` (`:~75`), `query` (`ToolsQueries.lua`), `taskgroup` (`TaskGroupIntegration.lua:12`), `listenFrontElementCallbacks` (Communication). | `Integration.lua:31` + all files |
| `utilities` | Pure Lua helpers (tables, interpolation, timers). The only unit-tested part. | `Integration.lua:53`, `TableIntegration.lua`, `ToolsFunctions.lua` |
| Legacy global aliases | `exists`, `existsInside`, `existsIndex`, `fusionList`, `removeFromList`, `removeListFromList`, `copyTable`, `tableSize`, `clearTable` (`TableIntegration.lua:305-321`); `integration.inverseTable` (`:323`); `LinearInterpolation`, `minValue`, `explode`, `affichePositions`, `waitInMin`, `getSimulationTime`, `getRandomNumber`, `getEtatAction` (`ToolsFunctions.lua:268-282`); `DEC_RC = integration.report` (`CRTaskListener.lua:101`) | |
| `DEC_*` wrappers | about 508 `function DEC_X(...) return _DEC_X( myself, ... ) end` | `DECFunctionWrappers.lua` |
| Enums / constants | about 560 lines of `eEtat…`, `eTypeObject…` (object type **strings**), `eTypeIti…`, `eActionObjet…`, `eRoeState…`, `ePath_…`, `eTerrainArea_…`, etc. | `Types.lua` |
| `eRC_*` report ids | `eRC_X = "eRC_X"` for every report known to C++, Reports.xml or the integration layer | **generated** `integration/Reports.lua` (section 6.3) |
| `reportFunction` | per-entity report function set in `InitMe*` | `SimulationIntegration.lua` |
| `reportOnceFunction(...)` / `reportOnceAction` | `spark.startStopAction` that reports only on the first tick of consecutive calls | `SimulationIntegration.lua:~823-835` |
| `emptyTable` | read-only shared empty table (writing to it raises an error) | `SimulationIntegration.lua:~460` |
| `CreateKnowledge`, `CreateProxyKnowledge`, `InitQueryReturn`, `localStorageVar` | knowledge factory and caches | `SimulationIntegration.lua:510-637` |
| `actionCallbacks`, `actionKnowledgeCallbacks`, `perceptionReconnaissanceCallbacks` | callback tables keyed by action id / perception id | `SimulationIntegration.lua:~855-859` |
| `DEC_Message`, `DEC_Warning`, `RC_WithParams`, `RegisterTaskListener`, `RegisterDoneTaskListener` | reporting and task listener | `CRTaskListener.lua` |
| `initializeAssignMissions`, `AssignMissionParameter` | mission parameter assignment for orders to subordinates | `CommunicationIntegration.lua:78,133` |
| `fillParameters`, `findBests`, `fillParametersAutomat`, `findBestsAutomat`, `removeFromListForLead` | **global** (not under `integration.`) Lead helpers | `LeadIntegration.lua:36,123,154,198,216` |
| `InitKnowledgeManager` | knowledge-manager listener (query result cache) | `KnowledgeManager.lua:1` |
| `initTaskKnowledge` | base task-knowledge mixin (`getPionEfficiency`, `computeEffectsLevels`, `hasDotation`, `canDoIt`) | `BaseTaskKnowledge.lua:3` |
| `startSettleCalcul`, `settleDistance`, `DEC_Geometrie_PositionAdvanceAlongFuseau[Automat]` | geometry globals | `GeometryIntegration.lua:469,477,935,939` |
| `S_EffetSecurisationSurDomination`, `S_IntensiteManifestationSurPions` | crowd globals | `CrowdBodyIntegration.lua:247,355` |
| `getBetterReachingEfficiencyInList`, `removeListElementWithSameLocation` | misc globals | `ReachingIntegration.lua:23`, `ReachableIntegration.lua:1771` |
| `TraceGraph` | devtools hook | `Integration.lua` |

**Not defined here:**

* `masalife` survives only as a stub in the tests and as `local masalife = masalife` at `CommunicationIntegration.lua:196`; it is a leftover from the older MASA Life runtime.
* `NIL` is a MASA Life sentinel value. Code defensively checks `x == nil or x == NIL` (e.g. `ReachableIntegration.lua:650,787`, many `self.params.* ~= NIL` in LeadIntegration).
* The Spark API (`spark.*`) comes from the plugin. Uses counted in integration: `spark.isOfType` ×21, `startStopAction` ×13, `getTask` ×4, `me` ×4, `getSupertype`/`getClass`/`addBehaviorListener` ×3, `start`, `resume`, `stopAll`, `send`, `isRegistered`, `isInstance`, `setGlobalMessageListener`, `registerUpdateCallback`, `registerEndTickCallback`, `createBrainLocalStorage`, `saveState`, `loadState`, `getDefaultTask`, `setTracing`, `setLogger`. Also `sw.ai.getRoleProperties`, `dai.share`, `dai.setErrorHandler`, `sparkle.Object`.

### 2.1 What the `*Integration.lua` names mean (capacities)

* Most file names are **capacity-oriented** and come from the historical "knowledge capacity" design: an objective is *Reachable*, *Destroyable*, *Buildable*, ….
* Each file groups the integration primitives that **act on** an objective of that capacity. They are mostly the `startXIt` / `updateXIt` / `stopXIt` triplets plus predicates such as `canXIt` and `xLevel`.
* **The integration layer does not define knowledge classes or interfaces.** Those live in sw.models. Examples:
  * `sw.models/.../dia5/common/agent/world/*.lua|.xml` holds the classes: Point, Agent, AgentKnowledge, Object, PlannedObject, UrbanBlock, Area, Company, MeKnowledge, Crowd, ….
  * `…/world/interfaces/*.xml` holds the interfaces, whose names differ from the files: `Destination`, `DestructibleElement`, `BuildableElement`, `Loadable`, `Observable`, `ReconnaissanceTarget`, `Reinforceable`, `Position`, `Teammate`, ….
* Knowledge methods call integration functions. For example, `Point.lua` `__init` uses `integration.geometryX(self.source)`, and its methods call `integration.isElementInAOR`, `integration.distance`, and so on.

Convention used by integration functions:

* **Arguments are either DirectAI knowledges** (tables with `.source` = the simulation object, plus methods such as `:getPosition()`, `:getLocalisation()`, `:getType()`, `:proximityLevel()`) **or raw simulation objects.** The doc comment says which: "Direct AI knowledge" / "Direct AI agent" versus "Simulation agent" / "Simulation position".
* A knowledge passed into a start function gets per-caller state written into it: `objective[myself] = {…}`.

| Capacity file | Acts on | Typical verbs |
|---|---|---|
| Reachable | anything with `getPosition` | move to, distance, path |
| Destroyable | agent knowledge | direct fire, ranges, PH, ROE checks |
| IndirectFireable | point / agent knowledge | indirect fire |
| Buildable / Improvable / Removable / Bypassable / Activable / Animatable | object / planned object / urban block | build, improve (= mine), demine / remove, bypass / breach, activate, animate |
| Observable / Identifiable / Reconnoitrable / Illuminable | agents, objects, urban blocks, points, areas | perception levels, observe, identify, recce / search / NBC recce, illuminate |
| Loadable | agents, crowds, prisoners, refugees | load / unload / transport |
| Controlable / Sealable / Safeguardable / Safeguarding / Supporting / Positionable / Neutralizable / Relievable / Reinforcable / Askable / Reaching | various | control area, seal off, force ratios, cover positions, support positions, posture, neutralize, relieve, reinforce, interrogate, reaching-efficiency |

---

## 3. `DECFunctionWrappers.lua` (2211 lines, 508 `function` definitions)

### 3.1 What it does

* C++ registers many DEC functions under an **underscore name whose first parameter is the caller**: `_DEC_EtatAction( DEC_Decision_ABC* caller, unsigned id )`. See for example `src/libraries/simulation/Decision/DEC_ActionFunctions.cpp:93-96`:
  ```cpp
  brain.RegisterFunction< unsigned int, DEC_Decision_ABC*, unsigned int >( "_DEC__StopAction", &StopAction );
  brain.RegisterFunction( "_DEC_PauseAction", &SuspendAction );
  brain.RegisterFunction( "_DEC_ReprendAction", &ResumeAction );
  brain.RegisterFunction( "_DEC_EtatAction", &GetActionState );
  ```
* `DECFunctionWrappers.lua` defines the old, caller-implicit names on top of them:
  ```lua
  function DEC_EtatAction( actionId ) return _DEC_EtatAction( myself, actionId ) end   -- :17
  function DEC__StopAction( actionId ) return _DEC__StopAction( myself, actionId ) end -- :4
  ```
* The historical reason (inferred) is that old DIA4 models called `DEC_X(...)` with an implicit "self". Making `myself` explicit lets the same C++ function be called on **another** entity, e.g. `_DEC_Agent_Position( otherAgent )`. The integration layer itself prefers the `_DEC_` form with an explicit `myself`.
* It is loaded **first** (`Integration.lua:61`), so later files can reference the `DEC_*` names, but it can only reference `_DEC_*` and C++ names.

### 3.2 Conventions inside the file

* Sections are marked by comments: `-- Actions`, `-- Debug`, `-- Representations`, `-- Orders`, `-- Knowledge`, `-- Geometry`, `-- Logistics`, `-- Automaton`, `-- Accessors`, `-- Move`, `-- NBC`, `-- Perception`, `-- Fire`, `-- Terrain`, … (headers at lines 3, 22, 58, 81, 168, 243, 370, 473, 627, 779, 1132, 1954, 2107, 2188).
* A comment inside each body says which entity kinds may call it: `-- Automaton`, `-- Agent & Automaton`. Calling an agent-only function from an automat or crowd brain usually throws in C++, because `GetPion()` is used on a non-agent.
* **Wrappers with real logic**, the only non-trivial ones:
  * `DEC_Trace` (`:40-51`) is a **no-op until `myself` exists**, because `spark.setLogger` can call it during brain construction. It then **redefines itself**.
  * `DEC_DetruireBlocUrbain(urbanObject, category)` (`:746`): crowd vs agent variant, depending on whether `category` is given.
  * `DEC_Agent_EstDansFoule(pion)` (`:770`) and `DEC_ConnaissanceObjet_Degrader(knowledge, factor, dotation)` (`:1533`): crowd vs agent dispatch.
  * `DEC_Pion_ChangeAutomate(a1, a2)` (`:1500`): one-argument or two-argument form.
  * `DEC_Tir_PorteeMaxPourTirer(phOrAgent, ph)` (`:2108`): "old compatibility" two-argument form.
  * **Query wrappers** fill a DirectAI result list via `InitQueryReturn`: `DEC_Connaissances_BlocUrbain(results)` (`:1642`), `DEC_Connaissances_Observable/Destroyable/Identifiable(results)` (`:1754-1773`), `DEC_Crossroads(results)` (`:2193`).
  * **Lua re-implementations that shadow a `_DEC_` name**: `_DEC_Agent_MettreTenueProtectionNBC = function(agent) … DEC_ActivateAirFiltrationSystem / DEC_GetNextActivableProtection / DEC_ActivateProtection … end` (`:1378-1384`) and `_DEC_Agent_EnleverTenueProtectionNBC` (`:1390-1393`). **Not every `_DEC_*` name is C++.**
  * `DEC_FillMissionParameters(missionRef, mission)` (`~:83`) passes the global `InitTaskParameter` to C++.
* **Deprecated tail** (`:2197-2211`): `DEC_Agent_AutonomieEnDeplacement` (use `integration.agentMovingAutonomy`), `DEC_ChangeValeurDotations2` and `…3` (use `integration.changeResourceValueUsingCombatTrains`).
* **Bug found** at `DECFunctionWrappers.lua:1758`: `local objects = DEC_Knowledge_GetObjects` is missing the call `( myself )`. `InitQueryReturn(results, types.object, objects, true)` then evaluates `#objects` on a function, which raises an error. `DEC_Connaissances_Observable` is therefore broken if anything calls it. A grep finds no caller in sw.models dia5.

### 3.3 The `local X = X` caching pattern in other files

Almost every integration file starts with a sorted block that ends with `local integration = integration`:

```lua
-- ReachableIntegration.lua:6-73 (≈67 cached names)
local DEC_Geometrie_DistanceBetweenPoints = DEC_Geometrie_DistanceBetweenPoints
local _DEC_StartDeplacement = _DEC_StartDeplacement
local _DEC__StopAction = _DEC__StopAction
local integration = integration
```

* **Why: performance.** Locals are upvalues, which are much faster than global lookups in hot per-tick code. Also, in the brain copy model the globals table may be a proxy (unverified).
* **Consequences:**
  1. The global must exist **when the file is loaded**. C++ registers everything before the include, so this holds for C++ functions. For Lua functions it requires that the defining file was included earlier.
  2. A later redefinition of the global (by sw.models, or by an `_DEC_` shadow defined later) is not seen by that file.
  3. If a C++ function is renamed or removed, the file silently caches `nil` and fails only when the function is called ("attempt to call a nil value (upvalue '_DEC_X')").
* **Inside function bodies** you also see `local myself = myself` / `local meKnowledge = meKnowledge` (`ReachableIntegration.lua:782-784`, `LeadIntegration.lua:954`). This is purely a micro-optimization. Do **not** hoist `myself` to file scope: it is per-brain and, in the archetype model, file-scope evaluation happens once in the archetype (unverified whether the copy re-evaluates).
* **Direct aliasing**: `integration.foo = DEC_Bar` with LDoc `@name` / `@class function` tags, so the doc generator sees a function. This is the cheapest way to expose a C++ function. Recent commits use it almost exclusively, e.g. `integration.isSpecialSensorsOfTypeEnabled = _DEC_Perception_IsRadarEnabled -- ( agent, radarType )`. The trailing `-- ( args )` comment documents the signature.

---

## 4. Per-file catalog

The format is: purpose, followed by notable public names. "start/update/stop X" means the triplet `integration.startX`, `updateX`, `stopX`. Line counts are in brackets.

### 4.1 Core and infrastructure

* **`DECFunctionWrappers.lua` [2211]**: caller-implicit `DEC_*` wrappers over `_DEC_*(myself,…)`. See section 3.
* **`Reports.lua` [generated, not in git]**: `eRC_X = "eRC_X"` for every report id. See 6.3.
* **`SimulationIntegration.lua` [963]**: the backbone.
  * **Physical-DB extensions and type lookups**: `GetPhysicalExtension`, `get{AgentDoctrine,DotationType,EquipmentType,ObjectType,AgentType,AutomatType,SensorType,RadarType,WeaponType,CrowdType,InhabitantType,FragOrderType,MissionType,BreakdownType,ResourceNetworkType,CBRNProtectionType,CBRNAgentType}Extension` and `find<Type>By{Name,Id,DIAType}` (`:12-404`); `getAgentExtension` / `setAgentExtension` (`:412,420`).
  * **Knowledge lookup**: `getAgentKnowledgeFromAgent`, `getAgentKnowledge`, `getObjectKnowledge`, `getCrowdKnowledge` (`:429-453`).
  * **Task lifecycle**: `StartEvent`, `ResumeEvent`, `StartTask`, `StartDefaultTask`, `StopEvents` (`:476-498`).
  * **Knowledge factory** (`:500-585`):
    * `CreateKnowledge(type, source, params)` returns the **same Lua object for the same source**. It uses a weak `knowledgeCache` plus a strong `persistentKnowledgeCache` in `spark.createBrainLocalStorage`, keyed by the metatable hash function for shared_ptr userdata.
    * `CreateProxyKnowledge(typeName, source, params)` defaults to `{proxy=source, distanceMin=0, distanceMax=200}` and is stored in the **global** `knowledgeFactory`.
  * **Mission parameters**: `InitTaskParameter`, `InitQueryReturn` (`:589-637`).
  * **Persistence**: `load/savePersistentState` (`:665,679`). `getPersistentState(skill, hash)`, `getPersistentKey(v)` (needs `knowledge:hash()`), `getSkillState(node)`, `setSkillState`, `setSkillStateElement`, `clearSkillState` (`:752-816`) give skills checkpoint-proof state.
  * `integration.stopAction(id, entity)` (`:688`).
  * `InitMe*` (`:692-750`).
  * `reportOnceAction` / `reportOnceFunction`.
  * Aliases `integration.getTimeInSecond = DEC_GetTimeInSeconds`, `getTickDurationInSeconds`, `isSimWithInfiniteSupplies`.
  * Callbacks (`:855-945`), `getMaxInt` (`:952`), `CreateExtendedEvent(name, xml)` (timeline, `:961`).
* **`KnowledgeManager.lua` [44]**: `InitKnowledgeManager()` installs a behavior listener with `willStart`/`willStop`/`finished`/`UpdateDecision` and a `getQueryResult` / `setQueryResult` cache.
* **`Types.lua` [562]**: enums only. Notable groups:
  * echelons `eEtatEchelon_*`;
  * object type names `eTypeObject*` (**strings** matching Objects.xml names, e.g. `"mines"`, `"abattis"`);
  * path types `eTypeIti*` (0-23);
  * move states `eEtatActionDeplacement_*` (`:195-204`);
  * `eIndirectFireState_*`, `eActionTransport_*` (`:218-220`), `eActionTirDirect_*`, `eActionObjet*` (`:245-249`), `eActionRunning/Finished/NotAllowed`;
  * logistic action enums `eConvoyerAction_*`, `eHaulingAction_*`, `eAmbulanceAction_*`;
  * ROE `eRoeState*`, `eEtatROEPopulation_*`;
  * `eDotation_*`, `eRadarType_*`, `eNiveauInstallation_*`;
  * `ePath_*` (bit flags), `eDiplomacy_*`, weather `ePasquillClass_*`, `eIllumination_*`, `ePrecipitation_*`;
  * terrain bitmasks `eTerrainArea_*` / `eTerrainLinear_*` (added in `c56e878682`) and `eOceanicEffect_*` (`94438cb7df`);
  * Scipio constants `PH_TIR_*`, `RAPFOR_*`;
  * report intervals `eTimeRCObject=600` / `eTimeRCReco=600` (seconds).
* **`BaseTaskKnowledge.lua` [33]**: `initTaskKnowledge` returns default task-knowledge methods.
* **`CRTaskListener.lua` [561]**: reports and the task listener. See 4.3.
* **`TableIntegration.lua` [325]**: `utilities.exists`, `existsInside`, `existsIndex`, `fusionList`, `fusionTable`, `appendIfNotMember`, `removeFromList`, `removeListFromList`, `copyTable`, `tableSize`, `clearTable`, `inverseTable`, `shuffleList`, `createTableWithSimIndexes`, `reverseList`, `getCommonValuesFromLists`, `getDifferenceBetweenLists`, `tableEqual`, `getKeys`, plus the legacy global aliases.
* **`ToolsFunctions.lua` [282]**: `utilities.LinearInterpolation`, `minValue`, `explode` (split string), `trimList`, `map`, `filter`, `intersectLists`, `reduce`, `affichePositions`, `displayLines`, `waitInMin`, `resetTimer`, `getSimulationTime`, `getRealTime`, `getRandomNumber`, `getEtatAction`, `areIntervalsOverlapping`, `nan`, `isNaN`.
* **`ToolsQueries.lua` [766]**: query helpers.
  * `integration.tableIsReachingFor` / `…DestroyingFor` / `…NeutralizingFor`.
  * `integration.query.getFirstFireOrder`, `getFirstFragOrderFromType`, `getNearbyPlatoons`, `getFuseaux`, `getNbrFront`, `getNbrGEN`, `getUnitWithoutMission`, `getPositionsToReach`, `getNearestPointFor`, `getSiteFranchissementDansZone`, `getPontoonBridgesInArea`, `getEnemiesToIndirectFireWhenSupport`, `getNearestPositionInAOR`, `getEntitiesToCombatSupport[Platoons]Task`, `getDestroyable`, `getPCUnit`, `getPositionsToFollow`.
  * `integration.GetSuperiorKnowledge`, **`getEntitiesFromAutomat`**, `getEntitiesFromAutomatCommunication`, `getEntitiesFromBatallion`, `getOperationnalEntitiesFromAutomat`, `getDestroyableInObjective`, `getTerroristsInObjective`, `getPCOfAutomate`, `positionInterception`, `getFirePositions`, `getNearestElement`, `getAgentFromListOfElements`, `getAvoidingPositionsFor`.
* **`CommandingFilter.lua` [221]**: company-side filters. `defaultPlatoonInCompany`, `filterPionWithRole`, **`filterPionWithEchelon[s]`**, `getPionsInEchelons`, `setPionsEchelons`, `filterPionByEchelon`, `reconPlatoons`, `nbPlatoonsHaveTask`, `isCompanyMoving` / `Flying` / `Stranded`.
* **`Commander.lua` [167]** (Security): `getCommanderPosition`, `getSubordinateAgentsFromCommander`, `getAgentsFromCommander`, `isCommanderEngaged`, `commanderGetHQUnit`, `isAutomat`, `isCommanderContaminated` / `Poisoned`, `getPopulationROEExist`, `getHQ`, **`getKnowledgeHQ`**.
* **`Element.lua` [68]**: `approachPriority`, `changeAutomate`, `agentEstBrouille` / `pcEstBrouille` (jammed).
* **`KeyPoint.lua` [120]**: efficiency functions `observationEfficiency`, `approachEfficiency`, `identificationEfficiency`, `fleeEfficiency`, `reconnaissanceEfficiency`, `reachEfficiency`. It also has the deprecated predicate tables `integration.isReachingForPredicate` / `isIdentifyingForPredicate` in `{dependencies=…, method=function}` form (old MASA Life predicate format).
* **`GetPerception.lua` [51]**: `getPerception`, `connaissanceAgentEstAPorteDeCapteurDansCone`, `knowledgePerceivedLivingEnemiesAgents`.
* **`WeatherIntegration.lua` [83]**: aliases only. `getWindSpeed`, `getWindAngle`, `getPasquillClass`, `getTemperature`, `getLighting`, `getPrecipitation` (all `DEC_Agent_Get*`), `getWindForce`.
* **`Transmissions.lua` [209]** (radio networks, SW-26492): `createNetwork`, `createDirectLiaison`, `joinNetwork`, `joinDirectLiaison`, `exitNetwork`, `exitDirectLiaison`, `exitAllLiaisons`, `changeTransmissionOrientation`, `getTransmitterFromAgentInLiaison`, `transferTransmissionCommand`, `canTransferTransmissionCommand`, `getLiaisonManager`, `getLiaisonMembers`, `getTransmitterType{FromId,Id}`, `getLiaison{FromId,Id,Name}`.

### 4.2 Capacity files: actions on objectives

* **`ReachableIntegration.lua` [1996]**: movement. Detailed in 4.3.
* **`ReachingIntegration.lua` [44]**: `isReachingFor` and the global `getBetterReachingEfficiencyInList`.
* **`DestroyableIntegration.lua` [543]**: direct fire.
  * Actions: `startDestroyingIt`, `startDestroyingItWithMissileAir`, `startRespondIt`, `startedDestroyingIt`, `stopDestroyingIt`, `stopRespondIt`.
  * Fire permission: `firePermitted`, `firePermittedForPoint`, `fireNotForbbiden` [sic], `isInForbiddenFireArea`.
  * Attrition: `getAttrition`, `getAttritionPower`.
  * Ranges: `portee{Max,Min}PourTirerSurUnite[PosturesReelles]`, `niTropPresNiTropLoin`, `getMaxRangeToFire*`, `isInFiringRange`.
  * Ammunition: `forbidAmmunition`, `autoriseAmmunition`, `autoriseAllAmmunitions`, `forbidAllAmmunition`.
  * Threats: `getKnowledgesUnitsEngaging`, `getKnowledgesDangerousUnits`, `getKnowledgeDangerousAgent`, `canBeDestroyedWithMissiles`, `getDefaultPH`.
  * Movement state: `isMoving`, `isFlying`. The latter is also defined in EquipmentIntegration, which is loaded later and therefore wins.
* **`IndirectFireableIntegration.lua` [356]**: `isOnRangeFor`, `hasLauncherFor`, start/update/stop `ApplyFireOnPlatoon` and `ApplyFireOnPoint`, `updateApplyFireImpossible`, `sendfireAvailable`, `isUnderIndirectFire`, `munitionPourTirIndirect`, `getMortarUnitsToNeutralize`, `findEnemyToMortarIndirectFire`, `launchDREB`, `launchSmokesOnEni`, `getMaxRangeIndirectFireWithoutSelectAmmo`.
* **`NeutralizableIntegration.lua` [82]**: `startNeutralizingIt` / `stopNeutralizingIt`, `isAgentNeutralized`, `unit_operationalLevel` (predicate table), `generic_neutralizationPriority`.
* **`BuildableIntegration.lua` [1003]**: engineering construction. Detailed in 4.3.
* **`ImprovableIntegration.lua` [163]** (improve = mine an obstacle): `canImproveIt`, `hasEnoughtDotationForImprovement` [sic], `agentCanImproveObject[WithMountedEquipments]`, `canBeImproved`, `improvementLevel`, start/update/stop `ImproveIt`, `updateImproveItWithFeedbacks`.
* **`RemovableIntegration.lua` [299]**: `isMined`, `isFullyMined`, `canDemineIt[WithMountedEquipments]`, start/update/stop `DemineIt`, `canRemoveIt…`, `removalLevel`, start/update/stop `RemoveIt`, `update/stopRemoveItSecu`, `startDecontructItUrbanBlock`, `isInEffectArea`, `isAvoidable`, `isInAvoidanceArea`.
* **`BypassableIntegration.lua` [197]**: `canBypassIt[WithMountedEquipments]`, `canBeBypassed`, `bypassLevel`, start/update/stop `BypassIt`, start/update/stop `BreachIt`, `isBreached`, `agentCanBreachObjectWithMountedEquipments`, `getUnbreachedAreas`, `getPositionsOnPathThroughObject`.
* **`ActivableIntegration.lua` [104]**: `activationLevel`, `activateIt` / `deactivateIt`, `activateObject` / `deactivateObject`, `isManeuverObstacle`, `isActifManeuverObstacle`, `delaiAvantActivationFor{GenObject,Object}`, `canActivateIt`, `activateItSecu`.
* **`AnimatableIntegration.lua` [65]**: `canAnimateIt`, `animationLevel`, `startAnimateIt` / `stopAnimateIt`.
* **`ObservableIntegration.lua` [266]**: `computeRelationAgent` / `Object` (diplomacy), `get{Agent,Crowd}PerceptionLevel`, `getObjectPerception`, `getUrbanBlockPerception`, `observeIt`, `observeDirection`, `resetObservationDirection`, `getEstimatePerception[ForPion]`, `isPointVisible`, `getDetectionDistance`, `isAgentPerceivesAgent`, `getIdentificationDistance`, and the `generic_*` priority helpers.
* **`IdentifiableIntegration.lua` [77]**: `get/set{Object,Agent}KnowledgeExtension`, `getAgentIdentificationLevel`, `identifyIt`, `isInIdentificationRange`.
* **`ReconnoitrableIntegration.lua` [788]**: recce, search and NBC-recce over four target kinds (UrbanBlock, Point, Area, Object):
  * `get<Kind>ReconnaissanceState` / `SearchState` / `NBC`, start/stop `Recce<Kind>`, `Search<Kind>`, `NBC<Kind>`;
  * `activate/deactivateSurveillanceOnArea`, `start/stopRensRecceArea`, `start/stopRecoEo`, `AddReconnoiteredPointBy`, `pointIsReconnoitedByMeOrNoOne`, `isInRecoRange`, `startedRecce`, `getTimeLeftToReco{Point,Area,UrbanBlock}`, **`displayReportForReco`** (periodic progress reports), `start/stopActivateLocationProgressiveRecce`, `getTypeCBRN`.
* **`IlluminableIntegration.lua` [104]**: `canIlluminateIt`, `isDefinitelyIlluminated`, `illuminationLevel`, start/started/stop `IlluminateIt`, `isInIlluminateRange`.
* **`LoadableIntegration.lua` [1099]**: transport. Detailed in 4.3.
* **`PositionableIntegration.lua` [95]**: `takeUpPosition[Object]`, `isPosted`, `isParked`, `takePosture`, `getInstallationLevel`, `isPositionContaminated`, `isItineraryContaminated`, `canBeOccupied`.
* **`SafeguardableIntegration.lua` [60]**: `computeAggressiveness`, `getForceRatio`, `getAgentKnowledgeForceRatio`, `getForceRatioAgent`, `getEstimateAttrition`, `getEstimateDangerosity`.
* **`SafeguardingIntegration.lua` [167]**: `get{UrbanBlock,Point}CoverAndConcealmentLevelFor`, `getSafetyPositionFromCrowd`, `getSafetyPositionsFromFireObjects`, `getSimPositionAwayFromknowledgeAgents`.
* **`SupportingIntegration.lua` [179]**: `firingRangeToSupport`, `firingRangeWith{Direct,Indirect}Fires`, `getSupportDistanceToCoordination`, `getPositionToSupportFriend`, `getPositionInAORToSupportFriend`, `computeSupportFriendEfficiency`.
* **`ControlableIntegration.lua` [97]**: start/stop `ControlArea`, `ControlUrbanBlock`, `ControlPoint`.
* **`SealableIntegration.lua` [146]**: `warnFriendsForBuildingObject`, `startSealOffArea` / `startedSealOffArea`, `startSealOffUrbanBlock` / `startedSealOffUrbanBlock`.
* **`ReinforcableIntegration.lua` [270]**:
  * `reinforcementLevel`, `isAgentKnowledgeReinforced`, `beginReinforcement{Platoon,AgentKnowledge,Company}`, `cancelReinforcement`;
  * `setNeedReinforcement` and the `setObjectNeedingReinforcementFor{Capacity,Dotation,NumberMissing,Action,Obstacle}` family, `setObjectStopNeedingReinforcement`;
  * `unitsAbleToReinforce`, `unitsAbleToWorkOnObject`, `nombreDotationAgent`, `agentHasDotationToImprove`, `canReinforceWithDotation`.
* **`RelievableIntegration.lua` [55]**: `canRelieveIt`, `relieveIt`, `isAgentInAutomatCanRelieveAgent`, `agentInAutomatRelieveAgent`.
* **`AskableIntegration.lua` [110]**: start/update/stop `InterrogatePion` and `InterrogateCrowd`.
* **`UndergroundNetworkIntegration.lua` [148]**: `EnterInUndergroundNetwork`, `ExitFromUndergroundNetwork`, `Enable`/`DisableUndergroundNetwork`, Start/Update/Stop `PassThroughUndergroundNetwork`, `enterAndExitInSameUndergroundNetwork`. These names start with a **capital letter**.

### 4.3 Detailed breakdown of the large files

#### `ReachableIntegration.lua` [1996]: movement

* **Positions** (`:83-275`): `getTeammatePosition(platoon)`, `getBodyPosition()`, `getRessourceNodePosition`, `getPointPosition`, `getItineraryPosition`, `getObjectPosition[s]`, `getEngineerObjectPosition`, `getAreaPosition`, `getCentralAreaPosition`, `getAreaPerimeterPositions`, `getUrbanBlockPosition[s]`, `getUrbanBlockNearestBorder`, `getAreaPositions`, `getPointPositions`, `pointIsInCity`. Plus aliases `getKnowledgeAgentPosition = DEC_ConnaissanceAgent_Position` and `isAgentOnWater`.
* **Distance / proximity**:
  * `distance(k1, k2)` calls `k:getPosition()` and **throws if a position is nil** (`:275-290`).
  * `proximity(k1, k2, defaultMax=4000)` returns a value in 1..100.
  * `normalizedInversedDistance`.
  * Aliases: `integration.copyPoint = DEC_Geometrie_CopiePoint` (`:1630`).
* **Pace**:
  * `setMovementPace(modulation, maxSpeed)` (`:394-453`) combines `myself.speedModulations.{safety,cover,slowDown}Modulation`, halves the speed in town, and floors it at 0.01.
  * Also `setPace`, `speedMaxModulation`, `speedCurrentModulation`.
* **Move triplet**:
  * `moveToItGeneric = spark.startStopAction{ start = startMoveToIt, started = updateMoveToIt, stop = deselect/stopMoveToIt }` (`~:538-556`).
  * **`startMoveToIt(objective, pathType, waypoints, ignorePreparation, computeArrivalTime, looped)`** (`:599-697`):
    * It resumes a paused `objective[myself].moveAction` if one exists.
    * It stops `myself.actionOccupy`.
    * Unless `ignorePreparation`, it undeploys and equips NBC if the destination is contaminated.
    * It computes a trafficable substitute destination if the target is in a non-trafficable urban block, and sends `"CannotEnterUrbanBlock"` to the company.
    * It builds the itinerary with `_DEC_CreerItineraireBM`, `_DEC_CreerItineraireListe` or `_DEC_CreateItineraryForLoop` (pathType defaults to `eTypeItiMouvement`).
    * It starts the action with `_DEC_StartDeplacement(myself, itinerary, false)` or `_DEC_StartLoopedMovement`.
    * It registers `actionCallbacks[id] = function(arg) objective[myself].etat = arg.first; objective[myself].object = arg.second end`.
  * **`updateMoveToIt(objective, pathType, waypoints, wantToStopBecauseOfObstacle, computeArrivalTime)`** (`:780-~1097`) switches on `objective[myself].etat`:
    * `Termine`: handles dismount when entering non-trafficable urban terrain.
    * `Pause`: handles mount/dismount resume.
    * `EnCours`: re-paths on `myself.updateItinerary` and mounts when leaving urban terrain.
    * `ManqueCarburant`, `NonAutorise`: the latter reports `eRC_TerrainDifficile`.
    * `BloqueParObjet`: sends `"ArrivedOnMines"` / `"subordinateIsBlockedByObstacle"` and reports `eRC_AskForEngineerSupport`, `eRC_ObjectCannotBeBypassed` / `Removed`, `eRC_PathIsClear`.
    * `DejaEnDeplacement`, `CheminPartiel`, `Teleporte`.
    * It also reports `eRC_ComputingPath` after 3 ticks of pathfinding and `eRC_PathComputed`.
    * It restarts the move when `myself.newMission` is set.
  * `stopMoveToIt(objective)` (`:1111-1137`) stops the move and the mount/dismount actions and clears `myself.isMoving`, `movingOnPath`, `currentObstacle`, `currentItinerary`, …. `deselectMoveToIt` only *pauses* (used when waypoints are given).
* **Variants**: `updateMoveToItArea` (moving target), `startMoveToItItinerary`, `computePath`, crowd moves (`start/update/stopMoveToItCrowd`, `startMoveToListPointCrowd`), `startMoveToItArea`, `magicMove`, `startPathFindComputation`.
* **Itinerary timing**: `getPathState` (pcall around `DEC_Itineraire_Etat`, returns `ePath_*` or nil), `timeItinerary` (deprecated), `timeCurrentItinerary`, `computeTimeOnItinerary`.
* **Terrain predicates**: `isUrbanBlockTrafficable`, `isPointInUrbanBlockTrafficable[ForProxy|ForPlatoon]`, `isPointInDestroyedUrbanBlock`, `isPointInLocalisation`, `isPointInSimLocalisation`, `isPointTrafficable`, `getPositionsOutsideUrbanBlock`, `getCrossroads`, `creerItineraireAPartirListePoint`, **`isElementInAOR`**, `getPointsCategory`, `findSafetyPositions` / `getSafetyPositions`, `getAgentTimeToReachPosition`, `canChangeHeight`, `getAgentMovingAutonomy`, `getAgentAutonomyAtMaxSpeed`, `getPlatoon[Ally]Position`, `renfortMouvement` / `annuleRenfortMouvement`, `getTerrainPositionsOnPath`, `getAgentMovementAutonomy`.
* **State it keeps**:
  * `objective[myself] = { moveAction, etat, object, computingTicks, longPathfindComputation, rcDone, isFlying }`;
  * `objective.destination` / `.initialeDestination`;
  * `myself.{currentItinerary, isMoving, mount, dismount, isMounting, stayDismounted, dismounted, enteringNonTrafficableElement, movingOnPath, updateItinerary, speedModulations, blocked, actionOccupy, restartLoopWhenDone}`.

#### `EquipmentIntegration.lua` [2204]: self-state and equipment (the biggest file)

* **Resources**: `hasDotation`, `hasDotationForFiring`, `canUseDotation`, `startConsumeResource` / `updateConsumeResource`, `startConsumeDotation` / `pauseConsumeDotation`, `start/update/pauseSupplyObjectFromAgent`, `movingAutonomy`.
* **NBC**:
  * `isContaminated`, `isDecontaminationDone`, `getDecontaminationActionState`, `isPoisoned`, `isAgentNBC`, `goOnNBCAlert`, `equipNBCOutfit` / `unequipNBCOutfit` (and `…Secu`), `activate/deactivateAirFiltrationSystem`, `selfDecontaminate`, `forcerImmunisationNbc`, `start/stopImmuniserNbc`.
  * CBRN protection: `activateCBRNProtection` / `deactivateCBRNProtection` are `spark.startStopAction`s (`~:330-362`) that keep state on `agent[protection].actionActivProt`. Also aliases `getCBRNProtectionName`, `getCurrentCBRNProtection`, `isCBRNProtectionAutoActivable`, `getNextActivableCBRNProtection`, `getCBRNProtectionTransitionTime`, `getOwnedCBRNProtections`, `getCBRNAgentsAtPosition`.
* **Radio and sharing**: `switchOffRadio`, `switchEmitOnlyOffRadio`, `switchEmitOnly`, `switchOnRadio`, `activateJammingCounterMeasures`, `en/disabledSharedPerception[WithKnowledge]`, `enableSharingKnowledges…`, `communicateWithKnowledge`, `isPartialRadioState`, `getRadioState`.
* **Radar and sensors**: `switchOn/OffRadar`, `activate/deactivateRadar`, `start/stopActivateRadarTirIndirect`, `hasRadar`, `activate/deactivateRecording`, `activate/deactivateSensors`, `activeRadarOnLocalisation`, `disableRadarOnLocalisation`, `activate/deactivateSpecialSensors`, `…SensorsUponRequest`, `getSpecialSensorsMaxRange`. Plus aliases `activate/deactivateSpecialSensorsOfType` and the new **`isSpecialSensorsOfTypeEnabled`** (`cdc4d5e8e5`).
* **Attitudes**: `switchOnSafetyMode` / `switchOnCoverMode` / `switchOff…` set `myself.safetyMode` / `coverMode`, which `setMovementPace` reads.
* **Mount / dismount** (`:695-845`): `canBeFullyLoaded`, `agentCanDismount` / `Mount`, `shouldDismount`, `mountStarted` / `dismountStarted` (return `false` while acting, `true` when done; state in `myself.orderMount` / `orderDismount` / `stayDismounted`), `stopDismount` / `stopMount`, `agentIsMounted` / `Dismounted`, and short forms `canDismount`, `canMount`, `isDismounted`, `isMounted` (deprecated).
  * ✔ **Trap**: `agentIsMounted` (`:829`, → `_DEC_Agent_EstEmbarque` → `PHY_RoleAction_Loading::IsLoaded`) and `agentIsDismounted` are **both false** while a mount/dismount is in progress (`Actions/Loading/PHY_RoleAction_Loading.cpp:228-236`: `… && nState_ == eNothing`). There is **no `integration.isLoaded`**: model objects' `self:isLoaded()` means *transported by another unit* (`integration.isBodyTransported` → `_DEC_Agent_EstTransporte`, `LoadableIntegration.lua:366`).
* **Deployment**: `startDeploy`, `undeploy`, `isDeployed` / `Undeployed` / `Deploying` / `Undeploying`, `get(Un)DeploymentDuration`.
* **Drones and UAV**: `startActivateDrone` / `startedActivateDrone` / `stopActivateDrone`, `setAvailableDrones[ForFlyTrack]`, `filterAvailableDrones`, `companyHasAvailableDrones`, `numberOfAvailableDrones`, `checkFlyTrack`, `getTimeToFlyOverDistance`, `start/started/stopDeployDrone`, `isUAVExploited` / `Deployed`, `setUAVDeployed`.
* **Lending**: `StartLendTugs`, `StartGetTugs`, `LendAmbulances`, `GetBackAmbulances`, `StartLendVSRAM`, `StartGetVSRAM` (capitalized).
* **Misc**: `increase/decreaseResourceNodeProduction`, `enable` / `disable`, `isDead`, `changeHeight`, `isFlying`, `unitAltitude`, `isLogisticTypeUnit`, `isLogisticConvoy`, `meKnowledgeIsTransported`, `getMaxSpeed`, `getAgentDirection`, `getAgentsWith[out]HQ`, `getTotalCrewSize`, `getBrainID`, `getUnitPC`, `getWorstMeteoSpeedFactor`, `AgentIsMoving`.
* About 25 `@deprecated` markers.

#### `BuildableIntegration.lua` [1003]: engineering

* **Can-build predicates**: `canBuildObjectType[WithoutReinforcement]`, `canBuildNowObjectType`, `hasEnoughtDotationForObjectType[WithoutReinforcement]`, `hasDotationToBuildObject`, `canBuildObjectWhenLoadedWithLocation`, `unitBuildSameObstacleAtSameTime`, `buildLevel`.
* **Build triplets**:
  * `startBuildIt(plannedObject, objectType, distance=10)` (`:140-170`): it looks for an existing object of the same type within `distance` and resumes it (`_DEC_StartReprendreTravauxObjet`, for checkpoint restore) or creates one (`_DEC_StartCreateObject(myself, source, false)`). It registers both `actionCallbacks` (state into `object[myself].actionBuildState`) and `actionKnowledgeCallbacks` (sets `object.knowledge = CreateKnowledge(type, newObjKnowledge)`), sets `myself.hasStartedBuilding = true`, and reports `eRC_DebutTravaux`.
  * `updateBuildIt(object, returnActionDone)` (`:231-259`) returns a **report-id string**: `"eRC_RAS"`, `"eRC_ConstructionObjetImpossible"`, `"eRC_PasDotationConstructionObjet"` or `"eRC_FinTravauxObjet"`. It calls `displayReportForObjectAction(object, eDisplayReportForObjectActionConstruct)` periodically.
  * `stopBuildIt` / `stopBuildItUrbanBlock`.
  * Variants: `startBuildItInstantaneously`, `startBuildItKnowledge`, `startBuildItUrbanBlock`, start/update/stop `BuildItSecu`, start/update/stop `BuildObjectOnLocalization`.
* **Instant helpers**: `removeObjectInstantaneously`, `buildInstantlyCheckPointOn`, `destroyInstantlyCheckpointOn`, `deleteObjectWithSameLocalisation`, `buildInstantlyObjectOn`, `buildInstantlyPolyligneOnArea`, `destroyInstantlyObjectOn`, `build/destroyInstantlyDecontaminatePlotOn`, `animateDecontaminatePlot`, `createDynamicObject`.
* **Object queries**: `obtenirObjetProcheDe[Position]`, `getImplantationObjects`, `getCollidingObjectsFromType`, `getBarycentreZone[FromLocalisation]`.
* **Crowd-related**: `doFiltration`, `changeCrowdDensity`, `setBodySearchIntensity`, `disarmCrowd`, `start/stopFilterCrowds`.
* **Mobility and bridges**: start/update/stop `AffectMobility`, `unAffectMobility`, start/update/stop `EquipBridge`, `unEquipSite`.
* **Urban blocks**: `get/setUrbanBlockState`, `getTypeUrbanBlock`.

#### `LoadableIntegration.lua` [1099]: transport

* **Bookkeeping on the transporter (default `myself`)**: `addToLoadedUnits` / `addListToLoadedUnits` / `removeFromLoadedUnits` / `removeListFromLoadedUnits` (set `transporter.loadedUnits[unit]=true`), `add/removeFromCapturedUnits` (list `transporter.capturedUnits`), `add/removeFromLoadedCrowds`.
* **Instant operations**: `dischargeAgent[Knowledge]`, `load/unloadFriendOrFoe`, `isFriendOrFoeTransported`, `load/unloadFriend`, `boardElementsWithoutDelay`, `unboardElementsWithoutDelay`, `transferElementsWithoutDelay`.
* **Queued, time-consuming operations**: start/update/stop `LoadQueue` and `UnloadQueue`, `addPlatoonInQueue`, `addKnowledgeInQueue`, `isTransportFinished`.
* **Crowds**: `canTransportCrowd`, `getCrowdTransportCapacity`, `transportCrowdRoundTrip`, start/started/stop `LoadCrowd` and `UnloadCrowd`, `isTransportingCrowd`, `canTransportCrowdConcentration`.
* **Capability and timing** (recent work SW-27111/SW-28116): `canTransportUnit`, `canTransportAnyEquipmentOfUnit`, `canTransportKnowledge`, `canAgentTransportKnowledge`, `transportUnitRoundTrip`, `transportKnowledgeRoundTrip`, `getNumberRoundTripsLeftForAgentKnowledge`, `hasPhysicalAbilityToTransportCargo`, `getCargo(Un)LoadingTime`, `getWeight`, `getHeaviestEquipmentWeight`, `getWeightOfEquipment`, `getRequiredTimeTo(Un)loadAgent`, `canLoadCompletely`, `couldFullyLoadUnits`, `canLoad[Knowledge|Friend]`, `canUnitTransportUnit`, `agentPeutTransporterPion`.
* **Status**: `isBodyTransported`, `isFriendTransported`, `isTransported`, `friendHasTransportationMission`, `unitHasTransportationMission`, `readyForLoad`, `knowledgeReadyForLoad`, `allowCarriers` / `retrieveCarriers`, `isAgentSurrendered`, `isKnowledgeAgentSurrendered`, `is[Knowledge]AgentARefugee`, `isDuringTransport`, `are/setSuppliesLoaded`.

#### `LogisticIntegration.lua` [1050]

* **Convoy and hauler APIs**, intended for the logistic convoy brains: `getActionCouranteConvoi`, `getRavitailleur`, `getDestinataireCourant`, `getTransporter`, `getItineraireVersProchaineDestination`, `getNextEntryPoint`, `deplacementVers{Ravitailleur,Destinataire,Transporteur}Effectue`, `finMissionConvoi`, `getHaulerRouteToNextDestination`, `notifyHaulerIsArrived`, `getCurrentHaulingRequestState`, `getNextVehicleProvider`, `getAmbulanceRouteToNextDestination`, `notifyAmbulanceIsArrived`, `getCurrentAmbulanceRequestState`. These go with the enums `eConvoyerAction_*` / `eHaulingAction_*` / `eAmbulanceAction_*`.
* **Equipment and stock**: `equipmentTypeCanTransportStock`, `equipmentMaxTransportable{Weight,Volume}`, `equipmentWeight`, `startGiveEquipment`, `giveEquipmentOfType`, `startTakeEquipment`, `stockCanBeDistributed`, start/update/stop `(Un)LoadEquipmentQueue`, `startConsumingResources`, `startProducingStock`, `triggerBreakdown`, `ensureVictimsEvacuation`, `supplyRequest`, `changeResourceValueUsingCombatTrains`, `enforceAviationResourcesFromRange`.
* **Chains and priorities**: `activate/desactivateLogisticChains`, `activate/deactivate{Health,Maintenance,Suppply}Chain` [sic: "Suppply"], `activate/desactivateInjured{Sorting,Treatment}`, `changeMaintenanceWorkMode`, `changeTactic{Healt,Maintenance}Priority` [sic: "Healt"], `changeMaintenancePriority`, `changeHealthPriority`, `modifyHumanWoundPriority`, `get{WorkRegime,TacticalPriorities{Health,Maintenance},Priorities{Maintenance,Health}}` plus `…Exist` variants, `isLogSupplyPushFlow`, `isLogisticAutomat[Exist]`, `isMedical`, `getHQofTC2`, `getHQknowledgeOfTC2`.

#### `LeadIntegration.lua` [1479]: company "Lead" skills (task distribution to platoons)

* **Entry points** called from sw.models Lead skills with `self` = the skill node: `integration.leadCreate(self, functionsToExecute, findBestsFunction, disengageTask, givePCTask, giveEngineerTask, giveMainTask, giveSupportTask, givePEITask, giveDefaultTask, giveTaskIfHQIsDead, giveTaskIfHQIsJammed, useDefaultValues)` (`:953`), `leadActivate` (`:1169`), `leadDelayActivate` (`:1292`), `leadDroneActivate` (`:1421`), `leadDestroy(self, setEchelonNone)` (`:1456`).
* **Contract with the company task knowledge** (`self.companyTask = integration.getAutomatonTaskKnowledge(meKnowledge, self.params.companyTask)`):
  * methods `init(params, parameters)`, `getNbrFront()`, `distributeObjectives`, `readyToGiveDynamicTasks`, `isDynamic`, `getObstaclesPlan`, `getObstaclesParameter`, `communicateWithSubordinates`, `canMissionEnd`, `destroy`;
  * platoon task knowledges must implement `fillParameters(companyTask, params, entity, context, …, taskName)`.
* **Skill `self.params` keys read**: `companyTask`, `mainTasks` / `supportTasks` / `peiTasks` / `pcTasks` / `droneTasks` (semicolon-separated DIA type lists), `defaultTask`, `engineerTask`, `withPC`, `allInFront`, `everyoneMustBeAble`, `restartMissionIfDead`, `taskForSupporting`, `taskForReinforcement`, `manageReinforcement`, `additionalStage`, `forbidFlyingAgentsFromLanding`, `continueIfNoMainTask`, `withScoutEchelon`, `withEquipment`, `reportWhenMainTaskFinished`, `relieveManager`, `performBlockingActions`, `pcObjective`, `noCoordination`, `mutePhaseLineReports`, `manageAddedAndDeletedUnits`, `disableWhenDone`.
* **Helpers**:
  * `issueMission(self, tasks, nbrFront, echelon, entities, isMain, findBestsFunction, disengageTask)` (`:406`) sends via `meKnowledge:sendTaskToPion(entity, taskName, params, echelon)` and diverts non-operational units to `disengageTask`.
  * Also `findBestsGEN`, `findBestsBuild`, `removeDetachedUnits`, `optimizeWorkMap`, `manageEndMission`, `manageDynamicTask`, `distributeObstacles`, `allocateElements` (optimizer), `setEchelon`, `manageFeedback`, `fillWorkMap`.
* **State**: `myself.leadData = { paramsGiven, workMap, tasksGiven, finishedSubordinates, taskError, mainTasksFinished, platoonHasTask }`, plus `myself.leadDataCount`. The count is a reference counter since `3f46b28cd3`, because Lead create and destroy calls can interleave when stages chain. Also `myself.feedback`, `meKnowledge.pionsToAwait`, `meKnowledge.nbPionsMain`.
* **Reports**: `eRC_EveryoneMustBeAble`, `eRC_FinMission`, `eRC_MainTasksFinished`, `eRC_MissionImpossibleUnitesSubordonneesNonOperationnelles`, `eRC_NoPEInAutomat`.
* **Messages**: `"DeactivateBehaviour"` / `"ReactivateBehaviour"`. The `"TaskDone"` / `"CleanUp"` listeners live in CommunicationIntegration.

#### `CommunicationIntegration.lua` [802]: orders and messages

* **Mission creation for subordinates**:
  * `initializeAssignMissions()` (`:78`) builds `myself.ParameterRegistrationFunctor[type]` and `ParameterListRegistrationFunctor[type]`, mapping ontology types and Lua types (`number`, `userdata`, `boolean`, `string`) to `DEC_AssignMission*Parameter`.
  * `AssignMissionParameter` (`:133`) walks `spark.getSupertype` until it finds a mapping, or errors "No parameter assignment for …".
  * Path parameters are coerced to points (`DEC_Mission_IsPath`).
  * Local `fillParameters` (`:195`).
* **`integration.communication.*`**: `StartMissionPion(content{mission_type, echelon, mission_objectives})`, `StartMissionPionVersPion`, `OrderMoveToFromCommander(missionName, waypoints)`, `OrderPerformWorkFromCommander(missionName, works, instantaneously)`, `StartMissionAutomate(content{entity, taskName, params})`, `StopMovingInFrontOfPE`, `SlowDown` / `Continue` (`myself.slowDown`, `myself.speedModulation.coordination`), `FollowMe`.
* **Messaging**: `integration.SendMessage(type, receiver, message)` (`:388-400`):
  * it returns false and traces if `not spark.isRegistered(receiver)`;
  * it adds `message.__senderID = DEC_GetID(myself)` inside a `pcall`, because the message may be the read-only `emptyTable` (commits `e5d69dded7`, `11305e9d13`);
  * then it calls `spark.send`.
  * Global listeners: `spark.setGlobalMessageListener("TaskDone", …)` maintains `myself.finishedSubordinates`, `myself.leadData.finishedSubordinates`, `myself.feedback`; `"CleanUp"`.
* **Task knowledge**: `getAutomatonTaskKnowledge(entity, taskName)` (errors if the task is not in the DIA5 role), `getAgentTaskKnowledge(entity, diaType)`, `isMissionAvailable`, `isFragOrderAvailable`, `isDirectiaKnowledge` (= `spark.isInstance`).
* **Misc**: `startFragOrder = DEC_StartFragOrder`, `ListenFrontElement` / `initializeListenFrontElement`, `getAgentFromKnowledge`, `DEC_GetMission` / `getMission` / `stopMission`, `getUnitsWithoutHQCommunication`, `giveCommanderTask`, `getAORFromCommander` / `Platoon`, `getEchelonState`, `getKnowledgeAgentDecisionalState`, `getAnyType`, `RetrieveAutomatInBatallionTask`.

#### `FragOrderIntegration.lua` [747]

* **Parameter getters** all follow one pattern: `getParameter[WithName](fragorder.source.GetXParameter, fragorder[, name])` **wrapped in pcall, returning nil if the parameter is absent** (`:15-30`). The methods come from `MIL_FragOrder::Register` (`DEC_Decision.cpp:~271`). Examples: `getIntegerParameter`, `getPointParameter`, `getAgentKnowledgeParameter`, `getObjectParameter`, `getLocationParameter`, `getMunitionParameter`, `getNbItParameter`, `getpointCibleParameter`, `getRequesterParameter`, …, plus the `getOrderConduite*Parameter` family (French "ordre de conduite" names).
* **Lifecycle**: `cleanFragOrder(fragOrder)` (`:39`) queues into `myself.fragorderToClean`. **The actual deletion happens at end of tick** in `RegisterTaskListener.OnEndTick` (`CRTaskListener.lua:~216`), which removes it from `myself.fragOrders` and calls `_DEC_RemoveFromOrdersCategory` and `_DEC_DeleteRepresentation`. `getOrdersCategory()` lists the received frag orders. Also `createFragOrder`, `getOrderType`, `getFragOrderName`.
* **ROE**: `updateROE` (consumes the frago, honors `meKnowledge.canEngageInMission` "Scipio compatibility"), `setROE` (never relaxes below `myself.ROEGivenByFragOrder`), `getROE`, `setCompanyROE`, `CR_ROE` / `CR_ROE_Foules` / `CR_Attitude_Foules` (reports `eRC_TirLibre` / `TirRestreint` / `TirInterdit`, …), crowd ROE changes.
* **Fire**: `getFireParameters`, `getLinearFireParameters`, `disableSmoke` (`myself.disableSmoke`).

#### `GeometryIntegration.lua` [956]

* **Mostly aliases** to `DEC_Geometrie_*`: `positionTranslateDir`, `locationInPoints`, `isPointInAOR_WithParam`, `computeDistance = DEC_Geometrie_Distance` (`:203`), `createLocationCircle`, `computeLocationBarycenter`, `isPointInsideLocation`, ….
* **Lua algorithms**: `conformPositionsToAOR` (`:90`), `advanceAlongAOR`, `advanceAlongDangerDirection`, `positionAdvanceAlongAOR`, `positionAdvanceAlongFuseau`, `computePointNearLocationsInsideAOR`, `createSectorFromAngle`, `geometryX` / `Y`, `computeSafetyPosition`, `splitAORInSections`, `computeMiddlePointsInAOR`, `clipperLocalisation`, `convertAORtoLocalisation`, `randomPositionIn/OnCircle`, `splitArea`, `geometrySplitLocalisation`, `isTerrainIn`, `computeParametersUrbanRatioForCompany`, `recursiveSplitLocalizationSurface`, `splitListPoints`, `intersectingAreas`, `computeBarycenterForKnowledgeAgents`, `distancePointToPolygon`, `polylabel` (pole of inaccessibility, `:819`).
* `distancePointToLine` was moved into C++ (`5ef4708146`, `1a9d8b7625`).

#### `Unit.lua` [854]

* **Identification**: `identificationPriority`, `canIdentifyIt`.
* **Decisional and operational state**: `setDecisionalState(category, value, agent)` (`_DEC_DecisionalState`), `setForceRatioState`, `setOperationalState`, `getAgentMajorOpsState`, `getAgentOpsState`, `getOperationalState`.
* **Knowledge searches**: `getAgentKnowledgesInArea`, `getAllAgentKnowledgesInArea`, `getFriendsInArea`, `getKnowledgesLivingAgentsInCircle`, `unitesDetecteesDansFuseau`, `getWoundedOrDeadUnitsInCircle`, `getCiviliansInArea`, `getWoundedInArea`, `getNearbyTerrorists`, `getCollidingCrowds`.
* **Flying**: `getStandard/TacticalFlyingHeight`, `getMaximumFlyingAltitude`, `agentIsFlying`, `agentCanFly`, `isInLandingArea`.
* **Activity**: `deactivate` / `reactivate` / `isInactive`.
* **Misc**: `threatLevel`, `fleePriority`, `getName`, `getFiringDistanceToEngageMe`, `UnitIsNeutralized` / `UnitIsDead`, `getBoolVariable` / `getIntVariable`, `unit_commandEfficiency`, `unit_communicate`, `getSupplyTransferRate`, `getInterceptionPosition`. Deprecated predicate table: `isIdentifiedPredicate`.

#### `Object.lua` [787]

* **Ids and knowledge**: `getObjectId`, `getKnowledgeFromObjectID`, `createResourceLinkWithObject`, `isConstructed`, `isObjectRemoved`.
* **Border and positions**: `getPositionOnBorder`, `getObjectNearestBorderPosition`, `get[Planned]ObjectNearestPositionOnBorder`, `getObjectPositionsForWork`, `getPositionsAroundObject`, `getPositionOutsideObjects`.
* **Spatial queries**: `getObjectsKnowledgeInZoneWithCapacity`, `getObjectsInCircle` / `ContainingPosition` / `InArea`, `getKnowledgesObjectsInCircle`, `objectKnowledgesInCircle`, `hasCapacity[Gen]KnowledgeObject`, `getNextObjectOnPath[WithBypassed|NotSurrounding]`.
* **Capability checks**: `canFilter`, `canDecontaminateAgents`, `canBerth`, `isCamp`, `isObjectTrafficable`.
* **Decontamination**: `addKnowledgeToDecontaminationList`, `requestForDecontamination`, `addToDecontaminationList`, `genObjectNeedsImprovement`, `objectNeedsImprovement`.
* **Three anonymous `spark.startStopAction{start, started, stop}`** blocks (`~:520-630`; names not captured).
* `displayReportForObjectAction(object, eDisplayReportForObjectAction*)` (`:647`): periodic "time left" reports.
* **Heights and environment**: `getInteractionHeights`, `getInteractionAltitudesAt`, `canAlwaysExit`, `areOceanicEffectsBlockingObjectOperation` (`94438cb7df`, returns a bitfield).
* A "DECLARATIONS ENSURING BACKWARDS COMPATIBILITY" section (`:776+`) keeps the deprecated names `getKnowledgeGenObjectLocation` and `isTrafficable`.

#### `TaskGroupIntegration.lua` [554]: task groups (newer subsystem, SWNG tickets)

* **Everything lives under `integration.taskgroup.*`, and functions take the task group explicitly** (usually `myself` in a task-group brain). The header comment states the rule: "Methods taking an echelon (name) as parameter are expected not to fail. They must return default values … or do nothing" (`:5-8`).
* **Mostly aliases**:
  * movement: `startMove(taskGroup, destination, keepDistanceOnArrival, retrograde, offsets, startPosition)` (`:47`, a wrapper defaulting `startPosition`), `startMoveAlong`, `startStealth`;
  * echelons: `changeEchelon{SensorOrientation,DispersionFactor,FormFactor,MovementOrientation,MovementOffset}`, `getEchelonNames`, `getMainEchelonNames`, `getEchelonFormFactor`, `getOrientationAndFormFactor`, `getEchelonOperationalState`, `getOperationalState` (`e1b5082da5`), `computeEchelonDispersionForSurface`;
  * movement settings and position: `setMovementRoadFactor`, `setSpeedModifier`, `isInContact`, `isArrived`, `getDefense/OffensePosition`, `getDefense/OffenseDepth`, `getPosition`, `echelonPosition`;
  * flying: `isEchelonFlying`, `canEchelonFly`, `get/setEchelon…FlyingHeight`, `lockFlight`;
  * fire and ROE: `setFreeFire`, `setRetaliateOnly`, `setForbiddenFire`, `setROE` / `getROE`, `add/removeHighPriorityTarget`, `displayHighPriorityTargetList`, `indirectFireOnPoint`, `hasAvailableLauncherFor`, `getNumberOfDotationsFor`, `isInRangeFor`;
  * maintenance, objects, transfers: `changeMaintenance[Tactical]Priorities`, `setObjectsToBuild[OnEchelons]`, `startTransferEquipment`;
  * transport: `numberOfTripsToTransport`, `cancelLoadTaskGroup`, `isTransportComplete`, `getTimeToCoverDistance` (`51eeb1cee9`), `startEmbark` / `startDisembark` (task group on a unit, `24ccbc0459`).
* `loadTaskGroup(transporter, transported, includeVehicles)` and `unloadTaskGroup(transporter)` (`:459,504`) are **`spark.startStopAction`s** that return `eActionTransport_*` and keep state in `transporter.loadAction` / `.loadActionState`.

#### `CrowdIntegration.lua` [799] and `CrowdBodyIntegration.lua` [447]

* **CrowdIntegration** is the agent side, acting on crowd knowledges:
  * attitude and engagement: `getAttitudeCrowd`, `getCrowdInformations`, `controlItCrowd`, `fire{Forbidden,Allowed}OnCrowd`, `wlrAllowedOnCrowd`, `start/stopShootingOnCrowd`, start/update `ControlCrowd`, start/update/stop `InterveneOnCrowd`, `getDomination`, `getDangerosityLevel`, `affinityLevel`, `start/stopAgress[Crowd]`;
  * membership and concentrations: `isInCrowd`, `isKnowledgeInCrowd`, `getCrowds`, `isCivilian`, `getNearbyConcentration`, `getPositionConcentration`, `getCrowdBarycenter`, `getNbPersonConcentration`;
  * casualties: `extract{Victims,Dead}FromCrowd`, `extractFromCrowd`, `crowdHas[Only]{Wound,Dead}Humans`, `getNbPeopleInCrowd`, `healWoundedInCrowd`, `getNbWoundedInCrowd`;
  * knowledge handling: `getCrowdsInArea`, `lock/unlockCrowdKnowledge`, `isKnowledgeCrowdValid`, `get/setCrowdKnowledgeExtension`;
  * hiding and positions: `start/stopHidingInCrowd`, `getAgentsHiddenInCrowd`, `getHeadPosition`, `getCrowdPosition`, `hasReachedDestination`;
  * setters: `setDensitePopulationSortante`, `setNombreIndividuArmeDansFoule`, `changeAttitude`, `channelIt`.
* **CrowdBodyIntegration** is the crowd brain side: `setAttitude` / `getAttitude`, `unitIsInCrowd`, `crowdRC` (crowd report function), start/update/stop `AttackingIt`, `hasReachUrbanBlock`, `hasEntirelyReachedDestination`, `manageInteractionsWithUnits`, `FouleEstSecurisee`, `get/setDomination`, start/update/stop `Demonstrate`, `crowdIsContamined`, `getCrowdDecontaminationDelay`, `getCrowdContaminatedHumans`, `damageObject`, `crowdAgentHasFlow`.

#### Security / asymmetric / misc

* **`AsymetricIntegration.lua` [346]**: `setStealth` / `isStealth`, `porteePourAttentat`, start/update/stop `AttackIt`, `commitSuicide`, `attackObject`, `killOfficers`, `isInFiringRangeForDotation`, `takeAsRefugee` / `takeAsPrisoner`, `capture` / `captureEnd`, `getCapturedUnits`, `dropUnit`, `shareKnowledge[Agent[FromAutomat]]`, start/update/stop `LaunchProjectile`, `knowledgeIsTerrorist`, `attackReport`.
* **`UrbanBlock.lua` [348]**: `alert` / `undoAlert` / `isUrbanBlockAlerted`; two startStopAction blocks for confine and evacuate plus `undoConfine` / `undoEvacuate` / `is…`; `decontaminateUrbanBlock`, `createResourceLinkWithUrbanBlock`, `isAgentInsideTown`, `getUrbanBlockInArea` / `InsideOrIntersectArea` / `InsideArea` / `FromPosition`, `start/stopDamageUrbanBlock`.
* **`Area.lua` [42]**: `decontaminateArea`, `getAreaPosition[s]Secu`, `getCircleAreaAroundPosition`.
* **`Fire.lua` [87]**: `getBurningState`, start/update/stop `Extinguish`, `canBeExtinguished`.
* **`LimaIntegration.lua` [208]**: phase lines. `getLimaFromType`, `getLimasFromType`, `getPointsLimas`, `computePositionsRelativeToLima`, `set/getMissionLimaFlag`, `setMissionLimaFlagSchedule`, `getLimaRealTime`, `removeFromPointsCategory`.

---

## 5. Recurring idioms and gotchas

### 5.1 Simulation actions

* **Start** a C++ action with `_DEC_Start*` / `DEC_Start*` / `DEC_StartAction_*`. It returns an **unsigned action id**, created through `StartAction<PHY_ActionX>(caller, args…)` (`DEC_ActionFunctions.h:39-40,167+`). The action runs on the C++ side every tick until stopped.
* **State**: there are two channels.
  1. **Generic**: `_DEC_EtatAction(myself, id)` / `DEC_EtatAction(id)` / `utilities.getEtatAction` return `PHY_Action_ABC::E_State` = `eError 0 | eDone 1 | eRunning 2` (`Entities/Actions/PHY_Action_ABC.h:26-31`). An unknown id returns `eError` (`DEC_ActionFunctions.cpp:338-343`).
  2. **Action-specific callback (the norm)**: C++ calls `CallbackAction(id, value)` → `actionCallbacks[id](value)`. Integration registers the callback right after the start. The value's meaning depends on the action:
     * move: `arg.first` = an `eEtatActionDeplacement_*` value, `arg.second` = the blocking object;
     * objects: `eActionObjet*`;
     * transport: `eActionTransport_*`;
     * direct fire: `eActionTirDirect_*`;
     * indirect fire: `eIndirectFireState_*`;
     * illumination: `eActionIllumination_*`.
     * Object-creating actions also call `KnowledgeCallbackAction(id, objectKnowledge)`.
* **Stop** with `_DEC__StopAction(myself, id)` (note the double underscore), `DEC__StopAction(id)` or `integration.stopAction(id, entity)`. C++ `UnregisterAction` runs only if `id ~= 0`, and the function **always returns 0** (`DEC_ActionFunctions.cpp:314-327`). C++ then calls `RemoveAction(id)`, which clears the callback tables.
  * **Gotcha:** the idiom `x.action = _DEC__StopAction(myself, x.action)` sets the field to `0`, which is **truthy in Lua**. Code that later tests `if x.action then` still enters the branch. It is harmless only because stopping id 0 is a no-op. Prefer `= nil` after stopping, as `stopMoveToIt` does.
  * Also: `_DEC__StopAction(myself, …)` stops actions *registered on myself's entity*. In TaskGroupIntegration, the action was started on `transporter` but is stopped with `myself`. That only works when `transporter == myself`.
* **Pause / resume**: `_DEC_PauseAction` / `_DEC_ReprendAction` (Suspend / Resume). Used for movement while mounting.
* **Triplet convention**: `integration.startXIt(target, …)`, `integration.updateXIt(target, …)` (call every tick; returns a done flag or a report id), `integration.stopXIt(target)`.
* **Spark wrapping**: `spark.startStopAction{ start=…, started=…, stop=… }` returns a callable that Spark skills invoke **every tick**. Spark calls `start` the first tick, `started` on following ticks, and `stop` when the calling skill/node stops calling it (13 uses; e.g. `moveToItGeneric`, `reportOnceAction`, `activateCBRNProtection`, `taskgroup.loadTaskGroup`). The exact semantics of `started` versus re-call are defined by the Spark plugin, outside this repo (unverified detail).
* **Per-caller state on shared knowledges**: `objective[myself] = objective[myself] or {}`, then store `moveAction`, `etat`, `actionBuild`, `actionBuildState`, …. Knowledges come from `CreateKnowledge`, which caches per brain, and **are only kept alive while referenced** (weak `knowledgeCache`). Keep references in skill `self` fields, as the comment at `SimulationIntegration.lua:500-508` says.

### 5.2 Reports

* `reportFunction( "eRC_X", params… )` uses the entity-appropriate function. For agents (`integration.pionRC`, `CRTaskListener.lua:192`) it emits a **report** if the automat is disengaged (`not _DEC_Agent_AutomateEstEmbraye`), and otherwise a **message** (`DEC_Message`, type 0). The C++ side uses typed emitters: `type_message=0`, `type_rc=1`, `type_event=2`, `type_warning=3` (`:69-72`).
* `integration.report(id, …)` / `integration.reportWithSource(emitter, id, …)` → `integration.displayReport(emitter, type, id, {params})` (`:135-~183`):
  * it looks up `DEC_RC_GetReportParameterTypes(id)` (cached per id);
  * it converts each Lua parameter with `reportParamFunction[type]` (Agent, AgentKnowledge, Point, Location, Integer, String, … `:25-64`). `List` parameters must be lists of lists;
  * **too few parameters triggers a `DEC_Trace` warning and the report is silently dropped**; a nil parameter makes it return silently;
  * an unknown parameter type raises an error.
* **Variants**:
  * `reportOnceFunction(...)` only on the first consecutive tick;
  * `meKnowledge:displayReportOnce(...)` (sw.models side);
  * periodic progress via `displayReportForObjectAction` / `displayReportForReco` with `eTimeRCObject=600` s;
  * typed legacy emitters `integration.RC_*` = `DEC_RC_*` (deprecated);
  * `DEC_RC = integration.report` (deprecated).
* **Ids are strings.** `"eRC_X"` literals and the generated globals `eRC_X` are interchangeable. `DEC_RC_IsReportDefined(id)` lets code fall back gracefully (used for `eRC_BM_EndMissionFailed`, `CRTaskListener.lua:~285`).
* **Mission lifecycle reports are emitted automatically** by `RegisterTaskListener`:
  * `eRC_BM_DebutMission` (willStart of the current mission);
  * `eRC_BM_DebutPhase` / `eRC_BM_FinPhase` (stages with a non-empty label; stage id 0 = parallel stage, ignored);
  * `eRC_BM_FinMission` or `eRC_BM_EndMissionFailed` + `_DEC_FinMission` (willStop).
  * Do not duplicate these in skills.

### 5.3 State across ticks

| Lifetime | Where |
|---|---|
| Per entity, all ticks, lost on checkpoint | fields on `myself` (`myself.isMoving`, `myself.leadData`, …) and on `meKnowledge` (`meKnowledge.currentTask`, …) |
| Per entity + objective | `knowledge[myself].*` |
| **Survives checkpoints** | `integration.getSkillState(self)` / `setSkillState` / `setSkillStateElement` / `getPersistentState(skill, key)`. Saved by `savePersistentState`; values must be serializable (nil/boolean/number/string/table). Knowledges need a `:hash()`. |
| Per tick | `myself.newMission` (true on the tick a mission starts, reset in `OnEndTick`); frag orders queued to clean |
| Per brain (Lua locals) | `spark.createBrainLocalStorage()` (`localStorageVar`, `persistentState`) |
| Global per Lua state (beware: archetype or copy sharing, unverified) | `knowledgeFactory` (CreateProxyKnowledge), `integration.listenFrontElementCallbacks[meKnowledge]` (keyed by meKnowledge for that reason), `actionCallbacks` |

* `OnNewTick` (registerUpdateCallback) handles delayed stealth visibility (`myself.stealthTick`).
* `OnEndTick` resets `myself.newMission` and purges `myself.fragorderToClean`.
* The task listener's `Cleanup` resets `myself.engineerObjectsOnPath`, `myself.blocked` and `myself.safetyAttitude` when a task stops or finishes.

### 5.4 Nil handling and errors

* Frag order getters and `getPathState` use `pcall` and return `nil` on failure. Most other functions **do not guard**: an invalid knowledge raises a C++ exception, which surfaces as a Lua error.
* `installErrorHandler` traces `err .. traceback` via DEC_Trace.
* The brain has a **Lua timeout** (`SetTimeout(config.GetLuaTimeout())`, `DEC_Decision.h:~318`), so heavy Lua loops in integration functions can abort a brain.
* `integration.distance` explicitly errors on nil positions.
* Check for MASA Life's `NIL` in addition to `nil` for optional skill params.
* Functions documented "This method can only be called by an agent / a company" will fail in other brain types.

### 5.5 Units, coordinates and types

* **Simulation positions** are C++ `MT_Vector2D` (shared_ptr userdata) in **simulation metric coordinates (meters)**. Lua never sees lat/long. `integration.geometryX` / `Y` give the components.
* **Distances** are in meters; `proximity` uses a default max of 4000 m.
* **Times**: `integration.getTimeInSecond()` returns sim seconds; `timeCurrentItinerary` returns seconds or nil while computing; `getTickDurationInSeconds()`.
* **Speed modulation** is a factor in ]0..1].
* **Areas** (`TER_Localisation`) are "Simulation area / location".
* **AOR** = "fuseau" (French).
* Unit conventions for each individual `DEC_` function are documented in the C++ headers. For example, flying heights in meters is unverified.

### 5.6 Naming

* **French and English DEC names coexist.** Older names are French: `DEC_Agent_EstEmbarque` (is mounted), `_DEC_StartDeplacement` (move), `DEC_Geometrie_*`, `DEC_ConnaissanceAgent_*`, `_DEC_ReprendAction` (resume), `DEC_EtatAction` (action state), `Fuseau` (AOR), `Lima` (phase line), `Pion` (platoon/agent), `Automate` (company/automat), `Dotation` (resource), `Embarquer` / `Debarquer` (mount / dismount), `Renfort` (reinforcement), `Contournement` (bypass), `Obstacle`.
* Newer additions are English: `DEC_GetOperationalState`, `_DEC_Perception_IsRadarEnabled`, `DEC_Agent_AreOceanicEffectsBlockingObjectOperation`.
* **Integration function names are mostly English camelCase**, with French leftovers (`obtenirObjetProcheDe`, `porteeMaxPourTirerSurUnite`, `niTropPresNiTropLoin`, `deplacementVersRavitailleurEffectue`, `agentEstBrouille`) and typos kept for compatibility (`hasEnoughtDotation…`, `fireNotForbbiden`, `activateSuppplyChain`, `changeTacticHealtPriority`, `integation.taskgroup.startStealth` in a doc comment).
* **Prefix meanings**:

| Prefix | Meaning |
|---|---|
| `_DEC_` | C++ caller-explicit (or Lua shadow) |
| `DEC_` | either C++ without caller, or a Lua wrapper |
| `DEC__` (double underscore) | historic action functions |
| `F_Pion_Get/Set*` | C++ accessors for agent "fields" (`F_Pion_SeteEtatEchelon`, `F_Pion_SetitMvt`) |
| `S_*` | crowd functions |

* **C++ methods on userdata**: `RegisterMethods` binds methods callable with `:` syntax, e.g. `fragorder.source:GetName()`, `DEC_Agent_EtatOps` as a method (`DEC_Decision.cpp:~100+`).

### 5.7 Deprecated material

* About 200 `@deprecated` markers across 33 files. The biggest concentrations: EquipmentIntegration 25, CRTaskListener 22 (the `RC_*` aliases), LogisticIntegration 13, ReachableIntegration 10, CrowdIntegration 10.
* The old predicate-table format `{ dependencies=…, method=function }` (KeyPoint, Unit, Observable, Neutralizable) is deprecated.
* C++ registers no-op stubs for removed actions, e.g. `_DEC__StartOccuperObjet` returns 0 (`DEC_ActionFunctions.cpp:~297`, "deprecated").
* The convention is to **keep deprecated names and alias them** to the replacement, as in Object.lua's backwards-compatibility section and DECFunctionWrappers' tail. Do not delete them.

---

## 6. Changing or extending the layer safely

### 6.1 Where a new function goes

* **Pick the capacity file that matches the target kind**: movement → Reachable, object work → Buildable / Removable / …, self equipment and sensors → Equipment, task group → `integration.taskgroup.*` in TaskGroupIntegration, terrain/geometry → Geometry, weather → Weather, pure table/string helpers → `utilities.*` in TableIntegration / ToolsFunctions (and add an lunit test).
* **Enums** go in `Types.lua`. Recent practice is to mirror C++ enum values and document them with `@see eX_xxx enum`.
* **Placement in the file**: before any "BACKWARDS COMPATIBILITY" tail. If your function is referenced at load time by another file, the defining file must be earlier in `Integration.lua`.
* **A new file** must be added to `Integration.lua` (it is otherwise never loaded) **and** to `integration/config.ld` (LDoc file list). Note that config.ld is already out of sync:
  * it lists `'Relievableintegration.lua'` with the wrong case;
  * it omits `DECFunctionWrappers`, `Types`, `KnowledgeManager`, `BaseTaskKnowledge`, `TaskGroupIntegration` and `Transmissions`.
  * Whether `ant integration-documentation` honors config.ld's `file` list is unverified: the ant `ldoc` task passes `dir=… input="."`.
* **Doc style**: LDoc comments.
  * `--- Summary.` (lines starting with `---` continue the summary)
  * `-- @param name Type, description`
  * `-- @return Type, description`
  * `-- @see integration.other`
  * `-- @deprecated Use X instead`
  * For direct aliases, add `-- @name integration.foo` and `-- @class function`.
  * State the caller restriction ("This method can only be called by an agent") and whether the parameters are "Direct AI knowledge" or "Simulation X". Commit `b52406eba2` / `b31fd4f88e` show that bad tags break the doc build.

### 6.2 Exposing a new C++ function (recipe from `cdc4d5e8e5`, `e1b5082da5`, `94438cb7df`)

1. Implement a static function in the right `src/libraries/simulation/Decision/DEC_*Functions.cpp/.h`. The first parameter is `DEC_Decision_ABC* caller` (or `const DEC_Decision_ABC*`) if it acts on or for an entity. Throw `MASA_EXCEPTION` / `std::runtime_error` on invalid input.
2. Register it in that file's `Register( sword::Brain& brain )`, which is called from `RegisterCommonUserFunctions` (`DEC_Decision.cpp:275-305`). Use `brain.RegisterFunction( "_DEC_Name", &Fn )`:
   * use the `_DEC_` prefix when the caller is explicit, `DEC_` otherwise;
   * use explicit template arguments or a `std::function` lambda for overloads and defaults (see `DEC_ActionFunctions.cpp:97-115`);
   * actions use `&StartAction< PHY_ActionX, Args… >`.
   * A new `DEC_*Functions` group needs its `Register` call added there.
3. Expose it in Lua:
   * preferably as `integration.niceName = _DEC_Name -- ( agent, arg )` with LDoc `@name` / `@class function`;
   * or as a wrapper function if you need `myself` injection or Lua logic;
   * add a `DEC_Name(...)` wrapper in DECFunctionWrappers only if models are expected to call the caller-implicit form.
4. If you call it from a file that caches locals, add `local _DEC_Name = _DEC_Name` to the sorted block.
5. **sw.models `decfunctions.txt`** (`sw.models/data/data/models/decfunctions.txt`, about 1100 names; header: "generated automatically by sword project. In models: do not overwrite, append new functions…") is checked by `ant checkdec` (`build/build.xml:~880-886`: `${bin.dir}/tools/checkdec.exe decfunctions.txt src/libraries/simulation`). Removing or renaming a registered DEC function that models list there **fails `checkdec`**. Adding one does not require editing the file, but append it if the models use it directly. The checkdec binary that does the function check is not in sw.sword/src; only `sw.models/src/checkdec/checkdec.go` with `tasks` / `sheets` subcommands was found (exact function-check implementation: (not yet explored)).

### 6.3 Reports: `Reports.lua` and `checkreports`

* **`integration/Reports.lua` is generated and gitignored** (`.gitignore:28`).
  * Generator: `build/checkreports.py makelua <swordRoot> <out>` (`:211-238`), wrapped by `ant generate-lua-reports` (`build/build.xml:~873-880`). It is part of `ant deploy` (`:1404`), and Docker runs it in a `reports` stage (`Dockerfile:342-356`).
  * Content: the union of `eRC_*` symbols from `MIL_DecisionalReport.h` (`namespace report { extern const MIL_DecisionalReport eRC_… }`), `data/data/models/ada/physical/mildb/Reports.xml` (`<report … key="eRC_…">`, coming from the sw.models release) and **any `eRC_\w+` token appearing anywhere in integration/*.lua**, even in comments.
  * Format: `eRC_X = "eRC_X"`.
* **`ant checkreports`** (`checkreports.py check`, `:186-207`) fails with "unreferenced report in integration: eRC_X" when an `eRC_` used in the integration layer is missing from Reports.xml, and likewise for C++.
* **So a new report used by the integration layer needs a Reports.xml entry in sw.models**, with matching parameter types, because `displayReport` validates parameters via `DEC_RC_GetReportParameterTypes`.
* A missing `Reports.lua` in a dev checkout means `include "integration/Reports.lua"` fails. Run `ant generate-lua-reports` (behavior on a missing include is unverified).

### 6.4 Tests: `data/app-data/resources/integration tests/`

* **Files**: lunit (Lua 5.1 module-based) `lunit.lua`, `lunit-console.lua`, `lunit` shell script, `DOCUMENTATION`, `lunit-tests.lua` (lunit's own self-test), `RunAllTests.lua`, `RunUnitTest.bat` (`lua.exe RunAllTests.lua` + `pause`), and **`toolsFunctions.lua`, the only integration test file**.
* **How they run**: `ant checkintegration` (`build/build.xml:~905-909`) runs `${bin.dir}/lua.exe RunAllTests.lua` with cwd = the tests dir. `RunAllTests` loads lunit and runs `lunit.main({"toolsFunctions.lua"})`, and calls `os.exit(1)` on errors or failures. It is part of `ant check` (`:911`). The test dir is excluded from packages (`build.xml:662`).
* **Harness**: `toolsFunctions.lua:1-22` stubs `integration = {}` and a fake `masalife.brain.*` table, then calls `dofile "../integration/ToolsFunctions.lua"` and `"../integration/TableIntegration.lua"`. It declares `module( "tables functions tests", package.seeall, lunit.testcase )`.
  * The cached `local DEC_X = DEC_X` lines simply capture nil, which is fine as long as tests do not call them.
* **Coverage**: 26 `*_TEST` functions: `existsIndex`, `exists`, `fusionList`, `fusionTable`, `minValue`, `explode`, `existsInside`, `removeFromList`, `removeListFromList`, `copyTable`, `tableSize`, `inverseTable`, `reverseTable`, `getCommonValuesFromLists`, `trimList`, `map`, `filter`, `intersectLists`, `reduce`, `appendIfNotMember`, `clearTable`, `linearInterpolation`, `getDifferenceBetweenLists`, `tableEqual`, `getKeys`, `nan`. **Nothing that touches DEC functions or Spark is tested.** Those are validated only by running the simulation and by sw.models' own tests (not explored).
* **To add a test**: add `fooBar_TEST = function() … assert_equal(…) end` in `toolsFunctions.lua`, or a new file listed in `RunAllTests.lua`'s `lunit.main{…}`. Only pure-Lua functions can be tested without heavy stubbing.

### 6.5 What can break in sw.models when the integration layer changes

1. **Renaming or removing an `integration.*` / `utilities.*` / global helper** turns every sw.models call into "attempt to call a nil value" **at runtime, on the first tick that reaches the call**. There is no load-time check.
   * About 954 distinct `integration.*` names are referenced from dia5 `*.lua` (2692 references; section 7).
   * Keep a deprecated alias instead.
2. **Changing argument order or kind** (knowledge vs `.source`, explicit `agent` vs implicit `myself`) breaks callers silently or with C++ exceptions. Many functions accept both forms through `agent or myself`.
3. **Changing return conventions** of triplets or startStopActions breaks skill loops. Examples: `updateBuildIt` returns report-id strings, `mountStarted` returns a "done" boolean, `loadTaskGroup` returns `eActionTransport_*`.
4. **Changing enum values in `Types.lua`** must match the C++ values.
5. **Changing the side effects on `myself.*` fields** that skills read: `myself.isMoving`, `myself.newMission`, `myself.feedback`, `myself.leadData`, `myself.taskParams` / `taskParamsWithDefault`, `myself.safetyMode`, `myself.fragOrders`, `myself.slowDown`, `meKnowledge.currentTask`.
6. **Changing message names or payloads**: `"TaskDone"`, `"CleanUp"`, `"CannotEnterUrbanBlock"`, `"ArrivedOnMines"`, `"subordinateIsBlockedByObstacle"`, `"pathIsClear"`, `"askForRoute"`, `"EndMissionAgent"` / `"EndMissionAutomat"`, `"DeactivateBehaviour"` / `"ReactivateBehaviour"`. sw.models listens to these. Also the new `__senderID` field.
7. **Changing the knowledge-class contracts** the integration layer calls on model objects: `:getPosition()`, `:getLocalisation()`, `:getType()`, `:proximityLevel()`, `:getProximity()`, `:isOperational()`, `:hash()`, `meKnowledge:sendTaskToPion()`, `meKnowledge:displayReportOnce()`, `:computeMovementCapability()`, the companyTask methods (`init`, `getNbrFront`, …), task `fillParameters`. These are cross-repo interfaces in both directions.
8. **Changing the ontology keys**: the role files' `ontology.types` must provide them, otherwise `sparkle.Object` is substituted silently.
9. **Changing the C++ entry-point globals** (section 1.5) breaks the simulation itself.
10. **New reports** need a sw.models Reports.xml entry (6.3).
11. **sw.models overrides of `integration.*`** (FragOrder.lua) can mask your change.

### 6.6 Typical change shapes (from `git log -- data/app-data/resources/integration`, last 30 commits, 2026)

The most common shape is a C++ registration plus a one-line LDoc-documented alias.

| Commit | Shape |
|---|---|
| `cdc4d5e8e5` feat SW-28139 "check the radars/sonars state" | **3 files, +28.** `DEC_PerceptionFunctions.cpp/.h`: new static `IsRadarEnabled( const DEC_Decision_ABC*, int )` with exception checks, registered as `"_DEC_Perception_IsRadarEnabled"`. `EquipmentIntegration.lua`: `integration.isSpecialSensorsOfTypeEnabled = _DEC_Perception_IsRadarEnabled -- ( agent, radarType )` with a full doc block listing `eRadarType_*`. The canonical "expose a C++ query" change. |
| `e1b5082da5` feat SWNG-1324 task-group operational state | **5 files, +23.** `MIL_TaskGroup` getter → `DEC_TaskGroupFunctions::GetOperationalState(DEC_Decision_ABC*)` registered as `"DEC_GetOperationalState"` → `integration.taskgroup.getOperationalState = DEC_GetOperationalState` (TaskGroupIntegration.lua). |
| `94438cb7df` feat SW-28156 weather blocks object building | Big C++ refactor (19 files) + `Object.lua` +7: `integration.areOceanicEffectsBlockingObjectOperation = DEC_Agent_AreOceanicEffectsBlockingObjectOperation` returns a **bitfield**, documented with `@usage bit32.band(bitfield, eOceanicEffect_Wind) ~= 0`. `Types.lua` +15 adds the `eOceanicEffect_*` bits. Shows the "enum in Types.lua mirrors C++" practice. The commit also registered a leftover alias `_DEC_Agent_PeutConstruireA`; it was removed five days later in `7e0593148a` ("fix(sim): reprise pr") and is **not** in HEAD (verified by the main session). |
| `3f46b28cd3` fix "Lead cache cleaning independent of create/destroy order" | **Lua only, +6/-1.** `myself.leadDataCount` ref-count in `leadCreate` / `leadDestroy`. Typical of state-lifetime bugs between chained skills. |
| `11305e9d13` (+ `e5d69dded7`) messaging `__senderID` | **Lua only.** The first commit added `message.__senderID = DEC_GetID(myself)` to every `SendMessage`. The second wrapped it in `pcall` because callers pass the read-only `emptyTable`. A cross-cutting change to a heavily used function had to be hardened. |
| `c56e878682` terrain enums | **`Types.lua` +63.** `eTerrainArea_*` / `eTerrainLinear_*` bitmasks "to replace hardcoded values in behaviours". |
| `6e8a462fa7`, `7b04d3eaaf`, `112f3f2ca7` (SW-27111/28116) | transport capability: extend semantics of `canTransportUnit`, fix a wrong parameter count in `agentPeutTransporterPion`, remove a not-implemented DEC function. |
| `b52406eba2`, `b31fd4f88e`, `cbf7e638bd` | doc-only fixes (LDoc tags). |

---

## 7. Usage from sw.models (rough frequency)

`grep -oE "integration\.[A-Za-z_]+(\.[A-Za-z_]+)?"` over `sw.models/data/data/models/ada/decisional/dia5/**/*.lua` finds **2692 references to 954 distinct names**. The top entries:

| # | Name | Refs | # | Name | Refs |
|---|---|---|---|---|---|
| 1 | `integration.ontology.types` | 365 | 21 | `integration.computeDistance` | 12 |
| 2 | `integration.pionRC` | 123 | 22 | `integration.isPointInsideLocation` | 11 |
| 3 | `integration.cleanFragOrder` | 101 | 23 | `integration.getAgentKnowledgeTypeParameter` | 11 |
| 4 | `integration.getIntegerParameter` | 45 | 24 | `integration.hasDotationToBuildObject` | 10 |
| 5 | `integration.getAgentFromKnowledge` | 35 | 25 | `integration.getObjectsKnowledgeInZoneWithCapacity` | 10 |
| 6 | `integration.isElementInAOR` | 34 | 26 | `integration.getNbItParameter` | 10 |
| 7 | `integration.SendMessage` | 32 | 27 | `integration.filterPionWithEchelon` | 10 |
| 8 | `integration.getPointParameter` | 31 | 28 | `integration.query.getFirstFragOrderFromType` | 9 |
| 9 | `integration.distance` | 29 | 29 | `integration.getAnyType` / `getAgentMajorOpsState` / `geometrySplitLocalisation` / `createLocationCircle` / `canBuildObjectWhenLoadedWithLocation` | 9 each |
| 10 | `integration.getEntitiesFromAutomat` | 24 | 30 | `integration.positionTranslateDir` / `getObjectPositionsForWork` / `getActionCouranteConvoi` / `computeLocationBarycenter` | 8 each |
| 11 | `integration.getAgentTypeParameter` | 18 | | `integration.report` / `reactivate` / `randomPositionOnCircle` | 7 each |
| 12 | `integration.displayReportForReco` | 16 | | | |
| 13 | `integration.isLogisticTypeUnit` | 15 | | | |
| 14 | `integration.copyPoint` | 15 | | | |
| 15 | `integration.hasCapacityKnowledgeObject` | 14 | | | |
| 16 | `integration.getResourceTypeParameter` | 14 | | | |
| 17 | `integration.getAgentTaskKnowledge` | 14 | | | |
| 18 | `integration.inverseTable` | 13 | | | |
| 19 | `integration.getKnowledgeHQ` | 12 | | | |
| 20 | `integration.getEntitiesFromAutomatCommunication` | 12 | | | |

Observations:

* **Frag order handling dominates.** `cleanFragOrder` plus the `get*Parameter` family together account for about 270 references.
* **Reporting goes through `pionRC`.**
* **`integration.ontology.types` is the most-referenced symbol.** Any change to the ontology mechanism is high-risk.
* The count only covers `.lua` files. Skill / task `.xml` files may embed more calls (not counted).
* Skills seem to call the integration layer rather than DEC functions directly: a grep for `DEC_X(` in dia5 `*.lua` returned 0 files. This should be re-checked before relying on it.

---

## 8. Known gaps / not yet explored

* The `spark-core` library path (`integrationDir/spark-core`) and the Spark `startStopAction` semantics live outside this repo.
* The implementation of the function-checking part of `checkdec.exe`.
* Whether the archetype → entity brain copy re-executes file-scope code. Inferred: no. The code is loaded once into the archetype and globals are copied or shared.
* The exact text of `Integration.lua` lines `~133-144` beyond what was read.
* Callback payload types per action, beyond movement (`arg.first`/`arg.second`) and object/transport (a scalar enum).
