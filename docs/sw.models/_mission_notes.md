# Mission builder: working notes (deleted at step 11)

Read at: sw.models `master` @ `d2f78fafcd`, sw.sword `master` @ `51bdc0ec9e`, directai `master` @ `9629aaf1a1`.
Aliases as in `docs/handoffs/mission-builder.md` (`phy/` = mildb, `wwt/`, `dia5/`, `sim/`, `int/`, `spark/`, `val/`).

## 1. Format questions (step 1)

### 1.1 Insertion order
Measured with ElementTree over both DBs; "sorted" = Python ordinal sort (case-sensitive, codepoint order).
- **Missions.xml `<units>`:** no order (neither id nor name). New missions are **appended at the end of `<units>`**.
  Evidence: the last 15 ids of each DB are the newest; `git show 3a63ffeebb` (AIR - Assault, 2026-10-05) inserts the
  new mission at the end and ADN renumbered the previous last one (445949726 → 445949727).
  - Attribute order is alphabetical on `<mission>` and `<parameter>` (`category dia-behavior dia-type id name`;
    `dia-name is-context max-occurs min-occurs min-value name optional type`). 2-space indent,
    `<?xml version="1.0" encoding="UTF-8" standalone="no" ?>`; `checkxml` (sw.tools binary, `build/build.xml:451`)
    enforces ADN normalization.
- **Models.xml unit `<missions>`:** unsorted in 90/94 mildb and 89/107 wwt models (ADN keeps load order). Rule for
  the scaffold: if the unit's list is already sorted, insert at the sorted position (what 3a63ffeebb did for
  "Navy strike fighters"); otherwise append at the end.
- **Stages.xml:** `<stage key value>` with key == value (0 exceptions), sorted ordinal by key in both DBs → insert
  sorted. `Stages_fr.ts`: one context `stage`, messages sorted by source → insert sorted.
- **Missions_fr.ts** (mildb only; wwt has no `Locales/`): contexts sorted ordinal by name (430), messages inside every
  context sorted ordinal by source → insert sorted. Exact content rule, verified on 247 missions (0 missing, 0 extra):
  - context `unit-missions`: one message per unit mission name;
  - context `unit - <mission name>`: every **non-context** parameter `name` + every `<value name>` of its
    enumerations; the context is absent when that set is empty (9 missions);
  - the 4 context parameters are translated once in context `unit-context`;
  - a new category goes in `mission-categories`.
  - `<translation>` has no attributes (never `type="unfinished"`).

### 1.2 Mission sheets
- `.html` = `phy/missionSheets.xslt` applied to the sheet `.xml` (`phy/physical.xml:59` `mission-sheets-xsl`). The
  XSLT is 154 lines, purely structural (context, parameters split Mandatory/Optional by `@optional`, behavior,
  specific-cases, comments, mission-end; `text`, `line`→`<br>`, `ul`/`li`, `bold`/`italic`/`underlined`, `link`→`<img>`)
  → reimplement in stdlib Python and compare byte-for-byte with an existing pair (`phy/Mission sheets/units/AIR -
  Assault.html`). Output quirks to mirror: `<META http-equiv ...>` line, `<h1 align="center">Mission : \n
  <name></h1>` inside `<head>`, `<br>` after each line, css href `./../missionSheets.css` (en) and
  `./../../../missionSheets.css` (fr). Headers stay English in fr sheets.
- **Coverage:** mildb 247/247 en pairs + 495 fr files (`Locales/fr/<French name>.xml/.html`, French name = the
  `unit-missions` translation; 247/247 match). wwt 286/286 en pairs, **no fr sheets**.
- **Checked by:** `checkdec sheets` (fatal, `build/build.xml:395`): en `.xml`/`.html` pairs and mission ↔ sheet
  name, per DB (`sw.models/src/checkdec/checkdec.go:77` `checkMissionsHaveSheets`, `src/models/phydb.go:199`
  `ListAllMissionSheets`, top-level `units` dir only). **fr sheets are not checked.**

