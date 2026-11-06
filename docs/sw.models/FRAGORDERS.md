# Frag orders: anatomy, kinds, delivery, checks, build order

Scope: **frag orders** (fragmentary orders, `<fragorders>` in `Missions.xml`) of model `ada` given to **units,
automats and crowds**. Not task-group frag orders (their `taskgroup.tasks.*` live outside sw.models). Read with
`MISSIONS.md` (§2 parameter types and binding, §3 task rules), `CHECKPOINTS.md` (save / resume) and the generated
`mission_index.md`. **Rules here are one line each with an anchor**; the code is the truth: read it before relying on
a rule.

Aliases (expanded by `tools/check_themes.py`): `dia5/` `dec/` `phy/` (mildb) `wwt/` (worldwide-tests) as in
`THEMES.md`; `sim/` = `sw.sword/src/libraries/simulation/`, `int/` = `sw.sword/data/app-data/resources/integration/`,
`spark/` = `directai/src/libraries/plugins/spark/`. Other paths are relative to the sw.models root. `file::text` =
that text occurs in that file. Written from sw.models `master` @ `d2f78fafcd`, sw.sword `master` @ `51bdc0ec9e`,
directai `master` @ `9629aaf1a1`.

## 1. Anatomy: what one frag order touches

A frag order lives in one DB or both (61 mildb-only, 31 wwt-only, 38 shared). Even shared names may differ in id,
dia-type or parameters, and the same dia-type may carry another name (`Infiltrate` is "Use infiltration path" in
mildb). Every row applies **per DB that has the frag order**, unless it says otherwise.

