# Unit missions: anatomy, rules, checks, build order

Scope: **unit (agent) missions** of model `ada` (`<units>` in `Missions.xml`). Not automat missions, frag orders or
crowd missions. Read with `CHECKPOINTS.md` (save / resume) and the generated `mission_index.md` (catalog, rows).
**Rules here are one line each with an anchor**; the code is the truth: read it before relying on a rule.

Aliases (expanded by `tools/check_themes.py`): `dia5/` `dec/` `phy/` (mildb) `wwt/` (worldwide-tests) as in
`THEMES.md`; `sim/` = `sw.sword/src/libraries/simulation/`, `int/` = `sw.sword/data/app-data/resources/integration/`,
`spark/` = `directai/src/libraries/plugins/spark/`, `val/` = `directai/src/libraries/spark/validation/`. Other paths
are relative to the sw.models root. `file::text` = that text occurs in that file. Written from sw.models `master` @
`d2f78fafcd`, sw.sword `master` @ `51bdc0ec9e`, directai `master` @ `9629aaf1a1`.

## 1. Anatomy: what one unit mission touches

A mission lives in one DB or both (mildb-only and wwt-only missions are common): every row below applies **per DB
that has the mission**, unless the row says otherwise.

| File | Element / field | Rule | Anchor |
|---|---|---|---|
| `phy/Missions.xml`, `wwt/Missions.xml` | `<units><mission>` | Append at the end of `<units>` (no sort order). Attributes alphabetical: `category` (mildb only) `dia-behavior` `dia-type` `id` `name`. No `package` attribute. | `phy/Missions.xml::<units>` |
| same | `id` | Unique across **all** sections of the file (units, automats, populations, fragorders). Convention: max `id` over both DBs' Missions.xml + 1, same id in each DB. | `sim/Entities/Orders/MIL_MissionType_ABC.h::Mission id already defined`, `src/models/phydb.go::duplicate mission identifiers found` |
| same | `name` | Unique; the key used by Models.xml, the `.ts` files and the sheet file names. | `sim/Entities/Orders/MIL_MissionType_ABC.h::Mission name already defined` |
| same | `dia-type` | Unique among unit missions; commander skills name unit missions by it in `<string>` lists (`mainTask`, `supportTasks`…). Usually = `dia-behavior`. | `sim/Entities/Orders/MIL_MissionType_ABC.h::Mission DIA ID already defined`, `src/checkdec/tasks.go::"mainTasks":` |
| same | `dia-behavior` | The Spark task started. Never checked at load: a missing task fails at start ("unknown behavior"). | `sim/Entities/Orders/MIL_PionMissionType.cpp::xml::attribute( "dia-behavior", strDIABehavior_ )`, `spark/behavior.lua::error( "unknown behavior '"` |
| same | `category` | mildb only: a name from `<categories>` (end of the file); translated in `mission-categories`. | `phy/Missions.xml::<categories>` |
| same | first 4 `<parameter>` | The context, read **by position** and skipped by the order type: `dangerDirection_` Heading, `phaseLines_` PhaseLine unbounded `<objects/>`, `boundaryLimit1_` / `boundaryLimit2_` Limit optional, all `is-context="true"`. Copy them verbatim. | `sim/Entities/Orders/MIL_PionMissionType.cpp::MIL_MissionType_ABC( nID, 4, xis )`, `sim/Entities/Orders/MIL_OrderType_ABC.cpp::if( index > contextLength )` |
| same | other `<parameter>` | Attributes alphabetical: `dia-name` `max-occurs` `min-occurs` `min-value` `name` `optional` `type`. Sim reads `name` `dia-name` `type` `optional` `min-occurs` `max-occurs`; the rest is for SWORD Adaptation (ADN). | `sim/Entities/Orders/MIL_OrderTypeParameter.cpp::xis.attribute< bool >( "optional", false )` |
| same | `<parameter>` children | ADN-only: `<choice><parameter type/>` (LocationComposite), `<objects …/>`, `<units>` (UnitType), `<value id name>` (Enumeration). | `phy/Missions.xml::<choice>` |
| `phy/Models.xml`, `wwt/Models.xml` | `<unit><missions><mission name>` | At least one decisional model per DB must list the mission, else no unit can receive it. Insert at the sorted position if the list is sorted, else append. Optional `<fragorder name>` children. | `sim/Decision/DEC_Model_ABC.cpp::Invalid mission name`, `sim/Entities/Orders/MIL_PionOrderManager.cpp::IsMissionAvailable` |
| `phy/Stages.xml`, `wwt/Stages.xml` | `<stage key value>` | **Both DBs**, even for a one-DB mission: each must hold exactly the stage labels of all dia5 tasks (outside `dia5/test/`). key = value, sorted. | `src/checkstage/main.go::is missing`, `src/checkstage/stage.go::stage label=` |
| `phy/Locales/fr/Stages_fr.ts` | context `stage` | One message per stage label, sorted by source. mildb only (wwt has no `Locales/`). | `phy/Locales/fr/Stages_fr.ts::<name>stage</name>` |
| `phy/Locales/fr/Missions_fr.ts` | contexts | mildb only. Context `unit-missions`: the mission name. Context `unit - <name>`: every non-context parameter `name` + every enum `<value name>` (no context when empty). New category → `mission-categories`. Contexts and messages sorted by name / source. Not checked by any ant target. | `phy/Locales/fr/Missions_fr.ts::<name>unit-missions</name>`, `phy/Locales/fr/Missions_fr.ts::<name>unit-context</name>` |
| `phy/Mission sheets/units/`, `wwt/Mission sheets/units/` | `<name>.xml` + `<name>.html` | Required pair per DB (fatal). `.html` = `phy/missionSheets.xslt` applied to the `.xml`. Example pair: `phy/Mission sheets/units/AIR - Assault.xml`. | `src/checkdec/checkdec.go::checkMissionsHaveSheets` |
| `phy/Mission sheets/units/Locales/fr/` | `<French name>.xml` + `.html` | mildb only; French name = the `unit-missions` translation; css href one level deeper. Not checked. | `phy/Mission sheets/units/Locales/fr/AIR - Assaut.xml` |
| dia5 `<family>/agent/tasks/<Task>.xml` | the task | Spark 1.5 task; no Lua. See §3. Example: `dia5/military/agent/tasks/AttackObjective.xml`. | `directai/etc/schemas/spark/1.5/spark.xsd` |
| `phy/Reports.xml` | `eRC_` | Only for a new report. | `build/checkreports.py` |
| `data/changelog.txt` | — | Only for renames and moves. | `data/changelog.txt` |

