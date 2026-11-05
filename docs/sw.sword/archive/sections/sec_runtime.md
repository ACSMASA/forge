# Runtime, plumbing and the rest of the sw.sword repo

All paths are relative to the `sw.sword` root (`C:\MASARepo\sw.sword`) unless they start with `sw.models/`. Line numbers come from `master` at `ffbf1a501a` (2026-10-15, "ci: bump dai version to '5.10'"). Claims marked **(unverified)** were inferred and not traced in code. Areas marked **(not yet explored)** were left out to save budget.

> **Local checkout state.** This clone contains only sources. `out/`, `run/`, `bin/`, `data/data/` (models and terrains) and `data/exercises/` are missing because `.gitignore` excludes them and `ant update` downloads them. The submodule `modules/sw.api.sword` (the `.proto` and `.xsd` files) is an **empty directory**, and so is `modules/sw.api.analysis`. The generated protobuf code is not here either: `*.pb.go` is git-ignored and C++ `*.pb.h` files go to the build tree. The models a designer edits live in the sibling repo `C:\MASARepo\sw.models` (`sw.models/data/data/models/ada/{physical,decisional}`, `sw.models/data/exercises`).

---

## 1. Simulation runtime

### 1.1 Entry point and startup sequence

| Step | Where | What |
|---|---|---|
| 1 | `src/applications/simulation_app/main.cpp:45-51` | Registers a console logger and the terrain logger, then calls `tools::Main(..., &Main)`. |
| 2 | `main.cpp:25-42` | Verifies the protobuf version and runs `Application(...).Execute()`. Fatal exceptions go to the log as `MT_LOG_FATAL_ERROR_MSG`. |
| 3 | `src/libraries/simulation/Application.cpp:35-73` | Creates `--debug-dir` (default `./Debug`), then `MIL_Config::Initialize` → `config.Parse(argc, argv)` → `config.LoadSettings()` (exercise `settings.xml`). In release builds it checks the `sword-runtime` and `sword-dispatcher` licenses (the dispatcher license sets the max connections; if it is missing, max is 1). It then opens **`<session>/Sim.log`** with the log settings from the session/debug.xml. The log is not truncated when loading a checkpoint (`!config.HasCheckpoint()`). |
| 4 | `core/Application.cpp:253-262` (`Execute`) | Calls `Initialize()`, then loops `while(!quit && Update())`. A hidden Win32 tray-icon GUI thread runs alongside (`core/Application.cpp:160-200`). |
| 5 | `Application.cpp:125-144` (`Initialize`) | If `<dispatcher embedded="true">`, starts the **embedded dispatcher thread** (`RunDispatcher`, which updates every 25 ms, `Application.cpp:89-123`). Seeds `MIL_Random`: `<random seed>`, or `time(0)` if the seed is 0, logged as "Setting seed=". Calls `MIL_AgentServer::CreateWorkspace(config)`. |
| 6 | `Application.cpp:146-149` (`Update`) | Calls `MIL_AgentServer::GetWorkspace().Update()`. |

`MIL_AgentServer` constructor (`src/libraries/simulation/MIL_AgentServer.cpp:166-280`), in order:
1. Registers itself as `MIL_Time_ABC` and initializes the Lua profiler (`:193`).
2. Reserves IDs above the max ID found in the orbat/urban files (`FindMaxId`, `:85-90`).
3. Checks the transmission-network license against the `transmission-networks` setting.
4. Reads exercise settings `engineer-speed-factor` and `logistics-speed-factor`. Reads physical `decisional` file (`Decisional.xml`) → `<task-groups combat-speed-divider combat-movement-divider>` (`:208-216`). A settings `combat-speed-divider` overrides it.
5. Reads `nTimeStepDuration_` (= session `time/@step`) and `nTimeFactor_` (= `time/@factor`).
6. `TER_World::Initialize` loads the terrain (`:225`).
7. `new DEC_Workspace(config_)` loads `decisional.xml`, missions and models, and compiles the brains (`:230`). See `Decision/DEC_Workspace.cpp:190-220`.
8. `MIL_EntityManager::Initialize` loads the physical database.
9. `CreatePathfindManager` reads physical `pathfinder` (`distance-threshold`, `max-end-connections`, `max-calculation-time`) and starts N pathfind threads (`:92-149`).
10. `new NET_AgentServer` opens the sim TCP server on `<network port>` or `--simulation-address` (`:237`).
11. On checkpoint load, reads the checkpoint `globals.xml` (time, factor, tick, dates; `ReadGlobals :420-442`).
12. `nCurrentTimeStep_++`. The first tick is 1, and magic actions received "between N and N+1" belong to N+1 (`:247-250`).
13. Creates the meteo manager, `MIL_EntityManager`, `MIL_CheckPointManager`, then `ReadODB` (orbat, urban; on checkpoint also knowledges, logistics, decisionalState, missions; `Entities/MIL_EntityManager.cpp:362-400`).
14. Loads `exercise-extensions.xml`, then `Resume(nextPause_,0,0)` and registers the timer (`:265-266`). It logs tick duration, acceleration factor, end tick and speed factors, then "Simulation initialized".

### 1.2 Main loop and time model

```
Application::Update → MIL_AgentServer::Update (:293)
   timerManager_.Update()          → may fire OnTimer() (MT_Timer_ABC, period = 1000*step/factor ms)
   WaitForNextStep() (:300)        → pAgentServer_->Update()  (network I/O: client msgs & magic actions are processed HERE, between ticks)
                                   → pathfinds_->Update()     (client-requested pathfinds)
                                   → sleep ~half of the remaining time to next step
OnTimer() (:319)
   nSimTime_ += step; nRealTime_ += step; MainSimLoop(); Wait(); ++nCurrentTimeStep_
   stop if end-tick reached; auto-pause if Resume(ticks) countdown hits 0
```

- **Tick length** is `session.xml config/simulation/time/@step` in seconds. It is mandatory and must be > 0 (`Tools/MIL_Config.cpp:170-175,202-205`). The Go test default is 10 s (`src/masa/sword/swapi/simu/session.go:477`).
- **Real-time factor** is `time/@factor`. The wall-clock period per tick is `1000*step/factor` ms (`MIL_AgentServer.cpp:579`). If a tick takes longer than that, the sim simply runs as fast as it can. Go tests use factor 10000, which means "as fast as possible". Clients change it with `control_change_time_factor` → `SetTimeFactor` (`:617-634`).
- **Pause / resume / stop**: `Pause` (`:565`), `Resume(ticks)` (`:576`; `ticks>0` means "run N ticks then pause again", via `nextPause_`), `Stop` (`:550`). Clients send these as `control_pause`, `control_resume{tick}` and `control_stop`. The sim is created "running" (`Resume` in the constructor), but the dispatcher gates the start (next bullet).
- **Pause at startup**: the dispatcher reads `time/@paused` (`dispatcher/Config.cpp:102`). It sends `ControlResume` only after `--clients=N` client announcements and only if not paused (`dispatcher/SimulationNetworker.cpp:171-177`). The exact interaction with the constructor's Resume is **(unverified)**.
- **Tick latency / back-pressure**: after each tick, `Wait()` increments `waitTicks_`. Once it exceeds `latency` (`time/@latency`, default 1 in `MIL_Config.cpp:58`, 0 in Go tests), the timer is suspended until the dispatcher returns a `control_tick_ack` → `Continue()` (`MIL_AgentServer.cpp:596-615`, `Network/NET_SimMsgHandler.cpp:122-128`). In other words, the sim never gets more than `latency` ticks ahead of the dispatcher.
- **Clocks**: `nSimTime_` is elapsed sim seconds, `nRealTime_` is the exercise date/time in epoch seconds (starts from `weather.xml`/exercise date, **(unverified)** source), and `nCurrentTimeStep_` is the tick. `control_date_time_change` can only move the date forward (`SetRealTime :636-652`). Helpers: `RealTimeToTick` and `TickToRealTime` (`:654-662`).
- `MIL_Time_ABC` (`src/libraries/simulation/MIL_Time_ABC.h`) is the singleton interface used everywhere (`MIL_Time_ABC::GetTime().GetCurrentTimeStep() / GetTickDuration() / GetRealTime()`).
- `end-tick` (session) stops the sim. `--test` forces end-tick 8 and an embedded dispatcher (`MIL_Config.cpp:106-121`).
- Random breakdowns are preprocessed once per simulated day (`MIL_EntityManager.cpp:1030-1040`). They can be disabled with `debug/@random-breakdowns="false"` (the default in the Go tests).