| File | Element / field | Rule | Anchor |
|---|---|---|---|
| `phy/Missions.xml`, `wwt/Missions.xml` | `<fragorders><fragorder>` | Append at the end of `<fragorders>` (no sort order; before `<categories>` in mildb, last section in wwt). Attributes alphabetical: `available-without-mission` `category` (mildb only, optional) `dia-type` `id` `name`. | `phy/Missions.xml::<fragorders>`, `sim/Entities/Orders/MIL_FragOrderType.cpp::xml::start( "fragorders" )` |
| same | `id` | Unique across **all** sections of the file (`phydb.go`); the sim compares it only with frag orders. Convention: max `id` over both DBs + 1, same id in each DB (ids already differ across DBs for 92 entries: never add more). | `sim/Entities/Orders/MIL_FragOrderType.cpp::Frag order already defined`, `src/models/phydb.go::duplicate mission identifiers found` |
| same | `name` | Unique among frag orders (throws at load); the key of Models.xml, the `.ts` files and the sheet file names. | `sim/Entities/Orders/MIL_FragOrderType.cpp::Entity fragOrder name already defined` |
| same | `dia-type` | The key of every Lua lookup: `FragOrder.lua` dispatch, `DEC_IsFragOrderAvailable`, `CreateFragOrder`. A duplicate is only a **warning and the last one wins**. Three styles: CamelCase (`PauseMovement`), legacy `Rep_OrderConduite_*`, and a dotted task name (kind (c), §2). | `sim/Entities/Orders/MIL_FragOrderType.cpp::defined multiple times`, `sim/Decision/DEC_OrdersFunctions.cpp::MIL_FragOrderType::FindByDiaType( fragorder )` |
| same | `available-without-mission` | Default false. `true`: the order is accepted with **no** Models.xml check. `false`: the entity needs a current mission whose model allows the order (§3). `available-for-all-mission` is read but dead. | `sim/Entities/Orders/MIL_FragOrderType.cpp::xml::attribute( "available-without-mission", bAvailableWithoutMission_ )`, `sim/Entities/Orders/MIL_FragOrderType.h::IsAvailableForAllMissions` |
| same | `category` | mildb only, optional (13 frag orders have none): a `<categories>` name shared with missions. A new one is translated in `mission-categories`. | `phy/Missions.xml::<categories>`, `phy/Locales/fr/Missions_fr.ts::<name>mission-categories</name>` |
| same | `<parameter>` | **No context block**: every parameter is a real one. Attributes and types as for missions (`MISSIONS.md` §2). ADN extras seen only here: `owned-transmitters-only`, `network-instance`, `direct-liaison-instance`, `consistent-unit-type`, `unit-type-display-range`; children `<value>`, `<units>`, `<objects>`, `<choice>`, `<transmitters>`, `<equipments>`. | `sim/Entities/Orders/MIL_FragOrderType.cpp::MIL_OrderType_ABC        ( nID, 0, xis )`, `phy/Missions.xml::owned-transmitters-only` |
| `phy/Models.xml`, `wwt/Models.xml` | model-level `<fragorders><fragorder name/>` | Allows the order **whatever the mission**, in unit, automat and crowd models alike. Indent 8; append (lists are not sorted); expand `<fragorders/>` when empty. An unknown name fails the load (lookup by **name**). | `sim/Decision/DEC_Model_ABC.cpp::Unknown orderConduite type`, `sim/Decision/DEC_Model_ABC.cpp::DEC_Model_ABC::IsFragOrderAvailableForMission` |
| same | `<missions><mission name><fragorder name/>` | Allows the order **during that mission only**, for that model. Indent 10; append; expand a self-closed `<mission/>`. The usual pair is "Pause movement" + "Resume movement" (+ "End mission" in wwt). | `phy/Models.xml::<fragorder name="Pause movement"/>` |
| `phy/Stages.xml`, `wwt/Stages.xml`, `phy/Locales/fr/Stages_fr.ts` | `<stage key value>` | Frag-order names are not stages, but a **new frago task's stage labels** are: both DBs + `Stages_fr.ts`, as for a mission task (`MISSIONS.md` §1). | `phy/Stages.xml::destroying the target`, `dia5/military/agent/tasks/frago/AirDestroyAircraft.xml::destroying the target` |
| `phy/Locales/fr/Missions_fr.ts` | contexts | mildb only. Context `frag-order`: the name. Context `frag-order - <name>`: every parameter `name` + every enum `<value name>`, **deduplicated** (no context when there are no parameters). Contexts and messages sorted by name / source. Not checked by any ant target. | `phy/Locales/fr/Missions_fr.ts::<name>frag-order</name>`, `phy/Locales/fr/Missions_fr.ts::<name>frag-order - Add itinerary</name>` |
| `phy/Mission sheets/fragmentary orders/`, `wwt/Mission sheets/fragmentary orders/` | `<name>.xml` + `<name>.html` | Required pair per DB (fatal). Same sheet format as unit missions; `<parameters/>` when there are none. The `.html` is ADN output (`\r\r\n` line ends); `tools/mission_scaffold.py` reproduces it byte-exact. | `phy/physical.xml::fragorders-mission-sheets-directory`, `src/checkdec/checkdec.go::checkMissionsHaveSheets` |
| `phy/Mission sheets/fragmentary orders/Locales/fr` | `<French name>.xml` + `.html` | mildb only; French name = the `frag-order` translation. The `.html` image paths keep the **English** name. Not checked. | `phy/Mission sheets/fragmentary orders/Pause movement.xml` |
| `dia5/FragOrder.lua` | `fragOrderTaskInitializers`, `fragOrdersToBePropagated`, `fragOrder.executeAutomatFragOrder` | The only registry of frag-order behavior, `include`d once. **CRLF**; entries grouped by theme (insert next to a related entry, not sorted, not appended); initializer format `    [ "<dia-type>" ] = function( mission, fragOrder )` … `    end,`. Needed by kinds (a), (b), (f), (d) (§2); not by kind (c). | `dia5/Decisional.lua::include "FragOrder.lua"`, `dia5/FragOrder.lua::fragOrderTaskInitializers =`, `dia5/FragOrder.lua::fragOrdersToBePropagated =` |
| dia5 frago task | `<family>/agent/tasks/frago/<Task>.xml` | Spark 1.5 task, no Lua (`MISSIONS.md` §3, §5 below). Folders: `dia5/common/agent/tasks/frago/`, `dia5/military/agent/tasks/frago/`, `dia5/security/agent/behaviors/frago/`, `dia5/crowd/behaviors/tasks/frago/`. | `dia5/common/agent/tasks/frago/WaitThere.xml` |
| `phy/mappings/hla.xml` | `<fragOrders>` | Fixed pause / resume / cancel names; never touched by a new frag order. | `phy/mappings/hla.xml::<fragOrders>` |

## 2. Kinds: how a frag order is wired

`dia5/FragOrder.lua::fragOrder.startFragOrderTask = function` dispatches on the dia-type:
- **No initializer**: `integration.startFragOrder` (the kernel starts a task named by the dia-type), then clean.
  Anchor: `dia5/FragOrder.lua::integration.startFragOrder( myself, fragOrder.source )`.
- **Initializer returns `true[, task]`**: clean, `spark.getBehavior` (`error` if missing), `start( mission )`.
  Anchor: `dia5/FragOrder.lua::error( "No behavior associated to DIA type: "`.
