# Knowledge, Objects, Entity Hierarchy, Crowds, Task Groups, Logistics, Pathfinding

> Section of the sw.sword navigator knowledge base. Audience: behavior designers writing DIA5 Lua skills in `sw.models` and editing physical XMLs.
>
> **Path conventions.** `sim/` = `src/libraries/simulation/` (relative to the `C:\MASARepo\sw.sword` root). `phys/` = `C:\MASARepo\sw.models\data\data\models\ada\physical\mildb\` (reference physical database; `worldwide-tests/` is a second DB with the same layout). Line numbers come from the working tree read on 2026-11-04.
>
> **Lua-facing names.** Functions registered with a leading `_` (e.g. `_DEC_ConnaissanceObjet_EstUnEnnemi`) take the caller as their first argument. Wrappers in `src/data/app-data/resources/integration/DECFunctionWrappers.lua` inject `myself` and re-expose them without the `_` (see `DECFunctionWrappers.lua:1514`). All `DEC_*` modules are registered in `sim/Decision/DEC_Decision.cpp:276-303` (`RegisterCommonUserFunctions`).
>
> **Confidence.** Claims marked "(unverified)" are inferred, not read in code. Areas marked "(not yet explored)" were outside the time budget.

---

## 0. TL;DR for modelers

| Question | Short answer |
|---|---|
| Who owns agent, object and crowd knowledge? | The **knowledge group** (KG), not the agent. Every pion and automat resolves knowledge through its KG's blackboard. The army blackboard holds only **urban** knowledge. |
| How long does an unseen unit stay known? | Relevance drops each tick by `1/max-lifetime` **plus** `distance(known pos, real pos)/max-unit-to-knowledge-distance`. The knowledge is deleted when relevance reaches 0. Both values come from `KnowledgeGroups.xml`. |
| How long does an unseen object stay known? | **Indefinitely.** Object knowledge does not decay with time. It drops to 0 only when the real object is gone **and** the KG's army perceives the location again (or for crowd or "hacked" objects). |
| Is my knowledge handle still good? | Call `DEC_ConnaissanceAgent_EstValide`, `DEC_IsValidKnowledgeObject` or `DEC_CrowdKnowledge_IsValid`. Once a handle is invalid it stays invalid; if the target is perceived again, a **new** knowledge with a **new ID** is created. |
| Do knowledge functions leak ground truth? | Some do. `DEC_ConnaissanceObjet_NiveauConstruction`, capacity-based queries and `DEC_ConnaissanceAgent_EnAgent` read the **real** entity. |
| Where are object behaviors defined? | In `Objects.xml`. Each child tag of `<object>` is a *capacity*, registered in `sim/Entities/Objects/CapacityFactory.cpp:106-155`. **Unknown tags are silently ignored.** |
| "Embrayé / débrayé"? | No separate mode exists in this kernel. "Embrayé" means *the automat currently has a mission* (`DEC_Decision<T>::IsBusy` returns `pMission_ != 0`). |

---

## 1. Entity hierarchy

### 1.1 Diagram

```
                      MIL_EntityManager  (sim/Entities/MIL_EntityManager.cpp)
                              |
          +-------------------+-------------------------------------------+
          |                                                               |
     MIL_Army (side)  -- diplomacy map --> other MIL_Army                MIL_ObjectManager (all objects,
     sim/Entities/MIL_Army.cpp                                            incl. universal list)
          |
  +-------+--------------------+-------------------+----------------+------------------+
  |                            |                   |                |                  |
<communication>           <tactical>           <objects>      <populations>      <inhabitants>
  |                            |                (MIL_Object)   MIL_Population     MIL_Inhabitant
MIL_KnowledgeGroup tree   MIL_Formation (recursive)                (crowd)          (static pop.)
  |  (parent/child KGs)        |                                    |
  |                            +-- MIL_Formation ...                +-- concentrations / flows
  |                            +-- MIL_Automate ------+
  |                            |     +-- MIL_AgentPion (one is the PC / command post)
  |                            |     +-- MIL_Automate (sub-automats, recursive)
  |                            +-- MIL_TaskGroup ---- MIL_Echelon[] (aggregated equipment)
  |                                  +-- MIL_TaskGroup (sub-groups)
  |
  +-- automats, task groups, crowds are *registered* in a KG (by ORBAT attribute
      knowledge-group="id"); crowds of a side share one hidden "Crowd" KG.