### 1.3 What happens in ONE tick (ordered)

`MIL_AgentServer::MainSimLoop` (`MIL_AgentServer.cpp:341-377`) → `MIL_EntityManager::Update` (`Entities/MIL_EntityManager.cpp:1097-1115`):

| # | Phase | Code | Notes for designers |
|---|---|---|---|
| 0 | *(between ticks)* Network receive | `WaitForNextStep` → `NET_AgentServer::Update` → `NET_SimMsgHandler::OnReceiveClient` (`Network/NET_SimMsgHandler.cpp:63-120`) | **Orders, frag orders and magic actions are applied immediately on receipt, not inside the tick.** A unit order is only stored as the "next mission" (see step 7). |
| 1 | `SendMsgBeginTick` | `MIL_AgentServer.cpp:379` | Sends `ControlBeginTick` (tick, date). |
| 2 | Urban cache clear | `:344` | |
| 3 | `CleanMagicActionCache`, `pObjectManager_->Clean()` | `MIL_EntityManager.cpp:1099-1101` | |
| 4 | `PreprocessRandomBreakdowns` (once per simulated day) | `:1030` | |
| 5 | `UpdateDynamicCommunications`, `UpdateTransmissions` | `:878-898` | Only when the `dynamic-communication` or `transmission-networks` settings are on. |
| 6 | **`UpdateKnowledges`** (profiler bucket "K") | `:905-929` → `MIL_Army::UpdateKnowledges` (`Entities/MIL_Army.cpp:387-397`) → knowledge groups → automats → pions → `DEC_KS_Perception` → `PHY_RolePion_Perceiver::ExecutePerceptions` (`Knowledge/DEC_KS_Perception.cpp:254`) | **Perception and knowledge are computed BEFORE decisions,** from the positions at the end of the previous tick. Army-level object knowledge follows, then `CleanKnowledges`. |
| 7 | `UpdateTaskGroupCollisions`, `UpdateCrowdCollisions` | `:1082-1095` | |
| 8 | **`UpdateDecisions`** ("D"; sub-buckets A/P/Pop/TG) | `:931-992` | Order: **automats → pions → crowds → task groups**. For each class: first `DeliverMessages()` (DirectAI inter-brain messages) for all entities, then `UpdateDecision(duration)` for all. In a pion (`Entities/Agents/MIL_AgentPion.cpp:369-419`), `pOrderManager_->Update()` starts a pending mission (`Orders/MIL_OrderManager_ABC.cpp:32-61`), then `role->UpdateDecision`, which runs the Lua brain. Dead units stop their tasks. A C++ exception during decision logs the error and posts the report `eRC_MissionImpossible`. |
| 9 | **`UpdateActions`** ("A") | `:994-1002` | Order: formations → task groups → automats → pions → crowds. Executes the `PHY_Action`s started from Lua (move, fire, etc.) **in the same tick** the brain started them. |
| 10 | **`UpdateEffects`** ("E") | `:1004-1009` | `MIL_EffectManager::Update()` applies the queued effects (damage, etc.), then `ObjectManager::ProcessEvents`. **Unit positions are committed here** (`MIL_Effect_Move` → `DEC_PathWalker::Apply`, `Entities/Effects/MIL_Effect_Move.cpp:30`), not in `UpdateActions`: the next perception pass sees the new position at N+1. |
| 11 | **`UpdateStates`** ("S") | `:1011-1028` | Calls `UpdateState` on automats, pions, crowds and task groups. For pions this is `UpdatePhysicalState` (`MIL_AgentPion.cpp:422-465`): dotations, composantes, humans, posture, location, NBC, comms, human factors, transport, perceiver, moving, firing, logistics, navy, and so on. **Then `UpdateNetwork` on every entity: changed attributes are sent to clients here.** Then object states (flood model), inhabitants, army network. |
| 12 | `UpdateKnowledgeGroups` | `:1064-1080` | |
| 13 | `HandleDeletedEntities` | `:1117+` | Deletes crowds marked for destruction. |
| 14 | Meteo update | `MIL_AgentServer.cpp:346` | `pMeteoDataManager_->Update(nRealTime_)` |
| 15 | Resource networks update | `:347` | |
| 16 | Pathfinder `UpdateInSimulationThread` | `:348` | Async pathfind threads hand results back here **(unverified: results consumed next tick)**. |
| 17 | `SendMsgEndTick` | `:391` | Includes tick duration, pathfind counts and memory. |
| 18 | Tick log line | `:353-371` | Writes to Sim.log: `**** Time tick N T ms - Profiling (K/D/A/E/S) : K D (A: P: Pop: TG: DEC:) A E S - Wait ... - PathFind ... - RAM`. **This is the first place to look for performance problems.** |
| 19 | `Brain::ResetProfiling(--dec-profile)` | `:374` | With `--dec-profile`, logs per-DEC-function `<profiling> DEC name count ms` lines (`Decision/Brain.cpp:106-120`). |
| 20 | `pEntityManager_->Clean()` | `MIL_EntityManager.cpp:1135-1151` | Resets per-tick role flags (`MIL_AgentPion::Clean :516`). |
| 21 | `pCheckPointManager_->Update()` | `CheckPoints/MIL_CheckPointManager.cpp:78-87` | Automatic checkpoint when the frequency is due. |

**Consequences:**
- A mission sent during tick N starts in the decision phase of tick N+1.
- A frag order is delivered to the brain on receipt or queued, depending on the entity **(not yet explored)**.
- Perceptions seen by Lua at tick N reflect movement done at tick N-1.
- Magic actions such as teleport or recover call `UpdatePhysicalState(true)` immediately (`MIL_AgentPion.cpp:779,1103,1113`). Their network update goes out in the next state phase.

### 1.4 Command-line options (simulation_app)

Options are layered: `CommandLineConfig_ABC` → `GeneralConfig` → `PhysicalConfig` → `ExerciseConfig` → `SessionConfig` → `MIL_Config`. The embedded dispatcher re-parses the same argv (`dispatcher/Config.cpp:58-67`).

