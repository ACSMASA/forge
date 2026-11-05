# sw.sword — Reference for the "sw.sword navigator"

> Knowledge base for a specialized agent that answers questions about **sw.sword** ("the sim"),
> MASA Group's SWORD simulation kernel, on behalf of a **behavior designer/modeler**
> (DIA5 Lua skills/tasks in `sw.models` / `sw.soult`, physical-DB XML editing, and frequent
> reading of and proposed changes to the sw.sword **Lua integration layer**).
>
> Snapshot: `C:\MASARepo\sw.sword`, branch `master`, HEAD `ffbf1a501a` (2026-10-15, "ci: bump dai version to '5.10'"),
> dev version 6.29.0 *at the time of writing* (6.28 was then the latest release). Written 2026-11-04.
> **master moves forward** (6.30, 6.31, …): read the current version from `build/build.xml` (`version.major`, ~L41) instead of trusting the number above.
> All paths below are relative to the sw.sword root unless stated otherwise. Line numbers drift — re-grep before quoting.

Companion files (same folder):
- [dec_function_index.md](dec_function_index.md) — **generated** index of all 1,616 names the kernel registers into the Lua brain
  (name → C++ file:line → bound C++ symbol → usage counts in integration / sw.models / sw.soult). Regenerate with
  `python -I tools/build_dec_index.py C:/MASARepo/sw.sword C:/MASARepo/sw.models C:/MASARepo/sw.soult docs/sw.sword/dec_function_index.md` from the forge root.
- `sections/` — five detailed deep dives, mapped in §3–7 below (kept as separate files so a reader loads only what it needs).

---

## 0. TL;DR mental model

```
 sw.models / sw.soult (separate repos)                 sw.sword (this repo)
 ─────────────────────────────────────                 ─────────────────────────────────────────────
 physical/*.xml  (Units, Equipments,  ──loaded by──▶   src/libraries/simulation  PHY_* types, MIL_* entities
   Sensors, Missions, Models, ...)                       (the C++ kernel, "MIL"/"PHY"/"DEC" prefixes)
                                                              │  registers ~1,616 functions into each brain
 decisional/dia5/**  (skills .xml+.lua, ──run inside──▶  sword::Brain  (wraps directai::brain::Brain, Lua VM)
   tasks .xml, queries)                                       ▲
                     │  call                                  │ loads first
                     ▼                                        │
               integration.*  ◀──────────────────── data/app-data/resources/integration/*.lua
               (Lua API layer shipped BY sw.sword: wraps _DEC_/DEC_ C++ functions into
                knowledge-capacity style APIs: Reachable, Destroyable, Loadable, Buildable, ...)
```

- **Prefixes**: `MIL_` = military entities/runtime (agents, automats, orders, objects), `PHY_` = physics/physical-DB types & roles,
  `DEC_` = decision (brain-facing functions, knowledge, paths), `TER_` = terrain. Much legacy naming is **French**
  (Pion = unit/agent, Automate = automat (unit group with command post), Composante = equipment, Dotation = resource/supply,
  Connaissance = knowledge, Embarquer/Débarquer = mount/dismount, Foule/Population = crowd, Itinéraire = path,
  Tir = fire, Contournement = bypass, Ravitaillement = resupply, Renfort = reinforcement, Rendu = surrendered).
- DirectAI (DIA5 / "spark") is a **separate product** (`C:\MASARepo\directai`); sw.sword links against it
  (`#include <directai/brain/Brain.h>`). The DAI version is pinned in CI (recent commit: "bump dai version to '5.10'").

---

## 1. Repository layout