```

- `MIL_Army` reads ORBAT sections in this order: `communication/knowledge-group`, `tactical/formation`, `objects/object`, `logistics`, `populations/population`, `inhabitants/inhabitant` (`sim/Entities/MIL_Army.cpp:74-87`).
- `MIL_Formation` contains `formation`, `automat` and `task-group` children (`sim/Entities/MIL_Formation.cpp:101-125`).
- An automat must reference a KG: `knowledge-group` is mandatory (`sim/Entities/Automates/MIL_Automate.cpp:160-162`). Sub-automats are created from the automat's own XML (`MIL_Automate.cpp:~150`).
- The **tactical tree** (formation → automat → pion) and the **KG tree** are independent. Two automats in the same formation can sit in different KGs, and then they do not share knowledge.

### 1.2 Diplomacy

| Item | Detail |
|---|---|
| Storage | Each army has a map `diplomacies_[otherArmy]` (`MIL_Army.cpp:257-269`). It is **per direction**, so A→B and B→A are separate entries (not enforced symmetric). |
| Self | `GetDiplomacy(self)` returns `Ami` (`MIL_Army.cpp:689-697`). |
| Unknown relation | Returns `eDiplomacy_Inconnu`, which makes `IsAnEnemy`, `IsAFriend` and `IsNeutral` return **`eDecTristate_DontKnow`**, not false (`MIL_Army.cpp:441-476`). In Lua, test for `== 1` / `== 0` explicitly. |
| Knowledge-based checks | Friend/enemy tests on an agent knowledge use the knowledge's *recognized* army (`IsAFriend(const DEC_Knowledge_Agent&)`). If the target is not recognized yet, the army is null and the result is `DontKnow` (`MIL_Army.cpp:467-473`). |

### 1.3 Automat types and "embrayé"

- In `Automats.xml`, `<automat type="...">` selects the C++ class (`sim/Entities/Automates/MIL_AutomateType.cpp:44-70`):
  - Most types map to `MIL_AutomateType`.
  - `Automate LOG TC2` and `Automate Organization` map to `MIL_AutomateTypeBaseLOG` (a **logistic base**).
  - `Automate LOG BLD/BLT Sante|Maintenance|Ravitaillement` map to `MIL_AutomateTypeLOG`.
  - `Automate REFUGIE` maps to `MIL_AutomateTypeREFUGIE`.
- Each automat type needs exactly one `<unit command-post="true">` and a `decisional-model` (`MIL_AutomateType.cpp:141,179-198`).
- **Embrayé:**
  - `DEC_Automate_EstEmbraye` is `DEC_Decision_ABC::IsBusy`, i.e. `pMission_ != 0` (`sim/Decision/DEC_Decision.cpp:126`, `DEC_Decision.h:1217`).
  - `DEC_Agent_AutomateEstEmbraye` / `_DEC_Agent_AutomateEstEmbraye` returns whether the pion's automat has a mission (`DEC_Decision.cpp:114`, `sim/Decision/DEC_AgentFunctions.cpp:566-569`).
  - `_DEC_AutomateSuperieur_EstEmbraye` checks the parent automat (`DEC_AutomateFunctions.cpp:93-97`).
  - No kernel-side "engage/disengage" toggle was found; grep finds only HLA enum strings. The client protocol still declares `SetAutomatMode` but the simulation does not handle it.
  - **Gotcha:** "embrayé" is just *has a mission*. A pion of an automat without a mission is considered free.
- `MIL_AutomateOrderManager::StopAllMissions` cancels sub-automats' and pions' missions only if the automat is busy, and **skips convoy pions** so they are not destroyed (`sim/Entities/Orders/MIL_AutomateOrderManager.cpp:88-100`).

### 1.4 Tick order (who sees what, when)

`MIL_EntityManager::Update` (`sim/Entities/MIL_EntityManager.cpp:1097-1115`) runs these steps in order:

```
pObjectManager_->Clean()                -> objects marked for destruction are deleted
UpdateDynamicCommunications()           -> KG leaders, comms status, dynamic KGs (if enabled)
UpdateTransmissions()
UpdateKnowledges()                      -> PERCEPTION + knowledge synthesis + clean (see Sec. 2.4)
UpdateTaskGroupCollisions(); UpdateCrowdCollisions()
UpdateDecisions()                       -> automats, then pions, then crowds, then task groups (Lua brains)
UpdateActions()                         -> formations, task groups, automats, pions, crowds (DEC_Start* actions)
UpdateEffects()                         -> MIL_EffectManager (fire, explosions...), object events
UpdateStates()                          -> entity states; objects UpdateStates (build %, flood...); inhabitants
UpdateKnowledgeGroups(); HandleDeletedEntities()
```

Consequences:

- Brains see knowledge built from this tick's perception pass, which uses positions from the **end of the previous tick**: movement is committed in
  `UpdateEffects` by `MIL_Effect_Move` → `DEC_PathWalker::Apply` (`Entities/Effects/MIL_Effect_Move.cpp:30`, `Decision/DEC_PathWalker.cpp:765-768`).
  So a unit that enters sensor range during tick N is known to brains at N+1 at the earliest (later outside the sensor sector: see the
  agents/physics section §5.2). Exception: magic moves/teleports are applied between ticks, so they are perceived in the same tick.
- Actions started by a brain this tick execute in the same tick's `UpdateActions`.
- Effects (damage) are applied after all actions.

---

## 2. Knowledge

### 2.1 Key classes

| Class | File | Role |
|---|---|---|
| `MIL_KnowledgeGroupType` | `sim/Knowledge/MIL_KnowledgeGroupType.cpp` | Static parameters from `KnowledgeGroups.xml`. |
| `MIL_KnowledgeGroup` | `sim/Knowledge/MIL_KnowledgeGroup.cpp` (2173 lines) | KG instance: tree, members, comms delay, jamming, dynamic groups, perception merging. |
| `DEC_KnowledgeBlackBoard_KnowledgeGroup` | `sim/Knowledge/DEC_KnowledgeBlackBoard_KnowledgeGroup.cpp` | Owns the agent, object, population and task-group knowledge containers, plus the KS synthetizer, object synthetizer and sharing. |
| `DEC_KnowledgeBlackBoard_AgentPion` | `...AgentPion.cpp:49-66` | Per-pion **perceptions** (agent, object, crowd, urban), **collisions** (object, crowd), local RapFor, and the KSs: Perception, ObjectInteraction, PopulationInteraction, DirectFire, IndirectFire. |
| `DEC_KnowledgeBlackBoard_Automate` | `...Automate.cpp` | Thin proxy that resolves through the automat's KG. |
| `DEC_KnowledgeBlackBoard_Army` | `...Army.cpp` | Holds only `DEC_BlackBoard_CanContainKnowledgeUrban` (urban block knowledge is **per army**). All agent and object resolvers return null. |
| `DEC_KnowledgeBlackBoard_TaskGroup` | `...TaskGroup.cpp` | Returns null for agent and object resolution; task groups use their KG. |
| `DEC_Knowledge_Agent` | `sim/Knowledge/DEC_Knowledge_Agent.cpp` | KG-level unit knowledge: detection, recognition and identification data, relevance, lock, perception sources. |
| `DEC_Knowledge_AgentPerception` | `...AgentPerception.cpp` | Pion-level, per-tick perception of a unit. |
| `DEC_Knowledge_Object` | `...Object.cpp` | KG-level object knowledge. Attribute *proxies* copy the real object's attributes. |
| `DEC_Knowledge_ObjectPerception` / `ObjectCollision` | | Pion-level. |
| `DEC_Knowledge_Population` (+ `Concentration`, `Flow`, `FlowPart`) | | KG-level crowd knowledge, tracked per concentration and per flow. |
| `DEC_Knowledge_Urban` | | Army-level urban block knowledge (recon progress). |
| `DEC_Knowledge_TaskGroup` | | KG-level task-group knowledge. |
| `DEC_Knowledge_RapForLocal` | | Pion local force ratio (`_DEC_RapportDeForceLocal`). |
| `DEC_KS_*` | `sim/Knowledge/DEC_KS_*.cpp` | Knowledge sources. Each one runs `Prepare()`, then `Talk()`, then `Clean()`. |

### 2.2 KnowledgeGroups.xml (`phys/KnowledgeGroups.xml`)

```xml
<knowledge-group name="Standard" communication-delay="600s" [delay-variability="..."]>
  <unit-knowledge max-lifetime="3h" max-unit-to-knowledge-distance="60000" [interpolation-time="120s"]/>
  <population-knowledge max-lifetime="30m"/>
  <task-group-knowledge max-lifetime="3600s"/>
