# Kernel decision layer (C++): brains, decisions, orders, actions

Scope: `src/libraries/simulation/Decision/`, `Entities/Orders/`, `Entities/Actions/`, `Entities/Agents/Roles/Decision/`, and the `DEC_*Decision` classes for automats, crowds and task groups. All paths are relative to `src/libraries/simulation/` unless they start with `data/` (sw.sword) or `sw.models/`. Line numbers come from the tree as read on 2026-11-04. Claims marked **(unverified)** were inferred and not traced end to end.

The other section covers the Lua side (`data/app-data/resources/integration`). This section stops where control passes into Lua, and names the Lua entry points that C++ calls.

---

## 0. One-screen mental model

```
network order ──► MIL_*OrderManager::OnReceiveMission ──► ReplaceMission (pNextMission_, async)
                                                           │
tick N: MIL_EntityManager::Update()                        ▼
  UpdateKnowledges ... UpdateDecisions ─► per entity: DeliverMessages (all), then UpdateDecision (all)
                                 UpdateDecision = OrderManager::Update()  (stop old → Start new → Lua StartEvent)
                                                  + Brain::SelectActions + Brain::TriggerSelectedActions
                    UpdateActions   ─► every PHY_Action_ABC::Update() → Execute()/ExecuteSuspended()
                                        → Callback(actionId, code) → Lua CallbackAction
                    UpdateEffects, UpdateStates, ..., HandleDeletedEntities
```

- A **brain** (`sword::Brain`) is one DirectAI Lua brain per entity. It is cloned from a cached **archetype** brain, one per model script file.
- A **decision** (`DEC_Decision<T>`, a role on the entity) owns the brain, binds `myself`, and turns orders into Lua events.
- **DEC functions** are C++ free functions or methods registered by name into the archetype's Lua VM before the integration scripts load.
- **Actions** are C++ objects (`PHY_Action_ABC`) started from Lua. They get an id and are executed by the kernel after all decisions each tick. They report back through `CallbackAction(id, value)`.

---

## 1. Brain and binding

### 1.1 `sword::Brain` (`Decision/Brain.h`, `Brain.cpp`)

A thin wrapper over `directai::brain::Brain` (`Brain.h:97`).

| Member | Meaning |
|---|---|
| `Brain(const std::string& config)` (`Brain.cpp:20`) | Creates a root brain. `config` is a Lua-ish plugin string, e.g. `plugins={'spark','devtools'} cwd='...'` |
| `Brain(Brain& parent)` (`Brain.cpp:26`) | Creates a **child** brain from an archetype. It shares the root's Lua VM: `GetVMIdentifier()` walks to the root parent (`Brain.cpp:141-153`) and is used for per-VM memory logging (`Entities/MIL_EntityManager.cpp:5149`) |
| `DeliverMessages / SelectActions / TriggerSelectedActions(elapsed)` | The three DirectAI phases, forwarded verbatim (`Brain.cpp:37-50`) |
| `ExecuteScript(script)` | Runs arbitrary Lua (used by `DEC_Decision_ABC::ExecuteScript`, `Decision/DEC_Decision_ABC.cpp:39`) |
| `SetTimeout(seconds)` | "0 = no timeout. Applies to SelectActions, TriggerSelectedActions and ExecuteScript" (`Brain.cpp:67-72`). The value comes from `<decisional lua-timeout=...>`, default **30 s** (`Tools/MIL_Config.cpp:80,159`) |
| `GetPersistentState / SetPersistentState` | Calls Lua `integration.savePersistentState()` / `integration.loadPersistentState(state)` when `integration` exists (`Brain.cpp:124-134`) |
| `RegisterFunction(name, fn)` | `(*brain_)[name] = BrainProxy<Sig>(...)` (`Brain.h:77-87`). This makes a **global Lua function** |
| `RegisterMethod(name, &T::m)` | Builds a `ScriptMemberFunctionN` and calls `RegisterMember<T&>(vm, name)` (`BrainTemplate.h:43-100`). This makes a **method on the userdata type T**, called as `obj:name(...)` in Lua |
| `ResetProfiling / GetTotalTime` | Static per-name `MT_Profiler` map (`Brain.h:98`). `BrainProxy::operator()` wraps every call in an `MT_ProfilerGuard` (`BrainTemplate.h:126-130`). Totals are logged as `<profiling> DEC <name> <count> <ms>` (`Brain.cpp:106-114`, called from `MIL_AgentServer.cpp:362,374`) |

Arity: up to **7 arguments** (`BRAIN_FUNCTION_MAX_ARITY 7`, `Brain.h:69`), produced by Boost.PP iteration over `BrainIterate.h` / `BrainTemplate.h`. For a method, the receiver is Lua stack slot 1 and the arguments start at slot 2 (`BrainTemplate.h:11-12,21`).

### 1.2 Where registrations happen

`DEC_DecisionImpl::CreateBrain` (`Decision/DEC_Decision.cpp:1493-1555`) runs once per archetype, in this order:

1. Creates the archetype brain:
   - `isDAISpark`: plugins `spark,devtools`, then `sw.ai.setCoreLibraryPath(integrationDir/spark-core)`, plus optional `sw.ai.setTypeChecking(true)` and `sw.ai.setEntityCaching(true)` (`:1515-1527`).
   - Otherwise: plugins `eventmanager,motivation,devtools` (`:1529-1534`).
2. `RegisterCommonUserFunctions(brain, isDAISpark)` (`:1539`, defined `:275-307`) calls every `DEC_*Functions::Register(brain)`, then `RegisterMethods(brain)` (`:104-273`). `RegisterMethods` also calls `MIL_FragOrder::Register` and `MIL_Report::Register`.
3. Registers `LoadResourcesFile(file)`, which includes `integrationDir/file` (`:1540-1541`, `:1487-1490`).
4. `include(brainFile, includePath, type)` loads the model's script and, through it, the integration layer (`:1542`). The comment says DEC functions are registered **before** the integration loads, so integration code can capture them as upvalues (`:1536-1538`).

Later registrations of the same global name overwrite earlier ones. Three names are registered twice:
- `DEC_GetRawMission`: Agent and Automate functions, same behavior.
- `DEC_ConnaissanceObjet_Localisation`: KnowledgeObject and Population functions, identical bodies.
- `DEC_MissionParameter_CreateDirection`: twice in `DEC_Decision.cpp:247,252`.

Methods named `GetType` exist on five different userdata types (`DEC_Decision.cpp:201-205`). This is not a clash, because methods are per type.

Totals: about **1,625 `Register*` call sites** across `Decision/*.cpp` and `Entities/Orders/*.cpp` (grep count; another script indexes the names).

### 1.3 Per-entity binding (after the clone)

`DEC_Decision<T>::InitBrain` (`Decision/DEC_Decision.h:300-331`):
1. Calls `CreateBrain`.
2. `RegisterSelf(brain)` sets the Lua global `myself = (DEC_Decision_ABC*)this`. Examples: `Entities/Agents/Roles/Decision/DEC_RolePion_Decision.cpp:199-202`; automat, crowd and task group are identical.
3. Optional `SetProfiler` and `SetTimeout(lua-timeout)`.
4. `SetName("<name> [<id>]")`.
5. `RegisterSpecific(brain, isDAISpark, groupName)`. Spark brains only: calls `InitMePlatoon`, `InitMeAutomat`, `InitMeCrowd` or `InitMeTaskGroup` with `(integration.ontology.types.body, myself, groupName)`. For example `DEC_RolePion_Decision.cpp:204-210` uses `groupName` = automat name; the task group uses its own name.
6. Caches `ScriptRefs` to the Lua entry points (table in §2.4).
7. Sets the GC `setpause` / `setstepmul` only for a new archetype (`:326-330`; config `<GarbageCollector>`, `Tools/MIL_Config.cpp:177`).

### 1.4 The `_DEC_` vs `DEC_` naming convention

- `_DEC_Xxx(caller, ...)`: the C++ function takes the calling `DEC_Decision_ABC*` explicitly as its first parameter. Example: `bool IsLoaded(const DEC_Decision_ABC* callerAgent)` registered as `_DEC_Agent_EstEmbarque` (`Decision/DEC_AgentFunctions.cpp:108,750`).
- The Lua integration wraps each one as a parameterless `DEC_Xxx(...)` that injects `myself`. Example: `function DEC_Agent_EstEmbarque() return _DEC_Agent_EstEmbarque( myself ) end` (`data/app-data/resources/integration/DECFunctionWrappers.lua:1104-1106`; that file defines about 507 such `function DEC_` wrappers).
- `DEC_Xxx` registered directly from C++ takes **all** arguments explicitly. The first argument is often an agent, but not necessarily `myself`.
- Exceptions to the convention:
  - `DEC_TaskGroupFunctions` and `DEC_ResourceNetworkFunctions` have no `_DEC_` names at all (0 of 56 and 0 of 17), yet many still take the caller first. Example: `DEC_StartAction_TaskGroup_MoveAlong` = `StartAction<PHY_ActionTaskGroupMoveAlong,...>(DEC_Decision_ABC*, ...)` (`Decision/DEC_TaskGroupFunctions.cpp:69`).
  - `DEC_CommunicationFunctions` uses an `F_Pion_Get/Set...` prefix (97 registrations, `Decision/DEC_CommunicationFunctions.cpp:20+`).
  - `DEC_TelepathyFunctions` uses `F_...`.
  - Some getters and setters are bare `Get<var>_` / `Set<var>_` methods on `DEC_Decision_ABC` (`DEC_Decision.cpp:131-167`).