- **Any other return**: nothing is started. The initializer either cleaned the order itself, or left it for a skill.

| The designer wants | Kind | Write | Example | How it ends | Survives a checkpoint? |
|---|---|---|---|---|---|
| A task beside the mission, parameters 1:1 | **(c) kernel** (house style, first choice) | dia-type = the task's full name; `dia-name`s = the task's `<parameter name>`s; **no** `FragOrder.lua` entry | `military.agent.tasks.frago.AirDestroyAircraft` | `disable-when-done`, or a mission stop | No: the task is not resumed (`CHECKPOINTS.md`) |
| A task, but parameters to convert or validate, an opposite task to stop, or the task chosen at run time | **(a) initializer** | an initializer filling `mission.<param>` and returning `true[, "<task>"]`; the task | `dia5/FragOrder.lua::return true, "common.agent.tasks.frago.WaitThere"`, computed: `dia5/FragOrder.lua::return true, orderType` | `disable-when-done`, the opposite order's `stopTask`, or a mission stop | No (as kind (c)) |
| A change in how the **running mission's** skills behave | **(b) read by skills** | an **empty** initializer (without one, the kernel looks for a task named by the dia-type) + a reader in the skill or query | `dia5/FragOrder.lua::[ "Infiltrate" ] = function`, read by `dia5/common/agent/behaviors/skills/actions/MoveAndTakePosition.lua::getInfiltrateFragOrder` | The reader consumes it (`cleanFragOrder` / `getFirstFragOrderFromType`); read by nothing → **never cleaned** | Only if the reader persists the derived value |
| An instantaneous effect (state, ROE, report) | **(f) immediate** | an initializer that acts, then `integration.cleanFragOrder( fragOrder )` | `dia5/FragOrder.lua::[ "SetSpeedLimit" ] = function` | At once | If the effect lives in the ODB / world (unit and crowd ROE: yes; `myself.*`: no) |
| An order to a company that its platoons execute | **(d) automat** | the dia-type in `fragOrdersToBePropagated` (the platoons then run their **unit** kind), and/or a branch in `executeAutomatFragOrder` | `dia5/military/commander/skills/ManageFragOrder.lua::typeOrder == "Realign"` | Per the unit kind | Per the unit kind; Lua-only orders from the commander are lost |
| An order to a crowd | **(e) crowd** | as for units, with a crowd frago task; crowd models | `dia5/FragOrder.lua::[ "CrowdWait" ] = function` → `dia5/crowd/behaviors/tasks/frago/Wait.xml`, stopped by `dia5/FragOrder.lua::stopTask( "crowd.behaviors.tasks.frago.Wait" )` | The opposite order, or a mission stop | No (as kind (c)) |

- **House style**: the review fix `6db5a91523` deleted the initializers and propagation entries that `39dacf24df` had
  added for two kind-(c) orders. Add an initializer only when the kind-(c) path cannot express the order.
- **An initializer that returns without cleaning is rerun every tick** while the order stays in the queue (the polling
  skill calls `startTask` on every pending order): clean it unless a reader must still see it.
- **Client extensions** add initializers through `extensionsFragosInit`; the base file wins on a key clash:
  `dia5/FragOrder.lua::extensionsFragosInit = extensionsFragosInit or {}`.

## 3. Delivery, availability, cleaning

- **Order time** (unit, automat, crowd and task group alike): not `available-without-mission` and (no current mission,
  or the model allows it neither at model level nor under that mission) → `error_invalid_frag_order`; parameters
  checked like a mission's: `sim/Entities/Orders/MIL_PionOrderManager.cpp::!pType->IsAvailableWithoutMission()`,
  `sim/Decision/DEC_Model_ABC.cpp::DEC_Model_ABC::IsFragOrderAvailableForMission`. Example:
  `sw.sword/src/masa/sword/simtests/fragorder_test.go::Crowd frag order is unavailable without a mission`.
- **Lua-side availability** (`integration.isFragOrderAvailable`, used by commander propagation) looks up by dia-type
  and is **stricter**: an `available-without-mission` order needs the entity's **model-level** list:
  `sim/Decision/DEC_OrdersFunctions.cpp::MIL_FragOrderType::FindByDiaType( fragorder )`.