</knowledge-group>
```

All values are parsed in `MIL_KnowledgeGroupType.cpp:78-90`. Times are converted to ticks.

| Attribute | Field | Default / rules |
|---|---|---|
| `unit-knowledge/@max-lifetime` | `rKnowledgeAgentMaxLifeTime_` | Required, must be > 0. |
| `unit-knowledge/@max-unit-to-knowledge-distance` (m) | `...MaxDistBtwKnowledgeAndRealUnit_` | Optional; default `UINT_MAX` (effectively no distance decay). 0 throws (`:68-76`). |
| `unit-knowledge/@interpolation-time` | `rKnowledgeAgentExtrapolationTime_` | Optional, clamped to at least 1 tick. Position keeps following the real unit for this long after contact is lost. |
| `population-knowledge/@max-lifetime` | | Required. |
| `task-group-knowledge/@max-lifetime` | | Optional; defaults to the unit max-lifetime (`:88-89`). |
| `@communication-delay` | `rCommunicationDelay_` | Optional (0 if absent). This is the **parent's** delay, applied when a child KG pulls from its parent (`RefreshTimeToDiffuseToKnowledgeGroup`, `MIL_KnowledgeGroup.cpp:871-879`). |
| `@delay-variability` | | Optional; uniform random `±variability` added to the delay. |

Lookup is by name (`FindType`). `FindTypeOrAny("Standard")` is used for the automatic crowd KG and falls back to *any* type if "Standard" is missing (`MIL_Army.cpp:~487`).

### 2.3 Knowledge group kinds (`E_KnowledgeGroupDistinction`, `MIL_KnowledgeGroup.h:81-86`)

| Kind | Created by | Notes |
|---|---|---|
| `None` | ORBAT `<knowledge-group id name type [leader]>` | The normal case. Can be nested (parent → children). |
| `Crowd` | `MIL_Army` finalize: one per army, holds all the army's crowds (`MIL_Army.cpp:485-499`) | Never sent to clients at creation. It gets ephemeral knowledge of every object of the army. |
| `Dynamic` | Dynamic communications option, when a child KG loses comms with its parent (`UpdateCommunicationsState`, `MIL_KnowledgeGroup.cpp:436-486`) | A clone of the static parent. It is merged back when comms return. |
| *Jammed copy* (`jammedPion_ != 0`) | `PHY_RolePion_Communications::Jam` / partial blackout (`sim/Entities/Agents/Roles/Communications/PHY_RolePion_Communications.cpp:124-164`) | A per-pion KG cloned from the automat's KG. While it exists, `MIL_AgentPion::GetKnowledgeGroup()` **returns the jammed copy** (`sim/Entities/Agents/MIL_AgentPion.cpp:601-610`). |

Exercise settings that switch these behaviors on (read in `sim/MIL_AgentServer.cpp:792-817`; the settings file is assumed to be the exercise `settings.xml`, unverified):

| Setting key | Effect |
|---|---|
| `dynamic-communication` | Enables leader hierarchy and comms status. |
| `dynamic-communication-display-only` | When true, comms status is only displayed; no dynamic KGs are created. |
| `jam-and-blackout-dynamic-knowledge-groups` | **Mandatory.** The simulation throws if it is absent (`MIL_AgentServer.cpp:811-817`). When true, jammed or silent pions stop feeding their KG. |

### 2.4 Perception → knowledge → brain flow

```
 TICK t  (MIL_EntityManager::UpdateKnowledges, MIL_EntityManager.cpp:905)
 for each army -> MIL_Army::UpdateKnowledges (MIL_Army.cpp:387)
   for each root KG -> MIL_KnowledgeGroup::UpdateKnowledges (MIL_KnowledgeGroup.cpp:488)  [recursive: children first]
     for each automat -> MIL_Automate::UpdateKnowledges (MIL_Automate.cpp:474)
        for each pion -> pion blackboard Update:
           DEC_KS_Perception::Talk  --> PHY_RoleInterface_Perceiver::ExecutePerceptions()   (DEC_KS_Perception.cpp:249-257)
              sensors -> NotifyPerception(agent|object|concentration|flow|urban, level)
              => DEC_Knowledge_AgentPerception / ObjectPerception / PopulationPerception  (PION level, per tick)
           DEC_KS_ObjectInteraction  --> object collisions/interactions  => ObjectCollision / ObjectPerception(identified)
           DEC_KS_PopulationInteraction, DEC_KS_DirectFire, DEC_KS_IndirectFire (being shot => perception of shooter)
     KG blackboard Update:
        DEC_KS_KnowledgeSynthetizer::Prepare  -> every DEC_Knowledge_Agent: current level := notSeen
        DEC_KS_KnowledgeSynthetizer::Talk (DEC_KS_KnowledgeSynthetizer.cpp:39)
           -> MIL_KnowledgeGroup::ApplyOnKnowledgesPerception (MIL_KnowledgeGroup.cpp:1175)
                 subordinate pions' perceptions        (if pion CanEmit or jam option off; CanReceiveUpdateFrom)
                 child KGs' knowledge                  (child jammed => only its pion perceptions, if CanReport)
                 crowd collisions                      (agents touching my crowds => delayed det->rec->id)
                 parent KG knowledge, if timeToDiffuse_ < t  (communication-delay; uses *saved previous* snapshot)
                 sharing groups (DEC_Connaissances_PartageConnaissances*)
           -> Extrapolate (interpolation-time) ; UpdateRelevance (decay)
        DEC_KS_ObjectKnowledgeSynthetizer::Talk -> object perceptions/collisions, ephemeral, universal, forget, relevance
        DEC_KS_Sharing
   army blackboard Update (urban)  ; KG UpdateObjectKnowledges (universal objects)
 then MIL_Army::CleanKnowledges -> KS Clean(): relevance <= 0 => DestroyKnowledge => Invalidate()

 UpdateDecisions (same tick)
   Lua brain: DEC_Connaissances_* / DEC_Knowledge_* -> caller.GetKnowledgeGroup()->GetKnowledge() ...
            returns boost::shared_ptr<DEC_Knowledge_X>  (the handle stored in Lua tables)
   every DEC_ConnaissanceX_* function checks pKnowledge && pKnowledge->IsValid() first