- Double underscore (`_DEC__StopAction`, `_DEC__StartTirSurPions`) is the same convention applied to a historical `DEC__` name.
- Some functions exist both as a method and as a global. Examples: `myself:DEC_Agent_EtatOps()` (method, `DEC_Decision.cpp:107`) and `_DEC_Agent_EtatOps(myself)` (function, `DEC_AgentFunctions.cpp:149`). Lua code uses both styles (`pion:DEC_Agent_EtatOpsMajeur()`, `position:DEC_Geometrie_X()`).

### 1.5 Type conversions (DirectAI binders, `directai/src/libraries/directai/tools/binders/ScriptBinder.h`)

| C++ type | Lua side | Notes |
|---|---|---|
| `bool`, `int`, `unsigned`, `float`, `double`, `std::string` | native values | |
| `T*` (raw pointer: `DEC_Decision_ABC*`, `MT_Vector2D*`, `TER_Localisation*`, `PHY_DotationCategory*`, `MIL_UrbanObject_ABC*`, …) | userdata `LuaTypeInstance<T*>` | Constructor and destructor call `UsedByDAI(p)` / `ReleasedByDAI(p)` (`ScriptBinder.h:323-334`) |
| `boost::shared_ptr<T>` (knowledges, `MT_Vector2D`, `TER_Localisation`, `MIL_Mission_ABC`, `MIL_FragOrder`, `TER_Path_ABC`, `DEC_Gen_Object`, …) | userdata holding a **copy of the shared_ptr** | Keeps the C++ object alive while Lua references it (`ScriptBinder.h:338-348`). Registered as a subtype of the raw type, so a shared userdata can usually be passed where `T*` is expected (`:523-551`, **unverified** for every case) |
| `std::vector/list/set<T>` | Lua table (array) | `SIMPLE_CONTAINER` (`:812-817`) |
| `std::map<K,V>` | Lua table keyed by K | `PAIR_COMPARATOR_CONTAINER` (`:818`) |
| `std::pair<A,B>` | table `{first=…, second=…}` | `ScriptBinder.h:860-913`. Example: the move-action callback value `(code, objectKnowledge)` |
| `directai::tools::binders::ScriptRef` | any Lua value | Used to pass Lua functions or tables into C++ (`_DEC_FillMissionParameters`) |

`UsedByDAI` / `ReleasedByDAI` specializations are in `Decision/DEC_Tools.h:57-…` and `DEC_Tools.cpp:20-224`.
- Every type **except `DEC_Decision_ABC`** has empty bodies: no lifetime tracking for raw pointers.
- `DEC_Decision_ABC` increments and decrements `nDIARef_` (`DEC_Tools.cpp:22-37`, `DEC_Decision.h:1170-1186`).

### 1.6 Lifetime and ownership pitfalls

- **Knowledges** (`shared_ptr<DEC_Knowledge_Agent/Object/Population>`): Lua keeps the object alive, but the blackboard can **invalidate** it (`DEC_Knowledge_Agent::Invalidate` sets `bValid_=false`, `Knowledge/DEC_Knowledge_Agent.cpp:953-955`).
  - Most DEC functions test `pKnowledge && pKnowledge->IsValid()` and silently return a default (0, nil, false, empty). Example: `Decision/DEC_KnowledgeObjectFunctions.cpp:408-413`.
  - Use `DEC_ConnaissanceAgent_EstValide`, `DEC_IsValidKnowledgeObject` or `DEC_CrowdKnowledge_IsValid` before relying on a cached knowledge.
- **Raw pointers into C++ state are not refcounted.**
  - Example: `DEC_ConnaissanceObjet_Localisation` returns `&pKnowledge->GetLocalisation()` (`DEC_KnowledgeObjectFunctions.cpp:408-413`). If the knowledge or object is destroyed and Lua still holds that userdata, it dangles (crash risk, **unverified**).
  - Prefer functions that return `shared_ptr`, such as `DEC_Geometrie_*` (69 `shared_ptr<MT_Vector2D>` returns in `DEC_GeometryFunctions.h`). Copy values you keep (`DEC_Copie_Point`, `DEC_Copie_Localisation`, `Decision/DEC_DIAFunctions.cpp:86-101,204-210`).
- **Points are mutable shared objects.** `DEC_Copie_Point(src, dest)` writes into `dest` in place (`DEC_DIAFunctions.cpp:86-94`). Two Lua variables holding the same `shared_ptr<MT_Vector2D>` see each other's mutations.
- **Agents (`DEC_Decision_ABC*`) are refcounted by Lua references.**
  - A pion marked for destruction is only deleted when `!IsUsedByDIA()` (`Entities/Agents/MIL_AgentPion.cpp:1799`); automats likewise (`Entities/Automates/MIL_Automate.cpp:718`).
  - A unit referenced from any Lua table therefore lingers as a "zombie" until Lua garbage-collects the reference.
  - Use `DEC_IsBrainDeleted` (`DEC_Decision.cpp:129`) or `DEC_Agent_EstValide` to detect this.
- **Calling a deleted brain** throws `"Trying to access a deleted brain …"` (`Decision/DEC_Decision_ABC.cpp:31-46`).

---

## 2. Decision lifecycle

### 2.1 Class hierarchy

| Class | File | Entity `T` | `GetKind()` | `myself` init (spark) | Group name |
|---|---|---|---|---|---|
| `DEC_Decision_ABC` | `Decision/DEC_Decision_ABC.h` | — (`tools::Role_ABC`) | enum `ePion, eAutomate, ePopulation, eTaskGroup` (`:47-53`) | | |
| `DEC_Decision<T>` | `Decision/DEC_Decision.h` (header-only template, 1,228 lines) | | | | |
| `DEC_RolePion_Decision` | `Entities/Agents/Roles/Decision/DEC_RolePion_Decision.cpp` | `MIL_AgentPion` | `ePion` | `InitMePlatoon` | automat name |
| `DEC_AutomateDecision` | `Entities/Automates/DEC_AutomateDecision.cpp` | `MIL_Automate` | `eAutomate` | `InitMeAutomat` | own name |
| `DEC_PopulationDecision` | `Entities/Populations/DEC_PopulationDecision.cpp` | `MIL_Population` | `ePopulation` | `InitMeCrowd` | (own) |
| `DEC_TaskGroupDecision` | `Entities/TaskGroups/DEC_TaskGroupDecision.cpp` | `MIL_TaskGroup` | `eTaskGroup` | `InitMeTaskGroup` | own name |

`DEC_Decision<T>` provides default implementations that **throw** `MASA_EXCEPTION` for the wrong kind: `GetPion`, `GetAutomate`, `GetPopulation`, `GetTaskGroup` (`DEC_Decision.h:599-645`), and `IsPC`, `IsTransported`, `IsFlying`, `GetOperationalState`, `IsDead`, `IsUnderCommand` (`:1096-1130,1222-1226`). Subclasses override what applies.

Important special case: **`DEC_RolePion_Decision::GetAutomate()` returns the pion's parent automat** (`DEC_RolePion_Decision.cpp:179-187`). Most "automat" DEC functions therefore silently work when called from a pion and act on its automat.

### 2.2 Creation and model assignment

- Constructor → `Initialize()`. If the entity is not marked for destruction: `SetModel(entity.GetType().GetModel())`, then `StartDefaultBehavior()` (`DEC_RolePion_Decision.cpp:37-63`, `DEC_AutomateDecision.cpp:53-67`; task group `DEC_TaskGroupDecision.cpp:14-28`).
- `SetModel` → `InitBrain(script, id, name, diaType, includePath, groupName, isDAISpark, reload=false, integrationDir)`. **Pions and automats skip `InitBrain` when the entity is remote** (`DEC_RolePion_Decision.cpp:782-795`, `DEC_AutomateDecision.cpp:187-199`).
- `StartDefaultBehavior()` calls Lua `StartEvent("BEH_Defaut")` and ignores errors (`DEC_Decision.h:456-468`). This is the DIA4/ADA default behavior; spark brains use `StartDefaultTask` instead.

### 2.3 Brain sharing and cloning

`brainTable` is a never-destroyed static `map<scriptFilePath, shared_ptr<Brain>>` (`DEC_Decision.cpp:1484-1485`; a long comment at `:1464-1483` explains why it is deliberately leaked).
- **First entity with a given script file**: builds the archetype (all registrations plus the include), stores it, and creates a child brain for the entity. The archetype is not visible in the debugger (`SetDebugVisible(reload)`, `:1551`).
- **Subsequent entities**: `new sword::Brain(*archetype)`; for spark, also `sw.ai.setRole(type)` (`:1504-1513`).
- **Reload** (`reload=true`): builds a fresh archetype and uses it directly as the entity's brain, so it is not shared (`:1500-1502,1549-1550`).