### 1.3 Ids
- ADN: `ADN_Missions_ABC.cpp:61,70` new mission → `idManager_.GetNextId()`, `:86` `Lock(id)` on load,
  `ADN_Missions_Data.cpp:760` renumbers duplicates. `tools::IdManager` lives in sw.tools (not local): next-id rule
  unknown, observed behavior = file max + 1.
- The DBs diverged: 209 unit ids shared, 42 of them with different names; same name with different ids exists too
  (`ENGINEER - Breach obstacles` 445949354 / 445949416). 75 unit missions are mildb-only, 114 wwt-only. Recent
  mildb missions were added to mildb only (3a63ffeebb, e18bdeabe2, 999f3365fa touch no wwt Missions.xml).
- Uniqueness is per DB (`sim/Entities/Orders/MIL_MissionType_ABC.h`, `sw.models/src/models/phydb.go:132`), across
  all sections of the file (units, automats, populations, fragorders share the id space).
- **Convention adopted:** id = max(every `id` attribute in both DBs' Missions.xml, excluding enum `<value id>`) + 1,
  the same id in every DB the mission goes to. Today: 545949904. It keeps new ids globally unique and avoids adding
  to the 42 "same id, different name" pairs. Enum `<value id>` (0, 1, 2…) are local to the parameter.

### 1.4 Optional parameter absent → task `<default>`? **Depends on the converter** (corrected at step 4, refined at step 5)
- `sim/Decision/DEC_Decision.cpp` `RegisterMissionParameterBMVisitor::Accept`: when the converter returns false it
  stores `brain.GetScriptRef("NIL")` = global lookup (`sim/Decision/Brain.cpp:96-99`); `NIL` is a **shared global
  sentinel table**, `spark/compatibility.lua:13` `setsharedglobal( "NIL", setmetatable( {}, … ) )`.
- Converters differ (measured by `tools/missionlib.py` `SwordTypes.absent_is_nil`): Boolean, DateTime/GDH, Distance(List),
  Enumeration, Integer(List), Numeric(List), ResourceType, Speed(List), String(List) are `XFunction(...); return true;`
  → an absent value is **never set → nil** → `spark/parameters.lua:211` applies `<default>`. All object converters
  (Agent, AgentKnowledge, Point, Polygon, Path, LocationComposite, ObjectKnowledge, lists of them…) return false when
  empty → **`NIL`** → the default is skipped; `spark/parameters.lua:156-157` warns (type checking only) when `NIL`
  reaches a non-`nullable` parameter.
- A type with **no BM converter** is never stored → plain nil → the default applies.
- Models: 206 `==/~= NIL` tests in dia5 Lua, 302 XML files use `<NIL/>`, 280 task params are `nullable`.
- Checkpoint side: `NIL` persisted in a skill state serializes as `{}` and reloads as an empty table, not `NIL`.

### 1.5 Lua string hash and `pairs` order
- directai links a **prebuilt Lua 5.2.4** (`directai/include/lua/lua.h:18-22`, `directai/build/CMakeLists.txt:54`
  `lua-${platform}-mt-5_2_4`). `include/lua/luaconf.h` does not define `luai_makeseed`, so stock 5.2.4 applies:
  `lstate.c` `makeseed` mixes addresses and `time(NULL)` → **string hash seed randomized per lua_State** (stock
  behavior; the library source is not local, so an override in the binary cannot be excluded).
- `int/SimulationIntegration.lua:768-787` `getPersistentKey`: a table key is the concatenation over `pairs( value )`,
  **unsorted**. With ≥ 2 keys, order depends on the seed and on insertion history → the key computed after a resume
  can differ from the saved one → `getSkillState` returns an empty state.
- Conclusion for the kit: treat `getPersistentKey` order as **unstable across save/resume** (hazard row + sim-side
  gap: sort keys). Single-parameter skills are safe. Confirm by the user's checkpoint test when it matters.

### 1.6 Which sw.sword version a sw.models branch targets; frozen?
- `sw.models/build/build.xml:13` `version.major` (6.29.0 on master) = `sw.sword/build/build.xml:41` `version.major`;
  CI bumps both together ("ci: create dev version '6.29.0'", sw.models e698c45d82, sw.sword f9aae1edca). Every
  physical XML also carries it in `xsi:noNamespaceSchemaLocation="schemas/6.29.0/..."`.
- `ada/decisional/decisional.xml` `model-version="6.24"` is **not** the version (stale).
- Releases: tags `vX.Y.Z` (sw.models up to v6.28.1, sw.sword up to v6.27.2 locally) and release branches
  `origin/X.Y` (only 6.23/6.24 fetched locally in sw.sword).
- **Rule for the builder:** read `version.major` of the working branch with `git -C sw.models show
  HEAD:build/build.xml`; compare with sw.sword `master`'s. Equal → sim-side changes can land on sw.sword master (not
  frozen). Lower → the branch targets a released line: a sim-side fix needs a sw.sword release-branch change →
  **treat as frozen and ask the user**; recommend the Lua-side fix.