```

### 2.5 Agent knowledge lifecycle (`DEC_Knowledge_Agent.cpp`)

1. **Creation.** The first perception creates the knowledge through `DEC_BlackBoard_CanContainKnowledgeAgent::CreateKnowledgeAgent` (`:127-135`). The ID comes from the global sim ID pool (`GetSimIds().AllocateId()`, `DEC_Knowledge_Agent.cpp:90`), so it differs from the real agent's ID and **differs per KG**.
2. **Update from perception** (`Update(const DEC_Knowledge_AgentPerception&)`, `:290-317`):
   - Sets relevance to 1.
   - Current level becomes the max over perceivers; the max level ratchets up.
   - Detection, recognition and identification data are merged.
   - The extrapolation counter is reset.
   - Perception sources are recorded only from **other** KGs (`:267-288`).
3. **Update from parent or child KG** (`Update(const DEC_Knowledge_Agent&)`, `:319-345`): relevance becomes the max of both, and data is merged.
4. **Update from a crowd** (`UpdateFromCrowdPerception`, `:347-394`): an agent colliding with a crowd is detected, then recognized after 3 ticks, then identified after 3 more ticks. For **enemy** agents the escalation is capped by `maxHostilePerceptionLevel_`.
5. **Extrapolation** (`:256-266`): while `nTimeExtrapolationEnd_ > 0` or the knowledge is **locked**, the known position follows the real unit and relevance stays 1. Teleported or magic-moved units are excluded.
6. **Decay** (`UpdateRelevance`, `:408-450`), each tick while the unit is not seen:
   - A magic move by the real unit sets relevance to 0.
   - If the unit is transported by a unit of the **same KG**, relevance stays 1.
   - Otherwise `relevance -= 1/maxLifetime + dist(knownPos, realPos)/maxDistance`. A unit that moves away decays faster.
   - "Hacked" (magic) perception levels keep relevance at 1 until the unit is unseen again.
7. **Deletion:**
   - `Clean()` returns true when `relevance <= 0` (`:943-946`).
   - `DestroyKnowledgeAgent` then calls `Invalidate()` (`bValid_=false`, sends a destruction message) and removes the knowledge from the maps (`DEC_BlackBoard_CanContainKnowledgeAgent.cpp:137-145`).
   - A real agent marked for destruction also triggers deletion (`CleanKnowledgeDeletedAgent`).
8. **Lock:** `DEC_ConnaissanceAgent_Verrouiller` / `Deverrouiller` increment and decrement `locked_` (`:738-746`). A locked knowledge is extrapolated with relevance 1, so it never decays. **Unbalanced locks keep a unit known forever and its position stays ground-truth.**
9. **Dangerosity** is reduced by up to 20% by low relevance (`rMaxDangerosityDegradationByRelevance_ = 0.2`, `:52`, `:583`).

### 2.6 Object knowledge lifecycle (`DEC_Knowledge_Object.cpp`)

- **Creation sources:**
  - Pion perception, via `DEC_KS_ObjectKnowledgeSynthetizer::GetKnowledgeToUpdate`, which creates a new knowledge if the existing one is missing or invalid.
  - Pion collision. This happens only if the object type has `detection-upon-collision="true"`, which is the default (`MIL_ObjectFactory.cpp:76`; `DEC_KS_ObjectInteraction.cpp:135,144`).
  - Interaction (build, mine, animate...), which yields perception level *identified* (`DEC_KS_ObjectInteraction.cpp:~84-88`).
  - **Ephemeral knowledge:** when an object is registered, every KG of the **owner army** gets it as *identified* for one tick (`sim/Entities/Objects/MIL_ObjectManager.cpp:134-152`). Your side always knows the objects it creates. Exception: objects whose type is `IsInternal()`.
  - **Universal objects** (`<universal/>` capacity): every KG of every army knows them (`UpdateUniversalObjects`, `DEC_BlackBoard_CanContainKnowledgeObject.cpp:233`).
- **No time decay.** `UpdateRelevance` (`:501-526`):
  - Relevance is 1 while seen.
  - When the real object is being destroyed, `CleanObjectKnown()` unlinks it (`pObjectKnown_ = 0`). Relevance is zeroed immediately only for crowd-capacity objects or "perception-distance-hacked" knowledge (`:850-859`).
  - Otherwise relevance becomes 0 only when the army perceives the object's location (`MIL_Army::IsPerceived`, `MIL_Army.cpp:429-435` → `MIL_KnowledgeGroup::IsPerceived`, `MIL_KnowledgeGroup.cpp:566-575`, which also counts crowds standing in the location).
  - Until then the brain keeps a **"ghost" knowledge**: valid, with a localisation, but `GetObjectKnown()==null`, so real-object queries return 0, false or DontKnow.
- **Attribute proxies** (what the knowledge copies from the real object):

| Proxy | When it updates | Attributes (`sim/Entities/Objects/*Attribute.cpp`) |
|---|---|---|
| `PassThrough` | Any perception, collision or magic perception | AltitudeModifier, BurnSurface, Bypass, Construction, Flood, InteractionHeight, Lodging, Mine, Obstacle, PersonnelHolding, ResourceNetwork, Seabed, Tide, Trafficability, Underground, WaterCurrent |
| `PassThroughWithLocalisation` | Same as above, plus localisation | Breachable |
| `PerceptionPassThrough` | Perception only | Logistic |
| `Recon` | Only if the perceiving **agent type** has reconned the object (`_DEC_ConnaissanceObjet_Reconnaitre`); recon is stored per `MIL_AgentType` (`:640-651`, `:790-793`) | CBRN, CrossingSite |
| *(none)* | Not mirrored. Lua functions read the real object | Animator, Stock, TimeLimited, Fire, Burn, Delay, Detector, Spawned, ... |

- **Forgetting:**
  - `DEC_KS_ObjectKnowledgeSynthetizer::AddObjectKnowledgeToForget` destroys the knowledge. `DEC_MoveObject` uses this for non-owner armies; the owner army gets fresh ephemeral knowledge instead (`sim/Decision/DEC_ObjectFunctions.cpp:~280-310`).
  - Universal objects are removed from all KGs when destroyed (`Object.cpp:136-145`).

### 2.7 Crowd knowledge

- `DEC_Knowledge_Population` holds per-concentration and per-flow sub-knowledge. Each part decays by `1/population max-lifetime` while unseen. A magic move or the destruction of the crowd sets it to 0 (`DEC_Knowledge_PopulationConcentration.cpp:219-240`).
- The population knowledge is cleaned when its parts are gone (`DEC_Knowledge_Population.cpp:220-...`).
- A crowd's own brain has a separate `DEC_PopulationKnowledge` (`sim/Entities/Populations/DEC_PopulationKnowledge.cpp`). It tracks who attacks or secures the crowd and channeling. Crowd KG knowledge of agents comes **only from physical contact**, because crowds have no sensors.

### 2.8 Knowledge flow between KGs (communication delay, jamming, sharing)

- **Upward** (child → parent): every tick, without delay. Conditions: the parent and the child are enabled, `CanReceiveUpdateFrom(child)` (comms status not `none`/`no_transmission` with dynamic KGs), and the child is not jammed. For a jammed child, only its jammed pion's raw perceptions are used, if `CanReport()` (`MIL_KnowledgeGroup.cpp:1452-1480`).
- **Downward** (parent → child):
  - Happens only when `timeToDiffuse_ < currentTimeStep`. With a delay > 0 it uses the parent's **previous snapshot** (`ApplyOnPreviousKnowledgesAgent`), then saves the current one (`:1497-1512`). Children therefore see the parent's picture from one diffusion period ago.
  - Requires that the child can receive (not jammed, or the jammed pion `CanReceive`).
- **Subordinate pions** feed their KG only if `CanEmit()`, i.e. not dead, jammed or emitting-blackout, *or* the jam option is off (`:1450-1456`). With the option on, a silent pion's perceptions go into its jammed KG copy (`PHY_RolePion_Communications::UpdateKnowledgesFromObjectPerception`, `:722-738`).
- **Explicit sharing:**

| Lua function | Behavior |
|---|---|
| `DEC_Connaissances_PartageConnaissancesAvec(receiver, minutes)` | Schedules a share from the caller KG to the receiver's KG after N minutes (`DEC_KnowledgeFunctions.cpp:462-476`). No-op if it is the same KG. |
| `DEC_Connaissances_PartageConnaissancesDansZoneAvec(receiver, center, radius)` | Immediate share, limited to the zone. |
| `DEC_Connaissances_PartageConnaissancesAvecConnaissanceAgent` | Agent-knowledge variant (`DEC_KnowledgeAgentFunctions.cpp`). |
| Sharing groups (`RegisterSharingPerceptions` / `RegisterSharingKnowledges`, `MIL_KnowledgeGroup.cpp:2006-2024`) | Continuous sharing between KGs. |

### 2.9 Brain-facing knowledge API (selection)

| Area | Functions (file) |
|---|---|
| Queries on KG | `DEC_Knowledge_GetEnemies`, `DEC_Knowledge_GetObjects`, `DEC_Knowledge_GetObjectFromID`, `DEC_Knowledge_GetUrbanBlocks`, `_DEC_Connaissances_UnitesEnnemiesVivantesPercues*`, `_DEC_Connaissances_UnitesEnnemiesDangereuses`, `_DEC_Connaissances_UnitesPrenantAPartie`, `_DEC_ObjectKnowledgesInZone`, `DEC_ObjectKnowledge_GetObjectsInCircle/InZone/ContainingPosition`, `_DEC_Connaissances_CollisionsObjets`, `_DEC_Connaissances_ObjetLePlusProche`, `_DEC_Connaissances_Populations`, `_DEC_Connaissances_CollisionsPopulations` (`sim/Decision/DEC_KnowledgeFunctions.cpp:55-116`) |
| Agent knowledge | `DEC_ConnaissanceAgent_EstValide`, `_Position`, `_EtatOps`, `_EstDetruitTactique`, `_EstMort`, `_NiveauPerceptionMax`, `_Verrouiller/_Deverrouiller`, `_DangerositeSurPion`, `_EstUnEnnemi`, `_EstUnAllie`, `_NiveauDePerceptionCourant`, `_EstPercuParUnite` (`DEC_KnowledgeAgentFunctions.cpp`) |
| Object knowledge | `DEC_IsValidKnowledgeObject`, `DEC_ConnaissanceObjet_Localisation`, `_Type`, `_EstContourne`, `_EstObstacleDeManoeuvreActif`, `_EstValorise`, `_NiveauConstruction`, `_NiveauValorisation`, `_NiveauAnimation`, `_PeutEtreContourne`, `_EstReconnu`, `DEC_ObjectKnowledge_HasCapacity`, `_IsImpassable`, `_IsTrafficable`, `DEC_GetObjectID`, `DEC_GetKnowledgeFromObjectID` (`DEC_KnowledgeObjectFunctions.cpp:57-125`) |
| Crowd knowledge | `DEC_CrowdKnowledge_IsValid`, `_DEC_ConnaissancePopulation_Attitude`, `_Dangerosite`, `_EstEnnemi`, `_Reconnaitre`, `_Verrouiller`, `_PointPlusProche`, `DEC_GetConcentrationLaPlusProche` (`DEC_KnowledgePopulationFunctions.cpp`) |
| Resolve by ID | `DEC_GetAgentKnowledge(callerId, knowledgeId)`, `DEC_GetObjectKnowledge`, `DEC_GetCrowdKnowledge` (`DEC_KnowledgeFunctions.cpp:748-776`). Resolves in the **caller pion's** KG. |
| "Telepathy" / ground truth | `DEC_GetAgentKnowledgeFromAgent(observer, observed)` returns the observer KG's knowledge of a real agent, or null (`:741-746`). `DEC_ConnaissanceAgent_EnAgent` / `DEC_Connaissance_EnAgent` return the **real agent** from a knowledge. `DEC_Connaissance_GetRawMission` returns the real agent's mission. `DEC_TelepathyFunctions.cpp` only holds legacy helpers that read and write brain variables (`F_CopyPlotsRavitaillement`, `F_GetListeEnisTirAutorise`, `F_RemoveAgentFromListeEnisTirAutorise`); this is not knowledge telepathy. |

### 2.10 Validity contract

- Every `DEC_Connaissance*` getter begins with `if( pKnowledge && pKnowledge->IsValid() )`. For invalid input it returns a neutral value (0, nil, `eDecTristate_DontKnow`=2, `eQueryInvalid`), for example `DEC_KnowledgeObjectFunctions.cpp:325-350`.
- Lua therefore rarely errors on a stale handle. It just gets "don't know" values. **Always test validity before acting.**
- `E_QueryValidity` has two values: `eQueryValid`=0 and `eQueryInvalid`=1 (`sim/Knowledge/QueryValidity.h`).

---

## 3. Objects

### 3.1 Concepts

| Concept | Where | What |
|---|---|---|
| **Object type / prototype** | `ObjectPrototype` from `Objects.xml` (`MIL_ObjectFactory::ReadObjectPrototype`, `sim/Entities/Objects/MIL_ObjectFactory.cpp:69-82`) | Name, `type`, `detection-upon-collision` (default true), `affected-by-engineer-speed-factor` (default true), `point-size` (default 250 m), plus a list of **capacities**. |
| **Capacity** (`ObjectCapacity_ABC`) | One XML child tag, created by `CapacityFactory` | Type-level behavior: build, mine, bypass, attrition, mobility... When an object is instantiated, each capacity `Instanciate()`s itself and **creates the matching attributes**. |
| **Attribute** (`ObjectAttribute_ABC`) | `AttributeFactory` (`AttributeFactory.cpp:151-185`) | Per-instance dynamic state, e.g. `construction` %, `mine` %, `bypass` %, `obstacle` (activated / activity times), `crossing-site`, `logistic-base`, `stock`, `flood`, `cbrn`. Set from ORBAT `<attributes>` or magic actions. This is what object knowledge mirrors. |
| **MIL_Object / Object** | `MIL_Object.cpp`, `Object.cpp` | Instance. `CanInteractWith`, `ProcessAgentEntering/Exiting/MovingInside/Inside`, `Update`. |
| **MIL_ObjectManipulator** | | Construct, destroy, mine, demine, bypass by percentage (used by actions). |

### 3.2 Capacity tags (`CapacityFactory.cpp:106-155`)

Marker-only capacities have no XML attributes (`GenericCapacity.h:41-48`): `altitude-modifier`, `berthing`, `crossing-site`, `lodging`, `logistic`, `personnel-holding`, `seabed`, `universal`.

| Tag | Class | Key XML attributes | Effect / attribute created |
|---|---|---|---|
| `activable` | ActivableCapacity | none | Creates `ObstacleAttribute`. Obstacle becomes active after construction or by `DEC_ActivateObject`; activity windows from the attribute (`ActivableCapacity.cpp:53-75`). **An inactive obstacle does not collide** (`MIL_Object_ABC.cpp:139-145`). |
| `avoidable` | AvoidanceCapacity | `distance` | Avoidance buffer (`DEC_ConnaissanceObjet_PointEstDansZoneEvitement`). |
| `attrition` | AttritionCapacity | none | Attrition on agents entering or moving inside. Uses the **dotation from the constructor's buildable/improvable resources** and consumes mines or construction per hit (`AttritionCapacity.cpp:50-52,128-147`). |
| `breachable` | BreachableCapacity | `breach-speed`, `impacted-by-indirect-fire` | `BreachableAttribute` (breached sub-areas). `DEC_StartBreachObject`. |
| `bypassable` | BypassableCapacity | `bypass-speed` | `BypassAttribute` (bypass %). `_DEC_StartCreerContournementObjet`. |
| `bridging` | BridgingCapacity | `type` | Bridges, linked to `crossing-site`. |
| `cbrn` | CBRNCapacity | `agent`, `name` | `CBRNAttribute` (contamination / intoxication). |
| `constructor` | ConstructionCapacity | `default-consumption-mode` (required), `unit-type` = `raw`\|`density`; children `buildable`, `improvable`, `impacted-by-indirect-fire` (`ConstructionCapacity.cpp`) | `buildable` creates `ConstructionAttribute` (build % and dotations). `improvable` creates `MineAttribute` ("valorisation"). `impacted-by-indirect-fire` creates DestructibleCapacity. |
| `crowd-attrition` | | `attrition-surface`, `ph` | Damage to crowds. |
| `decontamination` | | none | `DEC_ConnaissanceObjet_DemandeDeDecontaminationSurPion`. |
| `detection` | DetectionCapacity | `acquisition-time(s)`, `level` | Sensor zone. |
| `interact-with-enemy` | **InteractWithSideCapacity** | `friend`, `enemy`, `neutral`, `civilian` (bool, default false) | Restricts which sides interact. **If the tag is absent, every side interacts** (`MIL_Object_ABC.cpp:157-166`; `InteractWithSideCapacity.cpp:19-43`). |
| `interaction-height` | InteractIfHeightCapacity | `min`, `max` | Only agents within the height band interact. |
| `mobility` | MobilityCapacity | `default-speed` (km/h; <0 means infinite), `unit-speed-impact-mode` = `AuPlusLent`\|`VitesseMaxAgent`\|`VitesseMaxObjet`\|`VitesseBlocUrbain`, `max-unit-percentage-speed` (0-100), `units-can-always-exit` (default true) | Speed policy (`MobilityCapacity.cpp:27-130`). `IsImpassable()` when the speed or factor is 0, which feeds `DEC_ObjectKnowledge_IsImpassable`. |
| `population-filter` | PopulationFilterCapacity | `density` | Max crowd density through the object. |
| `perception` | PerceptionCapacity | `blinded` | |
| `propagation-object` | `RegisterPropagationObject` | `type` = `plume`\|`external`\|`constant`\|`decreasing`\|`flood`\|`fire`(`surface-fire`)\|`tide`(Navy license); `thresholds/threshold` | Plume, flood, fire, tide capacities (`CapacityFactory.cpp:181-212`). |
| `resources` | ResourceNetworkCapacity | (node definition) | Resource network node (Sec. 7). |
| `spawn` | SpawnCapacity | `object`, `action-range`, `autonomous` | Spawns a child object (`ChildObjectAttribute`). |
| `stock` / `supply` | Stock/SupplyCapacity | | `_DEC_StartSupplyObject`, `_DEC_StartExtractFromStockObject`, `_DEC_StartDistributionObjet`. |
| `structural` | StructuralCapacity | `value`, `threshold`, `structural-state` | Urban-like structural state. |
| `time-limited` | TimeLimitedCapacity | none | Lifetime from `activity-time` attribute. |
| `trafficability` | TrafficabilityCapacity | `default`, `max-weight` | Weight limit (`DEC_ObjectKnowledge_IsTrafficable`). |
| `underground-network` | UndergroundCapacity | `impacted-by-indirect-fire` | `DEC_Agent_SeDissimulerDansReseauSouterrain`, `_DEC_StartTraverserReseauSouterrain`. |
| `workable` | WorkableCapacity | `max-animator` (required) | `AnimatorAttribute`. `_DEC__StartAnimerObjet`, `DEC_ConnaissanceObjet_NiveauAnimation`. |
| `universal` | UniversalCapacity | none | Known by everyone (Sec. 2.6). |
| Others | | | `attitude-modifier` (`attitude`), `border`, `custom-attrition`, `delay`, `fire-forbidden`, `fire-propagation-modifier`, `forest-clearing`, `infrastructure`, `interference`, `mobility-support`, `protection`, `scattering`, `seabed`, `urban-destruction`, `water-current`, `logistic` (marks a logistic base object, `LogisticAttribute`). |

Tags found in `phys/Objects.xml`: every one is registered except `geometries`, which is read elsewhere. Registered but unused in mildb: `delay`, `forest-clearing`, `infrastructure`, `perception`. `MedicalCapacity.h` is included, but **no `medical` tag is registered**.

### 3.3 Instantiation paths

| Path | Code | Notes |
|---|---|---|
| ORBAT | `MIL_ObjectFactory::CreateObject(xis, army)` (`:134-152`) | Reads `<attributes>` children through `AttributeFactory`. |
| Magic action | `:154-185` | |
| Brain | `DEC_CreateDynamicGenObject` creates a `DEC_Gen_Object` (a "genie" object description). `_DEC_StartCreateObject` (`PHY_ActionConstructObject`) builds it progressively. `_DEC_CreerObjetSansDelais` / `DEC_GenObject_CreateInstantaneously` create it instantly. `_DEC_MagicGetOrCreateObject`. | |
| Generic create | `MIL_ObjectFactory::CreateObject(name,type,army,loc,activated,...)` (`:90-132`) | Builds capacities, initializes attributes, sets obstacle "activated after construction", density (for `unit-type="density"`), flood depth, CBRN agent, then `Finalize()`. |

Then `MIL_ObjectManager::RegisterObject` (`MIL_ObjectManager.cpp:134-152`) adds the object to the universal list and sends creation. For non-universal objects it gives the owner army's KGs ephemeral knowledge.

### 3.4 Work on objects (pion actions)

Registered in `sim/Decision/DEC_ActionFunctions.cpp:165-186`:

| Action function | Action class |
|---|---|
| `_DEC_StartCreateObject` | `PHY_ActionConstructObject` |
| `_DEC_StartPrepareObject` | `PHY_ActionPrepareObject` |
| `_DEC_StartValoriserObjet` | `PHY_ActionMineObject` |
| `_DEC_StartDevaloriserObjet` | `PHY_ActionDemineObject` |
| `_DEC_StartDetruireObjet` | `PHY_ActionDestroyObject` |
| `_DEC_StartCreerContournementObjet` | `PHY_ActionBypassObject` |
| `_DEC_StartReprendreTravauxObjet` | Resume work |
| `_DEC__StartAnimerObjet` | Animate |
| `DEC_StartBreachObject` | Breach |
| `_DEC_StartExtinguishObject` | Extinguish |
| `_DEC_StartTraverserReseauSouterrain` | Move through underground network |

- All actions except create and prepare take a `DEC_Knowledge_Object`.
- Action return codes are `eRunning`, `eFinished`, `eImpossible`, `eNoMoreDotation`, `eNoCapacity`, ... (`sim/Entities/Agents/Actions/Objects/PHY_RoleAction_Objects.h:33-40`).
- **Work speed comes from `Equipments.xml`**, not Objects.xml. Under `<objects><object type="...">` the attributes are `initial-construction-time`, `initial-destruction-time`, `valorization-time`, `devalorization-time`, `extinguishing-time`, `construction-time-factor`, `destruction-time-factor`, `bypass-gain-factor`, `non-bypassed-speed`, `bypassed-speed`, `consumption-mode` (`sim/Entities/Agents/Units/Composantes/PHY_ComposanteTypeObjectData.cpp:44-57`).
- Capability checks: `_DEC_Agent_PeutConstruireObjet`, `_DEC_Agent_PeutValoriserObjet`, `_DEC_Agent_PeutConstruireContournementObjet`, `DEC_Agent_CanBreachObjet`, `DEC_GetAgentsPouvantConstruire...` (`DEC_Agent_ObjectAction.cpp:31-79`).
- Destruction:
  - `Object::Update` destroys the object when it is marked for next update or `ConstructionAttribute::NeedDestruction()`. Children objects (non-autonomous) are destroyed as well (`Object.cpp:109-135`).
  - `MIL_ObjectManipulator::Destroy(entity, %)` lowers construction %.
  - `DEC_DetruireObjetSansDelais` is instant.

### 3.5 Interaction with agents

- `MIL_Object_ABC::CanInteractWith(agent)` (`MIL_Object_ABC.cpp:157-166`) requires the height band (if any), then the side filter (if any), and `CanCollideWithEntity()`: not marked for destruction, and the obstacle is activated if the object has one.
- The object knowledge exposes the same check to pathfinding (`DEC_Knowledge_Object::CanCollideWith`).
- Agent speed inside an object comes from MobilityCapacity's policy and the equipment's `bypassed-speed` / `non-bypassed-speed` (`ObjectAttributeSpeedHelper.cpp`, `PHY_RoleAction_Moving`; details not yet explored).

---

## 4. Crowds (`sim/Entities/Populations/`)

| Item | Detail |
|---|---|
| Physical file | **`Crowds.xml`** (root `<populations>`). It is mapped as `<populations file="Crowds.xml"/>` in `phys/physical.xml`. `Populations.xml` holds **inhabitants**. The naming is confusing, so double-check which file you edit. |
| Type attributes | `id`, `concentration-density`, `moving-base-density`, `moving-speed`, `armed-individuals`, `collides-with-crowds`, `decisional-model`. Children: `repartition` (male/female/children), `nbc/@decontaminationDelay`, `slowing-effects/slowing-effect[@population-attitude]/unit[@unit-size,@population-density,@max-speed]`, `attrition-effects`, `unit-fire-effects/unit[@rule-of-engagment, @attrition-surface, @ph]`, `urban-destruction-effects/urban-destruction-effect[@attitude,@density]` (`MIL_PopulationType.cpp:42-231`). Top level: `reloading-time-effect`, `split-probability`. |
| Attitudes | `calme`, `agitee`, `excitee`, `agressive` (peaceful, agitated, excited, aggressive) (`MIL_PopulationAttitude.cpp:24-31`, `src/libraries/ent/Conversion.cpp:227-230`). Each concentration and flow has its own attitude. `_DEC_Population_ChangerAttitude` sets it; objects with `attitude-modifier` change it (e.g. "Secured Area" sets crowds quiet). |
| Structure | `MIL_Population` holds `MIL_PopulationConcentration[]` (static blobs at a point) and `MIL_PopulationFlow[]` (moving ribbons with head and tail). `Move(dest)` asks every concentration and flow to move; concentrations emit flows, and flows that arrive form concentrations (`MIL_Population.cpp:874-898`). A magic move blocks `Move`. |
| Actions | `Actions/PHY_Population_ActionMove`, `ActionMoveAlong`, `ActionFireOnPion(s)`, `ActionBattle`, `ActionUrbanDestruction`. |
| Knowledge | Crowds sit in the army's automatic **Crowd KG**. Agents colliding with crowd elements become known with delayed escalation (Sec. 2.5, item 4). Crowd-vs-crowd flows are known through collisions (`sPopulationsCollidingPopulationVisitor`, `MIL_KnowledgeGroup.cpp:~1250-1300`). |
| Brain API | `DEC_PopulationFunctions.cpp` / `DEC_KnowledgePopulationFunctions.cpp`: `_DEC_Population_Attitude`, `_DEC_Population_ChangerAttitude`, `_DEC_Population_RalentissementPion_ChangeVitesse`, `_DEC_Population_ChangeUrbanDestructionState`, `_DEC_Population_ChangeDemonstrationState`, `_DEC_HasFlow`, `_DEC_Population_HasReachedDestination(Completely)`, `_DEC_Connaissances_PionsPrenantAPartie`, `_DEC_Connaissances_PionsSecurisant`, `_DEC_StartHidingInCrowd`, `_DEC_Crowd_ExtractWoundedFromCrowd`, `DEC_GetNombrePersonnesDansFoule`... |
| Pathfinding | Crowds use `DEC_Population_PathClass` / `DEC_Population_PathfinderRule` with `population-rules` in Pathfind.xml (unverified section name). |

Crowd gotchas:

- Crowd knowledge is per *element* (concentration or flow). The known shape can lag behind the real one.
- A crowd's brain does not perceive at a distance.
- Diplomacy with a crowd uses the crowd's army.

## 5. Inhabitants (`sim/Entities/Inhabitants/`, briefly)

- The type is defined in `Populations.xml` (`<inhabitants>`):
  - `associated-crowd` (a Crowds.xml type, required) and optional `angry-crowd-mission`.
  - `repartition`, `schedule[@transfer-time]/event[@day,@from,@to,@motivation]`.
  - `safety-level[@gain-per-hour,@loss-on-fire]`, `health-need`, `consumption/resource[@type,@need]` (`MIL_InhabitantType.cpp:48-71`).
- `MIL_Inhabitant::UpdateState` (`MIL_Inhabitant.cpp:134-163`):
  - Runs the schedule. While inhabitants move, a "population moving" object is created.
  - Computes satisfactions for safety, lodging, resources (from resource networks via the living area's urban blocks), motivation and health.
  - Spawns **angry crowds** from unsatisfied blocks (`ManageAngryCrowd`).
- Inhabitants have no brain.

## 6. Task groups (`sim/Entities/TaskGroups/`, added in 2023)

| Item | Detail |
|---|---|
| What | An **aggregated** unit under a formation, or nested under a parent task group. It has no pions. It holds `MIL_Echelon`s, each with equipment counts. It has its own decision (`DEC_TaskGroupDecision`, `decisional-model`), a KG (`knowledge-group` ORBAT attribute, `MIL_TaskGroup.cpp:100-104`) and its own logistic hierarchy and end point. |
| Physical file | `TaskGroups.xml`. `<task-group id name decisional-model logistic-echelon>`, `nature`, `echelons/echelon[@name,@dia-name,@offense-position,@offense-depth,@defense-position,@defense-depth]/equipments/unit[@type,@count]`, `sub-group[@type]` (`MIL_TaskGroupType.cpp:41-94`). `logistic-echelon` names the echelon that acts as the logistic one. |
| Roles | `Roles/PHY_RoleTaskGroup_{Humans,Maintenance,Medical,Objects,Posture,Supply,Transport}`, `PHY_RoleEchelon_{Equipment,FireControl}`, `MIL_Echelon_Posture`. |
| Convoy | `SupplyConvoyTaskGroup` (a `logistics::SupplyConvoy` that moves on `DEC_TaskGroupConvoy_Path` instead of spawning a convoy pion). Supply requests are built by `SupplyTaskGroupDotationRequestBuilder` / `StockRequestBuilder`. |
| Knowledge | `DEC_Knowledge_TaskGroup` in the KG. Lifetime is `task-group-knowledge/@max-lifetime`. Interactions come from `DEC_KS_TaskGroupInteractions`. |
| Brain API | `DEC_TaskGroupFunctions.cpp` (56 functions): `DEC_StartAction_TaskGroup_Move/MoveAlong/Stealth`, `DEC_GetEchelonNames`, `DEC_GetOffensePosition`, `DEC_TaskGroupChangeFormFactor`, `DEC_TaskGroup_SetRulesOfEngagement`, `DEC_TaskGroupIndirectFireOnPoint`, `DEC_TaskGroupSetObjectsToBuild(OnEchelons)`, `DEC_TaskGroupStartLoadTaskGroup`, `DEC_AddHighPriorityTarget`... |

## 7. Logistics (high level)

```
 logistic base (MIL_AutomateLOG = "brain logistic")
   = automat whose type IsLogisticBase (Automate LOG TC2 / Organization)
   = or formation with logistic-level="logistic-base"   (MIL_Formation.cpp:140-150)
   = or task group supply role (PHY_RoleTaskGroup_Supply.cpp:44, no quotas)
        ^  LogisticLink(s)  (superior chain; quotas per dotation)
        |
 any automat / formation / task group  --(LogisticHierarchy)-->  nominal superior + current superior(s)
```

- **Automat link (no quotas):**
  - Ordinary automats are created with a `LogisticHierarchy(useQuotas=false)` (`MIL_Automate.cpp:90,119,233`).
  - A logistic-base automat also links **to itself** first (`MIL_Automate.cpp:185-190`).
  - `logistic-level` on an automat must match its type, or an error is logged and the value corrected (`MIL_Automate.cpp:163-178`).
- **Base → superior base link (with quotas):** a `MIL_AutomateLOG` defaults to `useQuotas=true` (`MIL_AutomateLOG.h:81`). Quotas are read from the ORBAT link `<quotas><resource name quantity/>`. The alert threshold is hard-coded at 10% of the quota (`LogisticLink.cpp:41-63`).
- **`ConsumeQuota`:**
  - With quotas, a dotation that has **no quota defined gets 0 supply**.
  - Exhausting a quota calls `NotifyQuotaExceeded` (report). Crossing 10% calls `NotifyQuotaThresholdReached` (`LogisticLink.cpp:78-100`).
- **Chains:**
  - Supply, maintenance and medical consigns live in `sim/Entities/Agents/Roles/Logistic/` (`SupplyConsign`, `SupplyConvoy`, `PHY_Maintenance*Consign`, `PHY_Medical*Consign`, funeral).
  - Automatic dotation requests are triggered by `MIL_DotationSupplyManager` when stocks fall below the unit type's logistic threshold (`PHY_DotationStockContainer.cpp:73,182`, `GetStockLogisticThresholdRatio`).
- **Convoys:** `Supply.xml/<convoys mission="LOG - Convey" unit-type unit-type-air unit-type-sea>` configures:
  - constitution, loading and unloading times by truck count and resource nature;
  - speed modifiers.

  The convoy is a spawned pion running that mission. Its brain uses `_DEC_Ravitaillement_Convoi_*` (`DEC_LogisticFunctions.cpp`).
- **Other XML:** Maintenance.xml (working schemes Regime1-4), Health.xml (injuries, sorting times), Funeral.xml, LogisticSupplyClasses.xml.
- **Manual chains:** the ORBAT attributes `maintenance-manual`, `supply-manual`, `medical-manual` and `detailed-supply` put a chain under brain control (`MIL_Formation.cpp:140-145`, `MIL_Automate.cpp:179-183`).
- **Brain API:**
  - `_DEC_{Maintenance,Sante,Ravitaillement}_ActiverChaine/DesactiverChaine`, priorities and regimes.
  - `_DEC_DemandeDeRavitaillement`, `_DEC_Automate_PcDeTC2`, `_DEC_Pion_PcDeTC2`, `_DEC_Agent_GetLogisticManagerHQ`, `DEC_ChangerSuperieurLogistiqueConnaissance`.
  - `DEC_EquipmentType_*` capacity queries.
- TC2 pion types live in `Entities/Specialisations/LOG/TC2/MIL_AgentTypePionLOGTC2`. Detailed TC2 brain behavior was not yet explored.

## 8. Pathfinding (briefly)

- **`Pathfind.xml`:**
  - `<configuration distance-threshold max-calculation-time max-end-connections>`.
  - `<unit-rules><rule type flying autonomous [inherits]>` with: `optimisation@shortest[@theta-star]`, `preferred-terrains[@strength]/preferred-terrain@type`, `avoided-terrains`, `preferred-altitude@value`, `zone` (fuseau outer tolerance and inner comfort), `automat-zone`, `danger-direction`, `enemies@cost-on-contact/@cost-at-security-range/@maximum-cost`, `population-costs[@security-range,@maximum,@outside-of-population]/population-cost[@attitude,@contact-cost]`, `object-costs[@avoid,@threshold]/object-cost[@type,@value]` (`sim/Decision/DEC_Agent_PathClass.cpp:42-246`).
- **Path types:** `movement, recon, infiltration, assault, retreat, backup, minesweep, logistic, criminal, nbc` plus `*ENG` variants (`DEC_PathType.cpp:16-33`). Each needs a rule for all four (flying × autonomous) combinations, or startup throws (`DEC_Agent_PathClass.cpp:24-38`). `inherits` copies the base rule.
- **Knowledge-driven costs** (`DEC_AgentContext.cpp:64-170`):
  - Enemies come from KG `GetEnemies()` inside the fuseau.
  - Objects come from **KG object knowledge** (`FillObjectsKnowledges`) that `CanCollideWith(agent)`. Flood and burn-surface objects have dedicated cost classes.
  - Crowds come from KG crowd knowledge.
  - Urban structural state comes from army urban knowledge.
  - **Unknown objects are not avoided.** Ghost object knowledge (Sec. 2.6) **is** avoided.
- **Execution:** paths are computed asynchronously in `TER_Pathfinder` threads (`src/libraries/simulation_terrain/TER_Pathfinder.cpp:235-300`), using A*/theta* in `src/libraries/pathfind/`. `DEC_Itineraire_Etat` returns `eInvalid=0, eComputing=1, eValid=2, eImpossible=4, ePartial=8` (`simulation_terrain/TER_Path_ABC.h:26-30`). Lua must poll until the state is not 1.
- **Brain API:** `_DEC_CreerItineraire`, `_DEC_CreerItineraireListe`, `DEC_Itineraire_Etat`, `_DEC_GetNextObjectOnPath(WithBypassed)`, `DEC_GetNextRemovableObjectOnPath`, `DEC_GetPointsOnPathInObject`, `DEC_Itinerary_*`.