| Path | What | Size (files) |
|---|---|---|
| `src/libraries/simulation/` | **The kernel.** Decision, entities, knowledge, objects, orders, meteo, urban, network glue | ~1,690 |
| `src/libraries/simulation/Decision/` | Brain binding + all `DEC_*Functions.cpp` (Lua-callable API), paths, models/workspace | 136 |
| `src/libraries/simulation/Entities/` | Agents (Units/Roles/Actions/Perceptions), Automates, Objects, Orders, Populations, TaskGroups, Inhabitants, Effects, Specialisations (LOG, Refugie, Remote) | 1,218 |
| `src/libraries/simulation/Knowledge/` | Knowledge groups, blackboards, knowledge of agents/objects/crowds/urban | 131 |
| `src/libraries/simulation/{Meteo,Urban,Network,CheckPoints,Tools,Adapters}` | weather & lighting, urban blocks, sim↔client messages, save/restore, config/utilities | small |
| `src/libraries/simulation/*.cpp` (root) | `MIL_AgentServer`, `Application`, factories, `Default*Computer` + `*_ABC` interfaces (speed, posture, consumption, detection, heal, loading time…) | ~150 |
| `src/libraries/` (others) | `core`, `simulation_terrain`, `pathfind`, `propagation`, `resource_network`, `protocol`, `dispatcher` + `*_plugin`s, `clients`/`gaming`/`preparation` (GUI), `hla_plugin`, `3a`, `indicators`, `reports`, `timeline`, `web`, terrain/geo libs | ~6,500 |
| `src/applications/` | `simulation_app` (sim server), `gaming_app`, `preparation_app`, `adaptation_app` (physical model editor), `selftraining_app`, `replayer_app`, `repeater_app`, `dispatcher_app`, terrain/generation tools, `cloud_server/client`… | ~1,050 |
| `src/masa/` | **Go**: `sword/simtests` (functional sim tests), `swapi` (protobuf client), `gotester`, `timeline`, `admin`, … | ~590 |
| `src/tests/` | C++ unit tests (Boost.Test), `integration_test` (terrain gen tests) | ~440 |
| `data/app-data/resources/Integration.lua` + `integration/` | **The Lua integration layer**: root loader + 58 files (~27k lines) + generated `Reports.lua` | 60 |
| `data/app-data/resources/integration tests/` | lunit-based Lua tests for the integration layer | 9 |
| `data/app-data/` (rest) | `functions.xml`, styles, preferences, HLA plugin data, locales, themes | |
| `data/tests/` | Test exercises/models/terrains for unit & functional tests | ~3,000 |
| `modules/` | git submodules `sw.api.sword` (protobuf protocol), `sw.api.analysis`, `sw.api.timeline` — **NOT checked out locally (empty)** | 0 |
| `build/` | `build.xml` (ant targets), `CMakeLists.txt`, python check scripts | |

---

## 2. The Lua ↔ C++ seam (verified)

### 2.1 Brain and registration
- `sword::Brain` — [src/libraries/simulation/Decision/Brain.h](../../../sw.sword/src/libraries/simulation/Decision/Brain.h) wraps
  `directai::brain::Brain`. API: `DeliverMessages()`, `SelectActions()`, `TriggerSelectedActions()`, `ExecuteScript()`,
  profiling, timeout, `GetPersistentState()/SetPersistentState()` (checkpoints), `RegisterFunction(name, fn)` and
  `RegisterMethod(name, &Class::method)` (arity up to 7, generated via `BrainIterate.h` / `BrainTemplate.h`).
  Each registered function gets its own `MT_Profiler` (per-name profiling).
- Registrations live in a `Register( sword::Brain& brain )` function at the top of each `Decision/DEC_*Functions.cpp`
  (e.g. `DEC_AgentFunctions.cpp:103`, `DEC_ActionFunctions.cpp:90`), plus `DEC_Decision.cpp` (`RegisterMethods` ~L104),
  `Entities/Orders/MIL_FragOrder.cpp` (63 names), `Entities/Orders/MIL_Report.cpp`.
- **Counts**: 1,633 registrations / 1,616 distinct names. Biggest: `DEC_AgentFunctions` 213, `DEC_Decision` 143,
  `DEC_GeometryFunctions` 134, `DEC_ActionFunctions` 120, `DEC_CommunicationFunctions` 97, `DEC_MiscFunctions` 93,
  `DEC_LogisticFunctions` 86, `MIL_FragOrder` 63. 13 names are registered twice (listed in the index).

