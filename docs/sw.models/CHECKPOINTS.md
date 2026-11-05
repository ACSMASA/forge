# Checkpoints: what survives a save + resume, hazards, conventions, sim-side gaps

Valid for **any** behavior (unit missions, frag orders, default tasks, skills). A checkpoint saves a game session;
resuming it rebuilds every brain from scratch and restarts missions from saved data. A behavior is
**checkpoint-resilient** when, after resume, it continues where it was without redoing side effects or losing state.
Read with `MISSIONS.md` (and `FRAGORDERS.md` for frag orders). Aliases as there (`dia5/` `phy/` `sim/` `int/` `spark/` `val/`); `file::text` = an anchor.
Written from sw.models `master` @ `d2f78fafcd`, sw.sword `master` @ `51bdc0ec9e`, directai `master` @ `9629aaf1a1`.

## 1. Saved, lost, rerun

| What | Fate | Anchor |
|---|---|---|
| Per-brain decisional state: one string = `persistentState[""]` (per-skill states `[skillName][getPersistentKey(params)]`) + `spark.saveState()` | **saved**, as attribute `state` per entity | `int/SimulationIntegration.lua::integration.savePersistentState = function()`, `sim/Decision/DEC_Decision.h::WriteDecisionalState( xml::xosubstream xos )`, `sim/Decision/Brain.cpp::savePersistentState` |
| Spark store: per task `seqStageIndex`, per-stage-kind tables `seqStage` / `parStage` (one table per behavior **index**, shared by every sequential stage) | saved (inside the string above) | `spark/persistence.lua::function spark.saveState()`, `spark/task.lua::local key = stage == task.parallel and "parStage" or "seqStage"` |
| The current **unit**, **crowd** and **task-group** mission with its parameters | saved; a mission with an invalid parameter is silently not saved; automat missions are not saved | `sim/CheckPoints/MIL_CheckPointManager.cpp::WriteMissions`, `sim/Entities/MIL_EntityManager.cpp::missionController_->WriteMissions( xos );`, `sim/MissionController.cpp::crowd-mission` |
| Knowledge (with ids), ODB decision attributes (unit ROE `roe`, crowd ROE `roe-crowd`: so a frag order's ROE effect survives) | saved | `sim/Entities/Agents/Roles/Decision/DEC_RolePion_Decision.cpp::WriteODB`, `sim/Entities/Agents/Roles/Decision/DEC_RolePion_Decision.cpp::xml::attribute( "roe-crowd", pRoePopulation_, PHY_RoePopulation::Default() )` |
| **Frag orders waiting** in an entity's orders category (the kernel queue) | **lost**: the queue has no serialization and no checkpoint file holds it; nothing re-delivers or re-acks them | `sim/Decision/DEC_Representations.h::std::vector< boost::shared_ptr< MIL_FragOrder > > orderRepresentations_;`, `sim/CheckPoints/MIL_CheckPointManager.cpp::ws.WriteMissions(` |
| A **running frago task** (started by a frag order) | **not resumed**: Spark saves its `seqStageIndex`, but not the list of active behaviors, and only the saved mission gets `spark.resume`. The stage index stays orphaned in the store until a later `start` of the same task clears it | `spark/behavior.lua::activeBehaviors = {},`, `int/SimulationIntegration.lua::ResumeEvent = function( task, params )`, `directai/data/sparkle/src/sparkle/Behavior.lua::clearPersistentBehaviorState( self.name )` |
| Orders from the commander (`myself.fragOrders`, Lua messages), `myself.fragorderToClean`, frag-order effects kept in `myself` (`ROEGivenByFragOrder`, `blocked`, `isEngaged`…) | **lost** (Lua VM) | `dia5/_scripts/MessageTreatments.lua::myself.fragOrders[ content.fragOrder ] = content.fragOrder`, `int/FragOrderIntegration.lua::myself.ROEGivenByFragOrder =` |
| The order / mission id counter | **rebuilt** as the max saved mission id + 1: an id given after resume can repeat a frag-order id given before the save | `sim/MissionController.cpp::counter_ = std::max( counter_, id + 1 );` |
| The Lua VM: node instances, `self.*`, `myself.*` (`currentMission`, `taskParams`, `previousTaskParams`, `leadData`…), `meKnowledge` fields, knowledge caches, `startStopAction` state, pending messages, `scripts.registerActionCallback` entries | **lost** | `int/SimulationIntegration.lua::myself.taskParams = params`, `dia5/_scripts/Scripts.lua::scripts.registerActionCallback = function( actionID )` |
| World state: objects with their construction completion, knowledge, unit positions | **saved**; some integration starters recover from it, e.g. a planned work resumes the nearest object of its type within 10 m (`_DEC_StartReprendreTravauxObjet`). Read the starter before calling a hazard infrastructural. An object saved at completion 0 and not built by the ADN (`isBuiltByGen_` is not saved) is destroyed on load | `sim/Entities/Objects/ConstructionAttribute.cpp::xos << xml::attribute( "completion"`, `int/BuildableIntegration.lua::integration.startBuildItSecu = function( object, objectType )`, `sim/Entities/Objects/ConstructionAttribute.cpp::return !isBuiltByGen_ && constructionPercentage_.Get() == 0.;` |
| Pion kernel actions (move, fire, build…) | **lost**: not serialized; skills `start` them again with new ids from the sim id space | `sim/Entities/Actions/PHY_Actor.h::std::map< unsigned int, boost::shared_ptr< PHY_Action_ABC > > actions_;`, `sim/Entities/Actions/PHY_Action_ABC.cpp::GetSimIds().AllocateId()` |
| The task at the saved stage | **rerun from the stage's beginning**: node `create` / `start` run again, `willStart` and stage-start reports are re-sent | `spark/task.lua::self.stageIndex = self.state and self.state.seqStageIndex or 0` |

## 2. Resume sequence (hops)

1. Fresh brain per entity, `InitMePlatoon` resets the store: `int/SimulationIntegration.lua::persistentState[ rootKey ] = {}`.
2. Default task scheduled (role `<default-task>`), as at creation.
3. `integration.loadPersistentState` replaces the store and Spark's state:
   `int/SimulationIntegration.lua::spark.loadState( daiState )`, `sim/Decision/Brain.cpp::integration["loadPersistentState"]( state )`.
4. Missions read from the checkpoint; first tick `MIL_PionMission::Start` with `toResume_`:
   `sim/Entities/Orders/MIL_PionMission.cpp::pion_.GetDecision().ResumeMissionBehavior( self );`.
5. `ResumeEvent` → `spark.resume( task, params )`: `int/SimulationIntegration.lua::ResumeEvent = function( task, params )`,
   `spark/behavior.lua::function spark.resume( behavior, parameters, priority )`.
6. The task node's `create` reads `seqStageIndex` and restarts that stage; a saved index past the last stage → the
   task is final at once (counts as finished, silently): `spark/task.lua::self.final = true`.
7. There is **no resume hook**: a skill knows it resumed only because its persisted state is non-empty.
8. **Only the saved mission is resumed.** Frag orders are not in the checkpoint, and no frago task gets
   `spark.resume`. The unit carries on with its mission as if the order had never been given (a paused unit moves
   again): `int/SimulationIntegration.lua::StartTask = function( task, params )` has no resume counterpart. Fix pair:
   §7.

## 3. Hazards

| Hazard | Effect after resume | Anchor | Model-side fix |
|---|---|---|---|
| **Whole-state drop**: one non-serializable value anywhere in the brain's store (function, userdata, a knowledge object through its fields, a cycle) | the brain's whole state is not written (log "Error serializing persistent state" only): every task restarts at stage 1, every skill state is empty | `int/SimulationIntegration.lua::error( "cannot serialize " .. type( v ) )`, `sim/Decision/DEC_Decision.h::Error serializing persistent state of` | persist plain data only (§4); `tools/checkpoint_lint.py` flags stored objects |
| **Unhashable parameter**: `getSkillState` on a skill whose params can hold a concrete class without `hash`, own or inherited (PhaseLine, AreaOfResponsibility, Direction, FragOrder (e.g. `ManageFragOrder`'s `fragOrders`), EquipmentType, CBRNProtection, HumanWound; MeKnowledge inherits `Agent:hash`) | Lua error in the skill every tick | `int/SimulationIntegration.lua::has no 'hash' function defined.` | add `hash` to the class (`mission_index.md` §1 hashable column), or do not persist in that skill |
| **`pairs` key order**: the key concatenates params in `pairs` order, unsorted; stock Lua 5.2.4 seeds string hashing per state (`directai/include/lua/lua.h::LUA_VERSION_RELEASE`) | with ≥ 2 params the key may differ after resume → empty state | `int/SimulationIntegration.lua::for k, v in pairs( value ) do` | persist in a skill with one parameter, or under a key you build yourself (sorted) in the Default skill's state; model-wide: sort the keys in the `dia5/_scripts/Scripts.lua::scripts.getSkillState = function( node )` helpers (every ada skill that uses them) |
| **`or default` on booleans**: `getSkillState( self ).x or true` | a saved `false` becomes `true` | `dia5/navy/agent/skills/EmbarkToLandingShipDock.lua::askForLoadWhenFinished or true` | test `~= nil` |
| **Table equality**: `x == {}` | always false | `dia5/navy/agent/skills/LoadUponRequest.lua::== {}` | `next( x ) == nil` |
| **Stale action id** persisted | the action no longer exists; stopping it is a no-op; the id may be reallocated | `sim/Entities/Actions/PHY_Action_ABC.cpp::GetSimIds().AllocateId()` | never persist action ids; restart the action in `start` |
| **Non-idempotent `create` / `start`** (`issueMission`, `createEmptyUnit`, `displayReport`, `sendMessage`) | the side effect happens twice (a second unit, missions re-issued, duplicate reports) | `dia5/common/agent/behaviors/skills/actions/DetachUnit.lua::createEmptyUnit`, `dia5/common/commander/behaviors/skills/actions/IssueMission.lua::meKnowledge:issueMission` | persist a "done" flag (or the created id) before / right after the effect and test it first |
| **Lua-only progress flags / timers** in `self` (or in a knowledge's `[myself]` table: `utilities.waitInMin`, used by StandBy, Wait and the CBRN protection skills) | progress restarts from zero; absolute deadlines are lost | `dia5/military/agent/skills/OperateACheckpoint.lua::self.checkPointBuilt = true`, `dia5/navy/agent/skills/Unmoor.lua::self.unberthingEndDate`, `int/ToolsFunctions.lua::knowledge[myself].tempsDebut = knowledge[myself].tempsDebut or getSimulationTime()` | persist the flag / the absolute `utilities.getRealTime()` deadline (the sim date is checkpointed: `sim/MIL_AgentServer.cpp::xml::attribute( "real", xml::date( nRealTime_ ) )`) |
| **Re-sent reports**: `willStart`, stage start | `eRC_BM_DebutMission`, `eRC_BM_DebutPhase` appear again | `int/CRTaskListener.lua::"eRC_BM_DebutMission"` | none model-side (sim-side gap) |
| **Parent link lost**: the resumed task is started by the sim, not by its automat | `TaskDone` / commander bookkeeping that relies on `myself` state is gone | `int/SimulationIntegration.lua::myself.currentMission = task` | mirror needed `myself` data into the Default skill's state |
| **Stage-shared `seqStage` tables**: behavior *i* of every sequential stage gets the same persisted table | `self:getPersistentState()` in stage 2 sees stage 1's data | `spark/task.lua::stageState = taskState[ key ]` | prefer `scripts.getSkillState`; clear what you write |
| **Never-cleared skill states**: nothing clears them on task stop | the next run of the same skill with equal params starts from old data | `int/SimulationIntegration.lua::integration.clearSkillState = function( node )` | `scripts.clearSkillState( self )` in `destroy` and when the skill completes |
| **State left by a mission that is not resumed**: `destroy` never ran before the save, and the saved mission is dropped (invalid parameter, automat mission, **any frago task**) or replaced after resume | a later skill with equal params reads the old state as if it resumed | `sim/CheckPoints/MIL_CheckPointManager.cpp::WriteMissions` | treat a saved deadline older than one tick as stale (`utilities.getTickDurationInSeconds()`), or store the mission's start date with the state |
| **Persisting `NIL`** (the absent-parameter sentinel) | serialized as `{}`, reloaded as an empty table: `x ~= NIL` becomes true | `spark/compatibility.lua::setsharedglobal( "NIL"` | persist a boolean "absent" instead |
| **Non-finite numbers**: `tostring( math.huge )` gives a name such as `inf`, reloaded as a global (nil) | the value becomes nil | `int/SimulationIntegration.lua::return tostring( v )` | do not persist infinities / NaN |
| **Frag order lost**: a queued order, or the frago task it started (kinds (a), (c), (e) of `FRAGORDERS.md` §2) | the order's effect disappears: the unit resumes its mission as if the order had never been given | `spark/behavior.lua::activeBehaviors = {},` | none generic: the Lua-side design in §7; or tell the user the order must be given again |
| **Consumed order's effect kept in `self`**: a kind-(b) reader consumes the order and keeps what it derived only in a field | the effect is lost (the order was cleaned, so it is not read again) | `dia5/common/agent/behaviors/skills/MoveAlong.lua::self.pathType = eTypeItiInfiltration` | persist the derived value in the reader's skill state (§4) |
| **Frag-order effect kept in `myself`** by an initializer (kind (f)) | the effect is lost; ODB effects (ROE) are not | `dia5/FragOrder.lua::myself.isEngaged = true` | mirror it into the Default skill's state (§4), or set it through an integration call that reaches the ODB |
| **Orphan frago stage**: a frago task's saved `seqStageIndex` stays in the store after resume | harmless until something calls `spark.resume` on that task, which jumps to the stale stage; `start` clears it | `directai/data/sparkle/src/sparkle/Behavior.lua::clearPersistentBehaviorState( self.name )` | resume a frago task only from a record written by its own run (§7), else `start` it |
| **Removed or renumbered stage** between save and resume (model edited) | the task resumes at another stage or counts as finished | `spark/task.lua::self.state.seqStageIndex = self.stageIndex` | none (do not edit models under a running exercise) |

`tools/checkpoint_lint.py` (`--skill`, `--task`, `--all`) flags most rows above with file:line (rules
`lost-progress`, `unhashable`, `key-order`, `or-true`, `stored-object`, `no-clear`, `side-effect`, `re-report`,
`table-eq`, `lost-timer`); findings are upper bounds to confirm in the code. The four frag-order rows are not linted
yet (`--fragorder`, step 7 of `docs/handoffs/fragorder-builder.md`). Out of its scope: state kept in world
objects (`object[myself]`, `startStopAction` state) and in integration functions.

## 4. Conventions (from the model)

- Persist **plain data only**: ids (`getID`, back with `meKnowledge:getAgentFromID` or
  `integration.getAgentKnowledge( agentId, knowledgeId )`), XY points (`scripts.setSkillStatePointElement` /
  `getSkillStatePointElement`), indices, booleans, and absolute deadlines from `utilities.getRealTime()`:
  `dia5/_scripts/Scripts.lua::scripts.setSkillStatePointElement = function( node, key, position )`,
  `int/ToolsFunctions.lua::utilities.getRealTime = function()`.
- Read in `create` with `~= nil` (not `or default`); write on every change (`scripts.setSkillStateElement`); clear in
  `destroy` and on completion (`scripts.clearSkillState`): `dia5/_scripts/Scripts.lua::scripts.setSkillStateElement = function( node, key, value )`.
- Example to copy: `dia5/military/agent/skills/Delay.lua::flaggingIndex` (one parameter, so a stable key; an index
  written on every change, cleared in `destroy`). The same index pattern in
  `dia5/common/agent/behaviors/skills/MoveAlong.lua::scripts.getSkillState( self ).nextTargetIndex` is **not** safe as
  is: its key spans 6 parameters (`pairs` order hazard) and a non-looped completion leaves the state uncleared.
- Mirror `myself` state into the Default skill's state and restore it in its `create`, as for the detacher and the
  transporter: `dia5/military/agent/skills/Default.lua::self.detacherID = scripts.getSkillState( self ).detacherID`.
- Never persist knowledge objects, action ids or closures. Make `create` / `start` idempotent: test the persisted
  state before any side effect.
- **Frag-order readers persist the derived value**, not the order: once cleaned, the order cannot be read again. Do
  it on change, read it back in `create`:
  - `dia5/navy/agent/skills/ManageDiving.lua::scripts.getSkillState( self ).targetDepth`;
  - `dia5/navy/agent/skills/Transit.lua::scripts.getSkillState( self ).maxSpeedOnWater`.

  Both read with `or <number>`: safe for numbers, not for booleans.
- Spark also offers root-node `self:getPersistentState()` (unused in dia5; see the `seqStage` hazard):
  `spark/skill.lua::daiNode.getPersistentState = getPersistentRootNodeState`.
- Model helpers wrap the integration ones: `dia5/_scripts/Scripts.lua::scripts.getSkillState = function( node )`.
  `scripts.setSkillState` has no caller; ~18 skills use the helpers (`mission_index.md` §3 checkpoint column).

## 5. Test recipe (for the user; agents never run the sim)

1. Run the exercise, give the mission, let it reach the stage to test.
2. Save: `simulation_app --savecheckpoint <name>` (test mode) or a checkpoint from the GUI:
   `sim/Tools/MIL_Config.cpp::"savecheckpoint"`.
3. Resume: `simulation_app --checkpoint <name>`: `sw.sword/src/libraries/core/SessionConfig.cpp::"checkpoint"`.
4. Check: the stage reports after resume, the unit continues (not restarts) its stage, no duplicate side effects, no
   "Error serializing persistent state" / "has no 'hash'" in the sim log.
5. Automated: Go simtests `sw.sword/src/masa/sword/simtests/checkpoint_test.go::func checkpointAndRestart`; no simtest
   checks the decisional state yet.
6. **Frag order**: give the mission if the order needs one, give the order, and save while its task runs (or, for a
   kind-(b) order, after the reader consumed it). After resume, check:
   - whether the effect is still there: today a frago task is gone (§1);
   - whether a derived value persisted by the reader is reapplied;
   - that ODB effects (ROE) remain.

   Frag-order simtests: `sw.sword/src/masa/sword/simtests/fragorder_test.go::func TestUnitFragOrder`. None
   checkpoints with an order active.

## 6. Sim-side gaps

Candidate sw.sword changes. The builder **proposes** them as diffs, never writes them, and always gives the Lua-side
fix too (§3). **Blast radius** (measure, do not guess): callers and model-side overrides of the function in
`docs/sw.sword/integration_index.md` (§1 row, §2 model-side defs); model callers by `git -C C:/MASARepo/sw.models grep -n`;
sw.soult the same (it also consumes ada's common entities through its submodule `modules/sw.models`, pinned, and
targets its own `version.major`, `sw.soult/build/build.xml::<property name="version.major"`); client branches in one
call: `git -C C:/MASARepo/sw.models grep -c -e <name> $(git -C C:/MASARepo/sw.models for-each-ref --format='%(refname:short)' refs/heads refs/remotes) -- '*.lua' '*.bms'`
(a loop per branch times out); checkpoint simtests to extend. **Version freeze**: the sw.sword version a sw.models branch targets is
`build/build.xml::<property name="version.major"` (same property in `sw.sword/build/build.xml::<property name="version.major"`;
`dec/decisional.xml` `model-version` is stale). Equal to sw.sword master's → a sim-side change can land on master;
lower → the branch targets a released line (tags `vX.Y.Z`, branches `origin/X.Y`): treat sw.sword as frozen, ask the
user, recommend the Lua-side fix.

| Gap | Anchor | Candidate sw.sword change | Measure |
|---|---|---|---|
| The serializer drops the whole brain state on one bad value | `int/SimulationIntegration.lua::local function serialize( v )` | skip (and log the path of) non-serializable values instead of erroring | callers of `integration.savePersistentState`; behavior change only for states that were dropped |
| `getPersistentKey` errors on unhashable params and depends on `pairs` order | `int/SimulationIntegration.lua::integration.getPersistentKey = function( value )` | sort keys (numbers before strings: arrays and single-key tables keep their key, so only keys that were already unstable change); fall back to a stable id (`getID`) or skip unhashable values | callers of `integration.getSkillState` / `getPersistentKey` (models, sw.soult, branches) |
| Skill states are never cleared when their task stops | `int/SimulationIntegration.lua::integration.getPersistentState = function( skillName, paramsHashcode )` | clear the states of a task's skills in `integration.stopTasks` / on `willStop` | skills that rely on state across missions (Default) must be exempt |
| Pion kernel actions are not saved; no stale-id guard | `sim/Entities/Actions/PHY_Actor.cpp::actions_.find( actionId )` | serialize running actions, or expose a "resumed" epoch so a stored id can be invalidated | every `StartAction<` registration (`docs/sw.sword/dec_function_index.md`); large |
| No resume hook / `resuming` flag | `int/SimulationIntegration.lua::ResumeEvent = function( task, params )` | set `myself.resuming = true` until the first tick ends (or a `spark` event) | additive; readers only |
| Reports re-sent and parent link lost on resume | `int/CRTaskListener.lua::willStart = function( self, taskName )` | skip `eRC_BM_DebutMission` / stage-start reports when resuming; restore the parent from the automat | report consumers (gaming, AAR); automat tasks |
| Construction resumes by a spatial lookup (same type within 10 m), not by the object's id | `int/BuildableIntegration.lua::local existingObject = integration.obtenirObjetProcheDe( object:getLocalisation(),` | persist the built object's sim id per planned work (`getPersistentState`), resume it by id, clear it in `stopBuildItSecu` | `integration_index.md` rows of `startBuildItSecu` / `stopBuildItSecu` (caller: `PlannedObject:buildIt`) |
| `myself.*` mission state is not persisted | `int/SimulationIntegration.lua::myself.taskParamsWithDefault = copyTable(` | persist a whitelist of `myself` fields (plain data) with the brain state | readers of each field (`integration_index.md`, model grep) |
| The frag-order queue is not serialized | `sim/Decision/DEC_Representations.h::std::vector< boost::shared_ptr< MIL_FragOrder > > orderRepresentations_;` | write each entity's pending orders (type + parameters, as `WriteMissions` does for missions) and refill the queue on load, without a new `eRC_FragOrderReceived` | `_DEC_GetOrdersCategory` / `integration.getOrdersCategory` callers (`integration_index.md`); the models' polling skills; `fragorder_test.go` + `checkpointAndRestart` |
| Frago tasks are not resumed | `int/SimulationIntegration.lua::StartTask = function( task, params )` | record the orders started by `StartFragOrderBehavior` (type + parameters) per entity, write them with the missions, and resume them through a `spark.resume` path; or persist Spark's active-behavior list. Design in §7 | `StartFragOrderBehavior` / `DEC_StartFragOrder` callers; model initializers that start tasks with `behavior:start` (outside the kernel path); sw.soult |
| Orders sent by a commander exist only in Lua (`myself.fragOrders`) | `dia5/common/commander/world/MeKnowledge.lua::integration.SendMessage( "FragOrder", receiver, { fragOrder = fragOrder } )` | deliver them through the kernel queue (so the two rows above cover them) | `sendFragOrder` / `sendFragOrderFromType` callers (model grep, sw.soult) |
| Order ids can repeat after resume | `sim/MissionController.cpp::counter_ = std::max( counter_, id + 1 );` | save the counter with the missions | clients that key actions by id (gaming, AAR) |

## 7. Frag orders across a save + resume

A reference fix pair, designed once (sw.sword `master` @ `51bdc0ec9e`, directai `master` @ `9629aaf1a1`, sw.models
`master` @ `d2f78fafcd`). The builder adapts it per order and recommends one side (§7.4). Neither is applied today:
**a task-starting frag order is lost on resume** (§1).

### 7.1 Spark facts both designs rely on
- `spark.resume` on an inactive task restarts the stage that was running at save, from that stage's beginning.
  `seqStageIndex` is the last **completed** stage. With no store entry, `resume` behaves exactly like `start`:
  `spark/task.lua::self.stageIndex = self.state and self.state.seqStageIndex or 0`,
  `directai/data/sparkle/src/sparkle/Behavior.lua::function Behavior.resume( self, parameters, priority )`.
- `start` clears the task's persisted state. `stop`, a `disable-when-done` finish, a `failed` and `spark.stopAll` clear
  it too (they all go through `Behavior.stop`). `suspend` does not. So the store's entries after a load are exactly the
  tasks active at save: `directai/data/sparkle/src/sparkle/Behavior.lua::clearPersistentBehaviorState( self.name )`.
- **Call `resume` once.** Each call makes a new activation: calling it every tick restarts the task every tick. Guard
  it with `spark.isRunning`: `spark/behavior.lua::function spark.isRunning`.
- **No API returns the parameters of a running task.** Only the code that starts it has them; the kernel's `StartTask`
  discards the returned table: `int/SimulationIntegration.lua::StartTask = function( task, params )`.
- **Listeners**: `spark.addBehaviorListener( t )` gets `willStart` / `willStop` / `finished` / `interrupted` with the
  task name. Listeners are not persisted; register one shared table once per brain:
  `spark/behavior.lua::function spark.addBehaviorListener`.
- **Timing**: the default task is itself resumed, and its nodes are created at the first tick, after the store is
  loaded. A `spark.resume` called there takes effect from the next tick.
- **Mission resume never runs `StopEvents`** (`ActivateOrder` with the resume flag calls only `ResumeEvent`), so a
  frago task resumed in the same tick survives: `sim/Decision/DEC_Decision.h::pRefs_->resumeEvent_( strBehavior, refMission );`.
  A **new** mission does stop frago tasks (`FRAGORDERS.md` §5).

### 7.2 Sim-side design (B): record started frag orders, write them with the missions, resume them
Proposal only (unified diff in the builder's answer, never written).
- **Record**: `DEC_Decision::StartFragOrderBehavior` (the kernel path, task groups included) tells the entity's
  order manager, which stores `(frag order, task name)` in a new per-entity map of `MissionController`. Anchors:
  `sim/Decision/DEC_Decision.h::pRefs_->startTask_( fragorder->GetDIAType(), refFrago );`,
  `sim/Entities/Orders/MIL_TaskGroupOrderManager.cpp::StartFragOrderBehavior( frag );`.
- **Drop the record**:
  - a new brain function `_DEC_FragOrderTaskStopped( myself, taskName )` called from an `else` branch of the
    listener's `willStop` (today only the mission gets `_DEC_FinMission`): `int/CRTaskListener.lua::_DEC_FinMission( myself )`;
  - plus clearing all of the entity's records in `DEC_Decision::StopMission` (a mission stop kills every task).
- **Write / read**: `MIL_FragOrder::Write` fills a `sword::FragOrder` (type, id, parameters through `type_.Copy`,
  as for the network) and writes it with `xml::content`, exactly like missions. `Read` = constructor + `SetParameters`
  in a try/catch, after knowledges are loaded.
  - Anchors: `sim/Entities/Orders/MIL_PionMission.cpp::xml::content( "unit-mission", msg )`,
    `sim/Entities/Orders/MIL_FragOrder.cpp::MIL_OrderContext()`, `sim/MissionController.cpp::void MissionController::WriteMissions(`.
  - Reuse the `InvalidParameterVisitor` to drop orders whose parameters no longer resolve. Bump `counter_` on read
    (fixes the id-reuse gap). Do not write the display extension (a raw pointer into the received message).
- **Resume**: a new Lua global `ResumeTask = function( task, params ) spark.resume( task, params ) end` beside
  `StartTask`, a `resumeTask_` script ref beside `startTask_` (`sim/Decision/DEC_Decision.cpp::startTask_`), and
  `ResumeFragOrderBehavior` (a copy of `StartFragOrderBehavior` calling `resumeTask_`).
  - Called from `MIL_OrderManager_ABC::Update` after `controller_.Start`, and also on its early-return path (an
    entity with a frago but no saved mission): `sim/Entities/Orders/MIL_OrderManager_ABC.cpp::if( pNextMission_ == pMission_ )`.
  - Drop a record that needs a mission when the entity's mission was not restored.
- **Scope limits**:
  - automats: their missions are not checkpointed, so they need their own branch in `ReadMissions` / `Initialize`:
    `sim/MissionController.cpp::void MissionController::Initialize(`;
  - tasks the model starts itself (`behavior:start( mission )`, 8 sites in `dia5/FragOrder.lua`) and `StartTask`
    messages to subordinates are invisible to C++. Covering them needs a model wrapper that records through a brain
    function **and reruns the initializer on resume** (side effects twice): prefer §7.3 for them.
- **Add-on (A)**: with `MIL_FragOrder::Write` / `Read` in place, serializing the orders queue too (the
  pending kind-(b) orders) costs about 20 lines.
- **Blast radius** (measured at the commits above):
  - `integration.startFragOrder` (alias of `DEC_StartFragOrder`): 1 sw.models user, 1 sw.soult user, no override.
  - `StartFragOrderBehavior`: 2 C++ callers (`DEC_OrdersFunctions.cpp`, `MIL_TaskGroupOrderManager.cpp`).
  - The global `StartTask`: no Lua caller anywhere (sw.sword, sw.models, sw.soult).
  - The `willStop` listener is registered for all four entity kinds.
  - Checkpoint format: no version attribute; an older checkpoint without the new elements still loads (inferred:
    `xml::list` runs zero times; test it).
  - Simtests: add a task-starting frag order + `sw.sword/src/masa/sword/simtests/checkpoint_test.go::func checkpointAndRestart`
    to `fragorder_test.go`, plus a task-group variant.
  - Ant: `checkdec`, `checkintegration`, `checkreports` (`CRTaskListener.lua` holds `eRC_` tokens).
- **Version freeze**: `version.major` is 6.29.0 in both sw.sword and sw.models `master`, so it can land on sw.sword
  master. A branch with a lower value is frozen (§6).

### 7.3 Lua-side design (model only): record plain parameters, resume from the polling query
- **Store**: one model-owned table under a **fixed** key, `integration.getPersistentState( "fragOrder", "tasks" )`
  (task name → plain parameters). It is saved in the brain state, with no `pairs` key-order hazard and no skill
  hashing: `int/SimulationIntegration.lua::integration.getPersistentState = function( skillName, paramsHashcode )`.
- **Record** where the model starts a frago task, with the `mission` table in hand:
  - in `fragOrder.startFragOrderTask` right after `dia5/FragOrder.lua::behavior:start( mission )`;
  - optionally in the `StartTask` message handler (orders from a commander):
    `dia5/_scripts/MessageTreatments.lua::behavior:start( content.params )`;
  - and at the direct `:start( mission )` sites (e.g. `dia5/FragOrder.lua::common.agent.tasks.frago.DetachUnit:start( mission )`).

  Convert each value to plain data (§4):
  - numbers, strings and booleans as they are;
  - a Point → `{ X, Y }` (rebuild: `dia5/_scripts/Scripts.lua::scripts.getSkillStatePointElement = function( node, key )`);
  - an Agent → its id (`dia5/common/agent/world/MeKnowledge.lua::getAgentFromID = function( self, id )`);
  - an AgentKnowledge → `{ agentId, knowledgeId }` (`integration.getAgentKnowledge`);
  - a UnitType → its name;
  - lists of these.

  Any other value (sim types: equipment, resource or dotation types in 7 frago tasks; CBRNProtection; objects without
  a rebuild helper) → **do not record**, and trace "not checkpointable".
- **Forget**: one module-level listener table, `willStop` / `interrupted` → remove the task's record (a finish,
  failure, opposite order or mission stop all end in `willStop`).
- **Resume** once per brain from `dia5/common/agent/queries/getFragOrders.lua::local ordres_recus = integration.getOrdersCategory()`
  (polled every tick by every unit, automat and crowd role that handles frag orders). Behind a guard in `myself`
  (not persisted, so it is nil after a load), it does three things:
  - registers the listener;
  - rebuilds each record's parameters;
  - calls `spark.resume( task, params )` when the task is not running; a record whose rebuild fails (e.g. the agent
    is dead) is dropped and traced.
- **Limits**:
  - queued, unconsumed orders (kind (b) waiting for a reader) stay lost;
  - kernel-path (c) orders: the Lua code never sees their parameters, so it covers only tasks **without**
    parameters (16 of 40 frago / kernel tasks). With parameters, it needs a pass-through initializer, against the
    `FRAGORDERS.md` §2 house style;
  - initializer side effects (`stopTask` of the opposite task, `myself.*` writes) are not replayed: only the task
    resumes. Mirror the `myself` effects separately (§3);
  - the resumed stage reruns `create` / `start` like any resumed mission (§1–§4 apply to its skills).
- **Blast radius**: `dia5/FragOrder.lua` and `getFragOrders.lua` exist on essentially every sw.models branch (185
  refs), so the edit reaches them through merges. Every role that polls frag orders (military / navy Default,
  security agents and Commander, Company, Crowd, TestPlatoon) gets the resume pass. sw.soult has its own copies
  (`sw.soult/data/data/models/sws/decisional/dia5/FragOrder.lua`) and is not affected; port it there if wanted.

### 7.4 Recommendation, per kind (`FRAGORDERS.md` §2)

| Kind | Recommended | Why |
|---|---|---|
| (a) initializer, (e) crowd | **Lua-side (§7.3)** | the parameters are Lua tables only the model has; the sim-side would have to rerun the initializer |
| (c) kernel path, with parameters | **Sim-side (B)**, as a proposal | only C++ holds the typed parameters. On a frozen branch: a pass-through initializer + Lua-side |
| (c) kernel path, no parameters | Lua-side (records the name) or sim-side | either; Lua-side needs no sw.sword change |
| (b) read by skills | neither: the reader persists the derived value (§4) | waiting orders need sim-side add-on (A) |
| (f) immediate | nothing to resume | mirror `myself` effects; ODB effects survive |
| task group | sim-side (B) | pushed by the kernel, no model polling |

Builder rule:
- Before proposing the Lua-side helper, check whether it already exists on the branch (grep
  `integration.getPersistentState( "fragOrder"` in `dia5/`). If it exists, add the new order's record call (and the
  converter case it needs); if not, write the helper once.
- Always state what stays lost: queued orders, side effects, values that cannot be converted.