Triggers for a reload:
- Brain reload by magic action: `MIL_AgentPion::ReloadBrain` (`Entities/Agents/MIL_AgentPion.cpp:1836-1855`) calls `CancelAllActions`, `SetModel` if the model changed, `Reload(hadBrain, !modified)`, `ClearAllCategories()` (frag orders and points), and `CancelMission`.
- Automat, crowd and task group equivalents: `MIL_Automate.cpp:1585-1597`, `MIL_Population.cpp:1681`, `MIL_TaskGroup.cpp:1234`.
- An absent model deletes the brain ("Empty brain").

### 2.4 Lua entry points that C++ calls (`ScriptRefs`, `DEC_Decision.cpp:74-99`)

| Lua global | Called by | When |
|---|---|---|
| `StartEvent(behavior[, mission])` | `ActivateOrder` (`DEC_Decision.h:484-499`), `StartDefaultBehavior` | Mission start (`BEH_Defaut` at init/Reset) |
| `ResumeEvent(behavior, mission)` | `ActivateOrder(..., resume=true)` | Resuming a mission (`MIL_PionMission` `toResume_`) |
| `StopEvents(behavior)` | `StopMission`, `StopDefaultBehavior` | Mission stop |
| `StartTask(fragDiaType, frag)` | `StartFragOrderBehavior` (`DEC_Decision.h:665-675`) | Task-group frag order (pushed), or `DEC_StartFragOrder` from Lua |
| `StartDefaultTask()` / `spark.stopTasks(true)` | `StartDefaultTask` / `StopTasks` | **Spark only** (`DEC_Decision.h:1202-1214`) |
| `InitTaskParameter(missionRef, ontologyType, name, value, isList)` | Mission parameter registration (spark) | Every non-context mission parameter |
| `CallbackAction(actionId, value)` | `PHY_DecisionCallbackAction_ABC::Callback` | Actions, every tick |
| `KnowledgeCallbackAction(actionId, objKnowledge)` | `CallbackKnowledge` | Construct-object actions |
| `RemoveAction(actionId)` | `PHY_DecisionCallbackAction_ABC::Stop` | Action stopped |
| `CallbackPerception(id)` | Perception recognition | |
| `UndergroundCallback`, `OperationalStateCallback`, `EchelonCallback`, `IntoxicatedCallback`, `ContaminatedCallback`, `SurrenderedCallback`, `DeadCallback`, `EndMissionCallback` | State-change notifications (only if defined in Lua: `!!ref` checks, `DEC_Decision.h:543-597`) | |
| `SetStateVariable(name, value)` | `DEC_Decision_ABC::SetStateVariable` | |
| `ActivateBrainDebug(bool)` | Debug toggle | |
| `collectgarbage`, `step` | `GarbageCollect`, `MemoryUsageInKbytes` | |
| `integration.savePersistentState` / `loadPersistentState` | Checkpoint | §2.8 |

The Lua definitions of `CallbackAction`, `RemoveAction` and related functions are in `data/app-data/resources/integration/SimulationIntegration.lua:857-883`. `CallbackAction` ignores ids that have no registered handler.

### 2.5 Per-tick flow

Order inside `MIL_EntityManager::Update()` (`Entities/MIL_EntityManager.cpp:1097-1112`):
1. `CleanMagicActionCache`, object clean, random breakdowns
2. `UpdateDynamicCommunications`, `UpdateTransmissions`
3. **`UpdateKnowledges`**
4. task-group collisions, crowd collisions
5. **`UpdateDecisions`**
6. **`UpdateActions`**
7. `UpdateEffects`, `UpdateStates`, `UpdateKnowledgeGroups`
8. `HandleDeletedEntities`

`UpdateDecisions` (`:931-992`) runs four entity families **in this fixed order**: automats, then pions, then crowds, then task groups. For each family, it first calls `DeliverMessages()` on **all** entities, then `UpdateDecision(tickDuration)` on all entities. Each family is profiled separately (`rAutomatesDecisionTime_` and so on).

`UpdateActions` (`:994-1002`) runs formations, then task groups, automats, pions, crowds, calling `PHY_Actor::UpdateActions()`, which calls `Update()` on each registered action (`Entities/Actions/PHY_Actor.cpp:17-30`; exceptions are logged and swallowed).

Inside one entity:

- **`DEC_Decision<T>::DeliverMessages`** → `Brain::DeliverMessages` (DirectAI message delivery; **unverified** semantics). Exceptions go to `HandleUpdateDecisionError` (`DEC_Decision.h:353-366`).
- **`DEC_Decision<T>::UpdateDecision(duration)`** → `SyncDebugHook("select")`, `SelectActions()`, `SyncDebugHook("trigger")`, `TriggerSelectedActions(duration)` (`DEC_Decision.h:368-384`).
- **`HandleUpdateDecisionError`** (`DEC_Decision.h:416-424`):
  1. `LogError` writes `"Entity <id>('<name>') : Mission '<m>' impossible"` plus the exception text.
  2. `CleanStateAfterCrash` → `EndCleanStateAfterCrash`. For pions this is `CancelAllActions` + `DisableAllPerceptions` (`DEC_RolePion_Decision.cpp:100-104`); automat, crowd and task group do nothing.
  3. Posts report `eRC_MissionImpossible`.
  4. `OrderManager().CancelMission()`.

  A Lua error or timeout in a skill therefore **cancels the mission and, for pions, kills every running action and perception**.

- **Pion wrapper `MIL_AgentPion::UpdateDecision`** (`Entities/Agents/MIL_AgentPion.cpp:369-418`):
  1. Marked for destruction → `ProcessDestruction` and return. First tick: `StopTasks`, `ReleaseitMvt`, `DeadCallback`. Next tick: `DeleteBrain` (`:355-367`).
  2. `IsImmobilized()` → `CancelMission()`.
  3. `pOrderManager_->Update()` (mission switch, §4.4).
  4. If `IsDead()`: `CancelAllActions()` **every tick**. On the first dead tick only:
     - spark: `StopTasks`, one last `UpdateDecision`, `DeadCallback`;
     - non-spark: `Reload(false, true)`, which re-inits the brain from the archetype and restarts `BEH_Defaut`.
  5. If alive: when it was dead before, `StartDefaultTask()` (spark resurrection); then `UpdateDecision`.
- **Automat** (`Entities/Automates/MIL_Automate.cpp:450-470`): marked for destruction → `DeleteBrain` + `GarbageCollect` once. Otherwise `OrderManager.Update()` + `UpdateDecision`; any exception → `CancelMission`.
- **Crowd** (`Entities/Populations/MIL_Population.cpp:386-410`): if every human is dead → `Reset()` (StopEvents/StartEvent `BEH_Defaut`) + `CancelMission`. Because `updateDecisionsDead_ = true` is set unconditionally afterwards (`:401`), this reset appears to **repeat every tick** while the crowd is all dead (code reading; behavior not tested).
- **Task group** (`Entities/TaskGroups/MIL_TaskGroup.cpp:357-391`): children are skipped (only the root task group decides). Calls `CleanDecisionalState` on all echelons. If no echelon has valid equipment → `Reset()` + `CancelMission` (once, guarded). Then `OrderManager.Update` + `UpdateDecision`.

### 2.6 Engaged / disengaged automat ("embrayé / débrayé")

There is **no explicit engage flag** in the kernel. "Embrayé" means the automat's decision **has a mission**:
- `DEC_Decision<T>::IsBusy() { return pMission_ != 0; }` (`DEC_Decision.h:1216-1220`);
- `MIL_Automate::IsBusy()` (`MIL_Automate.cpp:1747-1750`).

| Lua name | Implementation |
|---|---|
| `DEC_Automate_EstEmbraye` (method) | `IsBusy` (`DEC_Decision.cpp:126`) |
| `_DEC_Agent_AutomateEstEmbraye(pion)` / `myself:DEC_Agent_AutomateEstEmbraye()` | `pion.GetAutomate().GetDecision().IsBusy()` (`DEC_AgentFunctions.cpp:566-569`) |
| `_DEC_AutomateSuperieur_EstEmbraye` | Parent automat busy (`DEC_AutomateFunctions.cpp:93-97`) |

Side effects:
- **Sending a unit order directly to a pion whose automat is busy stops the automat's mission** without propagating to the other pions: `MIL_AgentPion::OnReceiveOrder` calls `pAutomate_->GetOrderManager().StopAllMissions(false)` (`MIL_AgentPion.cpp:670-675`).
- When the automat's own mission stops with `propagate=true`, it cancels the missions of all its sub-automats and pions, except supply convoys (`Entities/Orders/MIL_AutomateOrderManager.cpp:88-109`).
- `PHY_ActionRecoLima` reports lima crossings through the automat when it is busy, otherwise through the pion (`Entities/Actions/PHY_ActionRecoLima.cpp:39-62`).

