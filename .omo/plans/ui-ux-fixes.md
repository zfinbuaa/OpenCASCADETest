# ui-ux-fixes — Work Plan

## TL;DR (For humans)

**What you'll get:** Four frontend UX fixes: (1) 编组 (grouping) results persist across all tabs and appear in every page's structure tree; (2) 标注 (annotation) labels get staggered vertical offsets to prevent horizontal line overlap, plus 位置图 tab gains grouping management UI; (3) a new "帮助" tab with detailed feature documentation from README.md; (4) six view-preset angles correctly focus on the assembly center from proper elevated positions.

**Why this approach:** All fixes are in the frontend layer (js/main.js, js/annotation.js, js/scene-manager.js, index.html) — no backend changes. Each fix addresses a single UX problem with minimal code change. The grouping fix targets the tree-rebuild convergence point (buildActiveTree) so it resolves all tab-switch scenarios at once.

**What it will NOT do:** Change any backend pipeline logic, modify the 3D rendering engine, add new API endpoints, or affect the existing code-audit-fix plan.

**Effort:** Medium
**Risk:** Low — all changes are UI-only, no data loss risks
**Decisions to sanity-check:** The grouping restore in buildActiveTree() affects all tabs simultaneously — every tab will now show compounds in its tree. This is the desired behavior.

Your next move: run `$start-work` to execute. Full execution detail follows below.

---

> TL;DR (machine): Medium, Low risk, 7 tasks across 4 JS files + index.html, 3 parallel waves

## Scope
### Must have
- Fix grouping persistence: call _restoreCompoundsToTree() in buildActiveTree() so compounds survive tab switches
- Update #compound-preview text on tab switch so the preview reflects current state
- Add "勾选部件→生成标注" grouping UI to Tab 0 (位置图), identical to Tab 2's "编组管理" but renamed
- Add vertical staggering to annotation horizontal lines to prevent overlap when targets are at similar heights
- Add Tab 5: "帮助" page with detailed feature documentation sourced from README.md
- Fix 6 view presets: use equal-weight diagonal vectors (1:1:1) for isometric views, fix center focus
- Remove the old Electron native "关于" dialog and wire the menu to switch to the new help tab

### Must NOT have (guardrails, anti-slop, scope boundaries)
- NO backend Python changes (pipeline/, pipeline.py untouched)
- NO changes to the annotation data model or compound data structure
- NO changes to build scripts, package.json, or Electron main.js (except wiring menu to IPC)
- NO CSS/theme changes beyond what's needed for the new help tab
- NO changes to the existing 5 tabs' core functionality

## Verification strategy
- Test decision: tests-after (manual code review + grep assertions for JS)
- Evidence: .omo/evidence/task-<N>-ui-ux-fixes.<ext>

## Execution strategy
### Parallel execution waves
- Wave 1 (parallel): T1 (group persistence), T2 (annotation stagger), T3 (view presets), T4 (add help tab HTML/CSS)
- Wave 2 (parallel): T5 (position-map grouping UI)
- Wave 3: T6 (verify all changes), T7 (commit)

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | — | — | 2, 3, 4 |
| 2 | — | — | 1, 3, 4 |
| 3 | — | — | 1, 2, 4 |
| 4 | — | — | 1, 2, 3 |
| 5 | — | — | — (only file: main.js) |
| 6 | 1,2,3,4,5 | 7 | — |
| 7 | 6 | — | — |

## Todos

- [x] 1. js/main.js: Fix grouping persistence across tabs via buildActiveTree()
  What to do: At the end of buildActiveTree() (after line ~981), add _restoreCompoundsToTree() and _updateCompoundPreview(). This ensures every tab-switch-triggered tree rebuild restores compounds from shared.compounds.
  Parallelization: Wave 1 | Blocked by: — | Blocks: — |
  References: js/main.js:759 (_restoreCompoundsToTree), js/main.js:742 (_updateCompoundPreview), js/main.js:889-981 (buildActiveTree), js/main.js:56-57 (shared.compounds).
  Acceptance criteria: grep -c "_restoreCompoundsToTree" js/main.js shows >= 2 occurrences. Called in both onPipelineComplete and buildActiveTree.
  QA scenarios: Happy: create compounds in Tab 2, switch to Tab 1, switch back — compounds still visible. Also visible in Tabs 0, 3 when switched to.
  Commit: Y | fix(main): persist grouping across all tabs via buildActiveTree restore