No XSD for Missions / Models / Stages is in any local repo (the files name `schemas/<version>/physical/*.xsd`, copied
at sw.sword build time); no generator exists besides `tools/mission_scaffold.py`; ADN is the usual editor and
`checkxml` (sw.tools binary, not in the repo) enforces its normalization.

## 2. Parameters: types, conversion, binding

- **Type registry** (case-insensitive name → kernel type); an unknown name throws "Unknown parameter type" at load,
  `Limit` included after position 4: `sim/Entities/Orders/MIL_ParameterType_ABC.cpp::sCaseInsensitiveLess`,
  `sim/Entities/Orders/MIL_OrderTypeParameter.cpp::Unknown parameter type: `.
- **Conversion to Spark** is keyed by the **registered** name, plus `List` when `max-occurs` > 1:
  `sim/Decision/DEC_Decision.cpp::typeName += "List";`, table `sim/Decision/DEC_Decision.cpp::functorsBM[ "Heading" ] = DirectionFunctionBM;`.
  A type (or list form) with no converter is **silently not passed** (Location, Direction, DirectionBM, bool,
  Automate, DotationType, GenObject, UrbanBlock, IndirectFire, Duration, Stage, LimaFunction, Mission, FragOrder,
  TaskGroupKnowledge, List; BooleanList, EnumerationList, DateTimeList, HeadingList, TransmitterTypeList…): use the
  canonical names Heading, Boolean, ResourceType, PlannedWork, UrbanKnowledge, Polygon. `mission_index.md` §1 lists
  each type with its converter and Spark type.
- **Binding is by name**: `dia-name` = the task `<parameter name>`; undeclared names are dropped:
  `spark/behavior.lua::res[ name ] = parameters[ name ]`.