## 9. Resource network and propagation (briefly)

- **`src/libraries/resource_network`:**
  - `ResourceNetworkModel` holds `NodeProperties`, which hold one `NodeElement` per resource type, connected by `ResourceLink`s to urban blocks or objects (`kind`).
  - Node attributes: `enabled`, `production`, `stock`, `initial-stock`, `consumption`, `critical-consumption`, `time-unit`, `link[@target,@capacity,@kind]` (`NodeElement.cpp:118-143`).
  - Network types are in `ResourceNetworks.xml` (Electricity, Natural Gas, Running Water; `defaults@production @time-unit`).
  - An object becomes a node through the `resources` capacity. Brain API: `DEC_ReseauRessource_*`, `DEC_ResourceNetwork_*` (`DEC_ResourceNetworkFunctions.cpp`).
- **`src/libraries/propagation`:** `PropagationManager` reads external time-indexed propagation grids (ASC) for `propagation-object type="external"` plumes. `FloodModel` computes flood extents from elevation for `flood` objects.

## 10. Effects (`sim/Entities/Effects/`, briefly)

- `MIL_EffectManager` queues `MIL_Effect_*` objects (direct and indirect fire, explosion, kill officers, scatter population, weather smoke and flare, move, suicide, ...). They are applied in `UpdateEffects`, after all actions of the tick.
- Example: `DEC_ConnaissanceAgent_TuerOfficiers` registers `MIL_Effect_KillOfficers` (`DEC_Knowledge_Agent.cpp:748-751`).