| Option | Source | Meaning |
|---|---|---|
| `-h/--help`, `-v/--version` | `core/CommandLineConfig_ABC.cpp:28-29` | |
| `--root-dir` | `core/GeneralConfig.cpp:103` | Root; the paths below are relative to it. |
| `--terrains-dir` (`data/terrains`), `--models-dir` (`data/models`), `--population-dir`, `--propagation-dir` (multi), `--exercises-dir` (`exercises`), `--plugins-dir` (`plugins`), `-l/--language`, `--license-url`, `--features "a;b"` | `GeneralConfig.cpp:103-123` | With `--root-dir=<sw.sword>/data`, models resolve to `data/data/models`. |
| `--exercise <name>` | `core/ExerciseConfig.cpp:35` | Folder under exercises-dir; may contain subfolders (`worldwide/Egypt`). |
| `--session <name>`, `--session-file` (default `session.xml`), `--checkpoint <name>` | `core/SessionConfig.cpp:33-35` | |
| `--test` | `MIL_Config.cpp:85,109-120` | Loads, runs 8 ticks, embedded dispatcher. |
| `--savecheckpoint <name>` | `:87` | Test mode plus a checkpoint at tick `(8/2+1)*step`. |
| `--integration-dir` (default `resources`) | `:89` | Where `integration/*.lua` and `spark-core` are looked up (`DEC_Decision.cpp:1520`). |
| `--dump-pathfinds <dir>`, `--filter-pathfinds id,id` | `:90-91` | Dumps pathfind requests. |
| `--simulation-address ip:port` | `:92` | Overrides the session `<network port>`. |
| `--test-commands` | `:93` | Enables harmful `debug_internal` commands (`trigger_error`). |
| `--dec-profile` | `:94` | DEC function profiling in Sim.log; `ControlInformation.profiling_enabled`. |
| `--dec-typecheck` | `:96` | DAI Spark dynamic type checking (`sw.ai.setTypeChecking`, `DEC_Decision.cpp:1523`). |
| `--checkpointorbat`, `--testdata`, `--deletecheckpoint` | `:84-88` | **Declared but unused** (no reader found). |
| `--debug-dir` (default `./Debug`) | `Application.cpp:39` | Crash dumps. |
| `--dump-level` (`normal/full/off`) | `tools::Main` **(unverified)** | Minidump level. |
| Dispatcher options: `--dispatcher-address`, `--clients N`, `--response-timeout`, `--no-log`, `--replay-*` | `dispatcher/Config.cpp:58-67` | `--clients` = announcements to wait for before resume. |

### 1.5 Session configuration (`<exercise>/sessions/<session>/session.xml`)

`MIL_Config::ReadSessionXml` (`Tools/MIL_Config.cpp:134-209`), path `session/config/simulation/...`:

| Element / attr | Default | Effect |
|---|---|---|
| `license/@url` | none | License server. |
| `orbat/subset/@parties`, `@no-party` | none | Loads only some parties. |
| `profiling/@lua` | false | Lua profiler; writes **`<session>/callgrind.txt`** at shutdown (`MIL_Config.cpp:206`, `MIL_AgentServer.cpp:288-290`). |
| `decisional/@lua-timeout` | 30 s | `Brain::SetTimeout` for every brain (`Decision/DEC_Decision.h:318`). |
| `decisional/@entity-cache` | false | Spark entity caching (`sw.ai.setEntityCaching`). |
| `decisional/@debug-server-port` | 0 | >0 starts the **DirectAI debug server** (`DEC_Workspace.cpp:200-202`). Use it with the DAI debugger/IDE **(unverified client tooling)**. |
| `dispatcher/@embedded` | required | Runs the dispatcher in-process. |
| `network/@port`, `@timeout` | required / 10000 ms | Sim server address. |
| `time/@step`, `@factor`, `@end-tick`, `@latency`, (`@paused` read by the dispatcher) | required, required, 0, 1 | See 1.2. |
| `GarbageCollector/@setpause`, `@setstepmul` | 100 / 200 | Lua GC parameters, applied once per new archetype brain (`DEC_Decision.h:325-329`). |
| `pathfinder/@threads`, `@max-calculation-time` | required / physical | |
| `random/@seed`; `random0..4/@distribution,@deviation,@mean` | 0 → time-based | Contexts: 0 Fire, 1 Wounds, 2 Perception, 3 Breakdowns, 4 KnowledgeGroups (`MIL_Random.h:23-30`). **Set a fixed seed for reproducible behavior debugging.** |
| `checkpoint/@frequency` (e.g. `1h`), `@keep`, (`@usecrc`) | required | Automatic checkpoints. |
| `debug/@decisional` | false | Writes **`<session>/Decisional.log`**: a line per entity update with tick, time, `name[id]` and Lua memory in KB (`MIL_EntityManager.cpp:292-303, 1042-1062`). Useful to find which brain crashed or hung. |
| `debug/@pathfind` | false | Pathfind debug → **`Pathfind.log`**. |
| `debug/@random-breakdowns` | true | |
| `debug/@loglevel,@logfiles,@logsize,@sizeunit` | all,1,0 | Sim.log rotation and level (`core/ExerciseConfig.cpp:567-574`). |

Dispatcher section (`session/config/dispatcher`): `network/@client` (sim address), `@server` (address clients connect to, usually 10001), `log`, `messages`, `debug` (protobuf dump), `plugins/*` (`recorder`, or `<x library="...">` DLL plugins). `dispatcher/Config.cpp:85-130`, `dispatcher_dll/PluginFactory.cpp:236-265`.

An exercise-level **`debug.xml`** (`<debug><dispatcher/><dispatcherlog/><sim/><messages/></debug>`) can override log settings (`core/ExerciseConfig.cpp:44-69`).

### 1.6 Decisional model loading checks

- `<models>/<dataset>/decisional/decisional.xml` (`core/GeneralConfig.cpp:239-242`). `DEC_Workspace::LoadDIA` (`Decision/DEC_Workspace.cpp:205-220`) requires `@model-version` ≤ runtime version on the first 2 components (`tools::CheckVersion`, ascending compatibility; non-version strings are tolerated). It then reads `RepertoiresSources/RepertoireSources@directory,@namespace` (for example `dia5`, `extensions`, `bms/sources`), then missions and models.
- Brains are compiled once per script file into an **archetype brain** that is cached forever (`Decision/DEC_Decision.cpp:1484-1512`). Every entity brain is a copy of the archetype (`new sword::Brain(*archetype)`). A `reload=true` call creates a fresh archetype, so changed scripts get re-read.

---

## 2. Exercises and sessions on disk

### 2.1 Folder layout

```
<root>/exercises/<exercise>/            (Go tests: data/tests/gosword/<exercise>)
  exercise.xml          main index (below)
  orbat.xml  weather.xml  profiles.xml  [urban.xml settings.xml scores.xml success-factors.xml drawings.xml
  knowledges.xml logistics.xml globals.xml chat-groups.xml decisionalState.xml missions.xml alerts.xml
  taggedEntities.xml exercise-extensions.xml analytics.json  urbanstate]   (optional, defaults in core/ExerciseConfig.cpp:92-106)
  debug.xml             optional log overrides
  scripts/*.lua         dispatcher script_plugin scripts (NOT brain code)
  docs/, scripts/resources/   briefing resources (exercise.xml <meta><resources>)
  sessions/<session>/
     session.xml
     Sim.log  Decisional.log  Pathfind.log  PathfindBreach.log  Dispatcher.log  Messages.log  Protobuf.log
     callgrind.txt (lua profiling)   actions.ord (logged client actions)
     LogSupply/ LogMaintenance/ LogMedical/ LogFuneral/ LogPersonnelServices/ (logistic_plugin CSV)
     record/               replay data (recorder plugin)
     checkpoints/<name>/   checkpoint (section 4)
```
The log names come from grepping `BuildSessionChildFile(` across `src/libraries`.