### 2.2 Naming conventions of registered names
- `_DEC_Xxx( myself, ... )` — **leading underscore** = takes the calling agent's decision object (`myself`) explicitly.
  Lua code should not call these directly; `integration/DECFunctionWrappers.lua` defines the un-prefixed `DEC_Xxx(...)`
  that injects `myself` (e.g. `DECFunctionWrappers.lua:1101`: `return _DEC_Agent_EstEmbarquable( myself )`).
  Other integration files often cache them as locals (`local _DEC_Agent_EstEmbarquable = _DEC_Agent_EstEmbarquable`,
  `EquipmentIntegration.lua:59`) and call them with `agent or myself`.
- `DEC_Xxx(...)` without underscore — free functions not needing `myself` (or wrappers).
- `DEC__StartXxx` / `_DEC__StopAction` / `_DEC_PauseAction` / `_DEC_ReprendAction` / `_DEC_EtatAction` — the asynchronous
  **action** mechanism (start returns an action id; state polled each tick; must be stopped).
- `Get/Set/Clear<legacyVar>_` methods (`GetbEnExploitation_`, `SetobjMisEnCours_`…) — legacy DIA4-era per-agent variables
  exposed as methods on `DEC_Decision_ABC` (`DEC_Decision.cpp:104-170`).
- Families: `DEC_Agent_*`, `DEC_Automate_*`, `DEC_Pion_*`, `DEC_Connaissance*_*` (knowledge), `DEC_Geometrie_*`,
  `DEC_Itineraire_*` (paths), `DEC_Perception_*`, `DEC_Tir_*` (fire), `DEC_Logistique_*`, `DEC_MissionParameter_*`,
  `DEC_RC*`/report functions, `DEC_TaskGroup_*`, `DEC_Population_*`/`DEC_Foule*` (crowds), `DEC_Objet*`, `DEC_Urban*`.

### 2.3 Keeping models in sync (build checks)
- `ant checkdec` (`build/build.xml:882`) runs `bin/tools/checkdec.exe` against
  `data/data/models/decfunctions.txt` and `src/libraries/simulation` — models must only use DEC functions that exist.
  sw.models carries that file at `sw.models/data/data/models/decfunctions.txt` (1,106 lines; header:
  "generated automatically by sword project… do not overwrite, append new functions").
- `ant generate-lua-reports` (`build/build.xml:873`) runs `build/checkreports.py makelua` to **generate**
  `data/app-data/resources/integration/Reports.lua` (report-key mappings). It is absent from the checkout → produced at build time.
  `ant checkreports` validates sim reports.

### 2.4 How the integration dir reaches the brain (to be detailed in §4)
- `integrationDir` flows `Tools/MIL_Config.cpp` → `DEC_Workspace.cpp:253-311` (per mission-type family: Pion, Automate,
  Population, TaskGroup) → `DEC_Model` / `DEC_Model_ABC` (`isDAISpark`, `integrationDir`) → `DEC_Decision.cpp`.
  `selftraining` exposes it in debug config (`DebugConfig.cpp`, `DebugConfigPanel.cpp`, `ScenarioLauncherPage.cpp`).

---

## 3–7. Deep-dive sections

The deep dives live in `sections/` so the navigator only loads what a question needs. Each one cites `file:line`
against the sw.sword root, marks inferred claims "(unverified)", and lists gaps under "Not yet explored".