- [x] 2. js/annotation.js: Add vertical staggering to annotation horizontal lines
  What to do: In the draw() method's label placement (lines 148-160), add logic to detect when two consecutive labels on the same column have target screen Y within 40px, then offset the second label's cy by +25px upward to prevent horizontal line overlap.
  Parallelization: Wave 1 | Blocked by: — | Blocks: — |
  References: js/annotation.js:148-160 (current Y calculation). New logic: track prevTargetY for each column; if |currentTargetY - prevTargetY| < 40, add staggerOffset of 25px.
  Acceptance criteria: node -c js/annotation.js passes.
  QA scenarios: Two parts at nearly same 3D height produce labels with different Y positions, horizontal lines don't overlap.
  Commit: Y | fix(annotation): stagger labels vertically to prevent horizontal line overlap

- [x] 3. js/scene-manager.js: Fix 6 view preset angles
  What to do: Change 4 diagonal view vectors from (dx, 0.7, dz) to (dx, 1.0, dz) for equal-weight isometric elevation. Keep viewTop as (0, 1, 0.001), viewBottom as (0, -1, 0.001).
  Parallelization: Wave 1 | Blocked by: — | Blocks: — |
  References: js/scene-manager.js:117-122. New: viewLeftRear(-1,1,-1), viewLeftFront(-1,1,1), viewRightRear(1,1,-1), viewRightFront(1,1,1).
  Acceptance criteria: grep "0.7" js/scene-manager.js returns zero matches in view methods. node -c passes.
  QA scenarios: After model load, view presets position camera at correct isometric angles.
  Commit: Y | fix(scene-manager): use equal-weight isometric view angles for presets

- [x] 4. index.html + js/main.js + main.js: Add "帮助" tab with detailed documentation
  What to do: (a) Add 6th tab button in index.html; (b) Add tabs entry + switchTab title + renderPanel dispatch in js/main.js; (c) Create renderHelpPanel() with content from README.md (system architecture, 5 feature pages, BOM, collision, build, output format) in collapsible sections; (d) In main.js, change "关于" to send IPC 'menu-show-help'; (e) In js/main.js, listen for menu-show-help and call switchTab(5).
  Parallelization: Wave 1 | Blocked by: — | Blocks: — |
  References: index.html:129-135, js/main.js:61-67,78-93,96-105, main.js:179-195, main.js:1401-1411, README.md:1-373.
  Acceptance criteria: grep 'data-tab="5"' index.html matches. grep "renderHelpPanel" js/main.js matches. grep "menu-show-help" main.js matches. No more dialog.showMessageBox in help menu.
  QA scenarios: Clicking "帮助" tab shows documentation. Menu → 帮助 → 关于 switches to help tab.
  Commit: Y | feat(help): add help tab with detailed documentation, replace about dialog

- [x] 5. js/main.js: Add "勾选部件→生成标注" grouping UI to Tab 0 (位置图)
  What to do: In renderPositionPanel(), add a section identical to Tab 2's "编组管理" but renamed: title "标注管理", button "勾选部件 → 生成标注", button "清空标注". In bindPositionPanel(), bind same compound handlers. Do NOT duplicate functions.
  Parallelization: Wave 2 | Blocked by: — | Blocks: — |
  References: js/main.js:179-211 (renderPositionPanel), js/main.js:481-561 (bindPositionPanel), js/main.js:250-259 (Tab 2 compound UI template).
  Acceptance criteria: grep "勾选部件.*生成标注" js/main.js matches. grep "标注管理" js/main.js matches.
  QA scenarios: On Tab 0, check parts in tree, click "勾选部件→生成标注", annotations appear in 3D view.
  Commit: Y | feat(position-map): add annotation grouping UI to position map tab

- [x] 6. Verify all changes: syntax checks, no regressions
  What to do: node -c on all modified JS files. Verify index.html tag balance. Grep for leftover old patterns.
  Parallelization: Wave 3 | Blocked by: 1,2,3,4,5 | Blocks: 7 |
  Acceptance criteria: All node -c pass, no leftover old pattern matches.
  QA scenarios: All syntax checks pass.
  Commit: N |

- [x] 7. Commit all fixes
  What to do: Stage all changed files (T1-T5), commit with message: fix: UX improvements — grouping persistence, annotation staggering, help tab, view presets, position-map grouping UI.
  Parallelization: Wave 3 | Blocked by: 6 | Blocks: — |
  Acceptance criteria: git diff --cached --stat shows only expected files. git log -1 --oneline shows correct message.
  Commit: Y | fix: UX improvements — grouping persistence, annotation staggering, help tab, view presets, position-map grouping UI

## Final verification wave
- [x] F1. Plan compliance audit
- [x] F2. Code quality review
- [x] F3. Scope fidelity — git diff HEAD~1 --stat
- [x] F4. Cross-reference — compounds visible in all tabs, help content complete

## Commit strategy
Single atomic commit at task 7. One commit message.

## Success criteria
1. All 7 todos completed with acceptance criteria met
2. Compounds persist across all 5 tabs
3. Annotation labels staggered, no line overlap
4. Help tab displays comprehensive documentation
5. View presets use correct isometric angles
6. Position map tab has annotation grouping UI