`exercise.xml` (`core/ExerciseConfig.cpp:110-200`): required `terrain/@name`, `model/@dataset @physical`, `weather/@file`, `orbat/@file`, `profiles/@file`. Everything else is optional. Example from `data/tests/gosword/crossroad-small-empty/exercise.xml`:
```xml
<exercise model-version="4.8.2">
  <profiles file="profiles.xml"/> <orbat file="orbat.xml"/> <weather file="weather.xml"/>
  <model dataset="ada" physical="mildb"/> <urban file="urban.xml"/> <settings file="settings.xml"/>
  <terrain name="crossroad-small"/> <drawings file="drawings.xml"/>
</exercise>
```

### 2.2 How it points to models and terrain (`core/GeneralConfig.cpp:209-271`)

| What | Resolved path |
|---|---|
| physical | `<models-dir>/<dataset>/physical/<physical>/physical.xml`, e.g. `data/models/ada/physical/mildb/physical.xml`. That file is an index of `<tag file="X.xml"/>` children (`Decisional.xml`, `Units.xml`, `Sensors.xml`, `Missions.xml`, `Pathfind.xml`, ...), resolved by `core/PhyLoader.cpp:28-40`. |
| decisional | `<models-dir>/<dataset>/decisional/decisional.xml` (one per dataset, shared by all physical bases) |
| terrain | `<terrains-dir>/<name>/terrain.xml` (case-insensitive), urban `…/urban/urban.xml` |
| sessions | `<exercises-dir>/<exercise>/sessions/<session>/` |

**Exercise settings** (`settings.xml`, `<setting name type value>`): `human-evolution`, `infinite-dotation`, `dynamic-communication`, `dynamic-communication-display-only`, `jam-and-blackout-dynamic-knowledge-groups`, `engineer-speed-factor`, `logistics-speed-factor`, `combat-speed-divider`, `transmission-networks`, `units-can-sink`. This list comes from the test exercises plus `core/ExerciseSettingsHelpers.h`.

### 2.3 Logs and debugging cheat-sheet

| Need | How |
|---|---|
| Lua errors / tracebacks | `Sim.log`. Lua stack traces are what simtests scan for (`swapi/simu/simerrors.go:336-360`). |
| Which brain was running when it hung | `debug decisional="true"` → `Decisional.log` |
| Per-tick timing (K/D/A/E/S) | `Sim.log` `**** Time tick` lines |
| DEC function cost | `--dec-profile` → `<profiling> DEC ...` lines |
| Lua function profile | `<profiling lua="true"/>` → `callgrind.txt` (KCachegrind format) |
| Toggle brain debug traces for one unit | Magic action `change_brain_debug` (bool), which calls `DEC_RolePion_Decision::ActivateBrainDebug` (`MIL_AgentPion.cpp:1856-1861`) → Lua `activateBrainDebug_` |
| Step-debug Lua | `<decisional debug-server-port="N"/>` (DirectAI debug server) |
| Type errors in Spark code | `--dec-typecheck` |
| Run arbitrary Lua in a live brain | Magic action `exec_script`, or the Go tool `exdec` (section 6.4) |
| Reproducibility | `<random seed="N"/>` |
| Pathfinding | `--dump-pathfinds`, `debug pathfind`, `pathfind_sandbox`, `pathfind_draw.py` |
| Wire-level messages | `Protobuf.log` (dispatcher debug), `Messages.log` (logger plugin) |

---

## 3. Network and protocol

### 3.1 Topology

```
clients (gaming, Go swapi tests, timeline, web, HLA...) ──TCP──> DISPATCHER (port ≈10001) ──TCP──> SIM (NET_AgentServer, port ≈10000)
                                                           (embedded thread in simulation_app, or dispatcher_app)
```
- The sim accepts **one** connection: the dispatcher. On `control_client_announcement` it calls `DenyConnections()`, sends `ControlSendCurrentStateBegin`, a full state, then `...End` (`Network/NET_SimMsgHandler.cpp:130-140`).
- The dispatcher keeps a mirror model (`dispatcher/SimulationModel`, `dispatcher/Model`) and handles authentication/profiles and rights. It fans messages out to clients and plugins (`dispatcher_dll/PluginFactory.cpp:160-185`):

  `rootHandler → CheckpointFilter → { Rights, DispatcherPlugin(forward to sim/clients), VisionPlugin → HideActionsPlugin → clients, Model, Messenger, Script(if HAS_DIRECTAI), Score(unless analysis service), Logger, Logistic, Reports, Hierarchy, ResourceFiles, session plugins (recorder=Saver, or DLL by library attr) }`

  (`PluginFactory.cpp:207-265`)
- Message envelopes (protobuf, `sword.*`): `ClientToSim`, `SimToClient`, `DispatcherToSim`, `ClientToAuthentication`, `ClientToMessenger`, `ClientToReplay`, `AarToClient`, ... Typed senders live in `src/libraries/protocol/*Senders.{h,cpp}`, and include wrappers like `protocol/magic_actions.h` include the generated `protocol/proto/*.pb.h`.
- **Generated code location:** CMake target `protocol` (`build/CMakeLists.txt:1167-1175`) generates C++ from `modules/sw.api.sword/proto` (`option protobufapi`) into `<build>/protocol.out/`. Go bindings: `ant go.proto` → `src/masa/sword/sword` (`build/build.xml:1010-1015`); Docker uses `protoc` in `Dockerfile:171-176`; they are git-ignored. The go module `github.com/masagroup/sw.api.sword.go` is also an indirect dependency (`src/masa/go.mod:29`). XSD schemas also come from that submodule (`sword.xsd.dir`, `build.xml:47-51`). **The submodule is empty in this clone**, so enum lists below are taken from their usage in C++ code.

### 3.2 Client → sim dispatch (`NET_SimMsgHandler::OnReceiveClient`, `NET_SimMsgHandler.cpp:63-120`)

| ClientToSim field | Handler |
|---|---|
| `control_stop/pause/resume/change_time_factor/date_time_change` | `MIL_AgentServer` (1.2) |
| `control_checkpoint_save_now/_set_frequency` | `MIL_CheckPointManager` |
| `unit_order` / `automat_order` / `crowd_order` / `task_group_order` | `MIL_EntityManager::OnReceive*Order` (`MIL_EntityManager.cpp:1240-1893`) → `MIL_AgentPion::OnReceiveOrder` (`MIL_AgentPion.cpp:670`) → order manager. Replies `TaskCreationRequestAck` with the error code (`error_invalid_unit`, `error_invalid_mission`, `error_unit_cannot_receive_order`, parameter errors). |
| `frag_order` | `OnReceiveFragOrder` (`:1895-1953`): tries automat → crowd → pion → task group |
| `unit_magic_action` | `OnReceiveUnitMagicAction` (`:1280`): manager-level types, else routed to army/formation/automat/pion/crowd/inhabitant/task group/echelon |
| `object_magic_action` | `ObjectManager::OnReceiveObjectMagicAction` (`:1973-1983`) |
| `knowledge_magic_action` | `:1825` |
| `magic_action` (global) | `NET_SimMsgHandler::OnReceiveMagicAction` (`:142-272`) |
| `unit_creation_request`, `burning_cell_request`, `compute_pathfind`, `segment_request`, `compute_intervisibility`, `transmission_magic_action`, `report_creation_request` | various |
| DispatcherToSim `control_tick_ack` | `Continue()` (back-pressure) |