| # | File | Read it for |
|---|---|---|
| 3 | [sections/sec_integration.md](sections/sec_integration.md) | **Lua integration layer**: load order, `integration.*` namespace, `DECFunctionWrappers.lua`, per-file catalog, idioms, **how to change/extend it safely**, lunit tests |
| 4 | [sections/sec_decision.md](sections/sec_decision.md) | C++ decision layer: Brain cloning/archetypes, `_DEC_` vs `DEC_`, type conversions & lifetimes, per-tick decision flow, embrayé/débrayé, models/workspace (`decisional.xml`, `Models.xml`, masalife/spark), missions & parameter types, frag orders, limas, reports, asynchronous actions, map of `DEC_*Functions.cpp` |
| 5 | [sections/sec_agents_physics.md](sections/sec_agents_physics.md) | Physical DB: XML → loader map, Equipments/Units attribute maps, role architecture, equipment states/attrition/breakdowns/lending, moving/firing/flying/loading/transport/objects/navy, perception energy model, radars, postures, human factors, consumption, weather, urban |
| 6 | [sections/sec_knowledge_entities.md](sections/sec_knowledge_entities.md) | Entity hierarchy & diplomacy, knowledge groups & blackboards, knowledge lifecycles (agent/object/crowd), validity contract, objects (capacity tags of Objects.xml), crowds, inhabitants, task groups, logistics, pathfinding, resource networks, effects |
| 7 | [sections/sec_runtime.md](sections/sec_runtime.md) | Startup, time model, **one tick in order**, command-line & `session.xml` options, exercise/session folders, logs & debug cheat-sheet, network dispatch, **magic actions**, checkpoints, other apps/libs, Go simtests, build/CI/branches |
| — | [dec_function_index.md](dec_function_index.md) | Look up any `DEC_*`/`_DEC_*`/legacy name: where it is registered, what C++ it binds, who uses it |

### Top gotchas across all sections (spot-verified ones marked ✔)

**Integration layer** (the user's main working surface)
- ✔ Entry point is **`data/app-data/resources/Integration.lua`** (one level *above* `integration/`), loaded by the model:
  `sw.models/.../dia5/BMAgent.bms:2` `LoadResourcesFile( "Integration.lua" )` → C++ `DEC_Decision.cpp:1540`. It `include`s ~58 files in a
  fixed order; then the model's `Decisional.lua`/`FragOrder.lua`; then `spark.createBrain{…}`. `--integration-dir` points at the dir *containing* `Integration.lua`.
- Models load **after** the layer, so they can override `integration.*` (sw.models `FragOrder.lua` does, ~10×) — but code that cached a
  load-time local (`local _DEC_X = _DEC_X`, `local f = integration.f`) won't see the override. Renamed/removed C++ names give a silent `nil`
  that only errors on first call.
- ✔ `_DEC__StopAction` always returns `0` (`DEC_ActionFunctions.cpp:319-324`) and **0 is truthy in Lua**: after `x.action = _DEC__StopAction(...)`, `if x.action` is still true.
- ✔ **Bug**: `DECFunctionWrappers.lua:1758` `local objects = DEC_Knowledge_GetObjects` (missing `( myself )`) → `DEC_Connaissances_Observable` breaks when called (no callers found in sw.models).
- ✔ `Integration.lua:71` and `:89` both include `RelievableIntegration.lua` (harmless). `integration/config.ld` (LDoc) is out of date.
- `integration/Reports.lua` is generated and gitignored; any `eRC_` token in integration (even in comments) is checked against Reports.xml by `ant checkreports`.
- ✔ There is **no `integration.isLoaded`**. Model objects' `self:isLoaded()` means *transported by another unit* (`_DEC_Agent_EstTransporte`); "mounted in its own vehicles"
  is `integration.agentIsMounted()` (`EquipmentIntegration.lua:829`). `agentIsMounted` and `agentIsDismounted` are **both false** while mounting/dismounting
  (`Actions/Loading/PHY_RoleAction_Loading.cpp:228-236`), so `not agentIsMounted()` ≠ dismounted.
- `integration.displayReport` drops reports with too few params (trace warning only) and returns silently on a nil param.
- `NIL` (MASA Life sentinel) ≠ `nil`; check both for optional parameters.
- Tests: `ant checkintegration` (lunit) only covers `utilities.*` (26 tests). Nothing touching DEC functions or Spark is unit-tested → breakages surface at runtime.
- TaskGroupIntegration may stop actions on `myself` that were started on `transporter` (unverified by main session).
- Typical change shape: C++ static fn → `brain.RegisterFunction( "_DEC_X", … )` in the right `DEC_*Functions.cpp` → one-line documented alias
  `integration.foo = _DEC_X` (+ enum mirror in `Types.lua` if needed); examples `cdc4d5e8e5`, `e1b5082da5`, `94438cb7df` (whose leftover alias `_DEC_Agent_PeutConstruireA` was removed in `7e0593148a`).

**Decision / Lua runtime**
- ✔ Lua brains of the same script share one Lua VM: each entity brain is a child of a cached archetype (`Decision/DEC_Decision.cpp:1484-1555`).
- A C++ exception in any DEC call (e.g. a pion-only function called on an automat) → "Mission impossible", mission cancelled; for pions also all actions cancelled and perceptions switched off.
- Called from a pion, "automat" DEC functions silently act on the pion's parent automat.
- Kernel does **not** stop Lua-started actions on mission change — the Lua stop path must. Callbacks fired inside an action's constructor are lost (Lua doesn't know the id yet). `_DEC_EtatAction` is meaningless for most actions.
- A new order is stored as pending and starts at the entity's **next** decision update; `_DEC_FinMission` takes effect next tick; `_DEC_NouvelleMission` is true only during the start tick. A direct order to a unit whose automat is busy stops the automat's mission.
- Mission parameters whose type has no Lua converter (`Automate`, `bool`, `Direction`, `GenObject`, `DotationType`, `UrbanBlock`, `Duration`…) are **silently not passed** to Lua. Missing Lua behaviors only fail when the mission starts, not at load.
- Frag orders: pions/automats/crowds must **poll** (`_DEC_GetOrdersCategory`); only task groups get them pushed.
- Reports with unknown keys or bad parameters are silently dropped.
- ✔ `DEC_Workspace::RegisterSourcePath` uses `map::insert` (`Decision/DEC_Workspace.cpp:222-230`): with two `RepertoireSources` for the same namespace (ada `decisional.xml:5-6`: `dia5` and `extensions` under `net.masagroup`) the second is ignored in that map.
- Any Lua reference to a deleted unit keeps it alive as a zombie (`DEC_Decision_ABC*` is refcounted). Raw `TER_Localisation*`/`MT_Vector2D*` returns can dangle.
- ✔ Checkpoints don't save the Lua VM: only what `integration.savePersistentState` returns (`Brain.cpp:127`, `SimulationIntegration.lua:679`), plain data only; missions restart on reload.

