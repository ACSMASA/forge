# Handoff: build the directai navigator

> **DONE 2026-11-05.** Built as `dai-navigator` + `/dai` (user only consumes DAI, so no change proposals).
> Kit: `docs/directai/`, `tools/build_dai_index.py`, generalized `tools/check_themes.py`. Validated on 4 questions; map updates applied.

> **How to use:** start a Claude Code session in `C:\MASARepo\forge` and say
> "Follow `docs/handoffs/directai-navigator.md`." Effort: high is enough.
> Written 2026-11-05 after the sw.sword navigator was built and then reworked to code-first.

## Goal
Build a lean, code-first navigator agent for the DirectAI repo (`C:\MASARepo\directai`), following the pattern
already used for sw.sword in this repo.

## Why this shape (lessons from sw.sword)
- The first sw.sword build started with a long prose study (a hub plus five deep dives, ~3,000 lines). During
  validation the agents went straight to the code for every claim. Two prose claims turned out wrong. The prose
  is now frozen in `docs/sw.sword/archive/`.
- What kept its value: a **generated index**, a **French↔English vocabulary**, a **theme → files map** (saves the
  first few searches), and a short list of **traps you wouldn't think to search for**, each anchored to code.
  A **checker** confirms every path and anchor still exists, so drift shows up as a failed check instead of a wrong answer.
- The agent must stay **light on spawn**: nothing loaded up front that a question may not need.

## Copy the existing pattern
Read these first; they are the template:
- `.claude/agents/sw-sword-navigator.md`: the agent (code-first, read-only, answer format, "Map updates")
- `docs/sw.sword/THEMES.md`, `docs/sw.sword/README.md`: the knowledge-base shape (vocabulary, theme → files table,
  anchored gotchas, `path::text` anchors, no line numbers, no descriptions of behavior)
- `tools/check_themes.py`, `tools/build_dec_index.py`: the checker and the generated index
- `.claude/skills/sword/SKILL.md`: the skill that forks a question into the agent
Do not write long prose docs. The code is the only source of truth.

## Context (surveyed 2026-11-05; re-check)
- The user is a SWORD behavior designer: they write DIA5/Spark models (skills, tasks, `.bms`, Lua) in
  `C:\MASARepo\sw.models` (standing model `ada`) and `C:\MASARepo\sw.soult`, on top of the sw.sword integration layer.
  They need DirectAI to understand how their models are parsed, validated and executed, and why they fail.
- sw.sword links against DirectAI (`sword::Brain` wraps `directai::brain::Brain`). sw.sword pins the DAI version
  (CI commits "bump dai version to 'X.Y'"); find where, and make the agent compare that pin with the directai branch it reads.
- directai has about 3.3k tracked files. These are out of scope:
  - `src/applications/ide` (Java Eclipse IDE, ~1.26k files)
  - `src/applications/demo` (Unity, ~880)
  - most of `data/tests`
- The core is about 250 files:
  - `src/libraries/directai` (brain, Lua VM, C API, tools)
  - `src/libraries/plugins` (spark, devtools, eventmanager, motivation)
  - `src/libraries/spark` (common, parsing, validation)
  - `data/sparkle`
  - `etc/schemas/spark` (XSD)
  - `src/applications/sparkcheck` (model validator)
- The repo has its own `README.md` (342 lines) and `ARCHITECTURE.md` (313 lines). Use them for orientation, check
  claims against the code, and link to them from THEMES.md instead of copying them.
- Branches: `master`, release branches `5.x`. Never assume a version number; read it at answer time.

## Steps
1. **Ask the user first** (one AskUserQuestion):
   - Do they ever change directai, or only consume it? This decides whether the agent drafts change proposals.
   - Agent and skill names (proposed: `directai-navigator`, `/dai`).
   - 3–4 real questions they would ask, to use for validation.
2. **Survey** (yourself, no parallel readers; the core is small):
   - the Spark XML language and `.bms` files
   - the spark Lua runtime API
   - the brain execution model (messages, task/skill lifecycle, scheduling)
   - validation and `sparkcheck` errors
   - debugging (devtools, HTTP/DBGp)
   - the C++ API sw.sword uses
3. **Generated index**: pick the most useful one after the survey. Likely candidate: every Lua-visible name the
   spark runtime defines → file:line → usage counts in sw.sword `data/app-data/resources/integration`, sw.models and
   sw.soult Lua. Write `tools/build_<name>_index.py` (stdlib only, run with `python -I`), output to `docs/directai/`.
4. **`docs/directai/THEMES.md` + `README.md`**, same shape as the sw.sword ones. Each gotcha must be verified in
   code and carry a `path::text` anchor.
5. **Generalize `tools/check_themes.py`**: one config per repo (root + path aliases). With no arguments it checks
   every kit (sw.sword's THEMES and agent, and directai's). It must still pass on sw.sword.
6. **Agent** `.claude/agents/directai-navigator.md`, adapted from the sw.sword one: paths, version/pin check,
   answer format with "Map updates". The description must make the boundary with sw-sword-navigator clear
   (sw.sword = the sim and its integration layer; directai = the Spark language and runtime), because subagents
   can't call each other and the main session routes. Add the skill.
7. **Validate**: the user's questions, run as general-purpose agents told to read the agent file and follow it
   (new or changed agent definitions only load at session start). Ask each for run stats (tool calls, docs
   opened). Check the citations yourself, apply verified map updates, re-run the checker.
8. **Cross-link and close**:
   - In `docs/sw.sword/THEMES.md`, point the "DirectAI / spark internals" row at the new kit.
   - In `sw-sword-navigator.md`, tell it to flag Spark-runtime questions as better suited to the directai navigator.
   - Update the forge memory.
   - Mark this handoff as done at its top.
   - Commit (Conventional style, short messages).