Every magic action is registered in `ActionManager` (action id), acked to the sender, and published as an action to all clients (`actions_->Send`).

### 3.3 Magic actions a designer will use

**Unit magic actions** (`sword::UnitMagicAction::*`). Pion-level ones are in `MIL_AgentPion::OnReceiveUnitMagicAction` (`MIL_AgentPion.cpp:927+`), manager-level ones in `MIL_EntityManager.cpp:1308-1500`.

| Purpose | Type | Notes / Go helper (`swapi/commands_sim.go`) |
|---|---|---|
| Teleport | `move_to` | `ProcessMagicActionMoveTo` (`MIL_EntityManager.cpp:3238`); `client.Teleport` |
| Full recovery | `recover_all`, `recover_troops`, `recover_equipments`, `recover_resources` (+ `_except_log`), `partial_recovery`, `recover_transporters` | `RecoverUnit`, `RecoverAllHumans`, `RecoverAllEquipments`, `RecoverAllResources`, ... |
| Human factors (fatigue/morale/experience/stress) | `change_human_factors` | `OnReceiveChangeHumanFactors` (`MIL_AgentPion.cpp:796`); `ChangeHumanFactors` |
| Damage | `destroy_all`, `destroy_component`, `create_wounds`, `create_breakdowns`, `change_equipment_state`, `change_human_state`, `change_dotation` | |
| Posture / speed / direction | `change_posture`, `change_speed`, `change_direction` | |
| **Run Lua in a brain** | `exec_script` (params: function name, script) | `ProcessExecScript` (`MIL_EntityManager.cpp:5072`) → `GetDecision().ExecuteScript(fn, script)`. Works for pion, automat, crowd and task group. Returns the result string. Go: `ExecScript`, `ExecTemplate` |
| Reload brain (other model) | `reload_brain` (model name) | `MIL_AgentPion.cpp:1807`; Go `ReloadBrain` |
| Brain debug on/off | `change_brain_debug` | Go `DebugBrain` |
| End current mission | `end_mission`, `end_crowd_mission` | |
| Transport | `load_unit`, `unload_unit`, `mount`, `change_transported_units` | |
| Fire | `create_fire_order`, `create_direct_fire_order`, `start_indirect_fire`, `stop_indirect_fire` | |
| Surrender / CBRN / misc | `surrender_to`, `cancel_surrender`, `cbrn_agents`, `set_cbrn_protection`, `change_stealth_state`, `set_altitude`, `set_radio_silence_state`, `toggle_special_sensors`, `change_extension`, `change_critical_intelligence`, `change_symbol`, `rename`, `delete_unit`, `set_remote` | |
| Creation / hierarchy | `unit_creation`, `automat_creation`, `automat_and_units_creation`, `formation_creation`, `crowd_creation`, `task_group_creation`, `unit_change_superior`, `change_automat_superior`, `change_formation_superior`, `change_knowledge_group` | |
| Logistics | `change_*_logistic_links`, `log_supply_*`, `change_supply_state` etc. | many |

**Object magic actions:** `create`, `update`, `destroy`, plus attribute types (`construction`, `mine`, `bypass`, `obstacle`, `fire`, `flood`, `crossing_site`, `stock`, `trafficability`, ...). Go: `CreateObject`, `UpdateObject`, `DeleteObject`, `BreachObject`, `BypassObject`.

**Knowledge magic actions:** `add_knowledge`, `delete_knowledge`, `update_party`, `update_type`, `enable`, `rename`, ...

**Global magic actions** (`NET_SimMsgHandler.cpp:154-257`): `create_knowledge_group`, `create_fire_order_on_location`, `create_linear_fire_order`, `change_diplomacy`, `global_weather`, `local_weather` (+ `_destruction`), `change_resource_network_properties`, `change_exercise_extensions`, `change_actions_speed_factor`, `activate_perception` (global perception kill switch), `pathfind_creation/update/destruction`, logistics state selections, and **`debug_internal`** (`:295-336`). `debug_internal` has these sub-commands: `create_report` (inject a report), `create_fire_report`, `echo`, and, only with `--test-commands`, `trigger_error null_pointer|stack_overflow`.

---

## 4. Checkpoints

- **Manager:** `src/libraries/simulation/CheckPoints/MIL_CheckPointManager.cpp`. Automatic checkpoints run at the end of a tick when due (`:78-87`). The name is `YYYY_MM_DD__HHhMMmSS` (`:172-189`). Rotation keeps `@keep` checkpoints (`:217-238`). Manual: `control_checkpoint_save_now` (`:347-376`, optional `send_state`). Frequency: `control_checkpoint_set_frequency`.
- **Location:** `<session>/checkpoints/<name>/`. The files are plain XML, written by `SaveCheckPoint` (`:240-265`): `globals.xml` (time, factor, tick, dates, reports id), `orbat.xml`, `knowledges.xml`, `weather.xml`, `urban.xml`, `logistics.xml`, **`decisionalState.xml`**, **`missions.xml`**, `exercise-extensions.xml`, `MetaData.xml`.
- **Lock files:** `sim.lock` and `dispatcher.lock` are created during the save (`:286-345`). A checkpoint still holding a `.lock` is considered invalid and gets deleted first on rotation (`:191-215`). The dispatcher must drain its messages at shutdown or it leaves its lock behind (see the comment at `Application.cpp:104-109`).
- **Load:** `--checkpoint <name>` → `SessionConfig` redirects orbat, weather, urban, knowledges, logistics, profiles, drawings, scores, decisionalState, missions, globals and extensions to the checkpoint dir (`core/SessionConfig.cpp:~148-240`). `ReadODB` then loads knowledges, logistics, decisional state and missions (`MIL_EntityManager.cpp:373-394`). Missions are re-created from serialized orders (`MissionController.cpp:128+`).
- **Implications for Lua state (important):**
  - The Lua VM is **not** serialized. Brains are re-created from the scripts on disk at load, so **current script code is used, not the code that was running at save time**. All Lua globals and `myself.*` fields are lost except the "persistent state".
  - Persistent state is `DEC_Decision::Write/ReadDecisionalState` (`Decision/DEC_Decision.h:1133-1159`) → `Brain::GetPersistentState` → Lua `integration.savePersistentState()` / `loadPersistentState()` (`data/app-data/resources/integration/SimulationIntegration.lua:656-681`). It serializes `persistentState[rootKey]` (brain-local storage) and `spark.saveState()`. It is stored as the `state` attribute per entity in `decisionalState.xml`.
  - Only nil/boolean/number/string/table values survive (`serialize` errors on other types, `:640-653`). **Knowledge objects, userdata or functions put in persistent state will fail to save.** The error is logged as "Error serializing persistent state of <id>".
  - Running missions are restarted (with `resume`), so behaviors must tolerate restarting mid-mission. The comment in `SimulationIntegration.lua:660-662` says the main use is "starting missions automatically after loading a checkpoint".
  - Go tests can check this: `-checkpoint` mode saves, reloads and diffs the client model at every `stopSim` (`simtests/setup_test.go:252-337`). `checkpointAndRestart` is used inside tests (`simtests/checkpoint_test.go:270-298`).