- **Absent value → task `<default>`**: an optional parameter not given (or a type with no converter) arrives as nil
  (`sim/Decision/DEC_Decision.cpp::refMission_[ dianame ] = brain_.GetScriptRef( "NIL" );`, no Lua global `NIL`),
  then Spark applies `<default>`: `spark/parameters.lua::if decl.default then`. No default and not `nullable` → the
  task gets nil; reported only with `--dec-typecheck`.
- **optional ↔ nullable/default**: `optional="true"` needs a task `<default>` or `nullable="true"`;
  `optional="false"` is enforced at order time (missing value, min/max-occurs, `IsOfType`, invalid geometry), by
  position after the context: `sim/Entities/Orders/MIL_Mission_ABC.cpp::IsOfType( parameterType.GetType().GetType() )`.
- **Lists**: `max-occurs="unbounded"` (or > 1) makes a list; the task type must be a `<list>` of the element type.
- **Hashability** matters for checkpoints: a skill keyed by a parameter of a class without `hash` cannot persist
  state (`CHECKPOINTS.md`).

## 3. Task rules (Spark 1.5)

- **Shape**: `<task name disable-when-done?>` > `parameters` > `let*` > `parallel?` > `stages` > `next-task?`;
  stage = `<stage label?>` > `let*` > `<behaviors><behavior name next-stage-when-done? priority? ignore-failure?>`
  with `<parameter name element-wise?>VALUE`: `directai/etc/schemas/spark/1.5/spark.xsd`, `val/parsers/task.lua`.
- **Ending**: only `disable-when-done="true"` (or a failure) ends the mission and sends `eRC_BM_FinMission` /
  `eRC_BM_EndMissionFailed` then `_DEC_FinMission`: `int/CRTaskListener.lua::_DEC_FinMission( myself )`. Without it
  the last stage's `done` only reports `TaskDone` to the automat and **the task keeps running**:
  `int/CRTaskListener.lua::"TaskDone"`.
- **Stage advance**: a stage ends when a behavior with `next-stage-when-done` sends `done`. A single-behavior stage
  defaults to it, **except the last stage of a task that is not `disable-when-done` (and has no next-task) or has a
  parallel stage**: `val/parsers/task.lua::if i < n or ( not data.parallel and ( data.nextTask or data.disableWhenDone ) ) then`.
- **Stage labels** become the reports `eRC_BM_DebutPhase` / `eRC_BM_FinPhase` and must be in Stages.xml:
  `int/CRTaskListener.lua::"eRC_BM_DebutPhase"`.
- **Parallel stage**: runs beside every sequential stage; its `done` is ignored unless the task is
  `disable-when-done`; its behaviors cannot set `next-stage-when-done`: `spark/task.lua::-- Ignore 'done' feedback from parallel stage`,
  `val/messages.lua::"task.parallel.cannot-be-done"`.
- **Failure**: any behavior's `failed` fails the task unless it has `ignore-failure="true"`:
  `spark/task.lua::if not mt.__properties.ignoreFailure then`.
- **Lets and queries**: resolved lazily; a `<call>` is re-evaluated **every activation** unless `once="true"`, the
  default only for a top-level call in a parameter `<default>`: `val/parsers/common.lua::once = parsers.toboolean( attrs.once, onceDefault )`,
  `spark/parameters.lua::if call.once then`. A query is `<query>` > `parameters` > `body-type?` > `<return>`; its Lua
  returns `function(params)`; `<call>` does no runtime type check. Example: `dia5/military/queries/getEnemiesInRange.xml`.
- **`element-wise`**: one activation per list element: `val/parsers/task.lua::attrs[ "element-wise" ]`.
- **Metadata class**: annotation `spark.metadata-class` with an `<entity>` subclassing `sparkle.TaskMetadata`, not
  abstract; only commander code reads it, so a unit mission needs it only when an automat gives it:
  `val/validators/task.lua::sparkle.TaskMetadata`, example `dia5/military/agent/tasks/AttackObjective.xml::spark.metadata-class`.
