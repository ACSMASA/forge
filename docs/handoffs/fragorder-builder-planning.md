# Handoff: plan the frag-order builder (planning only)

> **How to use:** start a fresh Claude Code session in `C:\MASARepo\forge` (Opus, effort high, **plan mode**) and say
> "Follow `docs/handoffs/fragorder-builder-planning.md`". Written 2026-11-05.
> **Your job is the plan, not the build.** You produce a step-by-step implementation handoff in the format of
> `docs/handoffs/mission-builder.md`. When the user accepts it, you save it (see "On approval") and stop. A later fresh
> session implements it step by step.

## Goal of the thing you are planning
The same capability as the finished unit-mission builder, but for **frag orders** (fragmentary orders) in
`C:\MASARepo\sw.models`. That means:
- creating new frag orders and wiring them, with checkpoint-resilient behaviors;
- knowledge readable by any agent (kit files + deterministic tools), not locked in an agent prompt;
- a builder that is a critic, not a scribe.

**Read first, in this order:**
1. `docs/handoffs/mission-builder.md`. It is your template and precedent: Goal, Shape, Decisions, Context, Working
   rules, Steps with "Done when", Progress log. Read the Progress log too: it records what each step found.
2. What it produced:
   - `docs/sw.models/MISSIONS.md`, `docs/sw.models/CHECKPOINTS.md`
   - `tools/missionlib.py`, `tools/mission_check.py`, `tools/mission_scaffold.py`, `tools/checkpoint_lint.py`
   - `.claude/agents/sw-mission-builder.md`, `.claude/skills/mission/`, `.claude/skills/mission-check/`
   - the `build_missions` part of `tools/build_models_index.py`
   - forge `CLAUDE.md`

   Skim the tools: the plan should **extend** them where it can, not duplicate them.

## Planning procedure
1. **Explore** (read-only).
   - Frag orders end to end: the physical XML, `FragOrder.lua`, the frago tasks, the sim side and the integration
     side. Starting points are below.
   - Use the kit: index greps (CLAUDE.md "Grep recipes"), THEMES rows.
   - For precise questions you can spawn `sw-sword-navigator`, `dai-navigator` or `sw-models-navigator`. Spawn them
     sparingly: one precise question each, in parallel when independent.
2. **Settle the open questions** below.
   - Answer from the code where possible, with a `file:line` you read.
   - Ask the user (AskUserQuestion) only what is genuinely their call: scope, shape, names, write mode, validation
     scenarios. Each question gets a recommendation.
3. **Write the plan** in the mission-builder format:
   - a **Context** section of facts you verified, each with an anchor; mark the rest "to verify at step N";
   - small steps, each ending in a commit `fragorder builder: step N <title>`, each with a "Done when";
   - a validation step with scenarios;
   - a close step.

   Size the steps like the mission builder: each fits one session. Budget a fix pass inside the validation step: in
   the mission builder, validation found most of the defects.
4. **Present the plan** (ExitPlanMode).