---

## 5. Other applications and libraries

### 5.1 `src/applications`

| App | One line |
|---|---|
| `simulation_app` | The simulation server (+ embedded dispatcher). |
| `dispatcher_app` | Standalone dispatcher (when `embedded="false"`). |
| `gaming_app` | Main operator/trainee GUI client (Qt). |
| `preparation_app` | Exercise authoring (orbat, weather, objects, scores). |
| `adaptation_app` | Physical database editor (the GUI for the physical XMLs). |
| `selftraining_app` | Launcher: create, configure and run exercises/sessions (writes session.xml). |
| `replayer_app` | Replays `record/` data to clients. |
| `repeater_app` | Relays a running exercise to more clients. |
| `cloud_server` / `cloud_client` | Admin service (web exercise management) and its client. |
| `timeline_client` | Timeline service client. |
| `terrain_app`, `generation_app`, `generation_light`, `raster_app`, `detection_app`, `extractor_app`, `mapnikgen`, `mapnik2png`, `svgl_app` | Terrain generation, rendering and preprocessing tools. |
| `pathfind_sandbox`, `pathfind_draw` (py), `debugindex` | Pathfinding graph debugging tools. |
| `migratexml` | Applies XML migrations (`migrations.xml`) with optional XSD validation. |
| `radioaar_app` | Radio AAR recorder bridge. |

### 5.2 `src/libraries` (59 dirs)

- **core** (248 files, shared by sim, dispatcher and clients): the config chain (`GeneralConfig` → `PhysicalConfig` → `ExerciseConfig` → `SessionConfig`), `PhyLoader` (physical.xml index), `ExerciseSettings`, `Schema` / `FileMigration` (XSD and migrations), static-model types (`AgentType`, `AutomatType`, `MissionType`, `FragOrderType`, `OrderParameter`, `DecisionalModel`, `SensorType`, `ObjectType`, `DotationType`, `SymbolFactory`/App6), `Entity_ABC` hierarchy for clients, `Application` (process shell), `Errors`, `CoordinateConverter`.
- **simulation**: the kernel (covered by other sections). **simulation_terrain**: `TER_World`, pathfinder, spatial queries. **pathfind**, **spatialcontainer**, **terrain**, **propagation** (flood/ASC), **resource_network**, **detection**: support libraries.
- **protocol**: protobuf helpers, senders, parameter readers (`MessageParameters`), enum maps, XML <-> proto.
- **dispatcher**, **dispatcher_dll**: client/sim networking, mirror model, plugin tree, profiles/rights.
- **Plugins**:
  - `logger_plugin`: Messages.log and actions.ord.
  - `saver_plugin`: recorder (replay `record/`).
  - `replay_plugin`, `aar_plugin`.
  - `script_plugin`: dispatcher-side **Lua scenario scripts** from `<exercise>/scripts/*.lua`. Triggers, events and conditions (`ScriptPlugin.cpp:207-210`, `StartupOrders.lua`). This is not DIA brain code.
  - `score_plugin`: scores and indicators.
  - `logistic_plugin`: CSV consign logs.
  - `reports_plugin`, `messenger_plugin` (drawings, chat, alerts), `rights_plugin`, `hierarchy_plugin`, `vision_plugin` (vision cones), `web_control_plugin(_dll)` (HTTP control).
  - `hla_plugin(_dll)` + `rpr` + `rti`: HLA/RPR-FOM/NETN federation; remote units have no brain (`MIL_AgentPion.cpp:1816-1830`).
- **Clients/UI**: `clients` (1596 files, shared Qt GUI), `gaming`, `preparation`, `gui`, `graphics`, `svgl`, `selftraining`.
- **Analysis**: `3a` (after-action analysis functions), `indicators`, `reports` (report templates), `analysis_client`, `analysis_terrain`.
- **Infrastructure**: `host` (admin/slave session hosting), `runtime`, `web`, `client`, `timeline` (timeline API), `tic` (platform-level formations for HLA), `ent` (enum conversions).
- **Geo**: `geocoord`, `geodata`, `geostore`, `gdal_ogr`, `dted`, `iso8211`, `vpf`, `milsrp`, `extractor`, `generation`.

### 5.3 Other source trees
- `src/tests/*`: C++ Boost.Test unit tests. `simulation_test` uses `data/tests/simulation_test/*.bms` brains. Run them with `ant unit_test [-Dctest.tests=X]`. The README notes these tests "tend to be deprecated" in favor of the Go tests.
- `src/scripts`: Python terrain and shapefile utilities, `osm2masa`, `export2sword`.
- `data/app-data`: runtime resources deployed to `run/<platform>`, in particular **`resources/integration/*.lua`** (the integration layer), `integration tests/` (pure-Lua tests, `ant checkintegration`), `Integration.lua`, `StartupOrdersTemplate.lua`, themes, locales.

---

## 6. Go side (`src/masa`, module `github.com/masagroup/sw.sword`, Go 1.27.2)

### 6.1 Packages
| Package | Role |
|---|---|
| `sword/swapi` | Protobuf client for Sword. `Client` (commands: `commands_sim.go`, ~300 methods), `Model`/`ModelData` (client-side mirror of every entity, `WaitCondition`, `WaitTicks`, `WaitUntilTick`), `DecClient` (Lua templates around DEC functions, `dec_client.go`). |
| `sword/swapi/simu` | Writes `session.xml` (`session.go:447-487` default session), starts/stops sim, replayer, repeater and gaming processes, and scans logs for Lua/fatal errors (`simerrors.go`). |
| `sword/swapi/phy` | Reads physical DBs (look up unit/automat type IDs by name). |
| `sword/swconfig` | Test flags and port allocation (`swconfig.go:61-127`). |
| `sword/swrun` | Helpers `StartSimOnExercise`, `ConnectAndWaitModel`, `LoginAndWaitModel`, and the **`sw` CLI** implementation (`swrun/sw.go`, commands: `sim`, `gaming`, `prepa`, `self`, `replay`, `repeater`, `timeline`, `admin`, `slave`, `agent`, `relay`, `license`, `draw`). |
| `sword/simtests` | **Functional simulation tests** (62 files: dec, movement, fire, knowledge, logistic, checkpoint, ...). |
| `sword/apptests`, `sword/simbench` | App-start tests and benchmarks. |
| `sword/cmd/*` | `exdec` (run Lua in a live brain), `addreports`, `csvclient`, `dumpreplay`, `stressclient`, `testclient`, `simclients`, `phy2virtual` / `phy2manualShift` (rewrite the test physical DB for convoy/maintenance variants). |
| `gotester` | Parallel test runner (one process per test, port ranges). |
| `timeline/` | Timeline server and services (Go + Angular front `www_ng`). |
| `admin/` | Cloud admin (node, agent, slave, proxy, www). |
| `analysistests`, `replay`, `resender`, `ter`, `testproto`, `util`, `generation_tester` | Misc. |