- **Queue**: the kernel adds the order to the entity's orders category, acks it and posts `eRC_FragOrderReceived`;
  no other report (stage and end reports go only to the mission, §5):
  `sim/Entities/Orders/MIL_PionOrderManager.cpp::representation.AddToOrdersCategory( frag );`,
  `sim/Entities/Orders/MIL_FragOrder.cpp::report::eRC_FragOrderReceived`. Task groups get it pushed instead:
  `sim/Entities/Orders/MIL_TaskGroupOrderManager.cpp::StartFragOrderBehavior`.
- **Polling**, every tick, by the role's default task: `Default` → `ManageFragOrder` → `startTask` on each order of
  `getFragOrders` (the kernel queue + `myself.fragOrders`, orders sent by the commander in Lua):
  `dia5/military/agent/roles/Agent.xml::manageFragOrder`,
  `dia5/military/agent/skills/Default.lua::Activate( skill.links.manageFragOrder, 1, { fragOrders = self.fragOrders } )`,
  `dia5/common/agent/queries/getFragOrders.lua::res[ i ] = CreateKnowledge( integration.ontology.types.fragOrder, x )`,
  `dia5/common/agent/behaviors/skills/ManageFragOrder.lua::self.params.fragOrders[i]:startTask()`.
  Known bug: `resSize` is never incremented in the first loop, so the commander's orders overwrite queued ones.
- **Kernel path** (kind (c)): `DEC_StartFragOrder` → parameters registered by `dia-name` → `StartTask( dia-type )` =
  `spark.start`; no `spark.getBehavior` guard, so a missing task fails with "unknown behavior":
  `int/CommunicationIntegration.lua::integration.startFragOrder = DEC_StartFragOrder`,
  `sim/Decision/DEC_Decision.h::pRefs_->startTask_( fragorder->GetDIAType(), refFrago );`,
  `int/SimulationIntegration.lua::StartTask = function( task, params )`, `spark/behavior.lua::error( "unknown behavior '"`.
- **Cleaning is deferred** to the end of the tick, so other skills still see a cleaned order in the same tick;
  `integration.query.getFirstFragOrderFromType` removes at once:
  `int/FragOrderIntegration.lua::myself.fragorderToClean[ #myself.fragorderToClean + 1 ] = fragOrder`,
  `int/CRTaskListener.lua::_DEC_RemoveFromOrdersCategory( myself, fragOrder.source )`,
  `int/ToolsQueries.lua::integration.query.getFirstFragOrderFromType = function`.
- **A new mission does not clear the queue**; only a brain reload does:
  `sim/Entities/Agents/MIL_AgentPion.cpp::GetRole< DEC_Representations >().ClearAllCategories();`.

## 4. Parameters and binding

- **Types and converters**: the same registry and the same `functorsBM` table as missions (`MISSIONS.md` §2); frag
  orders always use the Spark (BM) visitor: `sim/Decision/DEC_Decision.cpp::void RegisterFragOrderParameters(`.
  A type without a converter is silently not passed (`mission_index.md` §1).
- **Kind (c)**: binding by `dia-name` = task parameter name, with the missions' absent-value rule (object → `NIL`,
  `<default>` skipped; scalar → nil → `<default>`): `MISSIONS.md` §2.
- **Kinds (a), (f), (b)**: the Lua reads each value with `integration.get<Type>Parameter( fragOrder, "<dia-name>" )`
  (or `fragOrder:get<Type>Parameter`), wraps objects with `CreateKnowledge( integration.ontology.types.<t>, value )`,
  and fills `mission.<param>`. Getters return **nil on any error** (wrong dia-name included), and a nil field takes
  the task `<default>`: `int/FragOrderIntegration.lua::local ok, result = pcall( getter, fragorder.source, parameterName )`.
  So a misspelled dia-name fails silently: every dia-name read must exist in each DB's entry.
- **The order itself** (`common.agent.world.FragOrder`) has no `hash`: never key skill state on it (`CHECKPOINTS.md`
  §3), and the `FragOrder` parameter type has no converter.

## 5. Frago tasks

- **Run beside the mission**, started by a separate `spark.start`; they win shared resources by **priority** (1e2–1e6;
  most missions set none): `dia5/common/agent/tasks/frago/WaitThere.xml::priority="100000.0"` runs `actions.Wait`,
  which takes the motion resource (`dia5/common/agent/behaviors/skills/actions/Wait.lua::motion`), so the mission's
  movement stops.