## On approval ("accepting means saving")
- Save the approved plan as `docs/handoffs/fragorder-builder.md`.
- Add a Progress log line: "step 0: handoff written from the approved plan".
- Commit only that file and this planning handoff: `fragorder builder: step 0 handoff`.
- **Stop.** Do not start step 1: the user implements it in a fresh session ("Continue
  `docs/handoffs/fragorder-builder.md` from the first unchecked step").

## Starting points (pointers from the kit; verify before you rely on them)
- **sw.models physical** (mildb `sw.models/data/data/models/ada/physical/mildb/`, also `worldwide-tests/`):
  - `Missions.xml` section `<fragorders>`: 99 entries in mildb, 69 in wwt. Attributes include
    `available-without-mission`, `category`, `dia-type`, `id`, `name`. They share the id space with missions.
  - `Models.xml`: `<fragorder name>` children of a model's `<mission>`, and a model-level `<fragorders>`.
  - `Mission sheets/fragmentary orders/`.
  - `Locales/fr/Missions_fr.ts`: contexts `frag-order` and `frag-order - <mission name>`.
- **dia5** (`.../ada/decisional/dia5/`):
  - `FragOrder.lua` (~1430 lines): `fragOrderTaskInitializers`, `fragOrdersToBePropagated`,
    `fragOrder.executeAutomatFragOrder`.
  - Frago task folders: `common/agent/tasks/frago/`, `military/agent/tasks/frago/`,
    `security/agent/behaviors/frago/`, `crowd/behaviors/tasks/frago/`.
  - Frag orders handled inside skills, e.g. `military/agent/skills/ReactToCoordinationLine.xml`, Pause / Resume
    movement.
- **Indexes:**
  - `docs/sw.models/decisional_index.md` "Frag orders in `physical/<db>/Missions.xml`" (§7, per DB), and the task
    rows' "Frago" column (frag-order dia-types whose initializer starts the task).
  - `docs/sw.models/THEMES.md` row "Frag orders".
- **sw.sword:**
  - `src/libraries/simulation/Entities/Orders/MIL_FragOrder.cpp`, `MIL_FragOrderType.cpp`, `MIL_FragOrderParameter.cpp`.
  - `data/app-data/resources/integration/FragOrderIntegration.lua`.
  - `docs/sw.sword/THEMES.md` row "Frag orders" and the gotcha "Frag orders must be polled" (only task groups get them
    pushed).
  - `docs/sw.sword/integration_index.md`.
- **Checkpoints:** `docs/sw.models/CHECKPOINTS.md` §1 says the current **unit mission** is saved and the Lua VM is
  not. Whether a frag order in progress (or the task it started) survives a save + resume is **not established**:
  find out (sim `CheckPoints/`, `MissionController`, Spark saved state) before you plan anything checkpoint-related.

## Open questions to settle
1. **Scope.** Possible scopes:
   - unit frag orders only;
   - also automat frag orders and their propagation to subordinates (`fragOrdersToBePropagated`);
   - crowd frag orders;
   - `available-without-mission` frag orders vs frag orders tied to a mission (Models.xml children).

   Recommend a first scope; the mission builder deliberately excluded automats.
2. **Kinds of frag orders.** Classify the existing ones with counts, e.g.:
   - ones that start a task through an initializer;
   - ones handled inline by skills of the running mission;
   - ones with parameters;
   - mission-specific ones.

   The builder's procedure will differ per kind.
3. **Shape** (the user decides; give a recommendation). The mission builder's rule was "no extra agents, they only
   duplicate the kit". Options:
   - extend `sw-mission-builder` and its tools with frag-order support;
   - add a separate thin `sw-fragorder-builder` agent over shared tools.

   Same choice for the docs: a new `FRAGORDERS.md` vs extending `MISSIONS.md`. Same for `/mission-check`, the index
   (`mission_index.md` § vs new) and `mission_scaffold.py` (a frag-order mode vs a new tool).
4. **Write mode.** Is it the same as the mission builder (writes into sw.models on the user's branch, never commits,
   stops on master, sw.sword changes as proposals)? Confirm with the user.
5. **Checkpoints.** What is saved and lost when a frag order is active at save time? Which CHECKPOINTS.md hazards and
   sim-side gaps apply, and which are new (rows to add)?
6. **Checks.** What the ant targets catch for frag orders and what nothing catches. This mirrors MISSIONS.md §5.
   For example: a dia-type with no initializer, an initializer for an unknown dia-type, a frag order in Models.xml but
   not in Missions.xml, translations, sheets.
7. **Validation scenarios.** Propose them; the user amends. Analogues of the mission builder's ones:
   - a new frag order reusing an existing task;
   - a new frag order needing a new task and a stateful skill, checkpoint-safe;
   - a mission-specific frag order attached to an existing mission;
   - a duplicate request (the builder says so first);
   - a kit-only question answered without the builder.

## Lessons from the mission builder (put the relevant ones into the plan's Working rules)
- **Exploration.** Format facts were measured over every file: insertion order, attribute order, line endings,
  translations. ADN output was reproduced byte-exact: mission-sheet `.html` lines end with `\r\r\n`, and
  `tools/mission_scaffold.py` holds the renderer. Plan the same measurement for frag-order sheets and `.ts`
  contexts, and reuse the renderer.
- **Writing to scratch only.** Validation and tool tests write only to scratch copies of the model dir
  (`--root <copy>`), never into sw.models.
- **How validation is run.** Use general-purpose agents told to read and follow the agent file. Agent files load at
  session start, so the user must restart before using a new agent directly.
- **Builder procedure.**
  - Distinguish exact from near duplicates. Search duplicates by the skill a stage runs, not only by name.
  - Read the `integration.*` starter before calling a hazard "infrastructural": world state is saved, and some
    starters recover from it.
- **Windows file traps.**
  - Python on Windows writes CRLF in text mode: write with `newline=""` or in binary.
  - Bash heredocs mangle backslashes and stop at a line that is exactly the delimiter: put edit scripts in files.
- **House rules.**
  - Kit files hold paths and `path::anchor`, no line numbers.
  - `python -I tools/check_themes.py` must pass after every doc or agent step; register new files in its kits.
  - Indexes are grepped, never read whole. Commits are scoped (`git add <paths>`).
  - Never change git state in the sibling repos.