### 6.2 Test exercises and models
- The exercises directory defaults to `data/tests/gosword` (`swconfig.go:115-117`). It holds 26 exercises: `crossroad-small-empty`, `crossroad-small-test`, `crossroad-small-orbat`, `crossroad-log`, `CheckpointTests`, `dynamic-comms`, `Paris_Est-knowledge-groups`, `grad-x.y-test-empty`, `island`, ... Constants are in `simtests/setup_test.go:50-74`.
- Models: 20 exercises use `model dataset="ada" physical="test"` and 5 use `physical="mildb"`. The **`test` physical DB is versioned here in `data/tests/phy/test`**. `ant deploy-application-data` syncs it to `data/data/models/ada/physical/test` (`build.xml:97-99`, and Docker `Dockerfile:479`). The `ada` decisional model and `mildb` come from the sw.models release package `decisional-dev` (`build.xml:1396`). **So simtests run the real DIA5 behaviors from sw.models against a test physical base.**
- Terrains: `crossroad-small` (14 exercises), plus Angers, Cabourg, Paris_Est, island, land-of-*, grad-x.y, edge-river, Drosoville. They are downloaded by `ant update-test-databases` or come from Docker terrain images.
- Users in profiles: `alluser/alluser`, `admin/""`, `scriptingadmin/""` (`setup_test.go:129-151`).

### 6.3 Skeleton of a simtest

```go
package simtests   // src/masa/sword/simtests/xxx_test.go

func TestMyBehavior(t *testing.T) {
    // 1. start sim on an exercise (writes sessions/<ts>/session.xml with step=10s, factor=10000,
    //    latency=0, seed=0, random-breakdowns=false) + connect + login + wait initial state
    sim, client := connectAndWaitModel(t, NewAdminOpts(ExCrossroadSmallTest))
    defer stopSimAndClient(t, sim, client)        // stops sim, FAILS on Lua tracebacks / fatal errors in Sim.log
                                                   // (use stopSim(t, sim, DisableLuaChecks()) to tolerate)
    // 2. build entities (types looked up by name in the test physical DB)
    phydb := loadPhysical(t, "test")
    party := client.Model.GetData().FindPartyByName("party1")
    CreateFormation(t, client, party.Id)
    automat := createSpecificAutomat(t, client, "party1", getAutomatTypeFromName(t, phydb, "Mobile Infantry Platoon"))
    unit, err := client.CreateUnit(automat.Id, getUnitTypeFromName(t, phydb, "Mobile Infantry"), swapi.Point{X: -15.8193, Y: 28.3456})
    assert.NoError(t, err)

    // 3. send a mission (params positional, as in the mission definition)
    dest := swapi.Point{X: -15.8126, Y: 28.44}
    params := swapi.MakeParameters(swapi.MakeHeading(0), nil, nil, nil, swapi.MakePointParam(dest))
    _, err = client.SendUnitOrder(unit.Id, MissionMoveTestId, params)
    assert.NoError(t, err)

    // (optional) magic actions: client.Teleport(...), client.ChangeHumanFactors(...), client.RecoverUnit(...)

    // 4. wait for a model condition (dumps model.txt into the session dir on timeout)
    waitCondition(t, sim, client.Model, func(d *swapi.ModelData) bool {
        return isNearby(dest, d.Units[unit.Id].Position)
    })

    // 5. probe the brain directly with Lua
    out, err := client.ExecTemplate(unit.Id, "TestFunction",
        `function TestFunction() return tostring(DEC_AreMissionsEnabledIfTransported(myself)) end`, nil)
    assert.NoError(t, err); assert.Equal(t, "false", out)

    // 6. optionally: sim, client, _ = checkpointAndRestart(t, sim, client) and re-assert
}
```
This is condensed from `simtests/dec_test.go:47-89,513-583`, `unitorder_test.go:32-50` and `setup_test.go`. Mission IDs such as `MissionMoveTestId = 5` are constants in the test files (`movement_test.go:27`). They are IDs from the `test` physical `Missions.xml`.

**Relevance to behavior work:**
- `ExecScript`/`ExecTemplate` and `DecClient` let you unit-test DEC functions and integration functions inside a real brain.
- Mission tests check observable outcomes in the client model: position, reports (`opts.Reports` → `client.Model.RecordReports()`), knowledge, equipment, and so on.
- A behavior that throws a Lua error fails any test that touches it, through the Sim.log scan.

### 6.4 Running tests and tools
- **Prerequisites:** build `simulation_app` (plus `replayer_app` and `gaming_app` for some tests), run `ant update`/`deploy` (models, terrains, test physical DB).
- **ant:** `ant go.test` (all) or `ant go.test -Dtest.run=Collision`. Timeout: `-Dtest.timeout=2m` or env `MASA_TIMEOUT` (default 3 min per wait, `swconfig.go:118-125`).
- **Manual:** `source build/go_env.sh`, then from `src/masa`: `go test ./sword/simtests -run TestDecUnit [-application …/simulation_app.exe -root-dir …/data]`. Flags: `-platform`, `-bin-dir`, `-data-dir`, `-run-dir`, `-out-dir`, `-exercises-dir`, `-test-port` (35000), `-keep-output`, `-checkpoint`, `-repeater`, `-gaming`, `-show-log`, `-d` (debug exe), `-redundant-msg`.
- **gotester:** `bin/gotester.exe -f TestName src/masa -- -platform vc140_x64 -checkpoint=true -repeater=true`. Options `-j`, `-x`, `-pkg`, `-repeat`, `-fail-fast` (`gotester/gotester.go:378-405`). VS Code tasks wrap it (`.vscode/tasks.json`: GotesterTest, GotesterCheckpointTest, ...).
- **Docker (Linux):** `docker compose --profile test build tests-simtests`. This runs `gotester -pkg=sword/simtests -j=10` inside the image, with licenses mounted (`Dockerfile:501-520`).
- Session dirs are created under the exercise's `sessions/` and **deleted after a fully passing run** unless `-keep-output` is set (`setup_test.go:34-40`).
- **`exdec`**: `exdec -host 127.0.0.1 -port 10001 -user admin -unit <id> script.lua` wraps the script in a function and runs it in that unit's brain (`sword/cmd/exdec`). It is handy for interactive exploration against a running exercise.
- **`sw` tool:** `sw sim data/exercises/<exercise>` starts sim, timeline and gaming from a built tree. `sw --help` lists the commands.

---

## 7. Build, run and dev workflow