- **Ending**: `disable-when-done` (23 of 34), the opposite order's initializer calling `stopTask`, or any mission stop
  (`integration.stopTasks` → `spark.stopAll()` stops frago tasks too): `dia5/FragOrder.lua::[ "ResumeMovement" ] = function`,
  `int/CRTaskListener.lua::integration.stopTasks = function`. A frago task without `disable-when-done` and without an
  opposite order runs until the next mission stop.
- **Listener side effects**: any task start sets `meKnowledge.currentTask` to that task's metadata (nil for frago
  tasks, so the mission's is lost); any task stop or finish runs `Cleanup` (resets `myself.blocked`,
  `myself.safetyAttitude`): `int/CRTaskListener.lua::meKnowledge.currentTask = task:getMetadata()`,
  `int/CRTaskListener.lua::myself.safetyAttitude = eAmbianceMission_None`.
- **No reports**: stage and end reports go only to `myself.currentMission`, but stage labels must still be in
  Stages.xml (§1).
- **Shape**: one stage (GetDecontaminated has two), no metadata class, no Lua. Recent kind-(c) example, with a
  `let`: `dia5/military/agent/tasks/frago/AirDestroyAircraft.xml::priority="100.0"`. Its priorities were forgotten at
  first and added by a later fix (`d87f8b86d9`): always set one.

## 6. Automats and crowds

- **Automat polling**: the company's default task runs `military.commander.skills.ManageFragOrder` on the same
  `getFragOrders`. Per order: ROE, then `executeAutomatFragOrder`, then CancelMission, then propagation or `Realign`:
  `dia5/military/commander/roles/Company.xml::military.commander.skills.ManageFragOrder`,
  `dia5/military/commander/skills/ManageFragOrder.lua::fragOrder:executeAutomatFragOrder()`. The security
  Commander has its own: `dia5/security/commander/behaviors/skills/ManageFragOrder.lua`.
- **Propagation**: a propagated order is sent to each subordinate with two filters. **Echelon**: `CrossDepartureLine`
  is not sent to scouts, `AttendreSePoster` goes to the first echelon only. **Availability on the subordinate's
  model**: a failure is silently skipped.
  - Anchors: `dia5/military/commander/skills/ManageFragOrder.lua::local send = typeOrder ~= "CrossDepartureLine"`,
    `dia5/military/commander/skills/ManageFragOrder.lua::if self.params.fragOrder:isAvailableToAgent( self.params.entity ) then`.
  - The send is a **Lua message** into the subordinate's `myself.fragOrders`, followed by a clean of the commander's
    copy. The subordinate then runs its **unit** initializer: `dia5/common/commander/world/MeKnowledge.lua::integration.SendMessage( "FragOrder", receiver, { fragOrder = fragOrder } )`,
    `dia5/_scripts/MessageTreatments.lua::myself.fragOrders[ content.fragOrder ] = content.fragOrder`.
- **Automat branches** in `executeAutomatFragOrder` act directly (logistics priorities, ROE) or forward with a
  `StartTask` / `FragOrder` message and clean.
- **Hazard: unit tasks run on the commander.** Every brain is built from `dia5/BMAgent.bms` over all of dia5
  (`dia5/BMAgent.bms::spark.createBrain`). An order that is **not** propagated, and whose dia-type names a task, is
  started **in the commander's brain**: `dia5/FragOrder.lua::if not integration.mustBePropagate( fragOrder ) and spark.getBehavior( orderType ) then`.
  A unit kind-(c) order must be propagated, or kept out of automat models and `available-without-mission="false"`.
- **Propagation list names** must be exact dia-types. Today 26 match none, mostly client or legacy names; a misspelled
  one is silently never propagated.
- **Crowds** run the unit `ManageFragOrder` skill. Their role maps the frag-order type to the unit class. Their frago
  task sits in `dia5/crowd/behaviors/tasks/frago/`. Both crowd models carry both list levels.
  `dia5/crowd/behaviors/roles/Crowd.xml::common.agent.behaviors.skills.ManageFragOrder`,
  `dia5/crowd/behaviors/roles/Crowd.lua::fragOrder      = common.agent.world.FragOrder`.

## 7. Readers (kind (b)) and other hard-coded dia-types

Readers are named by dia-type string, so a rename or a new reader must find them:
- **Poll helpers** in `FragOrder.lua` (e.g. `dia5/FragOrder.lua::fragOrder.getInfiltrateFragOrder = function`) and their
  `get*FragOrder` queries.