**Timing (one tick)** — perception & knowledge → decisions (automats, pions, crowds, task groups) → actions → effects → states/network → knowledge groups → meteo → resource networks → pathfinder. So Lua sees this tick's perception pass, but computed on positions from the end of the previous tick: movement is committed in
`UpdateEffects` (`MIL_Effect_Move.cpp:30` → `DEC_PathWalker::Apply`). A unit entering sensor range during tick N is known to brains at **N+1 at the earliest**
(later if outside the sensor sector: see §5 perception). Actions Lua starts run later in the same tick. Orders/magic actions apply between ticks.

**Knowledge**
- Knowledge belongs to the **knowledge group**, not the agent; ids differ per group; a re-seen unit after deletion gets a **new** knowledge id → old Lua handles stay invalid. Jamming moves a pion to a cloned KG with new ids.
- Object knowledge never decays: a destroyed object stays a valid "ghost" until the side re-observes the place (and pathfinding keeps avoiding it).
- Some "knowledge" functions read the real entity (`DEC_ConnaissanceObjet_NiveauConstruction`, capacity helpers, `DEC_ConnaissanceAgent_EnAgent`). Locked agent knowledge (`DEC_ConnaissanceAgent_Verrouiller`) tracks the real unit and never decays.
- ✔ Exercise setting `jam-and-blackout-dynamic-knowledge-groups` must exist, or the sim throws when it is queried (`MIL_AgentServer.cpp:811-817`).