---

## 11. Gotchas (consolidated)

1. **Knowledge is per KG, and its IDs are per KG.**
   - The same real unit has different `DEC_Knowledge_Agent` objects and IDs in different KGs.
   - Passing a knowledge handle to an agent of another KG (e.g. in a mission parameter or a shared Lua table) gives it the *other KG's* picture. `DEC_GetAgentKnowledge(callerId, id)` resolves only in the caller's KG.
2. **Re-perception creates a new knowledge.** After a knowledge is destroyed (relevance 0), the old Lua handle stays invalid forever. Re-query instead of caching.
3. **Jamming swaps the KG.**
   - When a pion is jammed (with the jam option on), `GetKnowledgeGroup()` returns a **cloned KG** whose knowledges have **new IDs** (`DEC_BlackBoard_CanContainKnowledgeAgent.cpp:271-276`).
   - On unjam, `Merge` keeps the more relevant copy and may **destroy and replace the parent's knowledge** (`:278-290`). Handles held before or during jamming can become invalid.
   - Dynamic KGs (comms loss) behave the same way.
4. **Object knowledge never times out.**
   - Destroyed objects linger as "ghost" knowledges, still valid with a localisation, until the side looks at the place again.
   - Pathfinding keeps avoiding them, and `DEC_ConnaissanceObjet_*` queries that read the real object return 0, false or DontKnow.
   - Agent knowledge, in contrast, decays by time **and by the real unit's distance from the last known position**.