### 2.7 Reset on mission change

The kernel does **not** cancel Lua-started actions on mission change. It only calls `StopEvents(oldBehavior)` and `PostStopMission`. For pions, `PostStopMission` stops the two internal `PHY_ActionRecoLima` actions (LR and LIA) that `PostStartMission` started (`DEC_RolePion_Decision.cpp:762-775`). Stopping movement, fire and so on is the integration's job (`StopEvents` / spark task stop). **(unverified)** that every integration path does it.

`CancelAllActions` is only called on: pion death, crash recovery, brain reload, and immobilization (that last one through `CancelMission`, then Lua).

### 2.8 Persistent state and checkpoints

- **`WriteDecisionalState`** (`DEC_Decision.h:1132-1148`): writes `state="<lua string>"` when it is not empty and not `"return {}"`. Called per entity from `MIL_EntityManager::WriteDecisionalState` (`:4704`) when a checkpoint is saved (`CheckPoints/MIL_CheckPointManager.cpp:254`, file `config.GetDecisionalStateFileName()`).
- **`ReadDecisionalState`** → `integration.loadPersistentState(state)` (`DEC_Decision.h:1150-1159`; dispatched at `MIL_EntityManager.cpp:530-545`).
- Separately, the ODB/checkpoint stores C++ decision attributes: pion `force-ratio`, `roe`, `close-combat`, `op-state`, `fire-availability`, `roe-crowd`, `can-do-mission-if-transported` (`DEC_RolePion_Decision.cpp:70-98`); automat `roe`, `close-combat`, `roe-population` (`DEC_AutomateDecision.cpp:69-83`).
- Missions are restored through `MissionController::ReadMissions` → `ReplaceMission` (`MissionController.cpp:71-80,128`). **Everything else in the Lua brain is lost on checkpoint reload unless `savePersistentState` serializes it.**

### 2.9 Timeouts, profiling and GC

| Setting | Source | Effect |
|---|---|---|
| `<decisional lua-timeout=… entity-cache=… debug-server-port=…>` | `Tools/MIL_Config.cpp:158-162` | Timeout default 30 s per call. Debug server starts in `DEC_Workspace::InitializeDIA` (`Decision/DEC_Workspace.cpp:224-226`) |
| `<profiling lua=…>` | config | Attaches the LuaProfiler to every brain (`DEC_Decision.h:316-317`). `DEC_Profile(agent, on)` toggles one brain (`DEC_MiscFunctions.cpp:705-709`) |
| `dec-typecheck` (command line) | `Tools/MIL_Config.cpp:96` | Spark type checking |
| GC | `<GarbageCollector setpause setstepmul>` | Applied only to new archetypes. `GarbageCollect()` (one `collectgarbage("step")`) is only called when an automat is deleted (`MIL_Automate.cpp:457`); otherwise Lua's incremental GC is relied on |
| Memory | `MIL_EntityManager::LogLuaMemory` | Reported once per VM (`:5139-5165`) |

---

## 3. Models and workspace

### 3.1 `DEC_Workspace` (`Decision/DEC_Workspace.cpp`)

**Constructor** (`:55-65`) runs in this order:
1. `InitializeConfig` loads the physical file **"decisional"** (`physical/<db>/Decisional.xml`, `:79-83`).
2. `InitializeObjectNames`.
3. `InitializeDIA`.

**Physical `Decisional.xml`** (`LoadDecisional`, `:98-212`) holds kernel tuning, not Lua:
- `dangerosity-modifiers`, `operational-state-weights` (threshold), `urban-perception`, `critical-intelligence-delay`;
- reco/search speeds, force-ratio (`DEC_Knowledge_RapFor_ABC::Initialize`);
- `perception` (`detect-destroyed-units`, `max-level`, availability per perception type, `equipment-calculation-frequency`);
- `prototypes speeds`, `task-groups linear-speed-width-factor`, radar ocean profiles.

**Decisional file** (`config.GetDecisionalFile()`, for example `sw.models/data/data/models/ada/decisional/decisional.xml`; `LoadDIA`, `:229-245`):
- Checks `model-version` against the runtime version (`tools::CheckVersion(..., 2u)`, `:235-237`).
- Reads `<RepertoiresSources><RepertoireSources directory=… namespace=…/>` into `map<namespace, path>` (`:247-255`).
- Then calls `MIL_ParameterType_ABC::Initialize`, `InitializeMissions` (physical "missions" → Pion/Automate/Population/TaskGroup mission types + `MIL_FragOrderType`, `:257-269`), and `InitializeModels` (physical "models").

> Gotcha: `RegisterSourcePath` uses `std::map::insert`. With ada's `decisional.xml` (`bms/sources`→`none`, `dia5`→`net.masagroup`, `extensions`→`net.masagroup`), the **second `net.masagroup` entry (`extensions`) is silently ignored**. The include path for spark models is `dia5` (`DEC_Workspace.cpp:247-255`).

**Models.xml** (`LoadModels`, `:278-310`):
- Sections `<units><unit>`, `<automats><automat>`, `<crowd><crowd>` (the crowd section is literally `crowd` for both levels), and an optional `<task-groups><task-group>`.
- `ReadModel` (`:312-333`): `name` (duplicates are fatal); `masalife` → **`isDAISpark`** (default false). The source path is the namespace key `"net.masagroup"` for spark, `"none"` otherwise (unknown namespace is fatal).

### 3.2 `DEC_Model_ABC` / `DEC_Model` (`Decision/DEC_Model_ABC.cpp`, `DEC_Model.cpp`)

| Attribute | Use |
|---|---|
| `dia-type` | `GetDIAType()`. Passed to `include(file, includePath, type)` and to `sw.ai.setRole(type)`. Example: `security.agent.Agent` |
| `file` | Script, relative to the include path. Example: `BMAgent.bms` |
| `<missions><mission name="…">` | Must be a known mission **name** for that entity level, else fatal. Builds `availableMissions_`. Nested `<fragorder name>` builds the per-mission frag-order list |
| `<fragorders><fragorder name>` | Frag orders available regardless of mission |
| `PhysicalExtension` | Extension attributes, available through `DEC_GetPhysicalExtension` and similar |

- `IsMissionAvailable`, `IsFragOrderAvailable`, `IsFragOrderAvailableForMission` (global list OR per-mission list) gate order acceptance (§4.5).
- The comment "Check if the DIA behavior is present" in `ReadMission` is effectively a no-op: it re-tests the set it has just inserted into. **Missing Lua behaviors are not detected at load time**; they fail when `StartEvent` runs.
- `isDAISpark=true` (Models.xml `masalife="true"`) means:
  - DirectAI "spark" (DIA5 / MASA Life) brain;
  - `spark-core` loaded from `<integrationDir>/spark-core`;
  - `myself` wrapped as an ontology "body" through `InitMe*`;
  - mission parameters converted to ontology knowledges through `InitTaskParameter`;
  - `StopTasks`/`StartDefaultTask` active.

  `false` means DIA4-style "eventmanager+motivation" brain with `BEH_Defaut` and raw mission fields.
- `integrationDir` is `MIL_Config::integrationDir_`, default `"resources"`, command-line `--integration-dir` (`Tools/MIL_Config.cpp:78,89`). It is used by `LoadResourcesFile` and `spark-core`.

---

## 4. Orders

### 4.1 Mission types (`Entities/Orders/MIL_*MissionType.cpp`, `MIL_OrderType_ABC.cpp`)

Physical `Missions.xml` sections: `<missions><units|automats|populations|task-groups (optional)><mission id name dia-type …>`.

| Type | Behavior attributes | Lookup |
|---|---|---|
| `MIL_PionMissionType` | `dia-behavior` (`MIL_PionMissionType.cpp:38`) | `Find(id)`, `FindFromDiaID(diaType)` |
| `MIL_AutomateMissionType` | `mrt-dia-behavior`, `cdt-dia-behavior` (`MIL_AutomateMissionType.cpp:39-41`); `GetDIABehavior(ePhaseMRT/ePhaseCDT)` | |
| `MIL_PopulationMissionType`, `MIL_TaskGroupMissionType` | `dia-behavior` | |

Common (`MIL_OrderType_ABC.cpp:20-28`): `name`, `dia-type`, `<parameter>` list, `PhysicalExtension`. Duplicate id, dia-type or name is fatal (`MIL_MissionType_ABC.h:43-68`).

Each parameter (`MIL_OrderTypeParameter.cpp:31-40`) has `name`, **`dia-name`** (the Lua field name), `type` (case-insensitive lookup into `MIL_ParameterType_ABC`, unknown is fatal), `optional`, `min-occurs`, `max-occurs` (`unbounded`).

**Context parameters.** For pion and automat missions, the first 4 parameters are the order context (`MIL_OrderContext.cpp:85-104,157-160`):

| Index | Content | How Lua reads it |
|---|---|---|
| 0 | Danger direction | `DEC_Copie_DirectionDanger_Mission`, `DirDanger` |
| 1 | Phase lines (limas) | `_DEC_GetLima`, `_DEC_GetLimasFromType` |
| 2, 3 | Left and right limits (fuseau) | `_DEC_Fuseau`, `DEC_GetFuseau`, … |

