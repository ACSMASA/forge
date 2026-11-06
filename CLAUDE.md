# forge

Work-support repo for SWORD behavior modeling: three read-only **navigator** agents, their kits (theme maps +
generated indexes) and the tools that build them. The repos they read are siblings in `C:/MASARepo/`:
**sw.models** (the user's DIA5 models; standing model `ada`, physical DBs `mildb` and `worldwide-tests`; client
models live on temporary branches), **sw.sword** (the sim kernel and its Lua integration layer), **directai** (the
Spark language and runtime; the user only consumes it), **sw.soult** (French-named sister of sw.models, not indexed).
Navigator subagents: your own prompt comes first; this file is mainly for the main session.

## Pinpoint check or navigator?
- **Inline (default for one identifier or one hop)**: does X exist, where is it defined, what calls it, what does
  this validator code mean, which skills take this resource… Use the pipeline below: one index grep, then read the
  code it points to. Aim for ≤ 5 tool calls; no spawn.
- **Spawn a navigator** for depth: multi-hop traces, explaining behavior that needs several function bodies,
  rename / impact scans, change proposals (their prompts carry the checklists), studies (ontology, resources).
  `sw-models-navigator` = model content · `sw-sword-navigator` = sim + integration layer · `dai-navigator` = Spark
  language and runtime (agent files in `.claude/agents/`; the user can also type /models, /sword, /dai).
- **Cross-repo**: navigators cannot call each other. Trace hop by hop inline, or spawn one navigator per repo, in
  parallel when the parts are independent, else chained, passing the exact names from the previous answer's Caveats.