- **Validator codes** for tasks and parameters (`task.*`, `parameter.*`, `value.*`, `behavior.graph-has-cycle`):
  `val/messages.lua::"task.stage.non-final-but-not-terminating"`.

## 4. Running beside the default task, resources, modifiers

- **Default task**: the role's `<default-task>` (e.g. `dia5/military/agent/roles/Platoon.xml::<default-task>`) runs
  **concurrently** with the mission, both at priority 1 (missions start without priority):
  `int/SimulationIntegration.lua::( start and spark.start or spark.resume )( task, params )`. Stopping missions spares
  it: `spark/behavior.lua::function spark.stopAll( includingDefaultTask )`, `int/CRTaskListener.lua::integration.stopTasks = function`.
- **Resources**: the mission's skills and the default task's skills compete for the same `<resource>`s; check
  `action_index.md` §1 for every skill that acquires a resource the new skills need.
- **Modifiers**: declared by a skill (`<modifiers><modifier name><type><value>`, boolean/number/string), overridden by
  a role (`<skills><skill name><modifier name>`), read as `skill.modifiers.x` or
  `spark.getSkillModifier(skillName, name)`; tasks cannot set them:
  `dia5/military/agent/skills/SelfProtectFromEnies.xml::<modifier name="importance">`,
  `spark/skill.lua::function spark.getSkillModifier( skillName, modifierName )`, `val/messages.lua::"role.skill.modifier.unknown"`.
- **Role and model**: Units.xml `decisional-model` → Models.xml `<unit dia-type="<role>" file="BMAgent.bms">`; one
  archetype brain per file, role set by `sw.ai.setRole`: `sim/Decision/DEC_Decision.cpp::sw.ai.setRole`.

## 5. Checks: what catches what

| Check (`build/build.xml` target) | Catches | Severity |
|---|---|---|
| `checkdec` (`build/checkdec.py`) | `DEC_` calls from model Lua | fatal |
| `checkdec tasks` (`src/checkdec/tasks.go`) | `dia-behavior` → task missing; commander `<string>` lists → unknown unit `dia-type` | **warning** (`failonerror="false"`) |
| `checkdec sheets` (`src/checkdec/checkdec.go`) | en sheet `.xml`/`.html` pairs; mission ↔ sheet names | fatal |
| loading the DB (`src/models/phydb.go`) | duplicate ids across sections | fatal |
| `checkstages` (`src/checkstage/main.go`) | Stages.xml ≠ set of dia5 stage labels (fix: `ant generate-stages`) | fatal |
| `checktranslations` (`src/checktranslations/main.go`) | stages missing from the `.ts` files | fatal |
| `checkpackages`, `checkreports`, `checkhla` | package deps, Reports.xml, HLA | fatal |
| `checkxml` | ADN normalization of physical XML (binary not in the repo) | fatal |
| `generate-database` | sparkcheck: Spark validator on all entities (`val/messages.lua` codes) | fatal |
| sim load | unknown parameter type, duplicate id/name/dia-type, unknown mission in Models.xml | fatal at start |
| sim order time | missing / ill-typed non-optional parameters | order refused |
| **Nothing** | Missions_fr.ts entries, fr sheets, dia-name ↔ task parameter match, types without converter, optional without default, missing `disable-when-done`, mission in no model, wwt/mildb consistency | — |

`tools/mission_check.py` covers the "Nothing" row in the working tree.

## 6. Build order (designer)

1. Reuse first: an existing mission with the same task (`decisional_index.md` §7) or a task that fits.
2. Task XML: parameters (types from `mission_index.md` §1), lets, stages, `disable-when-done`, labels.
3. Missing skills / queries (checkpoint-safe: `CHECKPOINTS.md`), with their XML + Lua.
4. Physical files per DB: Missions.xml, Models.xml, Stages.xml (both DBs), then mildb `.ts` files and sheets
   (`tools/mission_scaffold.py` writes all of these from a spec).
5. `tools/mission_check.py --mission "<name>"` and `tools/checkpoint_lint.py --task <task>`, then `ant check` and
   `ant generate-database` in sw.models.
6. Test in the sim: give the mission, save a checkpoint mid-stage, resume (`CHECKPOINTS.md` test recipe).