`ReadParameter` skips them (`MIL_OrderType_ABC.cpp:36-40`), so they are **not** in `parameters_` and never reach the Lua mission table. The two limits must be both empty, or both set and different (`ORDER_BADLIMIT`). The fuseau is clipped by the LDM/LFM limas.

### 4.2 Parameter type zoo (`MIL_ParameterType_ABC.cpp:27-79`)

Registered type names (case-insensitive) and their aliases:

| Group | Names |
|---|---|
| Entities | `Agent`, `Automat` (= `Automate`), `TaskGroup` |
| Knowledges | `AgentKnowledge`, `ObjectKnowledge`, `CrowdKnowledge` (= `PopulationKnowledge`), `UrbanKnowledge` (= `UrbanBlock`), `TaskGroupKnowledge` |
| Scalars | `Boolean` (= `bool`), `Integer`, `Numeric`, `Distance`, `Speed`, `Duration`, `DateTime`, `String`, `Enumeration` |
| Geometry | `Point`, `Polygon`, `Location`, `LocationComposite`, `Path`, `Itinerary`, `Line`, `PhaseLine`, `LimaFunction`, `Heading` (= `Direction`, `DirectionBM`) |
| Types | `ResourceType` (= `DotationType`), `EquipmentType`, `UnitType`, `CBRNProtectionType`, `TransmitterType`, `LiaisonInstance`, `ResourceNetworkType`, `ResourceNetworkNode`, `NatureAtlas`, `Echelon` |
| Other | `PlannedWork` (= `GenObject`), `MaintenancePriorities`, `MedicalPriorities`, `IndirectFire`, `Stage`, `Mission`, `FragOrder`, `List` |

Each value is a `MIL_MissionParameter_ABC` subclass (`MIL_PointParameter`, `MIL_LocationParameter`, `MIL_AgentKnowledgeParameter`, `MIL_ListParameter`, `MIL_NullParameter`, …; `Entities/Orders/*Parameter.cpp`). The subclass answers the `To*` converters (`ToPoint`, `ToAgentKnowledge`, `ToPolygonList`, `ToPath`, …; `MIL_MissionParameter_ABC.h:85-151`). A converter that does not apply returns `false`.

### 4.3 How mission parameters reach Lua

`DEC_Decision<T>::ActivateOrder` (`DEC_Decision.h:484-499`):
1. `pMission_ = mission`.
2. `refMission = pBrain->GetScriptRef()` is an anonymous Lua value set to the `shared_ptr<MIL_Mission_ABC>`, which keeps the mission alive.
3. `RegisterMissionParameters(brain, InitTaskParameter, refMission, mission, isDAISpark)`.
4. `StartEvent(behavior, refMission)`, or `ResumeEvent`.
5. `PostStartMission()`.

`RegisterMissionParameters` (`DEC_Decision.cpp:1440-1453`) visits each non-context parameter (`MIL_Mission_ABC::Visit`, `MIL_Mission_ABC.cpp:205-217`). The type key is `paramType.GetName()` plus `"List"` when the parameter is a list:

- **Non-spark** (`RegisterMissionParameterVisitor`, `:1384-1408`): `functors[key](refMission, diaName, element)` sets the raw value directly, `refMission[diaName] = value` (point = `shared_ptr<MT_Vector2D>`, polygon = `shared_ptr<TER_Localisation>`, …; functors `:1258-1310`).
- **Spark** (`RegisterMissionParameterBMVisitor`, `:1410-1438`): `functorsBM[key]` calls `InitTaskParameter(refMission, integration.ontology.types.<t>, diaName, value, isList)`. Ontology types used: `point, area, urbanBlock, cbrnProtection, resourceNetwork, equipmentType, humanWound, genObject, limaStatique, unitType, transmitterType, liaisonInstance, taskgroup`, … (`:362-1245`). Scalars are set raw. If a conversion fails, the field becomes `NIL` (`:1429-1430`). The functor map is `:1312-1380`.
- **Gotchas**:
  1. The functor maps are case-sensitive `std::map`s keyed on the **registered** type name. A parameter declared with an alias that has no functor is **silently not passed to Lua**: `Automate`, `bool`, `Direction`/`DirectionBM`, `GenObject`, `DotationType`, `UrbanBlock`, `Duration`, `Stage`, `LimaFunction`, `Mission`, `FragOrder`, `TaskGroupKnowledge`.
  2. Some types exist only in one map. `Polygon` maps to "area" in spark. `Location` and `LocationList` are non-spark only; spark expects `LocationComposite`. `Echelon` and `TaskGroup` are spark only.
  3. Odd mapping: `CrowdKnowledgeList` and `PopulationKnowledgeList` → `LocationCompositeListFunctionBM` (`:1322,1361`).
- **Missions created from Lua** (automat giving pion missions): Lua fills parameters with `DEC_AssignMission*Parameter(mission, name, value)` (`DEC_Decision.cpp:211-244` → `MIL_MissionParameterFactory::Set*`), which calls `MIL_Mission_ABC::SetParameter(name, param)`. `SetParameter` resolves `name` by **dia-name** and throws `"Incorrect parameter name"` if it is unknown (`MIL_OrderType_ABC.cpp:96-106`).
  - `DEC_MissionParameter_Create*` builds standalone parameters, for example for reports and lists.
  - `DEC_Copie_*_Mission` copies points and localisations into mission parameters (`DEC_DIAFunctions.cpp:96-110`).
  - `_DEC_FillMissionParameters(myself, InitTaskParameter, missionRef, mission)` re-runs the visitor for an existing mission (`DEC_MiscFunctions.cpp:105-108,270-275`; used at `DECFunctionWrappers.lua:84`).

### 4.4 Order managers (`MIL_OrderManager_ABC.cpp`)

State: `pMission_` (current), `pNextMission_` (pending), `bNewMissionStarted_`.

