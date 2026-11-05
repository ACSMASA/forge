# sw.models theme map

Purpose: save the navigator its first few searches. Theme → search words → where to start in the files.
**No behavior is described here**: read the files. Paths and anchors are checked by `tools/check_themes.py`; no line numbers.

Path aliases (the checker expands them): `dia5/` = `data/data/models/ada/decisional/dia5/` · `dec/` = `data/data/models/ada/decisional/` ·
`phy/` = `data/data/models/ada/physical/mildb/` · `wwt/` = `data/data/models/ada/physical/worldwide-tests/` ·
other paths are relative to the sw.models root unless prefixed `sw.sword/`, `directai/`.
`file::text` = an anchor: that text occurs in that file (or anywhere under that dir).
Paths are for the standing model `ada` on the branch the index was built from; client branches add other model dirs
(see gotchas). Entity names map to files: name a.b.C = file dia5/a/b/C.xml (+ C.lua).
[branch-specific] on a line = some of its paths are absent on older client branches (checked with
`python -I tools/check_themes.py --ref <branch> sw.models`); verify with `git ls-tree` on the user's branch.
Orientation docs in the repo: `README.md`, `ARCHITECTURE.md` (layout, build, checks; accurate except "entities are paired .lua + .xml", false for tasks). [branch-specific]

## Vocabulary

French (physical DB, legacy dia-types, sw.soult): Pion = unit (agent) · Automate = automat (unit group with a CP; "commander" in dia5) ·
Composante = equipment · Dotation = resource/supply item · Capteur = sensor · Armement = weapon system · Lanceur = launcher ·
Modèle (décisionnel) = decisional model (`Models.xml`) · Ordre de conduite (`Rep_OrderConduite_*`) = frag order · Embarquer / Débarquer = mount / dismount ·
Fuseau = AOR · Lima = phase line · Conduite = conduct (automat CDT phase) · MRT = automat phase that creates subordinate missions.

Model terms: **entity** = one Spark XML (+ Lua) file, kind given by its root element: `skill`, `task`, `query`, `role`, `resource`, `class`, `interface` ·
**family** = top dir of `dia5/` (`common`, `military`, `mildb`, `security`, `navy`, `crowd`, `test`) · **agent** vs **commander** = unit vs automat side ·
**primitive** / **action** skill = skills under `skills/primitives/` and `skills/actions/` (actions acquire resources) ·
**link** = a skill's sub-skill (`<link name target>`, `skill.links.<name>` in Lua) · **feedback** = signal a skill sends up (`Feedback( self.feedbacks.x )`) ·
**stage** = sequential step of a task (`<stage label>`), its behaviors run in parallel · **metadata class** = a task's Lua-side companion class (`tasks/knowledge/`) ·
**body type** = the world class a skill/role runs on (usually `common.agent.world.MeKnowledge`; `meKnowledge` in Lua) ·
**decisional model** = `Models.xml` entry binding a role (`dia-type`) to a mission list; units pick one with `decisional-model`.

## Themes