5. **Some "knowledge" functions read ground truth.**
   - `DEC_ConnaissanceObjet_NiveauConstruction` reads the real `ConstructionAttribute` (`DEC_KnowledgeObjectFunctions.cpp:485-491`).
   - Capacity and attribute helpers (`IsValidObjectCapacity/Attribute`) go through `GetObjectKnown()`.
   - `DEC_ConnaissanceAgent_EnAgent` returns the real agent.
   - Mirrored attributes are listed in Sec. 2.6; everything else is real-time truth while the real object exists.
6. **Locked agent knowledge is perfect tracking.** `DEC_ConnaissanceAgent_Verrouiller` keeps relevance at 1 and extrapolates the position to the real unit. Always unlock.
7. **Recon is per agent TYPE.** `_DEC_ConnaissanceObjet_Reconnaitre` marks the object reconned for the agent's *type*. Any other unit of that type then updates the CBRN and crossing-site attributes on perception.
8. **Objects.xml typos are silent.** `CapacityFactory::Create` ignores unknown tags. A misspelled capacity simply does not exist. Attribute typos inside known tags usually fall back to defaults.
9. **`interact-with-enemy` absent means everyone interacts**, friends included. If present, only the flagged sides interact; civilians need `civilian="true"`. Inactive obstacles (`activable`) do not interact at all.
10. **Work durations are in `Equipments.xml`**, per equipment and object type. If an equipment has no `<object type=...>` entry, the unit cannot build that object (`_DEC_Agent_PeutConstruireObjet` returns false). Objects.xml has no such entry.
11. **Communication delay is applied downward, using the parent's previous snapshot.** A child KG sees the parent's knowledge from up to one delay period ago. Upward synthesis is immediate.
12. **Crowds detect only by contact.** They live in a hidden per-army Crowd KG that is never sent to clients.
13. **Diplomacy tri-state.** An undefined relation gives `DontKnow` (2), not "not enemy". It is not enforced symmetric between armies.
14. **Embrayé is not a mode.** It means the automat has a mission. Ordering a pion directly while its automat has a mission is a modeling concern only; the kernel does not block it (unverified).
15. **Quotas.**
    - Supply between a base and its superior base is quota-limited. A dotation with no quota means **zero resupply**.
    - Ordinary automats → base links have no quotas.
    - The quota threshold alert is fixed at 10%.
16. **File-name trap.** `Crowds.xml` contains crowd types (`<populations>` root). `Populations.xml` contains inhabitants.
17. **`jam-and-blackout-dynamic-knowledge-groups` must be present** in the exercise settings, or `HasJamAndBlackoutDynamicKnowledgeOption()` throws.
18. **Knowledge-source priority is ineffective.** In `DEC_KnowledgeBlackBoard_ABC::AddToScheduler` (`DEC_KnowledgeBlackBoard_ABC.cpp:~49-57`) the priority comparison compares an element with itself, so sources run in registration order. This is harmless, but do not rely on priorities.

## 12. Not yet explored

- Detailed speed math inside objects (`ObjectAttributeSpeedHelper`, `PHY_RoleAction_Moving`).
- Sensor model and `PHY_PerceptionLevel` thresholds (perception section).
- DEC_Knowledge_Urban recon timing.
- TC2 brain specifics and supply-request dispatch internals (`SupplyRequestHierarchyDispatcher`).
- Flow-collision mechanics between crowds (`MIL_FlowCollisionManager`).
- Population path rules.
- Effects details.
- `DEC_KS_Sharing` timing semantics.
- Task-group knowledge interactions (`DEC_KS_TaskGroupInteractions`).