- `int/ToolsQueries.lua::integration.query.getFirstFragOrderFromType = function`.
- `dia5/common/agent/world/MeKnowledge.lua::getFragOrder = function( self, type )`.
- `dia5/common/agent/world/PhaseLine.lua::needToWaitForCoordination` (`CrossDepartureLine`, `ResumeMovement`).
- `<string>` defaults: `dia5/navy/agent/skills/ManageDiving.xml::<string>Resurface</string>`.
- The commander skill's `typeOrder ==` tests, and `sendFragOrderFromType` callers.

Grep a dia-type as `"<dia-type>"` in `dia5/` (Lua and XML) and as `>dia-type<` in XML. `mission_index.md` §6 lists
the readers per frag order (an upper bound: senders and commander filters too).

## 8. Checks: what catches what

| Check | Catches | Severity |
|---|---|---|
| sim load (`MIL_FragOrderType.cpp`, `DEC_Model_ABC.cpp`) | duplicate frag-order id or name; unknown parameter type; unknown frag-order name in Models.xml | fatal at start |
| sim load | duplicate dia-type | **warning**, last one wins |
| loading the DB (`src/models/phydb.go`) | duplicate ids across sections within a DB | fatal |
| `checkdec sheets` (`src/checkdec/checkdec.go`) | en sheet `.xml` / `.html` pairs; frag-order name ↔ sheet name | fatal |
| `checkstages` | frago task stage labels missing from Stages.xml | fatal |
| `checktranslations` | stages missing from `Stages_fr.ts` (not `Missions_fr.ts`) | fatal |
| `generate-database` (sparkcheck) | the frago task XML; not the strings in `FragOrder.lua` | fatal |
| `checkdec tasks` | units, automats, crowds only, **not** frag-order dia-types | — |
| sim order time | availability; missing / ill-typed non-optional parameters | order refused |
| order execution | kind (c) task missing ("unknown behavior"); initializer returning a missing task (`error`) | Lua error |
| **Nothing** | dia-type handled by nothing (no initializer, no task); an initializer reading a dia-name the DB lacks; a kind-(b) order read by nothing; a propagation name that is no dia-type; a propagated order missing from the subordinate models; a unit task reachable on a commander; a frago task that never ends; `Missions_fr.ts` entries, fr sheets, attribute order, category; mildb / wwt consistency | — |

`tools/mission_check.py --fragorder "<name or dia-type>"` (or `--all-fragorders`; `--all` also covers missions)
checks the "Nothing" row in the working tree, plus frago tasks without a priority on a resource, a frago task named
for the opposite of what it runs (`SafetyDismount`), parameters never read, and the polling-query merge bug. Its kind
codes: `a` `b` `c` `c2` (task group) `d` `f` as in §2 (crowd orders are `a` / `f` with a crowd task), `b?` empty
initializer read by nothing, `f?` clean-only initializer read by nothing, `none` handled by nothing. Propagation to
subordinates: a WARN when an automat model's subordinates all lack the order, a note when only some do.

## 9. Build order (designer)

1. **Reuse first**: an existing frag order with the same dia-type, purpose or task, in **both** DBs and under other
   names (`decisional_index.md` §7 "Frag orders"; `mission_index.md` §6: kind, task, readers, models).
2. **Choose the kind** (§2). Kind (c) unless an initializer is needed.
3. **Task and skills** (kinds (a), (c), (e)): the frago task in the family's `frago/` folder, with a priority over the
   mission's resources, `disable-when-done` or an opposite order, and checkpoint-safe skills (`CHECKPOINTS.md`). The
   **reader** for kind (b), persisting what it derives.
4. **`FragOrder.lua`** (kinds (a), (b), (f), (d)): the initializer next to a related entry, propagation, automat
   branch.
5. **Physical files per DB**: Missions.xml, Models.xml (model-level or per mission, in unit / automat / crowd models;
   for propagation, the subordinate models too), Stages.xml (both DBs) for new labels, then mildb `.ts` files and
   sheets: `tools/mission_scaffold.py` frag-order mode (spec `"fragorder": true`, `--example fragorder`) writes all of
   them and the `FragOrder.lua` entries of the kind (`kernel` none, `task`, `read`, `immediate`; `after`, `propagate`,
   `automat_branch`), plus a frago task skeleton with a prioritized behavior.
6. **`tools/mission_check.py --fragorder "<name>"`** and `tools/checkpoint_lint.py --fragorder "<name>"`; then
   `ant check` and `ant generate-database` in sw.models.
7. **Test in the sim**: give the order (during a mission when it requires one), check the effect, save a checkpoint
   while it is active, resume, and observe what is lost (`CHECKPOINTS.md` test recipe).