**Physical DB (modeler)**
- Time strings need `s`/`m`/`h`; missing/malformed optional times silently become 0.
- Percent vs ratio is inconsistent (`hit-probability@percentage` is 0–1; breakdown eva/neva, injured/dead %, stock thresholds are 0–100).
- ✔ Speeds: km/h in XML, metres-per-tick internally; hard-coded min speed 5.571 (= 2 km/h only at 10 s ticks) (`DefaultSpeedComputer.cpp:48-49`).
- Equipment `speed-prototype` doesn't move units — inline `<speeds>` does; no `<speeds>` = can't move. Unit speed/max slope = slowest movable equipment.
- Morale has no physical effect; human-factor evolution only with exercise setting `human-evolution`.
- Silent fallbacks: protection `@type` not `humain`/`materiel` → crowd; bad `crossing-height` → never; unknown Objects.xml capacity tags ignored; no `interact-with-enemy` → every side (friends too) interacts.
- One PH roll per burst; PH = 0 outside the table distance range; flying units immune to indirect fire.
- Logistics: between a base and its superior, a dotation with no quota gets **zero** resupply; quota alert fixed at 10%.

**Environment / tooling**
- ✔ Integration Lua is deployed by copy into `run/<platform>/resources`; after editing, re-deploy (`ant deploy-application-data`) or run the sim with `--integration-dir` (`Tools/MIL_Config.cpp:89`).
- Simtests fail if `Sim.log` contains any Lua traceback unless the test calls `DisableLuaChecks()`.
- `decisional.xml @model-version` must be ≤ runtime major.minor or the sim refuses to start.
- This clone is **sources only**: no `out/`, `run/`, `bin/`, `data/data`, `data/exercises`; submodules (`modules/sw.api.*`, the `.proto` files) are empty. Models live in sibling `sw.models` (`ada/` is the standing model: physical `mildb`, `worldwide-tests`). Client-project models (e.g. `mcast_v3` for MCAST, the USMC work) appear on **temporary project branches** of sw.models, possibly under other names later — don't assume one exists; check `git -C C:/MASARepo/sw.models branch --show-current` and `ls data/data/models/`.

---

## 8. Navigator playbook (question → where to look)

Always: (1) read the relevant section, (2) **re-verify in code** with Grep/Read before answering (line numbers drift;
sections were written by readers under a budget), (3) cite `sw.sword/<path>:<line>`, (4) say what is verified vs inferred.

| Question shape | Start here | Then grep / read |
|---|---|---|
| "What does `DEC_Xxx` / `_DEC_Xxx` do?" | `dec_function_index.md` (file:line, bound C++ fn) | the C++ function body; `integration/DECFunctionWrappers.lua` for the wrapper; integration callers: `grep -rn "DEC_Xxx" data/app-data/resources/integration` |
| "What does `integration.foo` do / where is it?" | §3 per-file catalog | `grep -rn "integration.foo *=" data/app-data/resources/integration`, then follow the `_DEC_` calls into C++ |
| "Is there a function that does X?" | §3 catalog + index (search French & English words: e.g. *Embarquer/Load*, *Contourner/Bypass*, *Tir/Fire*) | `grep -rniE "RegisterFunction\( *\"[^\"]*(word)" src/libraries/simulation/Decision` |
| "I want to add/change an integration function" | §3 "how to change/extend safely" | callers in `C:/MASARepo/sw.models` and `sw.soult` (`grep -rn` over `data/data/models/**/*.lua`), lunit tests in `integration tests/`, `ant checkdec`, new C++ needs a `Register(...)` entry in the right `DEC_*Functions.cpp` |
| "Why is my mission impossible / my skill not running?" | §4 gotchas, §7 logs cheat-sheet | `Sim.log`, `Decisional.log` (`debug decisional`), exceptions in the DEC call, parameter types without converter, frag-order polling |
| "When does X happen relative to Y in a tick?" | §7 "one tick" + §4 per-tick flow | `MIL_EntityManager::Update`, `MIL_AgentPion::UpdatePhysicalState` |
| "Why can't my unit see / detect / identify Z?" | §5 perception, §6 knowledge | `PHY_RolePion_Perceiver.cpp`, `PHY_SensorTypeAgent.cpp`, KnowledgeGroups.xml settings, knowledge validity |
| "How is XML attribute `foo` of `Bar.xml` interpreted?" | §5 loader map / §6 Objects tags | grep the attribute string in `src/libraries/simulation` and `src/libraries/core` (`xml::attribute( "foo"`) |
| "Speed / movement / slope / terrain" | §5 moving | `DefaultSpeedComputer.cpp`, `SpeedComputerStrategy.cpp`, `PHY_RoleAction_Moving.cpp`, `DEC_PathWalker.cpp`, pathfind rules |
| "Fire / damage / attrition" | §5 firing, equipment states | `Actions/Firing/*`, `Units/Weapons/*`, `PHY_AttritionData.cpp` |
| "Objects: build/mine/bypass/obstacle/capacity" | §6 objects | `Entities/Objects/CapacityFactory.cpp`, `Decision/DEC_Agent_ObjectAction.cpp`, `integration/BuildableIntegration.lua` etc. |
| "Logistics (supply, maintenance, medical, convoys)" | §6 logistics, §5 6.5 | `Entities/Agents/Roles/Logistic/`, `Entities/Specialisations/LOG/`, `DEC_LogisticFunctions.cpp`, `integration/LogisticIntegration.lua` |
| "Crowds / task groups" | §6 | `Entities/Populations/`, `Entities/TaskGroups/`, `DEC_PopulationFunctions.cpp`, `DEC_TaskGroupFunctions.cpp`, `CrowdIntegration.lua`, `TaskGroupIntegration.lua` |
| "How do I test this?" | §7 Go simtests | `src/masa/sword/simtests/*_test.go`, `data/tests`; lunit for pure integration Lua |
| "Which magic action / option for debugging?" | §7 magic actions & options | `NET_SimMsgHandler.cpp`, `Tools/MIL_Config.cpp` |
| "Has this changed recently / on which version?" | — | `git -C C:/MASARepo/sw.sword log --oneline -- <path>`; branches `6.2x`; `git log -S"<name>"` |

