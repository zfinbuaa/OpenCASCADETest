# ui-ux-fixes — Draft & Decisions

Status: plan written
Pending action: user runs `$start-work` to execute

## Exploration Summary

### Issues analyzed

1. **编组 persistence**: Root cause — `_restoreCompoundsToTree()` only called from `onPipelineComplete` handler, never during tab switching. `buildActiveTree()` creates fresh TreeView every time, wiping compound DOM. Fix: add restore call at end of `buildActiveTree()`.

2. **标注 label overlap**: Current code uses uniform vertical spacing regardless of target proximity. When two annotated parts project to similar screen Y, their horizontal lines overlap. Fix: add proximity-based offset in the column Y calculation loop.

3. **帮助 page**: Currently a 3-line native Electron dialog. User wants full page with README content. Decision: new Tab 5 ("帮助"). Replace menu "关于" with IPC to switch to tab.

4. **View presets**: Current diagonal views use 0.7 Y (low elevation). User wants consistent isometric with equal (1:1:1) weighting. Fix: change 4 diagonal vectors from `(*, 0.7, *)` to `(*, 1.0, *)`.

5. **Position-map grouping UI**: Only Tab 2 has "编组管理". User wants it in Tab 0 too, renamed to "标注管理" / "勾选部件→生成标注".

### Decisions
- Help as new Tab 5 (user confirmed)
- Grouping restore at buildActiveTree convergence point (covers all tab-switch paths)
- Annotation stagger: add extra 25px offset when adjacent targets within 40px screen-Y
- View presets: equal-weight (1:1:1) diagonal vectors, keep top/bottom with 0.001 offset
- Position-map UI: reuse same handlers, rename labels only