### 7.1 Ant (`build/build.xml`, 1576 lines; Windows / vc140_x64 primary)
| Target | Purpose |
|---|---|
| `update` | Downloads packages: directai + `spark-core` (into `run/resources`), sw.cpplibs, sw.hla, sw.tools, sw.analysis, **sw.models `decisional-dev` → `data/data/models` (clean) and `exercises-dev` → `data/exercises`** (`:1384-1402`). |
| `deploy` | `generate-lua-reports` (→ `data/app-data/resources/integration/Reports.lua`, git-ignored), `generate.xsd`, `deploy-application-data` (copies `data/app-data` → `run/<platform>`, syncs the test phy DB), `generate-terrain-data`. |
| `configure` | `update` + `deploy`. `all` = configure + build + ... |
| `cmake.configure`, `cmake.msvc` (→ `out/<platform>/msvc/sword.sln`), `build [-Dninja.target=simulation_app -Dninja.jobs=8]`, `build.debug` | C++ build; outputs go to `out/<platform>/release|debug`, run from `run/<platform>`. |
| `unit_test`, `gen_test`, `go`, `go.proto`, `go.build`, `go.test(.sword/.analysis/.timeline/.admin/.sword.virtual_convoy)`, `go.benchmark` | Tests. |
| `checkdec` | **Checks that models are in sync with the DEC functions** (`data/data/models/decfunctions.txt` vs `src/libraries/simulation`, `:882-888`). |
| `checkreports`, `checkintegration` (Lua integration unit tests), `checkdeps`, `checkreplayfilters`, `check` | Sanity checks. |
| `timeline`, `admin`, `admin.deploy`, `admin.run`, `package*`, `format` | Misc. |

`build/build.properties` and a `PONEY_HOME` property are required by the ant build (sw.poney build system). `bin\tools\mmake simulation_app` does partial builds (README).

### 7.2 CMake (`build/CMakeLists.txt`)
Options: `BUILD_CLIENTS`, `BUILD_SERVERS`, `BUILD_TESTS`, `SWORD_WARNINGS_AS_ERRORS`, `DAI_VERSION`. `HAS_DIRECTAI` is defined (it enables script_plugin). `simulation_app` target is at `:1349-1384` (Windows `/STACK:15000000`). It links `dispatcher_dll`.

### 7.3 Docker / Linux
- `Dockerfile` (690 lines) is multi-stage, gcc 16.2, C++17. `base` builds the servers only (`BUILD_CLIENTS=OFF`) into .deb packages. Runtime images: `simulation`, `dispatcher`, `replayer`, `repeater` (entry `simulation_app --root-dir=/app/ --debug-dir=/app/debug --dump-level=off`, CMD `--exercises-dir=/app/exercises --exercise=worldwide/Egypt --session=session_1 --clients=2`). Test images: `simtests`, `simtests-virtual-convoy`, `analysistests`, `tests-cpp`, timeline tests. `dev` image: hot-rebuilds and restarts the sim on `./src` changes (`build/dev-hotreload.sh`).
- `docker-compose.yaml` profiles: `prod`, `dev`, `test`, `pack`. Mounts `SWORD_LICENSE_DIR` (`~/.sw/licenses`), `SWORD_DATA_DIR`, `SWORD_EXERCISE_DIR` and `SWORD_SESSION_DIR` (defaults in `.env`: `VERSION=6.29.0`, exercise `worldwide/Egypt`). DirectAI image tag is `DAI_TAG`, `DAI_VERSION=5.10`.

### 7.4 CI (`.github/workflows`)
- `ci.yaml` (push), `pr.yaml`, `bot.yaml`, `commands.yaml` (`rebase-all`) and `update.yaml` (dependency bump) all delegate to `masagroup/cicd` shared workflows.
- Weekly scheduled Windows self-hosted ant runs: `checkpoints.yaml` (`-Dtest-checkpoint=true`), `repeater.yaml`, `repeater-checkpoints.yaml`.
- The bot `swbuilder` opens "update submodule/go module" PRs.

### 7.5 Versions, branches and conventions
- **master = 6.29.0 dev as of 2026-11** — master advances over time, so always re-read `build/build.xml:40-41` (`version.project=master`, `version.major=…`). Recent history: "ci: create dev version '6.29.0'", "merge branch '6.28' into 'master'", "6.28.1".
- Release branches on remote `MASA`: `6.20` through `6.27` (plus `NAVY`, `TARAN`, `6.23-orion`, `6.23-ocrfit`). The local `6.24` to `6.27` branches are mostly translation work. The `6.28` branch is not fetched locally.
- Remotes: `MASA` = `masagroup/sw.sword` (upstream), `origin` = `ACSMASA/sw.sword` (fork). This is the triangular workflow from `.github/CONTRIBUTING.md`.
- sw.models `decisional.xml` declares `model-version="6.24"`. That is fine: the version check is ascending-compatible.
- **CONTRIBUTING vs reality:** CONTRIBUTING asks for `[pkg] JIRA: msg [EPS:x]`. The actual log uses **conventional commits** `type(scope): JIRA-ID msg`, e.g. `feat(sim, dec): SW-28156 …`, `fix(gaming): SW-27785: …`. Branch names are prefixed with the JIRA ID (`SW-…`, `SWNG-…`, `IO-…`).
- **C++ style:** `.clang-format` (Allman braces, 4 spaces, 120 columns, `SpacesInParentheses` style `f( x )`, includes regrouped with `*_pch.h` first). CONTRIBUTING: camelCase, members end with `_`, minimal comments. Some code uses `// clang-format off` around xml streams.
- **Recent themes** (last ~300 non-merge commits): `fix` 57, `build(docker)` 30 (Linux port/packaging), `feat` 27, `ci` 20, `fix(simulation)` 16, `refactor` 12, `build(cmake)` 10, `fix(hla_plugin)` 9 (NETN4/RPR), `fix(gaming)` 8, `test(simtests)` 6, `feat(dec)` 4. Recent DEC/integration additions: task-group travel-time DEC functions (SWNG-531), radar/sonar state check (SW-28139), object not buildable due to weather (SW-28156), cached unit terrain data (SW-28155), canLoad/heaviest-equipment checks (SW-27111), oceanic sound profile / sonic layer depth (SW-28024), terrain enum in integration, archetype brain cache never destroyed (SWNG-1055).

---

## 8. Top gotchas (runtime side)

1. **Perception and knowledge run before decision in each tick.** Actions started by Lua execute in the same tick, after all brains have run. Network attribute updates are sent in the state phase. Missions received during tick N start at N+1.
2. **Magic actions and orders are processed between ticks, as soon as they are received,** not in a tick phase. `exec_script` runs Lua synchronously at that moment.
3. **Checkpoints do not save the Lua VM.** Only `integration.savePersistentState` (plain data only) and the current missions are saved. On reload, the current script code runs and missions restart.
4. **Sim.log Lua tracebacks fail simtests** unless the test uses `DisableLuaChecks()`.
5. **`decisional.xml @model-version` must be ≤ the runtime version** (major.minor), or the sim refuses to start.
6. **`physical="test"` in the test exercises is the repo's `data/tests/phy/test`, synced at deploy.** Editing `data/data/models/ada/physical/test` directly gets overwritten. The decisional model is the downloaded sw.models package, not your working copy, unless you point `--models-dir`/`--root-dir` at it.
7. **Seed 0 means a time-based seed.** Set `<random seed>` for reproducible runs. Random breakdowns default to on in real sessions and off in Go tests.
8. **Integration Lua is deployed by copy** (`data/app-data/resources/integration` → `run/<platform>/resources/integration`). After editing, rerun `ant deploy-application-data` or use `--integration-dir`.
9. **`--checkpointorbat`, `--testdata` and `--deletecheckpoint` are dead options.**
10. **`modules/sw.api.sword` is empty in this clone,** so there are no `.proto`/`.xsd` files and no generated code locally. Enum names here come from their use in C++ code.