| Theme | Search words | Decisional (dia5) | Physical XML | Outside sw.models |
|---|---|---|---|---|
| Mission → task wiring | dia-behavior, dia-type, mrt-, cdt- | task XML `<stages>`/`<behavior name>`; index §1, §7 | `phy/Missions.xml`, `phy/Models.xml`, `phy/physical.xml` | `sw.sword/src/libraries/simulation/Entities/Agents/Roles/Decision/DEC_RolePion_Decision.cpp`, `sw.sword/src/libraries/simulation/Entities/Automates/DEC_AutomateDecision.cpp` |
| Unit type → decisional model → role | decisional-model, dia-type, role, default-task | `dia5/military/agent/roles/`, `dia5/military/commander/roles/`, `dia5/security/agent/`, `dia5/navy/agent/roles/`, `dia5/crowd/behaviors/roles/`; index §4 | `phy/Units.xml`, `phy/Automats.xml`, `phy/Models.xml` | — |
| Unit composition | equipment, sensor, weapon, crew, dotation | — | `phy/Units.xml`, `phy/Equipments.xml`, `phy/Sensors.xml`, `phy/WeaponSystems.xml`, `phy/Launchers.xml`, `phy/Dotations.xml` (grep, never read whole) | `docs/sw.sword/THEMES.md` (kernel meaning) |
| Tasks and stages | stage, next-stage-when-done, let, metadata-class | `dia5/*/agent/tasks/`, `dia5/security/agent/behaviors/tasks/`, metadata classes in `tasks/knowledge/` | `phy/Stages.xml` (stage labels) [branch-specific] | — |
| Skills, links, feedbacks | rootnode, connections, Activate, Feedback, links | `dia5/common/agent/behaviors/skills/`, `dia5/military/agent/skills/`, `dia5/navy/agent/skills/`; index §2 | — | directai (`docs/directai/THEMES.md`) |
| Commander (automat) behaviors | commander, subordinates, mainTasks, supportTasks | `dia5/common/commander/`, `dia5/military/commander/`, `dia5/mildb/commander/`, `dia5/security/commander/` | `phy/Missions.xml` (automats section) | `src/checkdec/tasks.go` |
| Queries | queries, call query, let | `dia5/common/agent/queries/`, `dia5/military/queries/`, `dia5/common/commander/queries/`; index §3 | — | — |
| Frag orders | fragorder, FragOrder, Rep_OrderConduite, initializer | `dia5/FragOrder.lua`, `dia5/common/agent/tasks/frago/`, `dia5/military/agent/tasks/frago/`, `dia5/security/agent/behaviors/frago/` | `phy/Missions.xml` (fragorders section), `phy/Models.xml` | `sw.sword/data/app-data/resources/integration/FragOrderIntegration.lua` |
| Integration calls (`integration.*`) | integration., meKnowledge | any skill/query/class `.lua`; index "integration.*" columns | — | `sw.sword/data/app-data/resources/integration/` (ask sw-sword-navigator) |
| Reports | eRC_, report, displayReport | skill/class Lua | `phy/Reports.xml` | `build/checkreports.py` |
| World objects, classes, interfaces (ontology) | world, class, interface, implements, body-type | `dia5/common/agent/world/`, `dia5/common/agent/world/interfaces/`, `dia5/crowd/world/`; index §6 | — | directai type system (dai-navigator) |
| Movement, paths | Move, MoveAlong, MoveTo, Path, Itinerary | `dia5/common/agent/behaviors/skills/MoveAlong.xml`, `dia5/common/agent/behaviors/skills/actions/MoveTo.xml`, `dia5/common/agent/behaviors/skills/primitives/MoveToward.xml` | `phy/Pathfind.xml` | — |
| Direct and indirect fire | Engage, Fire, Shoot, Destroy, Attack, IndirectFire | `dia5/military/agent/skills/primitives/Engage.xml`, `dia5/military/agent/skills/actions/Engage.xml`, `dia5/military/agent/skills/ApplyIndirectFire.xml`, `dia5/common/agent/behaviors/skills/actions/LaunchProjectile.xml` | `phy/WeaponSystems.xml`, `phy/Launchers.xml` | — |
| Recon, observation, sensors | Reconnoitre, Recon, Observe, Search, Radar, Sonar | `dia5/military/agent/skills/Reconnoitre.xml`, `dia5/common/agent/behaviors/skills/actions/Observe.xml`, `dia5/military/agent/skills/actions/MonitorAreaWithRadar.xml` | `phy/Sensors.xml` | — |
| Engineering, objects | Build, Breach, Bypass, Mine, Obstacle, Improve, Cross, Deconstruct, Activate | `dia5/common/agent/behaviors/skills/actions/Build.xml`, `dia5/common/agent/behaviors/skills/Breach.xml`, `dia5/common/agent/behaviors/skills/actions/Bypass.xml`, `dia5/mildb/agent/tasks/` | `phy/Objects.xml` | — |
| Mount / dismount own vehicles | Mount, Dismount, Mounting, MoveWhileMounted, canDismount | `dia5/common/agent/behaviors/skills/actions/Mount.xml`, `dia5/common/agent/behaviors/skills/actions/Dismount.xml`, `dia5/common/agent/behaviors/skills/Dismount.xml`, `dia5/common/agent/behaviors/resources/Mounting.xml`, `dia5/common/agent/tasks/frago/`, `dia5/security/agent/behaviors/frago/` | `phy/Missions.xml::dia-type="Mount"` | `dia5/common/agent/world/MeKnowledge.lua::integration.mountStarted` |
| Carried by another unit (load, unload, get transported) | GetTransported, Load, Unload, Embark, Disembark, Reembark, Transport | `dia5/military/agent/tasks/GetTransported.xml`, `dia5/military/agent/skills/GetTransported.xml`, `dia5/common/agent/behaviors/skills/actions/GetTransported.xml`, `dia5/military/agent/tasks/Embark.xml`, `dia5/common/agent/tasks/Disembark.xml`, `dia5/common/agent/behaviors/skills/primitives/Load.xml`, `dia5/navy/agent/tasks/` | `phy/Units.xml`, `phy/Equipments.xml` | — |
| Logistics | Supply, Convoy, Haul, Medical, Maintenance, Logistic | `dia5/military/agent/skills/`, `dia5/common/agent/tasks/`, `dia5/military/agent/skills/actions/ActivateLogisticChains.xml` | `phy/Supply.xml`, `phy/Health.xml`, `phy/Maintenance.xml` | — |
| CBRN | CBRN, NBC, Decontaminate, Contamination, AirFiltration | `dia5/common/agent/behaviors/skills/actions/`, `dia5/military/commander/tasks/` | `phy/NBC.xml` | — |
| Radio, jamming, emissions | Radio, Radar, Emission, Jam | `dia5/common/agent/behaviors/skills/actions/SetRadioState.xml`, `dia5/common/agent/tasks/frago/` | `phy/Transmitters.xml` | [branch-specific] |
| Air, drones, helicopters | Fly, Air, Drone, UAV, Helicopter, TakeOff, Land, GoBackToCP | `dia5/mildb/agent/tasks/`, `dia5/mildb/commander/`, `dia5/military/agent/roles/FlyingAgent.xml`, `dia5/military/agent/skills/GoBackToCP.xml` | `phy/Units.xml` (`can-fly`) | — |
| Navy | Vessel, Submarine, Dive, Surface, Berth, Amphibious | `dia5/navy/`, `dia5/navy/agent/roles/Vessel.xml` | `phy/Units.xml` | — |
| Crowds | Crowd, Population | `dia5/crowd/`, `dia5/crowd/behaviors/roles/Crowd.xml` | `phy/Crowds.xml`, `phy/Populations.xml` | — |
| Public safety (security family) | security, Police, Ambulance, Safety, Terrorist | `dia5/security/`, `dia5/security/agent/Agent.xml` | `phy/Missions.xml` ("SAFETY - …", "Public safety") | — |
| Task groups | taskgroup, TaskGroup, BMTaskGroup | `dec/taskgroups` (git submodule) | `phy/TaskGroups.xml`, `phy/Models.xml` (task-groups) | `.gitmodules` |
| Client models, extensions | extensions, client, branch | `dec/extensions/`, other model dirs (data/data/models/&lt;model&gt;/) on client branches | physical/&lt;db&gt;/ of that model | — |
| Translations, mission sheets | Locales, .ts, Mission sheets | — | `phy/Locales/fr/`, `phy/Mission sheets/` | `build/build.xml` (checktranslations, checkdec sheets) |
| Brain entry, loading | BMAgent, Decisional.lua, decisional.xml, createBrain | `dia5/BMAgent.bms`, `dia5/Decisional.lua`, `dia5/_scripts/`, `dec/decisional.xml` | `phy/Models.xml` (`file=`) | `sw.sword/src/libraries/simulation/Decision/DEC_Workspace.cpp` |
| Repo checks and build | ant check, checkdec, checkstages, sparkcheck, checkPackages | `dia5/.validation.lua` | — | `build/build.xml`, `build/checkdec.py`, `build/checkPackages.py`, `src/checkdec/`, `src/checkstage/`, `src/checktranslations/`, `src/models/` [branch-specific] |
| Tests (decisional) | test, Test | `dia5/test/` (roles, skills, tasks) | `wwt/` | — |