Useful one-liners (Git Bash, from `C:/MASARepo`):
```sh
# where is a brain name registered, and what does it bind?
grep -rn '"DEC_Agent_EstEmbarque"' sw.sword/src/libraries/simulation
# all integration functions defined in a file
grep -nE '^ *integration\.[A-Za-z_]+ *= *function' sw.sword/data/app-data/resources/integration/LoadableIntegration.lua
# who in the models uses an integration function
grep -rn 'integration.agentIsMounted' sw.models/data/data/models --include=*.lua | head
# history of one integration file
git -C sw.sword log --oneline -15 -- data/app-data/resources/integration/EquipmentIntegration.lua
```

---

## 9. Handoff: building the navigator in the next session

### 9.1 What exists now (in `C:\MASARepo\forge`)
| Path | Content |
|---|---|
| `docs/sw.sword/SW_SWORD_REFERENCE.md` | this hub: mental model, layout, seam, section map, consolidated gotchas, playbook, handoff |
| `docs/sw.sword/sections/*.md` | 5 deep dives (~2,800 lines total), file:line cited |
| `docs/sw.sword/dec_function_index.md` | generated index of all 1,616 brain names |
| `tools/build_dec_index.py` | regenerates the index |
| `.claude/agents/sw-sword-navigator.md` | the navigator (built 2026-11-05 from §9.2; read-only, self-contained routing, reports "Doc updates" instead of writing) |
| `.claude/skills/sword/SKILL.md` | `/sword <question>` → forks into the navigator |

**How it was produced:** the main session verified the seam (§2) and the generator; five parallel readers wrote the sections under a
budget cut at ~2/3 of their exploration. The main session spot-checked ~15 claims (marked ✔ above): all held except one stale claim
(already corrected). Treat the sections as a *map*, not ground truth.

**Known gaps** (shallow or "not yet explored"): detailed **logistics flows** (supply/medical/maintenance consigns, TC2 dispatch,
convoys) — the biggest gap given the user's work; perception-level thresholds; CBRN contamination; crowd flow collisions and crowd
paths; urban collapse; knowledge-group merge and sharing timing; DirectAI internals (`DeliverMessages`, spark task semantics — those
live in `C:\MASARepo\directai`); per-action return-code enums; protobuf messages (submodule `modules/sw.api.sword` is empty locally).