- A medium task done inline: read only the matching step of that navigator's "How to work" and follow it.
- **Building a unit mission** (writes into sw.models on the user's branch, never commits): `sw-mission-builder` (/mission).
  Linting one, or "is this skill checkpoint-safe?": the inline `/mission-check` skill (mission_check + checkpoint_lint,
  explained from MISSIONS.md / CHECKPOINTS.md). Explaining an existing mission's flow stays with sw-models-navigator.

## The pipeline (sw.models → directai → sw.sword)
Kits: `docs/sw.models/`, `docs/sw.sword/`, `docs/directai/`. "Meaning" = which navigator owns the semantics.
Model `ada` roots (index paths are relative to them): Spark entities `sw.models/data/data/models/ada/decisional/dia5/`
(entity `a.b.C` = `a/b/C.xml` + `C.lua`; tasks have no Lua), physical DBs `sw.models/data/data/models/ada/physical/mildb/`
and `sw.models/data/data/models/ada/physical/worldwide-tests/`; integration Lua `sw.sword/data/app-data/resources/integration/`.

| Hop | Grep (index, section) | Then read | Meaning |
|---|---|---|---|
| Mission → task, per DB | `decisional_index.md` §7.mildb / §7.worldwide-tests | `Missions.xml` `dia-behavior` | models |
| Unit type → decisional model → role | §7 "Decisional models"; unit in `Units.xml` (`decisional-model`) | `Models.xml` | models |
| Role → body type, ontology type map | `ontology_index.md` §5, §6 | role XML / Lua | models (rules: dai) |
| Task → stages → skills, queries | `decisional_index.md` §1 | task XML | models (stages: dai) |
| Skill → links, feedbacks, resources, `integration.*` | `decisional_index.md` §2; `action_index.md` §1, §2 | skill XML + Lua | models (Activate, arbitration: dai) |
| Lua → world-class method | `ontology_index.md` §2 (callers by name = upper bounds) | class `.lua` | models |
| Method / skill → action framework | `action_index.md` §2, §3 | `spark.action` / `spark.startStopAction` | dai |
| `integration.*` → body, brain calls, kernel starters, callers, overrides | `integration_index.md` §1 (§2 model-side defs, §3 anomalies) | the Lua body | sword |
| `DEC_*` / `_DEC_*` → C++ | `dec_function_index.md` | registration in `DEC_*Functions.cpp` | sword |
| Kernel action started / stopped | `action_index.md` §4, `integration_index.md` "Kernel starters" | `StartAction<` registration | sword |
| `spark.*`, `sw.ai.*`, shared global; validator message | `dai_lua_index.md` §1–§3; §4 (code or message fragment) | directai spark plugin / validator | dai |
| Physical XML attribute meaning | no index: grep `attribute( "name"` in sw.sword `src/libraries` | the C++ reader | sword |
| Unit mission: anatomy / build / check | `docs/sw.models/MISSIONS.md` §1 files per DB, §2 parameters, §5 checks; `mission_index.md` §2; `tools/mission_check.py`, `tools/mission_scaffold.py` | Missions.xml, Models.xml, task XML | models |
| Frag order: anatomy / kinds / build / check | `docs/sw.models/FRAGORDERS.md` §1 files per DB, §2 kinds, §3 availability, §6 automats / crowds, §8 checks; `CHECKPOINTS.md` §7 save / resume fix pair; `mission_index.md` §6; `tools/mission_check.py` / `tools/checkpoint_lint.py` --fragorder, `tools/mission_scaffold.py` (`"fragorder": true`) | Missions.xml `<fragorders>`, Models.xml, `dia5/FragOrder.lua`, frago task XML | models (delivery: sword) |
| Checkpoint safety of a behavior | `docs/sw.models/CHECKPOINTS.md` §3 hazards, §4 conventions, §6 sim-side gaps; `tools/checkpoint_lint.py` | the skill Lua; `integration.*State` in `SimulationIntegration.lua` | models (sim side: sword) |
| A theme, no name yet | the kit's `THEMES.md` (FR/EN words, theme → files, gotchas) | the files of the row | kit owner |

## Grep recipes (Grep tool; rows start with a backticked name, so `.` stands for the backtick)
- Column names: append `|^\| [A-Z][A-Za-z /.*()-]* \|` to any row pattern to also get the section header rows.
- Long rows come back as `[Omitted long matching line]`: Read the index at that line (`offset`=N, `limit`=1).
- Negatives: an empty "Kernel starters" cell (integration index, `action_index.md` §2/§4) is computed through the
  body, Lua helpers and other `integration.*`, so it means no `StartAction<` is reached. A skill absent from
  `action_index.md` §2 has no `<resources>` and is not under `skills/actions/`; what its links start is a multi-hop trace.
- entity (task, skill, query, role, resource): `^\| .military.agent.tasks.AttackObjective. ` in `docs/sw.models/decisional_index.md`
- mission: `^\| mildb \| "COMBAT - Attack objective"` (same file); decisional model: `^\| mildb \| "Infantry platoon"`
- world-class method: `:moveTo. ` in `docs/sw.models/ontology_index.md`; class / interface: `^\| .common.agent.world.Agent. `
- resource or action skill: `^\| .common.agent.behaviors.resources.ActivationCapacity. ` in `docs/sw.models/action_index.md`
- integration function: `^\| .integration.isFlying. ` in `docs/sw.sword/integration_index.md`; which wrap a brain function: grep `_DEC_X` there
- brain function: `^\| ._DEC__StopAction. ` in `docs/sw.sword/dec_function_index.md`
- Spark: `^\| .spark.startStopAction. ` in `docs/directai/dai_lua_index.md`; validator: the code (`action.unexpected-field`) or a fixed fragment of the message
- "is there already something that does X?": grep words in `decisional_index.md` (entity descriptions) and in
  `integration_index.md` (LDoc summaries), with the French and English words from the kit's THEMES vocabulary
- no index: definition `integration\.foo *=` in `sw.sword/data/app-data/resources/integration/`;
  registration `"DEC_X"` in `sw.sword/src/libraries/simulation/`; spark `function spark\.X` in
  `directai/src/libraries/plugins/spark/`; validator messages and rules in `directai/src/libraries/spark/validation/`

## Trust rules
- Code and files are the only truth; indexes and THEMES only save searches. Cite `repo/path:line` you read.
- Never Read an index whole (90 KB–900 KB): Grep it, then Read the file it points to with offset/limit.
- Each index header gives `branch @ commit`. `python -I tools/kits.py` lists the stale ones; a stale index is a hint
  only (line numbers drift first): confirm by grep. Uncommitted edits in a repo are invisible to its indexes.
- Say which branch you read (`git -C C:/MASARepo/<repo> branch --show-current`); sw.models and sw.sword switch
  often. Never assume a client model or branch exists; read other branches with `git show <branch>:<path>` /
  `git grep`, never by checkout. Don't change git state in the sibling repos (checkout, pull, stash, reset)
  unless asked: they hold the user's work.
- Not sources of facts: `docs/sw.sword/archive/` (frozen 2026-11-04 prose, drifts) and `docs/handoffs/` (build
  records). Exclude them when grepping `docs/`.
- sw.soult: same layout as sw.models with French physical file names (`Pions.xml` = Units, `Composantes.xml` =
  Equipments, `Capteurs.xml` = Sensors, `Modeles.xml` = Models, `Automates.xml` = Automats, `Armements.xml` =
  WeaponSystems); model `sws`, DB `sw.soult/data/data/models/sws/physical/soult/`. Not indexed: grep.

## Maintaining the kits
- After a pull or a branch switch: `python -I tools/kits.py` (status) or `--build` (rebuild the stale indexes, then
  run the checker). Per-index commands and options (e.g. a client `--model`): each kit's `README.md`.
- `python -I tools/check_themes.py` must pass: it checks every path and `path::anchor` in the THEMES files, the
  agent files and this file.
- Navigator **Map updates**, or a gap found inline: verify the anchor, edit the kit's `THEMES.md` (paths and
  `path::anchor` only, no line numbers, no behavior prose), re-run the checker.
- Agent files load at session start: after editing one, restart Claude Code, or validate first with a
  general-purpose agent told to read the file and follow it.
- Tools: Python stdlib only, run with `python -I`. Commits: short, scoped (`git add <paths>`), in the style of
  `git log --oneline`; commit regenerated indexes only when built from the reference branches.
