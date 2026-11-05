---
name: mission-check
description: Lint a sw.models unit mission, task or skill in the working tree (mission_check + checkpoint_lint) and explain each finding with the forge kit rules (MISSIONS.md, CHECKPOINTS.md). Use when the user asks whether a mission is well formed, why a parameter is not passed, whether a mission ends, or whether a skill / task survives a checkpoint save + resume. Usage /mission-check <mission name | task | skill> [--root <sw.models copy>]
argument-hint: <"mission name" | task a.b.C | skill a.b.C> [--root <path>]
---

Lint and explain, inline (no subagent). Target: $ARGUMENTS

1. **Context** (one Bash call): `git -C C:/MASARepo/sw.models branch --show-current && git -C C:/MASARepo/sw.models rev-parse --short=10 HEAD && git -C C:/MASARepo/sw.models status --short -- data/data/models | head -5`. The tools read the working tree, so uncommitted edits count. With `--root`, lint that copy instead.
2. **Kind of target**: a name with spaces or a `PREFIX - ` form is a mission name; a dotted name is a task if `<task` is the root of its XML (`dia5/<a/b/C>.xml`), else a skill. If unsure, grep `C:/MASARepo/forge/docs/sw.models/mission_index.md` (§2 `^| mildb | "<name>"`, §3 `^| .<skill>. `).
3. **Run** from `C:/MASARepo/forge`:
   - mission: `python -I tools/mission_check.py --mission "<name>" -v [--root R]`, then `python -I tools/checkpoint_lint.py --task <its dia-behavior> [--root R]`;
   - task: `python -I tools/mission_check.py --task <task> -v [--root R]` and `python -I tools/checkpoint_lint.py --task <task> [--root R]`;
   - skill: `python -I tools/checkpoint_lint.py --skill <skill> [--root R]`.
4. **Explain** each ERROR / WARN in one or two lines: the rule and its anchor from `C:/MASARepo/forge/docs/sw.models/MISSIONS.md` (§1 files, §2 parameters and `NIL`, §3 task rules, §5 checks) or `C:/MASARepo/forge/docs/sw.models/CHECKPOINTS.md` (§3 hazard row: effect, model-side fix; §6 when it is a sim-side gap). Checkpoint findings are **upper bounds**: open the Lua at the reported line and say whether the hazard is real. Notes (`-v`) only when relevant (continuous missions, cross-DB id differences).
5. **Answer**: first line `sw.models <branch> @ <commit>` (or the root); a table finding → real? → fix (model-side, or sim-side gap); then the ant targets that would or would not catch the rest (`MISSIONS.md` §5). Do not edit files; for a fix, show the diff. Deeper work (a rewrite, a new mission) → suggest `/mission` (writes) or `/models` (read-only proposal).
