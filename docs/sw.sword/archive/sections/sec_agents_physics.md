# Agents & Physics: how the kernel reads the physical DB and runs unit physics

> Scope: `src/libraries/simulation/Entities/Agents/**`, root `Default*Computer` / `*_ABC` algorithm interfaces, `Entities/Specialisations/**`, `Meteo/`, `Urban/`, plus the physical-DB loading entry points.
> Path convention: **`sim/` = `src/libraries/simulation/`** (relative to the sw.sword root). Line numbers are from the current `main` and may drift by a few lines. A `~` before a line number means the location is approximate.
> Claims I could not confirm from code are marked **(unverified)**. Areas I did not read are marked **(not yet explored)**.
>
> Note for the navigator: the path `C:\MASARepo\sw.models\data\data\models\mcast_v3\physical\mcast\` **does not exist** on this machine. The physical DB I found is `C:\MASARepo\sw.models\data\data\models\ada\physical\mildb\` (also `.../ada/physical/worldwide-tests/`). Its `physical.xml` maps root tags to file names, for example `<components file="Equipments.xml"/>`.

---

## 0. Executive model (read this first)

```
physical.xml  ──(root tag → file)──▶ core::PhyLoader::LoadPhysicalFile(tag, loader)     src/libraries/core/PhyLoader.cpp:84
                                       │
MIL_EntityManagerStaticMethods::Initialize  (sim/Entities/MIL_EntityManagerStaticMethods.cpp ~112-200)
   loads in a FIXED ORDER: volumes → protections → resources → launchers → weapons → sensors
   → components(Equipments) → units → automats → logistics → speeds(prototypes) ...
                                       │
Static type registries (PHY_*Type::Find(name)) ── referenced BY NAME from other files
                                       │
Unit instance (MIL_AgentPion) = role container
   roles own runtime state; equipment instances (PHY_ComposantePion) hold
   sensors (PHY_Sensor), weapons (PHY_Weapon), humans, breakdown/maintenance state
                                       │
Per tick (sim/Entities/MIL_EntityManager.cpp:1097): breakdowns preprocess → comms → knowledges
   → decisions (DIA/Lua brains) → actions (move/fire/build...) → effects (damage applied) → states
   (MIL_AgentPion::UpdatePhysicalState) → knowledge groups