## Gotchas you would not think to grep for

Each line: the trap, then an anchor to start reading. Re-verify before quoting.

- **An entity's `name` must equal its path under `dia5/`** (dots for slashes); the index relies on it. → `dia5/common/agent/behaviors/skills/actions/Activate.xml::name="common.agent.behaviors.skills.actions.Activate"`
- **Tasks have no Lua.** Task logic is in its skills and in its metadata class (`spark.metadata-class` → `tasks/knowledge/<X>.lua`). → `dia5/military/agent/tasks/AttackObjective.xml::spark.metadata-class`
- **The mission → task link is `dia-behavior`, not `dia-type`** (they can differ); automat missions use `mrt-dia-behavior` and `cdt-dia-behavior`. → `phy/Missions.xml::dia-type="agent.behaviors.tasks.PerformAWork_dike"`, `sw.sword/src/libraries/simulation/Entities/Automates/DEC_AutomateDecision.cpp::ePhaseCDT`
- **A mission is only available to a unit if its decisional model lists it by name** in `Models.xml`, whose `dia-type` is a **role** (not a task). A mission name appears in `Missions.xml`, `Models.xml` (once per model), `Locales/fr/Missions_fr.ts` and `Mission sheets/`. → `phy/Models.xml::<mission name="COMBAT - Attack objective">`
- **ada has two physical DBs** that differ (missions, models, frag orders): `mildb` and `worldwide-tests`. Check both. → `wwt/physical.xml`, `build/build.xml::checkstage base="worldwide-tests"` [branch-specific]
- **Client models are whole model dirs on their branch** (`DB-6.26.2`, `mcast_v3`, `USMC`, …), with their own decisional and physical DBs; `ada/decisional/extensions/` holds only a readme. → `dec/extensions/readme.txt::client decisional models`
- **`taskgroup.*` tasks and roles live in a git submodule**, empty unless initialised: unresolved in grep, not an error. → `.gitmodules::sw.taskgroup-decisional`
- **Frag orders start tasks through `FragOrder.lua`**: `dia-type` → `fragOrderTaskInitializers` entry → task; no entry → `integration.startFragOrder`. Client extensions add entries through `extensionsFragosInit`. → `dia5/FragOrder.lua::fragOrderTaskInitializers`, `dia5/FragOrder.lua::extensionsFragosInit`
- **Commander skills name unit tasks in `<string>` values**, `;`-separated, in params like `mainTasks`: a rename that only greps XML attributes misses them. → `src/checkdec/tasks.go::mainTasks`
- **Lua refers to tasks by string and to queries by bare dotted name** (`common.agent.queries.getX( {…} )`): grep the full dotted name, not the file name. → `dia5/common/agent/behaviors/skills/BreachObjectAlongRoads.lua::common.agent.queries.getAreasToBreachObjectAlongRoads(`
- **Models never call `DEC_*` directly**; only `integration.*` (enforced by `checkdec.py` against `decfunctions.txt`). → `build/checkdec.py::don't directly call DEC functions`
- **`FragOrder.lua` overrides `integration.*` functions** at load time (model-side override of the layer). → `dia5/FragOrder.lua::integration.startFragOrderTask = fragOrder.startFragOrderTask`
- **Families may only depend on allowed families** (common → none; military, security, crowd → common; mildb, navy → common + military). → `build/checkPackages.py::authorisedPackages[ "navy" ]`
- **Every `<stage label>` must exist in each DB's `Stages.xml`** (`ant generate-stages` fixes it); `dia5/test/` is ignored. → `src/checkstage/stage.go::stage label=`, `build/build.xml::generate-stages` [branch-specific]
- **`checkdec tasks` only warns** (`failonerror="false"`), and its parser reads an old task shape (`stages>skills>skill`). → `build/build.xml::failonerror="false"`, `src/models/decdb.go::skills>skill`
- **Names are case-sensitive references; two tasks cite their metadata class with the wrong case** (`BypassObjects` vs `ByPassObjects`). Index §10 lists such mismatches. → `dia5/mildb/agent/tasks/ByPassObjects.xml::mildb.agent.tasks.knowledge.BypassObjects`
- **`decisional.xml` is generated and gitignored** (`gendec`, `ant configure`); its `model-version` gates sim startup. → `.gitignore::decisional.xml`, `sw.sword/src/libraries/simulation/Decision/DEC_Workspace.cpp::model-version`
- **"Mount/Dismount" = the unit's own vehicles; "Embark/Disembark/Load/GetTransported" = carried by another unit** (French "embarquer" covers both). → `dia5/common/agent/behaviors/skills/actions/Mount.xml::dismounted state into mounted state`, `dia5/common/agent/behaviors/skills/actions/GetTransported.xml::ready to be transported`
- **Bug: frag order `SafetyDismount` mounts the unit**: its task runs `actions.Mount` (and `frago.Mount`'s description says "dismount"). → `dia5/security/agent/behaviors/frago/Dismount.xml::common.agent.behaviors.skills.actions.Mount`
- **Role Lua maps ontology types** for the integration layer (`sw.ai.setRoleProperties`). Only 14 of 32 roles have Lua. → `dia5/military/agent/roles/Agent.lua::sw.ai.setRoleProperties`
- **`sparkcheck` runs with a mock environment**: a load-time global missing from it fails the check. → `dia5/.validation.lua::Validation environment`
- **Translations are fixed by loading and saving the DB in SWORD Adaptation**, not by hand. → `build/build.xml::Fix translations` [branch-specific]