- directai pin: sw.sword consumes directai as a prebuilt dependency (`sw.sword/build/build.xml:1385`
  `update-gh name="directai"`); where the exact pin is recorded was not read (`.env` is off limits). Not needed by
  the builder: directai changes are out of scope.

## 8. Scaffold findings (step 8)
- **Sheet `.html` serializer** (measured, 780/780 byte-exact with `tools/mission_scaffold.py --check-html`): libxslt
  HTML output of `phy/missionSheets.xslt`; formatting and text newlines written `\r\r\n`, newlines inside the
  `disable-output-escaping` `<img>` text written `\r\n`; non-ASCII → HTML 4 named entities (`&eacute;`), else `&#N;`;
  newline after a start tag when the first child is an element, except `<td>` + inline child (`b i u br img`);
  newline before an end tag when the last child is an element; newline between sibling elements except `</td><td>`.
  The `<img src>` folder is historical per sheet (old mission names, `./../../Images/<en name>/` in fr sheets), so the
  self-test reads it from the existing `.html`; new sheets default to `./Images/<name>/`.
- **Sheets:** every sheet XML root is `<mission-sheet model-version="4.8.2" name … xsi:noNamespaceSchemaLocation=
  "schemas/6.29.0/physical/missionSheet.xsd">` (version = the DB's Missions.xml); empty sections are self-closing
  (`<attachments/>` 667×, `<comments/>` 592×…); a parameter without text is self-closing (1914×). Context parameter
  texts are standard (en "Expected enemy direction." 237×…, fr "Direction supposée de la menace." 236×); fr sheets
  use translated parameter names (`unit-context` for the context, `unit - <name>` for the others).
- **Missions.xml:** `<categories>` is unsorted (append). Parameter children seen: `objects` (1008), `choice`+`objects`
  (584), `value`* (Enumeration), `units`, `transmitters`, `equipments`. `<objects>` is on every LocationComposite,
  ObjectKnowledge and PlannedWork parameter (default `<objects all="true"/>`). No `&apos;` / `&quot;` in any physical
  file or sheet.
- **Models.xml:** `<missions/>` exists (2 models per DB); `<mission name/>` self-closing when no frag order.
- **Validation (scratch copy of `ada`, sw.models `master` @ `d2f78fafcd`):** spec (a) reusing AttackObjective with
  another dia-type and parameter set, spec (b) new task skeleton + new category + enum/list/boolean/distance + 2 new
  stages: `mission_check --mission` 0/0 for both; `--all` on the copy = the same 22 errors / 12 warnings as the real
  tree; 786/786 sheets re-render; touched XML/.ts well-formed; bad spec → 12 errors listed at once; `--write` refused
  on a `master` checkout.