```

* **Everything in the physical DB is cross-referenced by `name` strings.** Numeric `id`s are only for protocol/client identity, and they must be unique where they are checked. Renaming a launcher, resource, volume or protection breaks every reference to it, and loading throws `MASA_EXCEPTION`.
* **Load order matters.** For example, `Equipments.xml` is parsed after `WeaponSystems.xml` and `Sensors.xml`, and `Units.xml` is parsed after `Equipments.xml`. A reference to a type defined later fails at load time.
* **Internal units:** distance in metres (`ConvertMeterToSim` is the identity, `sim/Tools/MIL_Tools.cpp:98`), speed in **metres per tick**, time in **ticks**. The tick length comes from the session file `<session><config><simulation><time step="…">` (`sim/Tools/MIL_Config.cpp:171`), read at `sim/MIL_AgentServer.cpp:220`.

---

## 1. Physical DB loading map

### 1.1 Mechanics
* `core::PhyLoader` (`src/libraries/core/PhyLoader.cpp:21-130`) reads `physical.xml`. Each child element `<tag file=".."/>` or `<tag path=".."/>` registers a file. `LoadPhysicalFile(tag, fn)` opens the file (with a cache) and calls `fn(xis)`. `LoadOptionalPhysicalFile` tolerates a missing entry.
* Simulation-side entry: `MIL_EntityManagerStaticMethods::Initialize` (`sim/Entities/MIL_EntityManagerStaticMethods.cpp`, ~112-200). It uses the helper `InitializeType<T>(loader, tag)`, which calls `T::Initialize(xis)` (lines 90-101).
* Before that, `MIL_AgentServer` loads `pathfinder` (`sim/MIL_AgentServer.cpp:97`) and `decisional` (`:208`, task-group dividers). `DEC_Workspace` loads `decisional` (`sim/Decision/DEC_Workspace.cpp:80`), `missions` and `models` (`:234`, `:248`).
* **Hard-coded (not from XML):** the composante states, postures, morale levels, `PHY_NatureLevel`/`Atlas`, `PHY_RoePopulation`, maintenance/logistic levels, perception levels, radar classes, indirect-fire dotation classes, ammo dotation classes, human wounds (the list; the data comes from Health.xml), and `PHY_SeabedType` (`Initialize()` with no loader, lines ~115-133).

### 1.2 Table: XML file → loader → key attributes

| XML file (tag in physical.xml) | Loader (file:line) | Key attributes read (units) |
|---|---|---|
| **Volumes.xml** (`volumes`) | `PHY_Volume::Initialize` `sim/Entities/Agents/Units/Categories/PHY_Volume.cpp:19-35` | `volume@name`, optional `@surface`. Also reads `Decisional.xml/objects-on-damage/object-on-damage@volume,@object` (`:39-60`); the object type must exist or loading throws. |
| **SoundProfiles.xml** (`sound-profiles`) | `PHY_SoundProfile::Initialize` `.../Categories/PHY_SoundProfile.cpp:17` | name list (used by acoustic sensors) |
| **Ranks.xml** (`ranks`) | `PHY_HumanRank::Initialize` `.../Units/Humans/PHY_HumanRank.cpp:24-92` | `rank@name` (list order defines rank index) |
| **Protections.xml** (`protections`) | `PHY_Protection::ReadProtection` `.../Categories/PHY_Protection.cpp:44-133` | `@name`; `@type` = `humain`/`materiel`/anything else ⇒ **crowd** (case-insensitive, `:69-71`); `neutralization@average-time,@variance` (time strings → ticks, Gaussian, `:74-85`); for material only: `random-breakdown-probability@eva,@neva` (**percent 0-100**, `:101-111`), `attrition-effects/attrition-effect@equipment-state,@injured-percentage,@dead-percentage` (**percent**, `:119-133`). For humans: fixed effects (dead → 100 % dead; repairable → 100 % wounded). |
| **ActiveProtections.xml** (`active-protections`) | `PHY_ActiveProtection` `.../Composantes/PHY_ActiveProtection.cpp:24-55` | `@name,@coefficient,@hard-kill`, `resource@name,@usage`, per-weapon `@coefficient` |
| **DotationNatures.xml**, **LogisticSupplyClasses.xml** | `PHY_DotationNature`, `PHY_DotationLogisticType` (same folder) | names |
| **Dotations.xml** (`resources`) | `PHY_DotationType::ReadDotation` `.../Dotations/PHY_DotationType.cpp:74-104` → `PHY_DotationCategory` ctor `.../Dotations/PHY_DotationCategory.cpp:34-256` | `resource@category` (dotation type: munition, carburant, ration…), `@name`, `@id`, `@nature`, `@ied`, `@daily-crew-member-consumption`, `@consumption-per-kilometers`, `@package-size,@package-mass,@package-volume` (mass and volume **per package**; divided by size, `:66-82`), `attritions/attrition@protection,@destruction,@repairable-with-evacuation,@repairable-without-evacuation` (**ratios 0-1**, `PHY_AttritionData.cpp`), `crowd-attrition@scattered,@wounded,@dead`, `urban-modifiers/urban-modifier@material-type,@value` (0-1, **protection** factor), `indirect-fires@intervention-type,@x-dispersion,@y-dispersion (m),@detection-range (m),@task-group-delay`, `terrain-modifiers`, `object-modifiers`, `indirect-fire@type (Explosif/Fumigene/Eclairant/Effect)` with `@neutralization-ratio (≥1)`, `@crowd-ph (default 0.3)`, `ph@target-posture,@value` (`PHY_DotationCategory_IndirectFire.cpp:94-112`), `@logistic-supply-class`, `illuminating@range,@maintain`, `guided@use-pk` + volumes/armors/external-guidance/self-guidance, `@type` (ammo class) |
| **Launchers.xml** (`launchers`) | `PHY_LauncherType` `.../Weapons/PHY_LauncherType.cpp:25-110` | `launcher@name`, `@indirect-fire` (bool). Direct fire is enabled **by the mere presence** of a `ph-modifiers` element: `ph-modifiers@posture` / `ph-modifier@target-posture,@value` (0-1) form a firer-posture × target-posture matrix. |
| **WeaponSystems.xml** (`weapon-systems`) | `PHY_WeaponType` `.../Weapons/PHY_WeaponType.cpp:41-160`; `PHY_WeaponDataType_DirectFire.cpp:61-89`; `PHY_WeaponDataType_IndirectFire.cpp:36-56` | `weapon-system@launcher,@munition` (names; the weapon is named `launcher/munition`), `burst@munition,@duration`, `reloading@munition,@duration` (time strings, **required**, converted to ticks), `direct-fire@max-height` + `hit-probabilities@target (volume)` / `hit-probability@distance (m),@percentage (**0-1 ratio**)`, `indirect-fire@average-speed (m/s),@min-range,@max-range (m),@apex (default max-range/2)` |
| **Sensors.xml** (`sensors`) | `MIL_EntityManagerStaticMethods::LoadSensors` (~238) → `PHY_PerceptionRecoSurveillance::Initialize`, `PHY_PerceptionFlyingShell::Initialize`, `PHY_SensorType::Initialize` `.../Sensors/PHY_SensorType.cpp:27-71`, `PHY_RadarType::Initialize` `.../Radars/PHY_RadarType.cpp:70-300` | sensor `@name`, `@detection-delay` (time → ticks), `@activation-on-request`; `unit-detection` → `PHY_SensorTypeAgent` (§5.2); `object-detection/object@type` → `PHY_SensorTypeObjectData` (`@detection-distance`, `@max-detection-distance`, population modifier). Radar: `@name,@type` (radar class), `@action-range (m)`, `@min-height,@max-height`, `detectable-activities/detectable-activity@type (consumption mode),@value`, `acquisition-times/acquisition-time@level,@base-time,@command-post-time`, wind/wave modifiers, limited-to-sensors. |
| **Transmitters.xml** (`transmitters`) | `PHY_TransmitterType` `.../Transmitters/PHY_TransmitterType.cpp:38-121` | `@id,@name,@range,@proximity-range,@ignore-jamming`, compatibilities, modifiers |
| **NBC.xml** (`nbc`) | `PHY_CBRNProtection` `.../CBRN/PHY_CBRNProtection.cpp:32-100`; `PHY_CBRNAgent` `.../CBRN/PHY_CBRNAgent.cpp:46-147` | protection `@id,@name,@failure`, `effects@speed,@reloading,@posture,@ph,@detection` (multipliers), protection-delays; agent `@type,@id,@min,@max,@evaporation`, `contamination@distance,@ratio`, `intoxication@rate,@reference-concentration,@time-unit`, efficiencies `@value` (**integer percent** ×0.01) |
| **Equipments.xml** (`components`) | `PHY_ComposanteTypePion::Initialize/ReadElement` `.../Composantes/PHY_ComposanteTypePion.cpp:72-140` + base `PHY_ComposanteType_ABC.cpp:19-33` | see §1.3 |
| **Prototypes.xml** (`prototypes`) | `PHY_Speeds::Initialize` `.../Units/PHY_Speeds.cpp:334-344` | `speeds/speed@name` prototypes. **Used only by decision (`DEC_SpeedPrototypeContext.cpp:19`), not by physical movement** |
| **Units.xml** (`units`) | `MIL_AgentTypePion::Initialize/ReadUnit` `sim/Entities/Agents/MIL_AgentTypePion.cpp:85-180` → `MIL_AgentType_ABC` (`@id`, `nature@nature-app6,@level,@atlas-nature`) + `PHY_UnitType` ctor `.../Units/PHY_UnitType.cpp:30-270` | see §1.4 |
| **Automats.xml** | `MIL_AutomateType` (out of scope) | `@type` "Automate LOG TC2"… picks the class (`sim/Entities/Automates/MIL_AutomateType.cpp:44-63`) |
| **HumanFactors.xml** (`human-factors`) | `PHY_Experience/Stress/Tiredness/Temperatures::Initialize` `.../Units/HumanFactors/*.cpp` | `humans-factors/experience-factor\|tiredness-factor\|stress-factor/modifier@state,@max-speed,@loading-time,@ph,@posture-setup-time,@sensor-distance` (`PHY_HumanFactor.cpp:19-25`); `automatic-evolution/tiredness-evolution@first-threshold,@second-threshold,@engine-running,@engine-stopped,@moving,@working` (**per hour**, scaled to ticks, `PHY_Tiredness.cpp:31-50`); `stress-evolution@first-threshold,@second-threshold,@inc-per-shot,@dec-per-hour` (`PHY_Stress.cpp:29-44`); optional `temperature-factor/modifier@temperature` (linearly interpolated, `PHY_Temperatures.cpp:20-82`) |
| **Breakdowns.xml** | `PHY_BreakdownType` `.../Units/Logistic/PHY_BreakdownType.cpp:45-206` | `diagnosis@time`, `repair-duration-in-man-hours@value`, `category@name/breakdown@name,@type,@id,@average-repairing-time,@variance,@control-time`, `part@resource,@quantity`, facilities |
| **Health.xml** (`health`) | `PHY_HumanWound::InitializeMedicalData` `.../Units/Humans/PHY_HumanWound.cpp:64-195`; `PHY_MedicalResourcesAlarms`; `PHY_MedicalTransportConsign` | sorting time, `injury@category,@percentage,@treatment-time,@resting-time,@stable/unstable-evolution(-time),@back-to-war,@untreatable,@first-aid-kit...`, evacuation flags |
| **Maintenance.xml** | `PHY_MaintenanceWorkRate` `.../Logistic/PHY_MaintenanceWorkRate.cpp:55-100`, `PHY_MaintenanceResourcesAlarms`, `PHY_MaintenanceTransportConsign` | working-schemes `@working-time`, rest/guard delays, availability alerts |
| **Supply.xml** (`supply`) | `PHY_SupplyResourcesAlarms`, `logistics::SupplyConvoyConfig` `.../Roles/Logistic/SupplyConvoyConfig.cpp:46-217` | convoy `@unit-type,@unit-type-air,@unit-type-sea` (unit type names), `@mission`, setup/loading/unloading `unit-time@truck-count,@time`, `nature-time`, `speed-modifiers/speed-modifier@truck-count,@value` |
| **Communications.xml** | `PHY_RolePion_Communications::Initialize` `.../Roles/Communications/PHY_RolePion_Communications.cpp:~50-68` | `communication-breakdown@speed-modifier,@reloading-time-modifier`, counter-measure duration (default 60 s) |
| **Urban.xml** (`urban`) | `PHY_MaterialCompositionType`, `PHY_AccomodationType`, `PHY_InfrastructureType`, `PHY_RoofShapeType` (`sim/Urban/*.cpp`) | material-composition-types with attritions per protection; accommodations `@role,@nominal-capacity,@max-capacity`; infrastructures `@symbol,@medical` |
| **Decisional.xml** (`decisional`) | `DEC_Workspace::LoadDecisional` `sim/Decision/DEC_Workspace.cpp:97-183` | `dangerosity-modifiers@max-accuracy,@max-operational-state,@max-neutralized-state` (**percent**); `operational-state-weights@major-component,@crew,@threshold` (**ratios 0-1**); `urban-perception@view-factor` (percent, default 100); `perception@detect-destroyed-units,@max-level,@equipment-calculation-frequency`; `prototypes@speeds` (default speed prototype); `task-groups@linear-speed-width-factor`, `@combat-speed-divider`, `@combat-movement-divider` (`MIL_AgentServer.cpp:208-215`) |
| Objects, Fires, Injuries, Crowds, Populations, KnowledgeGroups, Funeral, Pathfind, Reports, ResourceNetworks | other sections | (not yet explored here) |

### 1.3 Equipments.xml (`PHY_ComposanteTypePion`) attribute map

| Element / attribute | Code | Meaning / unit / default |
|---|---|---|
| `@id` | `PHY_ComposanteType_ABC.cpp:20` | unique (checked, `PHY_ComposanteTypePion.cpp:90`) |
| `@size` | `:22` | **Volume name** (Volumes.xml). Drives the PH table row and the optical detection size factor. |
| `@radar-cross-section`, `@acoustic-cross-section` | `:23-24` | Volume names. Default = `@size`. Used by radar and sonar respectively (`PHY_RolePion_Composantes.cpp:647-654`). |
| `@sound-profile` | `:25` | used by non-optical (acoustic) sensors |
| `@protection` (+ optional `@protection-rear`, `@protection-sides`) | `:28-33` | Protection names. With directional armour, the fire direction selects the protection. |
| `@max-slope` | `PHY_ComposanteTypePion.cpp:114` | **ratio** (dz/dx; 1.0 = 45°). Default **1.0** (header `:420`). Must be ≥ 0. |
| `@slope-deceleration` | `:115` | 0-1. Default 0. |
| `@weight` | `:116` | **tons**, > 0 required. Used for transport capacity, refloat pulling, urban trafficability. |
| `@width`, `@air-filtration`, `@activated-on-request` | `:117-119` | width default 1 |
| `@length`, `@speed-prototype`, `codeEMAT*`, `@comment` | — | **Not read by `PHY_ComposanteTypePion`**. `speed-prototype` is ignored by physical movement (see §4.1). (unverified that no other sim code reads `length`) |
| `speeds@max` + `speed@terrain,@value[,@night-value][,@construction-speed]` | `PHY_Speeds.cpp:30-38, 352-369` | **km/h** → m/tick. `night-value` defaults to `value`. `construction-speed` is an **integer percent**. If `<speeds>` is absent the equipment is **motionless** (`IsMotionless`, `:1405-1408`). |
| `lighting-modifiers`, `weather-modifiers/weather-modifier@type,@value` | `PHY_Speeds.cpp:106-136` | speed multipliers keyed by lighting/precipitation **French names** (`Pluie`, `Brouillard`, `JourSansNuage`…) |
| `wind-force-modifiers`, `wave-height-modifiers`, `water-current-modifiers` | `PHY_Speeds.cpp:167-255` | naval multipliers (Navy licence) |
| `weapon-systems/weapon-system@launcher,@munition,@major` | `:206-227` | the pair must exist in WeaponSystems.xml |
| `sensors/sensor@type,@height` | `:271-293` | height in **m** above the ground |
| `radars/radar@type,@height` | `:297-315` | |
| `transports/crew@man-boarding-time,@man-unloading-time` | `:344-348` | **time per man** → rate = 1/ticks. A zero time means "everyone in one tick". |
| `transports/unit@capacity,@ton-loading-time,@ton-unloading-time,@can-transport-destroyed` | `:350-358` | capacity in **tons** (>0) |
| `transports/crowd@capacity,@man-boarding-time,...` | `:360-366` | |
| `objects/object@type, @initial-construction-time, @initial-destruction-time, @construction-time-factor, @destruction-time-factor, @valorization-time, @devalorization-time, @extinguishing-time, @bypass-gain-factor, @bypassed-speed, @non-bypassed-speed, @consumption-mode` | `PHY_ComposanteTypeObjectData.cpp:43-60` | All times are **time strings** (`"1800s"`); the `*-factor` values are also time strings (s per m²). Speeds are km/h. A missing time ⇒ **cannot do that operation** (stays `max`, `:71-100`). |
| `consumptions/consumption@status` + `resource@category,@name,@value` | `:395-419`, `PHY_DotationConsumption.cpp` | `status` ∈ engine-stopped/engine-started/moving/working. `value` is **per hour** (or **per km** if the resource has `consumption-per-kilometers`). |
| `composition` / `category/resource@name,@capacity,@low-threshold,@high-threshold,@normalized-consumption,@transfer-rate` | `PHY_DotationCapacities.cpp:19-60`, `PHY_DotationCapacity.cpp:15-23` | per-equipment dotation capacity (thresholds in **percent**) |
| `breakdowns/breakdown@origin (random\|attrition),@type,@percentage` | `:149-204` | per origin, the percentages **must total 100** or loading throws |
| `logistic-functions` (`maintenance-functions@control,@diagnose`, `repairing@category,@max-reparation-time,@all-mobility,@all-non-mobility`, `towing@capacity,@loading-time,@unloading-time`), `health-functions` (ambulance `@capacity,@nbc-transport,@reac-mental-transport,@emergency-only`, treatment `@sorting,@nbc,@psychiatry...`), `supply-functions@type,@nature` + carrying `@min/max-mass,@min/max-volume,@bulk,@containers`, trailer, handling | `:441-662` | (details in the Logistics section) |
| `aviation-quotas/…@range,@category,@value` | `:402-438` | |
| `active-protections`, `transmitters/transmitter@name,@height`, `oceanic-effects` | `:230-269` | |
| extensions | `PhysicalExtension` | free key/values exposed to decision |

### 1.4 Units.xml (`MIL_AgentTypePion` + `PHY_UnitType`) attribute map

| Element / attribute | Code | Meaning |
|---|---|---|
| `unit@name,@type,@id` | `MIL_AgentTypePion.cpp:153-180`, `MIL_AgentType_ABC.cpp:27` | `@type` selects the C++ class from a **fixed string table** (`:88-131`): `Pion INF/ABC/ALAT/ASA/ASS/GEN/RENS/TRANS/JOINT/Civilian/Emergency/Journalist/Notable` → plain; `Pion NBC` (NBC flag), `Pion ASY` (terrorist), `Pion MILICE`, `Pion REFUGIE`, `Pion CIRCULATION`; `Pion LOG TC2`/`Pion Organization` → TC2; `Pion LOG BLD/BLT Sante\|Maintenance\|Ravitaillement`; `Pion LOG Convoi`; `Pion Remote`. **An unknown type string throws** (`:162`). |
| `@decisional-model` | `:282-289` | Models.xml name, required |
| `nature@nature-app6,@level,@atlas-nature` | `MIL_AgentType_ABC.cpp:49-62` | |
| `@can-fly,@can-dive,@is-autonomous,@autonomy-range,@standard-flying-height,@tactical-flying-height,@maximum-flying-altitude (default 50000),@standard-diving-depth` | `PHY_UnitType.cpp:33-41` | `can-fly` registers `PHY_RoleAction_Flying` (`MIL_AgentTypePion.cpp:325`) |
| `equipments@critical-threshold,@critical-destruction-delay` / `equipment@type,@count,@crew (required),@major,@loadable,@convoyer,@criticality` | `PHY_UnitType.cpp:153-186` | `crew=0` throws unless the unit is autonomous or the equipment is activated-on-request. Activated-on-request equipment cannot be `major`. Each equipment type may appear only once. |
| `crew-ranks/crew-rank@type,@count` | `:188-207` | commanders. The rest of the crew gets the lowest rank (`:403-416`). |
| `postures/posture@name,@setup-time,@tear-down-time` | `:209-231` | **Required time strings** for every listed posture (name = French posture id, see §6). Unlisted postures default to 0 = instantaneous. |
| `setup@installation-time,@uninstallation-time,@footprint-radius,@speed-modifier (0-1)` | `:238-253` | deployment (§6.3) and traffic speed modifier |
| `drill-blow@width,@length` | `:262-270` | "coup de sonde" perception, m |
| `nbc@decontamination-delay` + `protections/protection@type,@auto` | `:128-151` | the delay is clamped to ≥1 s; **the `nbc` element is required** |
| `stocks/stock@logistic-supply-class,@threshold (0-100 %)` | `:115-126` | default threshold ratio **0.1** if absent (`GetStockLogisticThresholdRatio`) |
| `efficiencies@recon,@combat-support,@combat,@mobility-support,@counter-mobility-support,@protection-support,@engineering-support,@urban-area` | `:69-82` | integers read by decision |
| `crossing-height@height` | `:84-98` | `"0m"`/`"1m"`/`">1m"`; any other string ⇒ **Never** (silent) |
| `floating@draft,@refloating-time` | `:100-113` | only if the Navy licence is present. `refloating-time` is kept in **seconds** (not ticks). |
| `logistics` (TC1 dotation capacities) | `PHY_UnitType.cpp:31` | `PHY_DotationCapacities("logistics")` |
| `distance-before-points/distance-before-point@type,@value`, `force-ratio@feedback-time`, `repartition@male,@female,@children` | `MIL_AgentTypePion.cpp:222-280`, `sim/Tools/MIL_HumanRepartition.cpp:26-30` | |

### 1.5 Runtime hierarchy: unit → equipment → sensors/weapons/dotations
1. `Sink::Create` (`sim/Adapters/Legacy/Sink.cpp:107-125`) builds a `MIL_AgentPion`. It registers the roles (§2), applies `noDotation`, then `ReadOverloading(xis)` (ORBAT overrides: equipment states, humans, dotations, human factors, posture… `MIL_AgentPion.cpp:195-220`).
2. `PHY_RolePion_Composantes` calls `PHY_UnitType::InstanciateComposantes` (`PHY_UnitType.cpp:292-306`). For each `equipment@count` it creates one `PHY_ComposantePion` carrying the unit-level flags (major, loadable, convoyer, criticality, crew).
3. Each `PHY_ComposantePion` instantiates one `PHY_Weapon` per weapon system and one `PHY_Sensor` per sensor at its `@height`, plus radars, transmitters and humans.
4. Unit dotation capacity = the sum of the equipment `composition` capacities × count, plus the unit `logistics` (TC1) capacities (`GetDefaultLow/HighThreshold` search order, `PHY_UnitType.cpp:256-286`).
5. `InitializeHumansInComposantes` (`MIL_AgentTypePion.cpp:404-418`) sets each equipment's **default crew to the median crew across all units that use it**.

---

## 2. Role architecture

### 2.1 Mechanism
* `MIL_Entity_ABC : MIL_RoleContainer : tools::RoleContainer` (`sim/Entities/MIL_Entity_ABC.h:40-80`; template in `sw.models/include/tools/RoleContainer.h`).
* Each role class declares `typedef X RoleInterface`. `RegisterRole(role)` attaches it under that interface type. `GetRole<R>()` returns by interface (and throws/asserts if missing); `RetrieveRole<R>()` returns a pointer or null; `CallRole(&R::fn, args…)` is a no-op if the role is missing (used extensively in `MIL_AgentPion::UpdatePhysicalState`, `:422-463`).
* **Algorithm-modifier pattern:** `entity.Execute<Algo_ABC>(computer)` visits **every role that implements `tools::AlgorithmModifier_ABC<Algo_ABC>`**. The root `Default*Computer.cpp` files are these accumulators. This is how speed, posture timing, PH and perception distance are composed from independent roles. For example:

| Computer (root of `sim/`) | Modified by (roles implementing `Execute`) | Combines |
|---|---|---|
| `DefaultSpeedComputer` / `SpeedComputer_ABC` | Moving (modifiers + cap), Composantes (per-equipment speed **min**), HumanFactors, Location (temperature), CBRN (protection), Communications (jammed), Population (crowd collision), UrbanLocation (1-occupation), Reinforcement (min with reinforcing units), LOG convoy | `min(cap, min(max(ratio·speed, 5.571), speed))` (`DefaultSpeedComputer.cpp`) |
| `DefaultPostureComputer` | Moving/Flying/Loading (moving flag), HumanFactors/Location/CBRN (time coefficients), Reinforcement | §6 |
| `PerceptionDistanceComputer` | HumanFactors, CBRN, Location (temperature) | product of factors |
| `DetectionComputer` | Posture (stealth), Perceiver (already perceived), Underground | can-be-seen gate |
| `DefaultConsumptionComputer` | Posture (posture consumption mode), Perceiver (reco ⇒ moving) | **max** consumption mode |
| `DefaultWeaponReloadingComputer` | HumanFactors, Location, CBRN, Communications, Population | product |
| `DefaultDotationComputer`, `DefaultComponentFunctorComputer`, `DefaultHumanLoadingTimeComputer`, `DefaultLoadedStateConsistencyComputer`, `DefaultMaxSlopeComputer` (min over movable equipment), `SlopeDecelerationComputer` (max), `DefaultLocationComputer` (height: flying +, diving −, urban roof +), `DefaultUrbanLocationComputer`, `DefaultMagicMoveComputer`, `DefaultMoveComputer` (can-move vetoes: stranded, cannot fly, surrendered, transported…), `DefaultComposantesAbleToBeFiredComputer`, `DefaultWeaponAvailabilityComputer`, `DefaultHealComputer` | | |

### 2.2 Registration
* **Core roles that come from the "Sink" factory** (`Sink::CreateRoles`, `Sink.cpp:95-103`): `DEC_RolePion_Decision`, `moving::PHY_RoleAction_Moving`, `PHY_RolePion_Location`, `PHY_RolePion_Perceiver`, `PHY_RolePion_Composantes`, `transport::PHY_RoleAction_Loading` (must come after Composantes).
* **All others** come from `MIL_AgentTypePion::RegisterRoles` (`MIL_AgentTypePion.cpp:298-340`). `Flying` is registered only when `can-fly`, otherwise `InterfaceFlying` (a stub). `Diving` only when `can-dive`. `Navy` only with the Navy licence. Every unit gets `PHY_RolePionLOG_Medical/Maintenance(pion,false)` (non-provider).
* Specialisations add or replace roles: LOG Supply/TC2 add `PHY_RolePionLOG_Supply` (`LOG/Supply/MIL_AgentTypePionLOGSupply.cpp:30`, `TC2:38`). Medical/Maintenance/TC2 register the provider versions (`true`). Convoy adds `PHY_RolePionLOGConvoy_Supply/Maintenance/Ambulance` (`Convoy/...:36-41`). `Pion Remote` registers only UrbanLocation, NET dotations, Location and Composantes (`Remote/MIL_AgentTypePion_Remote.cpp:54-63`). `MIL_AutomateTypeREFUGIE` only flags `IsRefugee()`.

### 2.3 Main roles and their state

| Role (interface) | File (`sim/Entities/Agents/…`) | State owned |
|---|---|---|
| Location | `Roles/Location/PHY_RolePion_Location.h:129-141` | position, direction, height, current speed, terrain cache. Provides temperature from local weather (`.cpp:405`) for HF modifiers. |
| Composantes | `Roles/Composantes/PHY_RolePion_Composantes.cpp` (2757 l.) | list of `PHY_ComposantePion`, lent/borrowed maps, operational state (raw + major), neutralization end tick, criticality, immunity |
| Humans | `Roles/Humans/PHY_RolePion_Humans.h` | usable humans count, medical states, CBRN damages, evacuation mode |
| Dotations | `Roles/Dotations/PHY_RolePion_Dotations.cpp` | `PHY_DotationGroupContainer`, consumption mode (current/previous), fire and consumption reservations |
| HumanFactors | `Roles/HumanFactors/PHY_RolePion_HumanFactors.cpp:35-48` | morale (default *bon*), experience (*veteran*), tiredness (*normal*), stress (*calm*) + continuous `tirednessValue_`, `stressValue_` |
| Posture | `Roles/Posture/PHY_RolePion_Posture.cpp` | current/last posture, completion %, timing factor, stealth factor/flag, discrete mode, parked-on-engineer-area |
| Perceiver | `Roles/Perception/PHY_RolePion_Perceiver.cpp` | sensor surfaces per (sensorType, height), radars, active special perceptions (reco point/zone/objects/urban, surveillance, radar zones, flying shell, coup de sonde, ALAT), vision mode, record mode |
| Deployment | `Roles/Deployment/PHY_RolePion_Deployment.cpp` | deployment state/value (0-1) |
| Transported / Transport / Loading / CrowdTransport / TaskGroupTransport | `Roles/Transported`, `Actions/Transport`, `Actions/Loading`, … | transporter id; transported pions + weight; embarked state |
| Communications | `Roles/Communications/PHY_RolePion_Communications.h` | jammers, networks, direct liaisons, blackout flags |
| UrbanLocation | `Roles/Urban/PHY_RolePion_UrbanLocation.h` | current urban block, inside/outside delegate, in-city flag |
| Surrender | `Roles/Surrender/PHY_RolePion_Surrender.h` | prisoner flag, prison/surrendered-to ids |
| Refugee | `Roles/Refugee/PHY_RolePion_Refugee.h` | camp id, managed flag, satisfaction (lodging/security/health), affinities |
| Reinforcement | `Roles/Reinforcement/...h` | reinforced pion, reinforcing list (they constrain speed and posture time) |
| Illumination | `Roles/Illumination/...h` | illuminators, target |
| CBRN | `Roles/CBRN/PHY_RolePion_CBRN.h` | contamination state, worn protection, failure scheduling, decontamination, air filter |
| Population | `Roles/Population/...h` | colliding crowd density |
| ActiveProtection | `Roles/Protection/...` | PH reduction / hard-kill vs. given ammo |
| TerrainAnalysis | `Roles/Terrain/...h` | crossroads/safety caches |
| NET_RolePion_Dotations | `Roles/Network/...` | network diffing |
| Action roles | `Actions/*/PHY_RoleAction_*` | Moving, Flying, Diving, Navy, Objects, DirectFiring, IndirectFiring, MovingUnderground, ComposantesTransport |
| Logistic | `Roles/Logistic/PHY_RolePionLOG_{Medical,Maintenance,Supply}`, `…LOGConvoy_*` | consigns, work rates, stocks (Logistics section) |

### 2.4 Per-tick physical update order (`MIL_AgentPion::UpdatePhysicalState`, `MIL_AgentPion.cpp:422-463`)
Dotations (consumption committed) → Composantes → Humans → Posture → Reinforcement → Location → CBRN → Communications → HumanFactors → Transported → Surrender → Refugee → Population → **Perceiver (must follow Composantes)** → Loading → Transport → TaskGroupTransport → CrowdTransport → Objects → Moving → Flying → DirectFiring → IndirectFiring → Underground → Deployment → Illumination → Maintenance/Medical/Supply → Navy.
Decision (`UpdateDecision`, `:369-420`): if `IsImmobilized()` (op state < decisional threshold) **the current mission is cancelled every tick**.

---

## 3. Composantes (equipment)

### 3.1 States (`Units/Composantes/PHY_ComposanteState.cpp:20-50`), ordered by id
| id | Name (protocol) | usable? | damaged? | notes |
|---|---|---|---|---|
| 0 | `Detruit` (dead) | no | yes | |
| 1 | `ReparableAvecEvacuation` | present | yes | needs maintenance evacuation; a breakdown is attached |
| 2 | `ReparableSurPlace` | present | yes | **auto-repairs** after a protection neutralization time draw (`PHY_ComposantePion.cpp:806-811, 1056-1062`) |
| 3 | `Disponible` (undamaged) | present | no | |
| 4/5 | `EnMaintenance(Evacuation)` | no | no | |
| 6 | `Prisonnier` | present | no | |
| 7 | `Lent` | no | no | |

* Damage only **worsens**: `ApplyFire` applies the new state only if `new < old` (`PHY_ComposantePion.cpp:433-457`). For human protection, "repairable without evacuation" becomes undamaged (`:448-449`).
* Capability predicates: `CanFire` = activated, not neutralized, present, **not damaged**, not underground (`:970-977`). `CanMove` also requires speeds (not motionless) and a usable crew (`:1481-1486`). `CanPerceive` only needs present + crew (`:1468-1479`). **A repairable vehicle still perceives but neither moves nor fires.**

### 3.2 Attrition roll (`Units/Weapons/PHY_AttritionData.cpp`)
* One uniform draw r. `dead` if r ≤ D·(1−u). Then `repairable-with-evac` and `repairable-without-evac` bounds accumulate the same way. u = urban protection (§7.2).
* For human protections, "without evacuation" is folded into "with evacuation" at load time.
* `score = D + R_evac/2 + R_noevac/4` drives weapon and target choice (dangerosity).
* Humans aboard: `PHY_Protection` attrition-effects give the wounded/dead percentage per resulting equipment state (`PHY_Protection.cpp:119-133`). `WoundLoadedHumans` also wounds transported humans.

### 3.3 Operational state (`Roles/Composantes/PHY_RolePion_Composantes.cpp:449-510`)
* Per equipment: 0 if not undamaged; 1 if it has no humans; otherwise `(1−w_crew) + w_crew·humanOpState` (`PHY_ComposantePion.cpp:1098-1116`). `w_crew` = `Decisional.xml operational-state-weights@crew`.
* Unit: mean of major equipment and mean of non-major (deactivated equipment excluded). For a mixed unit: `w_major·major + (1−w_major)·nonMajor`. Zero if no operational humans remain.
* States: `FullyDestroyed` if op = 0. `TacticallyDestroyed` if majorOp = 0 **or** op < `@threshold` (immobilized ⇒ mission cancelled). Otherwise `Operational`.
* **Criticality:** `equipment@criticality` values are summed. When the current criticality ≥ `equipments@critical-threshold`, the unit gets a destruction timer derived from `critical-destruction-delay` (`:2591-2602, 2648-2670`). (Exact trigger semantics: unverified.)

### 3.4 Breakdowns
* **Random breakdowns** (if enabled in the exercise config): once per simulated day, each equipment draws `eva`, then `neva`, from its **protection** (`PHY_ComposantePion.cpp:781-796`). The breakdown is scheduled at a random tick within the next 24 h (`MIL_EntityManager.cpp:1030-1039`) and applies only if the equipment is still undamaged then.
* The breakdown type comes from `breakdowns@origin=random|attrition` percentages (`ReinitializeState`/`ApplyNewComposanteState`, `:344-373, 603-616`).

### 3.5 Lending / borrowing (`Actions/ComposanteLending/*`, `Roles/Composantes/...:2340-2400`)
* The lend action waits `max(ApproximateTravelTime(src→dst))` ticks, then transfers the matching equipment. The lender keeps `lentComposantes_[borrower]` and the borrower keeps `borrowedComposantes_[lender]`.
* Lending back first retrieves equipment previously borrowed from the target. Variants: collection (ambulances), hauler (towing), specific, load/unload composantes.

### 3.6 Neutralization
* Indirect fire calls `Neutralize()`: end tick = now + max over equipment of the protection's Gaussian neutralization time. A neutralized unit cannot fire (`CanFire`). (`Roles/Composantes/...:895-904`)

---

## 4. Actions

### 4.1 Moving (`Actions/Moving/PHY_RoleAction_Moving.cpp`, `SpeedComputerStrategy.cpp`, `sim/Decision/DEC_PathWalker.cpp`)
* **Base speed per equipment** = `PHY_Speeds::GetMaxSpeed(terrain)` = max over the terrain bits present (area / border / linear). In **urban + road**, the result is `(area + max(border, linear))/2` (`PHY_Speeds.cpp:453-468`). An impassable terrain gives 0.
* That value is multiplied by lighting × precipitation × wind × wave × current multipliers, using the **weather at the unit position** and **day/night** (`:543-570`).
* Unit speed = **min over movable equipment** (`DefaultSpeedComputer::ApplyOnComponent`), min with reinforcing units, then × modifiers. Max-speed modifiers (`isMax=true`): HF (experience × tiredness × stress `max-speed`), temperature, CBRN protection `speed`, urban `1−occupation`, decision `SetMaxSpeedModificator` × traffic (`setup@speed-modifier`). Plain modifiers: decision `SetSpeedModificator`, jamming `speed-modifier`. A cap comes from `SetMaxSpeedCap`.
* **Floor: 5.571 m/tick**, which equals 2 km/h only at a 10 s tick (`DefaultSpeedComputer.cpp`, "minimum speed"). The floor does not exceed the raw speed.
* Speed is 0 if transported or underground (`PHY_RoleAction_Moving.cpp:82-91`).
* **Slope** (`DEC_PathWalker.cpp:125-139`): unit `maxSlope` = min over movable equipment, `decel` = max. If `slope > maxSlope` the speed is 0. Otherwise `speed·(1 − slope/maxSlope·decel)`. Only uphill counts in the path-cost version (`SlopeSpeedModifier.cpp`). Bridges ignore slope.
* Inside an object, the speed uses the object policy with the equipment's `bypassed-speed`/`non-bypassed-speed` (`PHY_ComposanteTypeObjectData::GetMaxSpeed`).
* Pathfinding: `Move(DEC_PathResult)` → `PHY_MovingEntity_ABC`/`DEC_PathWalker` (decision side; see the pathfind section). `Pathfind.xml` is loaded at `MIL_AgentServer.cpp:97-121`.
* **The `speed-prototype` attribute does not drive physical movement.** Prototypes are used by `DEC_SpeedPrototypeContext` (decision/path estimation) only.

### 4.2 Direct firing (`Actions/Firing/DirectFiring/*`, `Units/Weapons/*`)
* `FirePion` (`PHY_RoleAction_DirectFiring.cpp:111-148`) collects the firer's weapons. An equipment is eligible if `CanFire` and has ammo (`PHY_DirectFireData.cpp:176-196`). Modes: normal/optimal (limited to `percentage × #equipment`, min 1) or free (all weapons).
* Targets: the target's fireable equipment (optionally major only). For each target equipment the best weapon is chosen (by dangerosity), then unused weapons pick the best target.
* **Per burst:** reserve `burst@munition` (capped by the remaining loader) → one PH roll → if hit, a `MIL_Effect_DirectFirePion` is applied later in `UpdateEffects` → attrition roll (§3.2). Next fire tick += `burst duration × combat-speed-divider`. When the loader is empty, add the reloading duration modified by HF/temperature/CBRN/jamming/crowd (`PHY_Weapon.cpp:DirectFire`).
* **PH** = `phTable[targetVolume](distance / postureMod)` × activeProtection × HF `ph` × CBRN `ph` × temperature `ph` (`PHY_WeaponDataType_DirectFire.cpp:~107-121`). `postureMod` interpolates the launcher posture matrix with both units' posture completion (`PHY_LauncherType.cpp:123-138`). The PH table returns **0 beyond the last point and before the first point**.
* `direct-fire@max-height`: targets higher than firer + maxHeight cannot be engaged (non-zero value only). The protection used is directional unless the equipment has global armour. The line of fire also hits crowds it crosses.
* Crowd fire: hits = the ROE-dependent crowd PH rolled per round.

### 4.3 Indirect firing (`Actions/Firing/IndirectFiring/*`, `Units/Dotations/PHY_DotationCategory_IndirectFire*.cpp`)
* Range: `min-range`/`max-range`. Shell flight uses `average-speed` (converted to m/tick, `PHY_WeaponDataType_IndirectFire.cpp:53`).
* Rounds are converted to "intervention types" (`rounds / intervention-type`). The dispersion ellipse comes from `x/y-dispersion`. The neutralization ellipse = attrition ellipse × `neutralization-ratio`.
* Every unit in the neutralization ellipse that is **on the ground** (height > 0 is immune) is neutralized. Then, per equipment, `HasHit` = PH(target posture) × urban ratio, followed by the attrition roll.
* Active protection may destroy or counter the round. Friendly fire produces a report. The classes Fumigene/Eclairant/Effect create weather or object effects instead.

### 4.4 Flying / Diving (`Actions/Flying/*`)
* Height is set directly via an effect each tick, with no climb rate. It is capped at `maximum-flying-altitude − ground altitude` (`PHY_RoleAction_Flying.cpp CapHeight`).
* While flying the unit is forced into the **moving** consumption mode and the moving posture. If it cannot pay that consumption, it lands.
* Diving subtracts depth from height.

### 4.5 Loading (embark/disembark) (`Actions/Loading/PHY_RoleAction_Loading.cpp:90-185`)
* time (ticks) = humans in loadable equipment / Σ carrier rate. A carrier rate is `1/man-boarding-time` per tick for each equipment that `CanTransportHumans`.
* If no carriers have a rate the time is ∞ (impossible). If there are no loadable humans the call returns −1.

### 4.6 Transport of units (`Actions/Transport/PHY_RoleAction_Transport.cpp:120-230`)
* Capacity = Σ `unit@capacity` (tons). Per-tick load = Σ `1/ton-loading-time`. This is weight-based against the transported unit's equipment weight.

### 4.7 Objects (`Actions/Objects/*`)
* Per equipment: time = `initial-*-time + sizeCoef·*-time-factor` (sizeCoef = object area in m² for density-type objects, otherwise 0). It is divided by the `construction-speed` % on that terrain.
* The unit (plus its reinforcements) works at **Σ 1/time_i** in parallel. The result is × `engineer-speed-factor` if the object is affected (`PHY_RoleAction_Objects_DataComputer.cpp ComputeWorkTime`).
* Bypass = destruction time × `bypass-gain-factor` (+ demining time if mined).
* Consumption mode while working = the max of the per-equipment `consumption-mode` for that object type, default **working** (`PHY_ComposanteTypePion.cpp:975-982`).

### 4.8 Navy (`Actions/Moving/PHY_RoleAction_Navy.cpp`)
* Stranded when tide depth ≤ `draft`; refloat after `refloating-time`, or by pulling units whose total weight ≥ own weight.
* Sinking (if enabled) applies to non-floating, non-diving, non-flying units in water, according to `crossing-height`; all equipment is destroyed after 2 ticks.

### 4.9 Other
* Underground (speed/transfer time via network objects).
* CrowdTransport (crowd capacity).
* TaskGroupTransport.
* Logistics actions (consume/produce stock, supply object).
* Illumination.

Details (not yet explored).

---

## 5. Perception

### 5.1 Pipeline (`Roles/Perception/PHY_RolePion_Perceiver.cpp`)
1. `InternalUpdate` (`:789-798`): peripheral vision is enabled **one tick every 12** (`:97`, hard-coded). `PreparePerceptionData` (`:592-637`) builds one `PHY_PerceptionSurfaceAgent` per (sensor type, height) from every equipment able to perceive. Sensors `activation-on-request` are used only when perception-on-request is enabled. The direction depends on the vision mode: normal (movement direction), direction, or point.
2. `ExecutePerceptions` (`:676-732`): candidate agents/objects/crowds within the max sensor range. That range is reduced by urban occupation when blocks lie within 100 m: `range·(1−0.9·occ) + maxBlockLength`. Then each active perception runs (View, RecoPoint, RecoLocalisation, RecoUrbanBlock, RecoObjects, Surveillance, Radar, FlyingShell, optional CoupDeSonde/ALAT).
3. `PHY_PerceptionView::Execute` (`Perceptions/PHY_PerceptionView.cpp:140-180`): skip own knowledge group and dead units (unless `detect-destroyed-units`). The `DetectionComputer` gate covers stealth/underground. The level is the best over surfaces. In a city a ZURB urban computation applies, taking the min.
4. `NotifyPerception` → `owner.GetKnowledge().GetKsPerception()` (agent knowledge blackboard) → the knowledge group creates or updates `DEC_Knowledge_Agent`. Levels: NotSeen < Detected < Recognized < Identified (`Perceptions/PHY_PerceptionLevel.cpp`). The unit always perceives itself and its transporter as identified. (Knowledge lifetimes and merging are covered in the knowledge section.)

### 5.2 Sensor geometry and energy model (`Units/Sensors/PHY_SensorTypeAgent.cpp`, `PHY_PerceptionSurfaceAgent.cpp`)
* A target is considered only if it is already known, **or** in a scan tick with `@scanning=true`, **or** inside a sector of `@angle` (**degrees**) around a perception direction, or within `base-distances@close-range` (m) (`PHY_PerceptionSurfaceAgent.cpp:50-73`).
* **Scan ticks are per unit and staggered**: each unit's peripheral vision fires every 12 ticks with a per-unit offset (`PHY_RolePion_Perceiver.cpp:97,103,766-777`). So an *unknown* target outside the `@angle` sector (and outside close-range) of a `scanning="true"` sensor can wait up to ~12 ticks for first detection; with `scanning="false"` it must enter the sector.
* Base distances: `base-distance@level=identification|recognition|detection,@distance` (m — `MIL_Tools::ConvertMeterToSim` is the identity, `Tools/MIL_Tools.cpp:98-101`). Required: ident ≤ reco ≤ detection (`:114-152`).
* Hard cutoff: if distance > detection distance × (target × source × cell modifiers), the target is not seen at all, before any ray march (`:339`). Source-side `sensor-distance` contributors: experience/tiredness/stress (`Roles/HumanFactors/PHY_RolePion_HumanFactors.cpp:341-346`), CBRN protection (`Roles/CBRN/PHY_RolePion_CBRN.cpp:190-194`), temperature (`Roles/Location/PHY_RolePion_Location.cpp:370-373`).
* Modifier product: **target** = max volume factor over present equipment (`size-modifiers`; acoustic sensors use `sound-profiles-modifiers` instead) × target-posture factor (interpolated by completion; ignored if surrendered) × crowd density factor. **source** = source-posture factor × HF/CBRN/temperature `sensor-distance` × crowd factor. **cells** = lighting factor at the target cell and the source cell (`visibility-modifiers`, `source-visibility-modifiers`) × target terrain factor (`target-terrain-modifiers`). (`:190-255, 337-424`)
* **Ray march:** energy starts at the detection distance. For every vision cell crossed, `energy −= cellLength / (mod × precipitation(cell) × terrainFactor(cell))`. Terrain classes: `Sol`, `Vide`, `Foret`, `Urbain` (`Meteo/RawVisionData/PHY_RawVisionData.cpp:23-28`). Urban blocks crossed subtract via `urbanBlock-material-modifiers` (`DistanceModifiersHelpers.cpp`).
* Result: remaining energy ≥ det−ident ⇒ Identified; ≥ det−reco ⇒ Recognized; ≥ 0 ⇒ Detected (`:316-325`). The source height is `sensor@height` + urban roof (if inside a block and not flying). The target point is ground + 2 m.
* `@detection-delay`: a target must stay perceived `delay` ticks before it is reported (`PHY_PerceptionSurfaceAgent.cpp:87-101`).
* `limited-to-sensors`: the target is seen only if it emits with a listed sensor or radar.
* All distance-modifier `value`s must be in [0,1] or loading throws (`DistanceModifiersHelpers.cpp:31`).

### 5.3 Radars (`Units/Radars/PHY_RadarType.cpp`)
* Classes: radar, tapping (listening, needs the target to emit), tappingRadar, sonar, passiveSonar, sonarListening (`PHY_RadarClass.cpp:20-36`).
* Acquisition requires: the target's **consumption mode** is in `detectable-activities`; the target altitude is in [min-height, max-height]; sonar only works in water (`:352-382`). The level is reached after the acquisition `base-time` (or `command-post-time` for HQ). With no acquisition times, detection is never reached, recognition is immediate, and identification is never reached (`:195-210`).
* The cross-section used is `@radar-cross-section` (radar) or `@acoustic-cross-section` (sonar).

---

## 6. Postures, human factors, consumption

### 6.1 Postures (`Units/Postures/PHY_Posture.cpp:27-80`)
| XML name | protocol | consumption mode | auto next / previous |
|---|---|---|---|
| `Mouvement` | moving | moving | instantaneous |
| `MouvementDiscret` | infiltrating | moving | instantaneous |
| `Arret` | stopping | engine-started | → `PosteReflexe` |
| `PosteReflexe` | short_stopping | engine-started | → `Poste` / ← `Arret` |
| `Poste` | parked | engine-stopped | → `PosteAmenage` / ← `PosteReflexe` |
| `PosteAmenage` | parked_on_self_prepared_area | engine-stopped | ← `Poste` |
| `PostePrepareGenie` | parked_on_engineer_prepared_area | engine-stopped | forced when on an engineer-prepared area |

* **Computation** (`sim/DefaultPostureComputer.cpp`): while stopped, the unit climbs the chain using `setup-time`. On moving it tears down with `tear-down-time` back to Mouvement. Completion += 1/(time × modifier) per tick. The modifier = HF `posture-setup-time` × temperature × CBRN `posture` × (1 / decision timing factor). The time = **max** with reinforcing units' times.
* Stealth: each tick `rand > stealthFactor` ⇒ stealthy. A dead unit is forced into Arret.
* Posture effects: PH matrix (launcher), sensor source/target posture factors, consumption mode, urban deployment (`Poste` completion / `PosteAmenage` = 1 → protection in urban areas).

### 6.2 Human factors
* Effects of the categorical states multiply: speed (`max-speed`, a **max** modifier), reload duration (`loading-time`, multiplied), PH, posture time, sensor distance (`PHY_RolePion_HumanFactors.cpp:319-353`). Temperature uses the same five fields, interpolated by °C (weather `temperature@value`, default 20).
* Automatic evolution requires the exercise setting `human-evolution` (`:44-48`). Tiredness value += per-hour rate × tick/3600 according to the **consumption mode** (`:157-170`). Stress += `inc-per-shot` when attacked and += `dec-per-hour` each tick (**must be negative to decrease**). Threshold hysteresis is in `UpdateTiredness`/`UpdateStress`.
* **Morale has no physical effect.** It is only exposed to decision and makes `GetRapForLocalValue` = 5 when *fanatique* (`sim/Knowledge/DEC_KnowledgeBlackBoard_AgentPion.cpp:258-266`). Morale levels are hard-coded, not in the XML.

### 6.3 Deployment (`Roles/Deployment/PHY_RolePion_Deployment.cpp`)
* Value moves by `1/installation-time` per tick up to 1 (and back with uninstallation). Reports are emitted at deployed/undeployed.

### 6.4 Consumption (`Roles/Dotations/PHY_RolePion_Dotations.cpp:255-435`, `Units/Dotations/PHY_DotationConsumption*.cpp`)
* Mode = **max** of the requests (posture mode, reco ⇒ moving, object work, flying ⇒ moving). Order: engine-stopped < engine-started < moving < working (`ent/Enums.h:852`).
* Each equipment's `consumptions[mode]` is reserved per tick. Embarked loadable equipment does not consume in moving mode. Transported units consume no fuel.
* Kilometric fuel: `value/1000 × current speed (m/tick)` **only for fuel (`carburant`) in moving mode**. Daily rations: `daily-crew-member-consumption × tick/86400 × living humans` **only for `ration`**. Ocean modifiers apply.
* If the reservation fails, the mode is rolled back and the action reports the missing resource (movement stops on a missing resource: `FindMissingResource`).

### 6.5 Logistics specialisations
* `MIL_AgentTypePionLOG*` only change role registration (§2.2). The behaviour lives in `Roles/Logistic/*` (Supply 1250 l., Medical 1128 l., Maintenance 976 l., convoys, consigns). (Detailed flow not yet explored in this section; see the Logistics section.)

---

## 7. Meteo & Urban

### 7.1 Weather (`sim/Meteo/*`, `src/libraries/core/Meteo.cpp`)
* Source: `weather.xml` of the exercise (not the physical DB). `exercise-date@time-zone`, `tide@period,@initial-phase`, `global-weathers/global@start-time,@sunrise,@sunset` + `cloud-cover@floor,@ceiling,@density(%)`, `wind@speed,@direction`, `temperature@value` (default 20), `precipitation@value`, `lighting@day-lighting,@night-lighting`, stability, solar-index, wave-height. `local-weathers/local@start-time,@end-time,top-left,bottom-right (MGRS)` overrides a rectangle while active.
* The active global weather is the latest whose `start-time` ≤ now. Night is decided from the global sunrise/sunset (`PHY_MeteoDataManager.cpp:~420-427`). Effects: smoke/illumination ellipses patch vision cells.
* **Precipitation names (keys in every modifier list):** `PasDePrecipitation, TempeteDeSable, Brouillard, Crachin, Pluie, Neige, Fumigene`. **Lighting:** `JourSansNuage … JourTresNuageux, NuitPleineLune … NuitNouvelleLune, Eclairant` (`core/PHY_Precipitation.cpp:16-22`, `core/PHY_Lighting.cpp:16-26`).
* Uses: equipment speed (lighting/precipitation at the position, day/night speed tables), sensor ray march (per-cell precipitation, target/source lighting), temperature HF.

### 7.2 Urban (`sim/Urban/*`, `Roles/Urban/*`)
* Urban protection for direct and indirect fire = `dotation urban-modifier[material] × block occupation × structural state` (`Roles/Urban/InsideUrbanBlockPosition.cpp:84-90`). Attrition is × (1 − protection).
* Indirect-fire hit ratio = (ellipse ∩ block)/block area × urban deployment (posture).
* Speed inside a block × (1 − occupation). Mounting is allowed only if trafficability ≥ max equipment weight (`PHY_RolePion_UrbanLocation.cpp:177-190`).
* Perception adds the roof height to sensors and attenuates by block materials. Direct view through blocks can fail randomly with probability `1 − view-factor%` (`PHY_PerceptionView.cpp:125-138`).
* Urban types from Urban.xml: materials (with attritions per protection, used when blocks collapse), accommodations, infrastructures, roof shapes. `MIL_UrbanObject`/`UrbanPhysicalCapacity` details: (not yet explored).

---

## 8. Gotchas for a modeler

1. **Time strings must end in `s`, `m` or `h`** (`sw.models/include/tools/TimeHelpers.h:20-63`). `"1d"` or a bare `"30"` fails decoding. `tools::ReadTimeAttribute` then **silently sets 0** for optional times (missing = 0 = instantaneous for posture/loading). Required ones (posture setup/tear-down, burst/reload, neutralization) throw instead.
2. **Even "factors" in Equipments.xml objects are time strings** (`construction-time-factor="0.10s"`, seconds per m²).
3. **Percent vs ratio is inconsistent across files:**
   * **0-1 ratio:** `hit-probability@percentage` (despite its name), attrition `destruction/repairable-*`, `ph-modifier@value`, sensor `distance-modifier@value`, `slope-deceleration`, `setup@speed-modifier`, `urban-modifier@value`, `operational-state-weights`.
   * **0-100 percent:** `random-breakdown-probability@eva/neva`, protection `injured-percentage/dead-percentage`, breakdown `@percentage` (must total 100 per origin), `stock@threshold`, dotation `low/high-threshold`, `cloud-cover@density`, Decisional `dangerosity-modifiers`, `urban-perception@view-factor`, `speed@construction-speed`, NBC efficiency/wound integers.
4. **Speeds are km/h in XML and m/tick internally.** The conversion factor is cached in a `static` at first use (`MIL_Tools.cpp:51`), so the tick length must not change mid-run. `indirect-fire@average-speed` is **m/s** (its code comment saying "km/h" is wrong).
5. **The 5.571 m/tick floor** in `DefaultSpeedComputer` equals 2 km/h only with **10 s ticks**. With other tick lengths the floor is a different km/h value.
6. **`speed-prototype` on equipment does not drive physical speed.** The inline `<speeds>` block does. Without `<speeds>`, the equipment is motionless and is excluded from slope and speed minima.
7. **Unit speed = the slowest movable equipment.** A slow or impassable support vehicle limits the whole unit (unless it is broken or deactivated). `max-slope` is a ratio, not degrees; the unit takes the min over equipment.
8. **Sensor `@angle` is in degrees.** `close-range` is a radius in metres (squared internally). Peripheral scan happens 1 tick in 12, hard-coded.
9. **Sensor terrain modifiers:** if you define `terrain-modifiers`, define **all four** classes (`Sol`, `Vide`, `Foret`, `Urbain`). Missing entries are looked up with `find()->second` on a missing key (`PHY_SensorTypeAgent.cpp:284-300`), which is likely a crash or undefined behaviour. **(unverified)**
10. **Protection `@type`:** only `humain` and `materiel` are recognised. **Any typo means "crowd"** (`PHY_Protection.cpp:69-71`).
11. **`crossing-height`:** only `"0m"`, `"1m"` and `">1m"` are recognised. Anything else silently means *never*.
12. **Kilometric consumption** (`consumption-per-kilometers`) only scales with distance for the **fuel** dotation type in moving mode. For other resources, or other modes, the value is treated as `value/1000` per tick. Daily per-crew consumption is multiplied by living humans only for **ration**.
13. **`dec-per-hour` stress is added**, so give it a negative value. Tiredness rates are per hour, signed (`engine-stopped="-10"` recovers).
14. **Morale is decorative physically.** Only experience, tiredness, stress and temperature change speed, PH, reload, posture time and sensors. Human-factor evolution only runs with the exercise setting `human-evolution=true`.
15. **Attrition only degrades state.** "Repairable without evacuation" auto-repairs after a protection neutralization-time draw. A damaged but present vehicle still perceives.
16. **PH table edges:** the PH is 0 below the first and beyond the last `hit-probability` point (`MT_InterpolatedFunction(0,0)`). Start tables at distance 0. A missing volume row gives −1, which means no hit.
17. **One PH roll per burst, not per round.** `burst@munition` rounds are consumed per burst. Fire rate is also divided by the exercise `combat-speed-divider`.
18. **Indirect fire ignores units with height > 0** (flying). `neutralization-ratio` must be ≥ 1.
19. **Missing optional elements give defaults:** unit stock threshold 10 %, NBC decontamination delay ≥ 1 s, `maximum-flying-altitude` 50000, `max-slope` 1.0, `width` 1, `apex` = max-range/2, `crowd-ph` 0.3, temperature 20 °C, `view-factor` 100, counter-measure duration 60 s.
20. **Unique names and IDs:** equipment id uniqueness is checked, but `ids_` is a **local** set in `ReadElement` (`PHY_ComposanteTypePion.cpp:82-91`), so **duplicate equipment ids across elements are not actually detected**. Dotation and unit IDs are checked. Everything references by **name**.
21. **Unit `@type` strings are a closed list** (`MIL_AgentTypePion.cpp:88-131`). A new logistic or specialist type needs C++ changes.
22. **Default crew per equipment** is the **median** of the crews across all units using it. This matters for magic-created equipment.
23. **`refloating-time` and `critical-destruction-delay`** are decoded to seconds via `DecodeTime`, not converted to ticks like most other times. Check their usage before assuming units **(unverified)**.
24. The `Decisional.xml` `perception@max-level` caps the perception level obtained on hostile units, and `availability` sets which knowledge info is available at which level (`DEC_Workspace.cpp:151-170`).

(Not yet explored in depth: CBRN contamination dynamics, the full logistics consign flows, crowd/task-group transport, urban object physics/collapse, the knowledge-group merge rules.)