| Call | Effect |
|---|---|
| `ReplaceMission(m)` | **Asynchronous**: `controller_.Next(m); pNextMission_ = m` (`:63-67`) |
| `CancelMission()` | **Asynchronous**: `controller_.Stop(pMission_); pNextMission_.reset()` (`:69-74`). Lua `_DEC_FinMission` calls this (`DEC_OrdersFunctions.cpp:73-76`), so the mission really ends at the **next** `Update` |
| `Update()` (start of each entity's decision tick) | Clears `bNewMissionStarted_`. If `pNextMission_ != pMission_`: `StopAllMissions()` (old `mission->Stop` → `StopMissionBehavior` → Lua `StopEvents(behavior)` + `PostStopMission`), then `controller_.Start(new)` → `mission->Start` → `StartMissionBehavior` / `ResumeMissionBehavior` → `ActivateOrder` → Lua `StartEvent`. Sets `bNewMissionStarted_ = true` (`:32-61`). If start throws, it is logged as `"Failed to start mission"`, everything is stopped, and the exception is rethrown |
| `IsNewMissionStarted()` | Lua `_DEC_NouvelleMission`. **True only during the tick the mission started** |

Subclasses: `MIL_PionOrderManager`, `MIL_AutomateOrderManager` (adds MRT/CDT), `MIL_PopulationOrderManager`, `MIL_TaskGroupOrderManager`.
- Lima and fuseau lookups on a pion fall back to its automat's mission (`MIL_PionOrderManager.cpp` `FindLima`, `GetFuseau`).
- The MRT fuseau assigned to the pion takes precedence.

**Network → brain path for a unit order:**
1. `MIL_EntityManager::OnReceiveUnitOrder` (`:1240`) → `MIL_AgentPion::OnReceiveOrder` (stops a busy automat's mission, `MIL_AgentPion.cpp:670-675`) → `MIL_PionOrderManager::OnReceiveMission`.
2. That function rejects the order (ack error code) in these cases:
   - unit dead;
   - unit transported, unless `DEC_EnableMissionsIfTransported` was set, in which case it is magically unloaded first; otherwise the report `eRC_TransportedUnitCannotReceiveOrder` is posted;
   - immobilized;
   - surrendered;
   - invalid end date;
   - mission not in the model.
3. It then builds `MIL_PionMission(type, pion, id, clientId, params, displayExt)`; building the parameters can throw on bad parameters. Then `ReplaceMission`.
4. On the next `MIL_AgentPion::UpdateDecision` → `OrderManager::Update` → `MIL_PionMission::Start` → `DEC_RolePion_Decision::StartMissionBehavior` → `ActivateOrder(dia-behavior)`. It also sends the `UnitOrder` echo and registers the order with `ActionManager` (`MIL_PionMission.cpp:94-139`).

Automat orders follow the same path through `MIL_AutomateOrderManager::OnReceiveMission` (`:45-67`), rejected if the automat is surrendered, the mission is unavailable, or the end date is invalid.

**Automat MRT → CDT (conduite)** (`MIL_AutomateMission.cpp:84-116`, `MIL_AutomateOrderManager.cpp:135-246`, `DEC_OrdersFunctions.cpp:40-49`):
1. Start runs **only the MRT behavior** (`StartEvent(mrt-dia-behavior)`).
2. Lua prepares pion missions with `_DEC_MRT_CreerMissionPion(pion, missionDiaType)`. This returns nil if the mission is not available to the pion, if the automat has no current mission, or if MRT is already active.
3. Lua optionally splits fuseaux with `_DEC_MRT_AffecteFuseaux(pions)`.
4. `_DEC_MRT_Valide()` → `mrt_.Activate()`: affects the fuseaux and calls `ReplaceMission` on each pion. Then `GoToCdt`: `StopEvents(mrt)` + `StartEvent(cdt-dia-behavior)`.
5. During conduite: `_DEC_CDT_CreerMissionPion` + `_DEC_CDT_DonnerMissionPion_Mission`.
6. "BM" variants that need no MRT: `_DEC_CreerMissionPion` / `_DEC_DonnerMissionPion`.

Automats decide before pions in the same tick (§2.5), so **a mission given by an automat to its pion starts in the pion's update of the same tick**.

`ActionManager` (`ActionManager.cpp/.h`) is **not** the physical-action mechanism. It is the registry of client orders and magic actions with unique ids, which it replays to newly connected clients and saves in checkpoints.

### 4.5 Frag orders (`MIL_FragOrder.cpp`, `MIL_FragOrderType.cpp`)

**Types**: from `Missions.xml` fragorders. Attributes include `available-without-mission` and `available-for-all-mission` (`MIL_FragOrderType.cpp:58-59`).

**Reception** (pion: `MIL_PionOrderManager::OnReceiveFragOrder`; automat `:69-86`; crowd similar):
1. Rejected if the entity is surrendered, the type is unknown, or the type is neither available without a mission nor available for the current mission (`IsFragOrderAvailable` → model lists).
2. Builds `MIL_FragOrder`. `SetParameters` checks non-optional parameters are not null and have the right size (`MIL_FragOrder.cpp:71-88`).
3. **Appends it to `DEC_Representations::orderRepresentations_`** (`Decision/DEC_Representations.cpp:35-38`).
4. Acks and echoes.

The brain is **not notified**: Lua must **poll** `_DEC_GetOrdersCategory(myself)` (`DEC_MiscFunctions.cpp:92,241-244`) and remove handled ones with `_DEC_RemoveFromOrdersCategory` / `_DEC_DeleteRepresentation`. Otherwise they accumulate until `ClearAllCategories` on brain reload.

- **Task groups** are pushed: `taskGroup_.GetDecision().StartFragOrderBehavior(frag)` → Lua `StartTask(fragDiaType, frag)` (`MIL_TaskGroupOrderManager.cpp:66`, `DEC_Decision.h:665-675`).
- **Lua-to-Lua**: `CreateFragOrder(diaType)` makes an "incomplete" order with no parameters (`MIL_FragOrder.cpp:179-186`); `DEC_StartFragOrder(recipient, frag)` pushes it into another brain's `StartTask` (`DEC_OrdersFunctions.cpp:224-227`).

**Lua accessors**: 63 registrations, all methods on the frag-order userdata (`MIL_FragOrder.cpp:107-177`).
- `GetType` (dia-type), `GetName`.
- Legacy named fields such as `GetambianceMission_`, `Getmunitions_`, `GetnbIT_`, `GetpointCible_`, `GetzoneCible_`, `GetpionRenforce_`, `GetorderConduiteChangerReglesEngagement_`, `GetorderConduiteModifierPrioritesReparations_`, …
- Generic typed getters by **dia-name**: `GetIntegerParameter(name)`, `GetPointParameter`, `GetLocationParameter`, `GetAgentKnowledgeParameter`, `DEC_GetAgentListParameter`, `DEC_GetBoolParameter`, `GetResourceTypeParameter`, `GetTimeParameter`, `GetDirectionParameter`, `HasIntegerParameter`, …
- `FindParam` throws `"Frag Order <name> : Unknown parameter <name>"` (`:256-265`), and typed getters throw when the value has the wrong type (for example `GetIntegerParameter`, `:329-339`). A wrong name in Lua is therefore a Lua error, which **cancels the current mission** (§2.5).

### 4.6 Limas and limits

- `MIL_LimaFunction`: LD, LCA, LC, LI, LO, LCAR, LR, LDM, LFM, LIA (`MIL_LimaFunction.cpp:18-27`).
- `MIL_LimaOrder` has `Flag` / `IsFlagged` and `FlagSchedule` / `IsScheduleFlagged` (`MIL_LimaOrder.cpp:127-145`).
- Lua functions:
  - `_DEC_GetLima(funcId)` → lima id or 0;
  - `_DEC_GetLimasFromType`;
  - `_DEC_ProchaineLimaHoraireNonFlagee`;
  - `_DEC_SetMissionLimaFlag(limaId, flag)` and `_DEC_SetMissionLimaFlagHoraire`. These apply to the **highest busy automat's whole hierarchy** (`DEC_OrdersFunctions.cpp:347-357`).
- Automatic lima crossing: each pion mission starts two internal `PHY_ActionRecoLima` actions (LR and LIA) that post `eRC_CrossedLima` (`DEC_RolePion_Decision.cpp:762-768`, `PHY_ActionRecoLima.cpp`).

### 4.7 Reports (`MIL_Report.cpp`, `MIL_DecisionalReport.*`)

- **Loading** (`Initialize`, `:36-75`): `<reports><report id key? message category><parameter type …>`. Duplicate ids are logged. `key` maps to an id in `keys_`.
- **Lookup** (`Find(string)`, `:295-304`): a numeric string is treated as an id, otherwise as a key.
- **From Lua**: `DEC_RC1(caller, type, reportId)` and typed variants `DEC_RC_AgentKnowledge`, `DEC_RC_Point`, `DEC_RC_Pion`, `DEC_RC_Float`, `DEC_RC_String`, …, plus `DEC_RC_Generic(caller, type, id, {params})` (`DEC_MiscFunctions.cpp:111-135,356-579`).
  - An **unknown report id or key is silently ignored**.
  - `type` (Lua argument) sets `sword::Report::category`, overriding the XML category. Values: 0 message, 1 operational, 2 event, 3 warning, 4 custom (`MIL_Report.h` `E_Type`).
- **Parameter checking** (`ConvertParameters`, `:145-178`): too few parameters, a type mismatch, or a failed protobuf conversion **logs an error and drops the report** (`"Report '<msg>' send failed (parameters missing)"` / `"does not match report type"`).
- **Introspection**: `DEC_RC_GetReportParameterTypes`, `DEC_RC_GetReportListParameterTypes`, `DEC_RC_IsReportDefined` (`:138-143`).
- **Kernel-posted reports** use `MIL_DecisionalReport` constants (`report::eRC_*`, `MIL_DecisionalReport.h`/`.cpp`, `MAKE_REPORT(X)` → key `"X"`). `PostEvent` resolves the key `"eRC_MissionImpossible"` against the `key=` attribute in Reports.xml (for example `sw.models/.../mildb/Reports.xml:8`) and sends with the XML category (`MIL_Report.h:198-203`). It does nothing if `!receiver.CanEmitReports()` (`:206-212`).
  - `ConvertCategory` only knows `information / exceptional / warning / operational` (`MIL_Report.cpp:79-92`). The ada database uses `decision, info, firing, logistic, mission, request, detection, error`, so kernel-posted reports go out as category **custom (4)**, with the text in `category_name`.
- **Report ids** count **down** from `UINT_MAX` (`AllocateId`, `:44-47`) and are restored from checkpoints.
- `_DEC_Trace(msg)` sends a trace (`DEC_MiscFunctions.cpp:581-592`). `_DEC_DecisionalState(key, value)` sends a `DecisionalState` message to clients (`DEC_EntityFunctions.cpp:34-…`; it deliberately falls through the source switch).

---

## 5. Actions

### 5.1 Mechanism

- **`PHY_Action_ABC`** (`Entities/Actions/PHY_Action_ABC.h/.cpp`):
  - `id_ = GetSimIds().AllocateId()`, the global sim id space; 0 is never an action.
  - `Update()` → `ExecuteSuspended()` if suspended, else `Execute()` (`:41-47`).
  - `Suspend`/`Resume` toggle `bSuspended_`.
  - `GetState()` returns `eError=0`, `eDone=1`, `eRunning=2`. The **default is always `eRunning`**; only a few actions override it (`PHY_ActionMove`, `PHY_ActionRefloatShip`, `PHY_ActionTaskGroupMove`, `PHY_ActionTaskGroupChangeDispersion`).
- **`PHY_Actor`** (`PHY_Actor.cpp`) is the base of entities and holds `map<id, shared_ptr<action>>`.
  - `RegisterAction`.
  - `UnregisterAction(id)` → `Stop()` + erase.
  - `CancelAllActions()`.
  - `UpdateActions()` iterates in id order; exceptions are logged as `"Action failure"` and do not stop other actions.
- **`PHY_DecisionCallbackAction_ABC`** (`PHY_DecisionCallbackAction_ABC.h/.cpp`): about 40 subclasses. Holds the actor's `DEC_Decision_ABC&`.
  - `Callback(value)` → Lua `CallbackAction(GetId(), value)`.
  - `CallbackKnowledge` → `KnowledgeCallbackAction`.
  - `Stop()` → `StopAction()` (usually a final `Callback`) + Lua `RemoveAction(id)`.

### 5.2 Starting, stopping and querying from Lua (`Decision/DEC_ActionFunctions.h:39-172`, `.cpp:90-345`)

```cpp
template< typename ActionType, typename... T >
unsigned int StartAction( DEC_Decision_ABC* agent, T... args ) {
    auto& caller = GetEntity< typename ActionType::ActorType >( agent ); // GetPion/GetPopulation/GetTaskGroup/GetEntity
    return RegisterAction( caller, boost::make_shared< ActionType >( caller, args... ) ); // returns action->GetId()
}
```

- `ActorType` selects the entity kind. Starting a pion action from a crowd brain throws in `GetPion()`, which leads to mission impossible.
- Most starters are registered directly as `&StartAction<PHY_ActionX, Args...>` under `_DEC_Start...` names. Some use lambdas to inject extra arguments (day/night, tick duration, nullptr requester).
- `_DEC__StopAction(caller, id)` → `UnregisterAction(id)` and **returns 0**, so the Lua idiom is `id = DEC__StopAction(id)`. An id of 0 is a no-op (`:316-325`).
- `_DEC_PauseAction` / `_DEC_ReprendAction` → `Suspend` / `Resume`.
- `_DEC_EtatAction` → `GetState()`, or `eError(0)` if the id is unknown or already stopped (`:327-342`).

**States as Lua sees them** ("running / finished / paused / impossible") are **not** `PHY_Action_ABC::E_State`. They are the per-role return codes delivered through `CallbackAction` on every `Execute`:

| Action | Codes |
|---|---|
| Move | `DEC_PathWalker::E_ReturnCode` = `eRunning 0, eFinished 1, ePaused 2, eNotEnoughFuel 3, eNotAllowed 4, eAlreadyMoving 5, eItineraireMustBeJoined 6, ePartialPath 7, eBlockedByObject 8, eTeleported 9, eBlockedByMissingObject 10` (`Decision/DEC_PathWalker.h:34-47`) |
| Direct fire | `PHY_RoleAction_DirectFiring::E_ReturnCode` = `eImpossible 0, eEnemyDestroyed 1, eNoCapacity 2, eNoAmmo 3, eRunning 4, eFinished 5` (`Entities/Agents/Actions/Firing/DirectFiring/PHY_RoleAction_DirectFiring.h:53-61`) |
| Others | Their own `PHY_RoleAction_*` enums (not yet explored in detail) |

### 5.3 Examples

| Lua (wrapped) | C++ | Notes |
|---|---|---|
| `_DEC_StartDeplacement(agent, path, suspended)` | `PHY_ActionMove(pion, isDay, path, suspended)` (`DEC_ActionFunctions.cpp:110-116`) | Day/night is sampled at start. `Execute` (`Entities/Agents/Actions/Moving/PHY_ActionMove.cpp:172-218`): if teleported → `eTeleported`; does nothing while deployed; recomputes the path to avoid obstacles or after a suspension; `role.Move(path)`. Callback value is the **pair** `{first=code, second=objectKnowledge}` (`:256-260`). `StopAction` sends `eFinished` (`:234-243`). Paths come from `_DEC_CreerItineraire*` (asynchronous pathfinder; `DEC_Itineraire_Etat` → `TER_Path_ABC::E_State` `eInvalid 0, eComputing 1, eValid 2, eImpossible 4, ePartial 8, eCanceled 16`) |
| `_DEC_StartLoopedMovement(agent, initial, loop, suspended)` | `PHY_ActionMove` with a loop path | |
| `_DEC_StartTirDirect(agent, knowledge, pct, mode, ammoClass)` | `PHY_ActionDirectFirePion` | **The constructor calls `Callback(initialCode)`** (`PHY_ActionDirectFirePion_ABC.cpp:38`); then `Execute` → `role.FirePion` → callback each tick; `StopAction` → `Callback(finalCode)` |
| `_DEC_StartTirIndirectSurPosition(agent, dotation, nbIt, point)` | `PHY_ActionIndirectFire_Position` (requester nullptr) | `...AvecDemandeur` and `...SurConnaissance(Ptr)` variants |
| `_DEC_StartEmbarquement(agent)` / `_DEC_StartDebarquement` | `transport::PHY_ActionLoad` / `Unload` (`Entities/Agents/Actions/Loading/PHY_ActionLoad.cpp:21-41`) | Callback with the initial code in the constructor, then `role.Load()` each tick |
| `_DEC_StartCreateObject(agent, genObject, instantaneous)` | `PHY_ActionConstructObject` (`Entities/Agents/Actions/Objects/PHY_ActionConstructObject.cpp:40-99`) | Creates the object at construction; each tick sends `Callback(code)` **and** `KnowledgeCallbackAction(id, objectKnowledge)` |
| `_DEC__StartDeplacement(crowd, point)` / `_DEC_StartDeplacementItineraire` | `PHY_Population_ActionMove/MoveAlong` | Crowd actor |
| `DEC_StartAction_TaskGroup_Move/MoveAlong/Stealth(tg, …)` | `PHY_ActionTaskGroup*` (`DEC_TaskGroupFunctions.cpp:68-70`) | Caller is explicit even without `_` |

**Timing:**
- Lua starts an action during `UpdateDecisions` of tick N. The **first `Execute` happens in `UpdateActions` of the same tick N**, after every brain has run.
- Callbacks therefore arrive in Lua **between ticks**, and the brain sees them in its next `SelectActions` (tick N+1).
- Callbacks fired from an action **constructor** run synchronously inside the Lua `_DEC_Start...` call, **before** Lua knows the id. `CallbackAction` drops unknown ids (`SimulationIntegration.lua:861-865`), so that initial code is effectively lost.

Deprecated or no-op starters:
- `_DEC__StartControlerZone` → `DeprecatedFunction` (logs `"Calling deprecated DEC function"`, `DEC_ActionFunctions.cpp:178,773-776`).
- `_DEC__StartOccuperObjet` → returns 0 (`:299-300`).

---

## 6. Map of the `DEC_*Functions` files

Counts are `Register*` call sites from a grep (approximate). "u" = names with the `_DEC_` prefix (wrapped with `myself` in Lua). The full name index is generated elsewhere; this table gives the domains and a few anchor names only.

| File | Domain | # (u) | Anchor names |
|---|---|---|---|
| `DEC_Decision.cpp` (`RegisterMethods`) | Methods on decision, vector, knowledge, gen-object and mission types; mission-parameter factory | ~143 (2) | `myself:DEC_Agent_EtatOps`, `DEC_Geometrie_X`, `GetType`, `DEC_AssignMission*Parameter`, `DEC_MissionParameter_Create*`, `DEC_Elongation` (no-op) |
| `DEC_ActionFunctions` | Starting and stopping physical actions, transport, prisoners and refugees | ~120 (79) | `_DEC__StopAction`, `_DEC_StartDeplacement`, `_DEC_StartTirDirect`, `_DEC_StartCreateObject`, `_DEC_Transport_AjouterPions` |
| `DEC_AgentFunctions` | Pion state and queries (posture, NBC, blackout, meteo, hierarchy, ROE) | ~213 (82) | `_DEC_Agent_EstEmbarque`, `_DEC_Agent_ChangeEtatROE`, `DEC_Agent_MaxSpeed`, `DEC_GetUnitById`, `DEC_GetRawMission` |
| `DEC_AutomateFunctions` | Automat hierarchy (pions with or without PC), ROE, MRT helpers | ~42 (26) | `_DEC_Automate_PionsSansPC`, `_DEC_AutomateSuperieur_EstEmbraye`, `DEC_SetMission` |
| `DEC_CommunicationFunctions` | Legacy "etat" variables as `F_Pion_Get/Set*` | 97 (0) | `F_Pion_GeteEtatFeu`, `F_Pion_SeteEtatDec` |
| `DEC_DIAFunctions` | Utilities: time, random, point and localisation copies | ~20 | `DEC_TempsSim`, `DEC_RandomValue`, `DEC_Copie_Point`, `_BreakForDebug` |
| `DEC_EntityFunctions` | Entity-kind tests, decisional state message | 4 (1) | `DEC_IsAgent`, `DEC_IsCrowd`, `_DEC_DecisionalState` |
| `DEC_Extensions` | Get and set physical or runtime extensions | 26 | `DEC_Agent_GetExtension`, `DEC_GetMissionTypeExtension` |
| `DEC_FireFunctions` | Fire ranges, ammunition authorization | ~26 (21) | `_DEC_Tir_PorteeMaxPourTirerSurUnite`, `_DEC_Pion_InterdireMunition` |
| `DEC_GeometryFunctions` | Geometry and fuseau computations | ~134 (27) | `DEC_Geometrie_CreerPointXY`, `_DEC_Geometrie_EstPointDansFuseau`, `DEC_Geometrie_StartCalculLignesAvantEtArrierePourPion` |
| `DEC_Knowledge*Functions` (Agent / general / Object / Population) | Knowledge queries | 51 / 60 / 60 / 36 | `DEC_ConnaissanceAgent_EstValide`, `_DEC_Connaissances_UnitesEnnemiesVivantesDansZone`, `DEC_ConnaissanceObjet_Localisation`, `_DEC_ConnaissancePopulation_Attitude` |
| `DEC_LogisticFunctions` | Maintenance, medical, supply chains | ~86 (38) | `_DEC_Maintenance_ActiverChaine`, `_DEC_DemandeDeRavitaillement` |
| `DEC_MiscFunctions` | Reinforcements, debug draw, representations (frag orders), **reports**, type lookups | ~93 (14) | `_DEC_GetOrdersCategory`, `DEC_RC1`, `DEC_RC_Generic`, `_DEC_Trace`, `DEC_GetFragOrderTypeByDIAType` |
| `DEC_ObjectFunctions`, `DEC_Agent_ObjectAction`, `DEC_Gen_Object` | Objects: magic create or destroy, capabilities | 22 / 43 | `_DEC_CreerObjetSansDelais`, `_DEC_Agent_PeutConstruireObjet` |
| `DEC_OrdersFunctions` | Missions, MRT/CDT, limas, fuseau | ~36 (22) | `_DEC_FinMission`, `_DEC_NouvelleMission`, `_DEC_MRT_Valide`, `_DEC_GetLima`, `DEC_StartFragOrder` |
| `DEC_PathFunctions` | Path creation and inspection | ~34 (7) | `_DEC_CreerItineraire`, `DEC_Itineraire_Etat`, `_DEC_GetNextObjectOnPath` |
| `DEC_PerceptionFunctions` | Sensor modes, reconnaissance, radar | ~43 (41) | `_DEC_Perception_ActiverReconnaissanceLocalisation`, `_DEC_Perception_VisionVerrouilleeSurPoint` |
| `DEC_PopulationFunctions` | Crowd self-functions | ~35 (30) | `_DEC_Population_ChangerAttitude`, `_DEC_GetNombrePersonne` |
| `DEC_TaskGroupFunctions` | Task-group echelons, movement, ROE | 56 (0, caller explicit) | `DEC_StartAction_TaskGroup_Move`, `DEC_TaskGroup_SetRulesOfEngagement` |
| `DEC_TerrainFunctions`, `DEC_UrbanObjectFunctions`, `DEC_ResourceNetworkFunctions`, `DEC_Transmissions`, `DEC_TelepathyFunctions`, `DEC_Agent_Dotation` | Terrain, urban blocks, resource networks, radio liaisons, legacy telepathy, dotations | 11 / 15 / 17 / 18 / 3 / 23 | `DEC_IsTerrainIn`, `DEC_UrbanBlocksInZone`, `DEC_JoinNetwork`, `_DEC_HasDotation` |
| `MIL_FragOrder::Register` | Frag-order accessors | 63 | §4.5 |
| `MIL_Report::Register` | Report introspection | 3 | §4.7 |

---

## 7. Gotchas for behavior designers

1. **Wrong-kind calls throw, and a throw ends the mission.**
   - `GetPion()` on an automat or crowd decision (and the like) throws `"GetPion cannot be called for this Decision class"` (`DEC_Decision.h:599-645`).
   - Any uncaught exception in a DEC call propagates through Lua to `HandleUpdateDecisionError`, which logs "Mission impossible", posts `eRC_MissionImpossible`, `CancelMission()`, and for pions `CancelAllActions()` plus all perceptions off (`DEC_Decision.h:416-424`, `DEC_RolePion_Decision.cpp:100-104`).
   - Catch errors in Lua (`pcall`) if a partial failure should not abort the mission.
2. **From a pion, "automat" functions silently act on its automat**, because `DEC_RolePion_Decision::GetAutomate()` returns the parent automat (`:179-187`).
3. **`_DEC_FinMission` and `CancelMission` are asynchronous.** The mission stops at the next `OrderManager::Update`, at the start of the entity's next tick. **`_DEC_NouvelleMission` is true only during the start tick** (`MIL_OrderManager_ABC.cpp:32-74`).
4. **Frag orders are polled, not pushed** (except for task groups). You must remove them from the orders category yourself, or they pile up (§4.5).
5. **A direct order to a unit disengages its busy automat**: `StopAllMissions(false)` on the automat (`MIL_AgentPion.cpp:670-675`).
6. **Mission parameter names are dia-names.** Some type aliases are silently dropped, and spark versus non-spark parameter shapes differ (§4.3). The 4 context parameters (danger direction, limas, limits) never appear in the mission table; read them with the dedicated DEC functions.
7. **Knowledges can go invalid while Lua still holds them.** Functions then return defaults without any error. Raw-pointer returns (`TER_Localisation*`, `MT_Vector2D*`, `MIL_UrbanObject_ABC*`) are not refcounted and can dangle (§1.6).
8. **Holding a unit in a Lua table keeps a deleted unit alive** (`IsUsedByDIA` blocks deletion, `MIL_AgentPion.cpp:1799`).
9. **Action timing:**
   - The first `Execute` runs in the same tick, after all brains.
   - Callbacks are processed by Lua on the next tick.
   - A constructor callback (initial code) is fired before the Lua handler exists and is dropped.
   - `_DEC_EtatAction` is meaningless for most actions (always `eRunning` until stopped, then `eError`). Rely on callback codes instead (§5.2).
   - The kernel does **not** stop your actions on mission change; your stop path must do it.
10. **Perception and knowledge results reflect the previous update.** `UpdateKnowledges` runs before decisions; perceptions you activate this tick only affect knowledge from the next tick **(unverified for every sensor type)**.
11. **`GetVariable` has a side effect.** The `Get*_` legacy getters, when the Lua field is nil, write a default (`false`/`0`/nil-ptr) into `myself.<field>` (`DEC_Decision_ABC.h:227-234`).
12. **Reports fail silently.** An unknown key or id is ignored. A parameter mismatch is logged and the report dropped. The category in `DEC_RC*` is whatever `type` you pass (§4.7).
13. **Behavior names are not validated at load.** A mission whose `dia-behavior` has no Lua implementation fails only when it is started (`DEC_Model_ABC.cpp` `ReadMission`).
14. **Brains are shared per script file.** All entities whose model uses the same `file` share one archetype and one Lua VM (`DEC_Decision.cpp:1503-1513`, `Brain.cpp:141-153`). Isolation of globals between children is **(unverified)**; avoid writing true globals. A brain reload creates a private archetype, so reloaded entities stop sharing.
15. **Dead-unit handling differs by brain type.** A dead non-spark pion has its brain **reloaded** (all Lua state lost) on the first dead tick. A spark pion gets `StopTasks` + `DeadCallback`, and `StartDefaultTask` on resurrection (`MIL_AgentPion.cpp:388-410`). A dead crowd appears to reset its default behavior every tick (§2.5).
16. **Lua timeout** is 30 s per `SelectActions` / `Trigger` / `ExecuteScript` call by default. A timeout is treated like an error, so the mission becomes impossible **(unverified exact exception path)**.
17. **French and English names and deprecated functions coexist.** Deprecated or no-op names include:
    - `_DEC__StartControlerZone`, `_DEC__StartOccuperObjet`, `DEC_Elongation`;
    - `_DEC_Agent_TempsPourParcourirDistanceEnLigneDroite`, `DEC_AutonomieEnDeplacement`, `_DEC_Agent_ImmuniserNbc` / `_DEC_Agent_StopImmuniserNbc` (comments at `DEC_AgentFunctions.cpp:183,197-198,250`);
    - `DEC_ConnaissanceObjet_EstBreche` (`IsBreachedDeprecated`);
    - the medical sorting and healing toggles, which have empty bodies (`DEC_LogisticFunctions.cpp:428-446`);
    - `DEC_CreerLienObjetVersReseau` and `DEC_DetruireObjetResourceSansDelais` ("obsolete?").

    Common vocabulary: Embarquer = load, Débarquer = unload, Tir = fire, Itinéraire = path, Fuseau = zone between limits, Connaissance = knowledge, Renfort = reinforcement, Valoriser = mine/improve an obstacle, Contournement = bypass, EtatOps = operational state.
18. **Duplicate global names**: `DEC_GetRawMission`, `DEC_ConnaissanceObjet_Localisation`, `DEC_MissionParameter_CreateDirection`. The last registration wins; the bodies are currently equivalent.
19. **Remote entities have no brain.** Pions and automats skip `InitBrain` when remote (`DEC_RolePion_Decision.cpp:782-795`).

Not yet explored: the DirectAI internals of `DeliverMessages` and spark task semantics, per-role return-code enums for every action, crowd and task-group mission context handling, `DEC_PathWalker` point representations (`_DEC_GetPointsCategory`).
