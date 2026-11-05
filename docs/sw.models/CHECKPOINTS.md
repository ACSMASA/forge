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

The reference fix pair (a sim-side design and a Lua-side design, with a recommendation) is written by step 4 of
`docs/handoffs/fragorder-builder.md`. Until then, the builder states for every order that a task-starting frag order is
lost on resume (§1, §3 "Frag order lost").