### 9.2 Recommended navigator design
1. **Claude Code subagent, project-level in forge**: `C:\MASARepo\forge\.claude\agents\sw-sword-navigator.md` (the user always works
   from forge; the navigator still reads the sibling repos by absolute path). Other forge agents summon it via the Agent tool. Suggested frontmatter: `name: sw-sword-navigator`; `description:` "Answers questions about the
   SWORD simulation kernel (sw.sword): DEC functions, Lua integration layer, tick order, missions/orders/reports, knowledge, physical-DB
   XML interpretation, debugging. Use proactively whenever a question involves how the sim behaves or where something lives in sw.sword.";
   `tools: Read, Grep, Glob, Bash` (read-only use); model inherit/opus.
2. **System prompt essentials:**
   - Paths: sw.sword `C:\MASARepo\sw.sword`, models `C:\MASARepo\sw.models` (only `ada/`), `C:\MASARepo\sw.soult`, DirectAI `C:\MASARepo\directai`, knowledge base `C:\MASARepo\forge\docs\sw.sword\`.
   - Protocol: read the hub first, then only the relevant section/index rows; **verify in code before answering**; cite `repo/path:line`; label verified vs. from-docs; answer in the user's terms (behavior designer, not C++ dev); give Lua-side consequences.
   - Staleness: compare `git -C C:/MASARepo/sw.sword rev-parse --short HEAD` with the hub's snapshot (`ffbf1a501a`); if different, warn and prefer code; suggest regenerating the index.
   - Version awareness: the user mostly runs **master**, whose version keeps advancing (6.29 dev in Nov 2026, then 6.30, …), and sometimes an older release branch (`6.x`) for clients still on it. **Never hardcode a version number** in the agent: detect it at answer time with `git -C C:/MASARepo/sw.sword branch --show-current` and `grep -m1 'version.major' C:/MASARepo/sw.sword/build/build.xml`. Each bump leaves a `ci: create dev version 'X.Y.0'` commit (`git log --grep="create dev version" -1`). When an answer could differ on an older version, say so and offer `git log`/`git show <branch>:<path>` to compare (no checkout needed).
   - Model branches: sw.models may be on a temporary client-project branch (e.g. MCAST/`mcast_v3`, USMC); detect which model dirs exist instead of assuming.
   - **Integration-layer change proposals** (a primary use case): output a proposal with (a) the diff, (b) placement rationale (§3 "where a new function goes"), (c) C++ registration if needed, (d) impact scan of callers in sw.models/sw.soult (`grep -rn`), including load-time-cached locals, (e) checks to run (`ant checkdec`, `ant checkreports`, `ant checkintegration`, deploy via `ant deploy-application-data` or `--integration-dir`), (f) Conventional-commit style message (`feat(sim, dec): SW-xxxxx …`), as recent history does.
   - Never modify sw.sword / sw.models unless explicitly asked; never run the sim or builds without asking.
   - Return format for calling agents: short answer first, then evidence (file:line), then caveats/unknowns.
3. **Optional** user-invocable skill (e.g. `/sword <question>`) that just delegates to the subagent; and a memory/notes file in forge
   where the navigator appends verified findings that fill the §9.1 gaps (so the docs improve with use).
4. **Validate** with 6–8 real questions before declaring done, e.g.: "What does `integration.isLoaded` check and which C++ does it hit?"
   (trick question, as it turned out: no such function exists — see the Integration-layer gotchas),
   "Why might a frag order never reach my pion's skill?", "Which XML field controls detection range for an optical sensor and in what unit?",
   "Propose adding `integration.getRemainingFuel()`", "In which tick phase are knowledges updated relative to my skill's `activate`?",
   "Why would an object my unit destroyed still block its path?". Check citations resolve.

### 9.3 Effort for the next session
**High** is enough: the work is synthesis plus writing an agent definition, with the research already done and indexed. Use **Extra High**
only if you also want it to fill the §9.1 gaps (e.g. a logistics deep dive) or re-verify the sections line by line.
