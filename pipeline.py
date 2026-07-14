#!/usr/bin/env python3
"""
整车数模自动拆装方案生成管线

用法:
  python pipeline.py input.stp --output-dir ./output/
  python pipeline.py assembly.json --validate --output-dir ./output/

产出:
  output/
  ├── parts/*.glb        # 每个零件一个 glb 文件
  ├── assembly.json       # 装配结构与拆装方案
  └── report.txt          # 碰撞验证报告
"""

import argparse
import os
import sys
import json
import time
import signal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_cancelled = False


def _signal_handler(signum, frame):
    global _cancelled
    _cancelled = True
    log("CANCEL: received signal {}, setting cancellation flag".format(signum))


def is_cancelled():
    return _cancelled


def log(msg):
    """Print with flush for live progress."""
    print(msg, flush=True)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    try:
        signal.signal(signal.SIGINT, _signal_handler)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _signal_handler)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _signal_handler)
    except (ValueError, OSError):
        pass

    import pipeline as _pipeline_pkg
    _pipeline_pkg.set_cancel_check(is_cancelled)

    parser = argparse.ArgumentParser(
        description="整车数模自动拆装方案生成管线"
    )
    parser.add_argument("input", help="STEP (.stp) 或 assembly.json (配合 --validate)")
    parser.add_argument("--output-dir", default="./output")
    parser.add_argument("--mesh-deflection", type=float, default=1.0)
    parser.add_argument("--explosion-distance", type=float, default=100.0)
    parser.add_argument("--skip-collision", action="store_true")
    parser.add_argument("--preview", action="store_true",
                        help="仅导入 STP → 网格化 → 导出 glb + JSON (跳过分析)")
    parser.add_argument("--validate", action="store_true",
                        help="仅对已有 assembly.json 运行碰撞验证")
    parser.add_argument("--export-body", action="store_true",
                        help="将 STP 转换为单个车壳 .glb（不拆分零件）")
    parser.add_argument("--root-node", default=None,
                        help="仅处理指定子装配节点下的零件（层级选择）")
    parser.add_argument("--bom", default=None,
                        help="BOM Excel (.xlsx) 文件路径，启用多文件加载模式")
    parser.add_argument("--models-dir", default=None,
                        help="BOM 模式下 STP 文件所在目录（默认同 BOM 文件目录）")
    parser.add_argument("--target-part", default=None,
                        help="仅计算指定零件的拆卸依赖链（依赖链模式）")
    parser.add_argument("--no-optimize-dir", action="store_true",
                        help="依赖链模式下关闭方向最优化（使用旧的单方向递归算法）")
    parser.add_argument("--center-part", default=None,
                        help="爆炸视图模式 (--skip-collision)：指定中心固定零件名 "
                             "(leaf 或 sub-assembly 节点)。未指定则使用几何重心。")
    parser.add_argument("--compounds", default=None,
                        help="JSON encoded list of compound definitions: "
                             '[{"name":"C1","members":["p1","p2"]},...]')
    parser.add_argument("--interference-tolerance", type=float, default=0.0,
                        help="Ignore penetrations up to this distance (mm) "
                             "during collision check. Useful for assemblies "
                             "with small geometric interferences.")
    parser.add_argument("--clean", action="store_true",
                        help="数模清洗模式：按 BOM J列匹配 + 干涉检查 + 去重清洗模型")
    parser.add_argument("--clean-bom", default=None,
                        help="清洗模式下的 XLSX 文件路径")
    parser.add_argument("--export-step", action="store_true",
                        help="清洗模式下同时导出清洗后的 STEP 文件")
    parser.add_argument("--diag", action="store_true",
                        help="装配树诊断模式：输出层级误判报告 (STP only)")
    parser.add_argument("--pmi", action="store_true",
                        help="PMI诊断模式：输出PMI标注内容及关联零件 (STP only)")
    parser.add_argument("--compare", default=None,
                        help="数模对比模式：Excel(.xlsx)路径，Sheet3的A/B列为待对比模型代号")
    parser.add_argument("--diff-glb", action="store_true",
                        help="对比模式：同时生成差异颜色模型 GLB（绿=一致，黄=微小，红=显著）")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    # ── Compare mode: model comparison ──
    if args.compare:
        return _run_compare(args)

    # ── PMI mode: annotation diagnosis ──
    if args.pmi:
        return _run_pmi(args)

    # ── Diag mode: assembly tree diagnosis ──
    if args.diag:
        return _run_diag(args)

    # ── Clean mode: model cleaning ──
    if args.clean:
        return _run_clean(args)

    # ── BOM mode: multi-file loading from Excel BOM ──
    if args.bom:
        if args.preview:
            return _run_bom_preview(args)
        else:
            return _run_bom_full(args)

    # ── Body export mode: STP → single .glb body shell ──
    if args.export_body:
        return _run_body_export(args)

    # ── Preview mode: STP → glb only, no analysis ──
    if args.preview:
        return _run_preview(args)

    # ── Validate mode: collision only on existing output ──
    if args.validate:
        return _run_validate(args)

    # ── Full import pipeline ──
    parts_dir = os.path.join(args.output_dir, "parts")
    os.makedirs(parts_dir, exist_ok=True)

    from pipeline.stp_reader import read_stp_with_doc, verify_doc
    from pipeline.xcaf_utils import extract_assembly_tree, flatten_assembly_tree
    from pipeline.gltf_exporter import export_assembly_indexed
    from pipeline.contact_detector import detect_contacts
    from pipeline.fastener_identifier import identify_fasteners
    from pipeline.dag_builder import build_disassembly_dag_v2
    from pipeline.direction_calc import compute_all_directions
    from pipeline.assembly_json import build_assembly_json, write_assembly_json

    t_total = time.time()

    # Step 1
    log("[1/8] Reading STEP: {}".format(args.input))
    t0 = time.time()
    doc = read_stp_with_doc(args.input)
    log("  Read in {:.1f}s".format(time.time() - t0))
    summary = verify_doc(doc, filepath=args.input)
    log("  Root shapes: {}".format(summary["root_count"]))
    if not summary["valid"]:
        log("ERROR: No valid shapes found")
        return 1
    unit_scale = summary.get("unit_scale_to_mm", 1.0)
    if unit_scale != 1.0:
        log("  WARNING: STEP file unit scale = {} (not mm); values are in file units, not mm".format(unit_scale))

    # Step 2
    log("[2/8] Extracting assembly tree...")
    t0 = time.time()
    roots = extract_assembly_tree(doc)
    parts, sub_assemblies = flatten_assembly_tree(roots)
    if args.root_node:
        from pipeline.xcaf_utils import filter_parts_by_ancestor
        filtered = filter_parts_by_ancestor(parts, args.root_node)
        log("  {} leaf parts, {} sub-assemblies → {} under '{}' ({:.1f}s)".format(
            len(parts), len(sub_assemblies), len(filtered),
            args.root_node, time.time() - t0))
        parts = filtered
    if len(parts) == 0:
        log("ERROR: No parts found")
        return 1

    # Step 3
    log("[3/8] Meshing + exporting glb (deflection={}mm)...".format(args.mesh_deflection))
    t0 = time.time()
    parts = export_assembly_indexed(parts, parts_dir,
                                     linear_deflection=args.mesh_deflection)
    log("  {} glb files written ({:.1f}s)".format(len(parts), time.time() - t0))

    if args.compounds:
        from pipeline.compound_utils import apply_compounds
        try:
            compounds_def = json.loads(args.compounds)
        except Exception:
            log("  WARNING: failed to parse --compounds JSON, ignoring")
            compounds_def = []
        if compounds_def:
            log("  Applying {} compound definitions...".format(len(compounds_def)))
            parts, compound_info = apply_compounds(
                parts, compounds_def, args.output_dir,
                linear_deflection=args.mesh_deflection)
            log("  {} parts after compounding ({} compounds)".format(
                len(parts), len(compound_info)))

    # Pre-compute collision mesh data (shared for contact filter + DAG)
    from pipeline.collision_check import prepare_collision_data
    from pipeline.direction_calc import _compute_assembly_centroid, _compute_centroids

    log("  Pre-computing mesh collision data...")
    t_mesh = time.time()
    collision_data = prepare_collision_data(parts)
    log("  {} meshes ready ({:.1f}s)".format(len(collision_data), time.time() - t_mesh))

    # Step 4
    log("[4/8] Detecting contacts ({} pairs)...".format(len(parts) * (len(parts) - 1) // 2))
    t0 = time.time()
    contacts = detect_contacts(parts, intra_parent_only=True,
                               collision_data=collision_data, parallel=True)
    log("  {} contact pairs ({:.1f}s)".format(len(contacts), time.time() - t0))

    # Step 5
    fasteners = identify_fasteners(parts, contacts)
    if fasteners:
        log("  {} fasteners: {}".format(len(fasteners), ", ".join(fasteners[:10])))
    else:
        log("  No fasteners identified")

    # Step 6
    log("[6/8] Computing outward directions...")
    t0 = time.time()
    directions = compute_all_directions(parts, contacts, sub_assemblies)
    for part in parts:
        part["direction"] = directions.get(part["name"], [0, 1, 0])
    log("  {} directions computed ({:.1f}s)".format(len(directions), time.time() - t0))

    # Step 7
    centroids = _compute_centroids(parts)
    assembly_centroid = _compute_assembly_centroid(parts, centroids)

    chain_result = None

    if args.target_part:
        log("[7/8] Computing dependency chain for target: {}".format(args.target_part))
        from pipeline.dependency_chain import compute_dependency_chain
        t0 = time.time()
        stages, verified_dirs, dist_mults, details = compute_dependency_chain(
            parts, directions, collision_data, args.target_part,
            max_distance=args.explosion_distance,
            assembly_centroid=assembly_centroid,
            sub_assemblies=sub_assemblies,
            optimize_direction=not args.no_optimize_dir,
            interference_tolerance=args.interference_tolerance)
        log("  {} stages in dependency chain ({:.1f}s)".format(
            len(stages), time.time() - t0))

        target_detail = None
        for d in details:
            if d.get("part") == args.target_part:
                target_detail = d
                break
        if target_detail is None and details:
            target_detail = details[0]

        chain_result = {
            "mode": "dependency_chain",
            "target": args.target_part,
            "chain": [{"stage": i + 1, "parts": s} for i, s in enumerate(stages)],
            "feasible_count": sum(1 for d in details if d.get("feasible")),
            "blocked_count": sum(1 for d in details if not d.get("feasible")),
            "total_count": len(details),
            "optimize_direction": not args.no_optimize_dir,
            "chosen_direction": target_detail.get("chosen_direction") if target_detail else None,
            "considered_directions": target_detail.get("considered_directions", []) if target_detail else [],
            "expected_chain_cost": target_detail.get("expected_chain_cost") if target_detail else None,
        }
        log("CHAIN_RESULT_JSON: " + json.dumps(chain_result, ensure_ascii=False))
        _write_chain_log(stages, details, parts,
                         args.input, args.target_part, args.output_dir)
    else:
        if args.skip_collision:
            from pipeline.explosion_planner import build_explosion_plan
            log("[7/8] Building explosion view (geometry-based, no swept collision)...")
            t0 = time.time()
            stages, verified_dirs, dist_mults, details = build_explosion_plan(
                parts, directions, collision_data,
                sub_assemblies=sub_assemblies,
                center_part=args.center_part,
                max_distance=args.explosion_distance,
                base_explosion_distance=150.0)
            feasible = sum(1 for d in details if d.get("feasible"))
            blocked = len(details) - feasible
            log("  Explosion plan: {} stages, {}/{} parts placed ({:.1f}s)".format(
                len(stages), feasible, len(details), time.time() - t0))
        else:
            label = "Building collision-driven disassembly plan"
            log("[7/8] {}...".format(label))
            t0 = time.time()
            stages, verified_dirs, dist_mults, details = build_disassembly_dag_v2(
                parts, directions, collision_data, fasteners,
                max_distance=args.explosion_distance,
                assembly_centroid=assembly_centroid,
                sub_assemblies=sub_assemblies,
                correct_directions=True)

            feasible = sum(1 for d in details if d.get("feasible"))
            blocked = len(details) - feasible
            log("  {} stages, {}/{} parts feasible ({:.1f}s)".format(
                len(stages), feasible, len(details), time.time() - t0))

    for part in parts:
        name = part["name"]
        if name in verified_dirs:
            part["direction"] = verified_dirs[name]

    # Propagate isExplosionCenter from details to parts (for assembly.json)
    center_names = {d["part"] for d in details if d.get("isExplosionCenter")}
    if center_names:
        for part in parts:
            if part["name"] in center_names:
                part["isExplosionCenter"] = True

    feasible = sum(1 for d in details if d.get("feasible"))
    blocked = len(details) - feasible

    # Write report
    report_lines = []
    report_lines.append("=" * 60)
    report_lines.append("Collision-Driven Disassembly Plan Report")
    report_lines.append("=" * 60)
    report_lines.append("Total parts: {}".format(len(details)))
    report_lines.append("Feasible:    {}".format(feasible))
    report_lines.append("Blocked:     {}".format(blocked))
    report_lines.append("Stages:      {}".format(len(stages)))
    report_lines.append("-" * 60)
    for d in details:
        status = "OK" if d.get("feasible") else "BLOCKED"
        line = "  [{}] Stage {:2d} | {:20s} | dir=[{}] | safe: {:.1f}mm".format(
            status, d.get("stage", 0), d.get("part", ""),
            ",".join("{:.1f}".format(x) for x in d.get("direction", [0, 0, 0])),
            d.get("safe_distance", 0))
        if not d.get("feasible") and d.get("collision_with"):
            line += " | collision: {}".format(d["collision_with"])
        report_lines.append(line)
    report_lines.append("-" * 60)
    report = "\n".join(report_lines)
    report_path = os.path.join(args.output_dir, "report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    log(report)

    # Step 8
    log("[8/8] Writing assembly.json...")
    t0 = time.time()
    assembly = build_assembly_json(
        parts, stages, args.input, contacts, fasteners,
        verified_directions=verified_dirs,
        distance_multipliers=dist_mults,
        chain_info=chain_result,
        roots=roots)
    json_path = os.path.join(args.output_dir, "assembly.json")
    write_assembly_json(assembly, json_path)
    log("  {} ({:.1f} KB, {:.1f}s)".format(
        json_path, os.path.getsize(json_path) / 1024, time.time() - t0))

    log("Done in {:.1f}s. Output: {}".format(time.time() - t_total, args.output_dir))
    return 0


def _run_preview(args):
    """Preview-only mode: STP → mesh → glb + minimal assembly.json, no analysis."""
    parts_dir = os.path.join(args.output_dir, "parts")
    os.makedirs(parts_dir, exist_ok=True)

    from pipeline.stp_reader import read_stp_with_doc, verify_doc
    from pipeline.xcaf_utils import extract_assembly_tree, flatten_assembly_tree
    from pipeline.gltf_exporter import export_assembly_indexed
    from pipeline.assembly_json import build_assembly_json, write_assembly_json

    t_total = time.time()

    log("[1/3] Reading STEP: {}".format(args.input))
    t0 = time.time()
    doc = read_stp_with_doc(args.input)
    log("  Read in {:.1f}s".format(time.time() - t0))
    summary = verify_doc(doc, filepath=args.input)
    if not summary["valid"]:
        log("ERROR: No valid shapes found")
        return 1
    unit_scale = summary.get("unit_scale_to_mm", 1.0)
    if unit_scale != 1.0:
        log("  WARNING: STEP file unit scale = {} (not mm)".format(unit_scale))

    log("[2/3] Extracting assembly tree...")
    t0 = time.time()
    roots = extract_assembly_tree(doc)
    parts, sub_assemblies = flatten_assembly_tree(roots)
    log("  {} leaf parts ({:.1f}s)".format(len(parts), time.time() - t0))
    if len(parts) == 0:
        log("ERROR: No parts found")
        return 1

    log("[3/3] Meshing + exporting glb...")
    t0 = time.time()
    parts = export_assembly_indexed(parts, parts_dir,
                                     linear_deflection=args.mesh_deflection)
    log("  {} glb files ({:.1f}s)".format(len(parts), time.time() - t0))

    assembly = build_assembly_json(parts, [], args.input, roots=roots)
    json_path = os.path.join(args.output_dir, "assembly.json")
    write_assembly_json(assembly, json_path)
    log("  assembly.json ({:.1f} KB)".format(os.path.getsize(json_path) / 1024))

    log("Preview done in {:.1f}s".format(time.time() - t_total))
    return 0


def _run_validate(args):
    """Validate-only mode: load assembly.json, reload STP, run collision."""
    json_path = args.input

    if not os.path.exists(json_path):
        log("ERROR: File not found: {}".format(json_path))
        return 1

    log("[Validate] Loading assembly.json: {}".format(json_path))
    with open(json_path, "r", encoding="utf-8") as f:
        assembly = json.load(f)

    source_file = assembly.get("sourceFile", "")
    if not source_file or not os.path.exists(source_file):
        log("ERROR: Source STP not found: {}".format(source_file))
        log("  TIP: Place the .stp alongside assembly.json or update sourceFile path")
        return 1

    log("[Validate] Reloading source STP: {}".format(source_file))

    from pipeline.stp_reader import read_stp_with_doc
    from pipeline.xcaf_utils import extract_assembly_tree, flatten_assembly_tree
    from pipeline.direction_calc import compute_all_directions

    t0 = time.time()
    doc = read_stp_with_doc(source_file)
    log("  Read in {:.1f}s".format(time.time() - t0))

    roots = extract_assembly_tree(doc)
    parts, _sub_assemblies = flatten_assembly_tree(roots)
    log("  {} parts extracted".format(len(parts)))

    # Map assembly.json stage data to loaded parts
    stage_map = {}
    for part_entry in assembly.get("parts", []):
        stage_map[part_entry["name"]] = part_entry.get("disassemblyStage", 1)

    # Build stages from assembly.json
    max_stage = max(stage_map.values()) if stage_map else 1
    stages = [[] for _ in range(max_stage)]
    for part_entry in assembly.get("parts", []):
        s = part_entry.get("disassemblyStage", 1) - 1
        if s >= 0:
            stages[s].append(part_entry["name"])

    # Filter out empty stages
    stages = [s for s in stages if s]

    # Use directions from assembly.json or compute
    directions = {}
    for part_entry in assembly.get("parts", []):
        name = part_entry.get("name", "")
        directions[name] = part_entry.get("direction", [0, 1, 0])

    if not directions:
        # Compute directions from contacts
        from pipeline.contact_detector import detect_contacts
        contacts = detect_contacts(parts, intra_parent_only=True)
        directions = compute_all_directions(parts, contacts)

    log("[Validate] Running collision check ({} parts, {} stages)...".format(
        len(parts), len(stages)))

    from pipeline.path_validator import validate_disassembly_plan, generate_report

    t0 = time.time()
    validation = validate_disassembly_plan(
        parts, stages, directions, max_distance=args.explosion_distance,
        progress_callback=lambda done, total, name: log(
            "    collision {}/{}: {} {}".format(done, total, name,
                "(...)" if done % 5 != 0 else ""))
    )
    report = generate_report(validation)
    report_path = os.path.join(args.output_dir, "report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    log(report)

    valid_str = "PASS" if validation["valid"] else "PARTIAL"
    log("[Validate] Result: {} ({}/{} parts feasible, {:.1f}s)".format(
        valid_str, validation["feasible_parts"],
        validation["total_parts"], time.time() - t0))

    return 0 if validation["valid"] else 2


def _run_body_export(args):
    """Export a STEP file as a single body shell .glb (no part splitting)."""
    from pipeline.stp_reader import read_stp_with_doc
    from pipeline.gltf_exporter import export_merged_glb
    from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool
    from OCC.Core.TDF import TDF_LabelSequence
    from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Builder
    from OCC.Core.TopAbs import TopAbs_SOLID
    from OCC.Core.TopExp import TopExp_Explorer

    log("[1/2] Reading STEP: {}".format(args.input))
    t0 = time.time()
    doc = read_stp_with_doc(args.input)
    log("  Read in {:.1f}s".format(time.time() - t0))

    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    compound = TopoDS_Compound()
    builder = TopoDS_Builder()
    builder.MakeCompound(compound)

    free_labels = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_labels)
    solid_count = 0
    for i in range(free_labels.Length()):
        shape = shape_tool.GetShape(free_labels.Value(i + 1))
        exp = TopExp_Explorer(shape, TopAbs_SOLID)
        while exp.More():
            builder.Add(compound, exp.Current())
            solid_count += 1
            exp.Next()

    log("  {} solids collected".format(solid_count))

    log("[2/2] Meshing + exporting glb...")
    t0 = time.time()
    os.makedirs(args.output_dir, exist_ok=True)
    body_name = os.path.splitext(os.path.basename(args.input))[0]
    output_path = os.path.join(args.output_dir, body_name + '.glb')

    result = export_merged_glb(compound, output_path, body_name,
                               linear_deflection=args.mesh_deflection)
    if result:
        log("  Body exported: {} ({:.1f}s)".format(result, time.time() - t0))
    else:
        log("  ERROR: Failed to mesh body")
        return 1
    return 0


def _run_bom_preview(args):
    """BOM preview mode: read BOM, mesh+glb each STP, combine into one assembly.json.
    No contact detection or DAG — fast preview for position map."""
    from pipeline.bom_loader import read_bom, validate_bom_entries
    from pipeline.stp_reader import read_stp_with_doc, verify_doc
    from pipeline.xcaf_utils import (extract_assembly_tree, flatten_assembly_tree,
                                     find_sub_assembly_by_code_and_name)
    from pipeline.gltf_exporter import export_assembly_indexed
    from pipeline.assembly_json import build_assembly_json, write_assembly_json

    parts_dir = os.path.join(args.output_dir, "parts")
    os.makedirs(parts_dir, exist_ok=True)

    t_total = time.time()

    log("[1/2] Reading BOM: {}".format(args.bom))
    entries = read_bom(args.bom, args.models_dir)
    _models_dir = args.models_dir or os.path.dirname(os.path.abspath(args.bom))
    valid_entries, missing, report = validate_bom_entries(entries, _models_dir)
    for line in report:
        log(line)
    if not valid_entries:
        log("ERROR: No valid BOM entries with existing STP files")
        return 1

    all_parts = []
    all_roots = []
    skipped_count = 0

    for i, entry in enumerate(valid_entries):
        code = entry["code"]
        target_name = entry["target_name"]
        stp_path = entry["stp_path"]
        bom_info = {"name": target_name or code, "code": code}
        log("  [{}/{}] Processing: {} ({})".format(
            i + 1, len(valid_entries), target_name or "(unnamed)", code))

        t0 = time.time()
        doc = read_stp_with_doc(stp_path)
        summary = verify_doc(doc, filepath=stp_path)
        if not summary["valid"]:
            log("    WARNING: no valid shapes, skipping")
            skipped_count += 1
            continue

        roots = extract_assembly_tree(doc)
        all_sub_parts, sub_assemblies = flatten_assembly_tree(roots)

        if target_name:
            matched_names, matched_node = find_sub_assembly_by_code_and_name(
                roots, code, target_name)
            if matched_names:
                sub_parts = [p for p in all_sub_parts if p["name"] in matched_names]
                log("    matched '{}' → {} leaf parts".format(
                    matched_node, len(sub_parts)))
            else:
                log("    WARNING: no node matched pattern '^{}-.*-{}$'".format(
                    code, target_name))
                log("    loading ALL {} parts from STP".format(len(all_sub_parts)))
                sub_parts = all_sub_parts
        else:
            sub_parts = all_sub_parts

        for p in sub_parts:
            p["name"] = "{}_{:04d}__{}".format(code, i, p["name"])
            p["bomSource"] = bom_info
            p["bomCode"] = code
            p["bomRowIndex"] = i

        sub_parts = export_assembly_indexed(
            sub_parts, parts_dir,
            linear_deflection=args.mesh_deflection)

        for p in sub_parts:
            if "bomSource" not in p:
                p["bomSource"] = bom_info

        _prefix_hierarchy_roots(roots, "{}_{:04d}__".format(code, i))

        all_parts.extend(sub_parts)
        all_roots.extend(roots)

        log("    {} parts ({:.1f}s)".format(len(sub_parts), time.time() - t0))

    if not all_parts:
        log("ERROR: No parts loaded from any BOM entry")
        return 1

    # Verify uniqueness
    names = [p["name"] for p in all_parts]
    if len(names) != len(set(names)):
        dupes = [n for n in names if names.count(n) > 1]
        log("WARNING: duplicate part names after BOM prefix: {}".format(
            list(set(dupes))[:5]))

    assembly = build_assembly_json(all_parts, [], args.bom, roots=all_roots)
    json_path = os.path.join(args.output_dir, "assembly.json")
    write_assembly_json(assembly, json_path)
    log("  assembly.json ({:.1f} KB)".format(
        os.path.getsize(json_path) / 1024))

    log("BOM preview done in {:.1f}s. {} parts total, {} BOM entries skipped.".format(
        time.time() - t_total, len(all_parts), skipped_count))
    return 0


def _run_bom_full(args):
    """BOM full mode: read BOM, mesh+glb, detect contacts, build DAG,
    output full disassembly plan. Optionally scoped to --target-part."""
    from pipeline.bom_loader import read_bom, validate_bom_entries
    from pipeline.stp_reader import read_stp_with_doc, verify_doc
    from pipeline.xcaf_utils import (extract_assembly_tree, flatten_assembly_tree,
                                     find_sub_assembly_by_code_and_name)
    from pipeline.gltf_exporter import export_assembly_indexed
    from pipeline.contact_detector import detect_contacts
    from pipeline.fastener_identifier import identify_fasteners
    from pipeline.dag_builder import build_disassembly_dag_v2
    from pipeline.direction_calc import compute_all_directions, _compute_assembly_centroid, _compute_centroids
    from pipeline.assembly_json import build_assembly_json, write_assembly_json
    from pipeline.collision_check import prepare_collision_data

    parts_dir = os.path.join(args.output_dir, "parts")
    os.makedirs(parts_dir, exist_ok=True)

    t_total = time.time()

    log("[1/7] Reading BOM: {}".format(args.bom))
    entries = read_bom(args.bom, args.models_dir)
    _models_dir = args.models_dir or os.path.dirname(os.path.abspath(args.bom))
    valid_entries, missing, report = validate_bom_entries(entries, _models_dir)
    for line in report:
        log(line)
    if not valid_entries:
        log("ERROR: No valid BOM entries with existing STP files")
        return 1

    all_parts = []
    all_roots = []
    skipped_count = 0

    for i, entry in enumerate(valid_entries):
        code = entry["code"]
        target_name = entry["target_name"]
        stp_path = entry["stp_path"]
        bom_info = {"name": target_name or code, "code": code}
        log("  [{}/{}] Processing: {} ({})".format(
            i + 1, len(valid_entries), target_name or "(unnamed)", code))

        t0 = time.time()
        doc = read_stp_with_doc(stp_path)
        summary = verify_doc(doc, filepath=stp_path)
        if not summary["valid"]:
            log("    WARNING: no valid shapes, skipping")
            skipped_count += 1
            continue

        roots = extract_assembly_tree(doc)
        all_sub_parts, sub_assemblies = flatten_assembly_tree(roots)

        if target_name:
            matched_names, matched_node = find_sub_assembly_by_code_and_name(
                roots, code, target_name)
            if matched_names:
                sub_parts = [p for p in all_sub_parts if p["name"] in matched_names]
                log("    matched '{}' → {} leaf parts".format(
                    matched_node, len(sub_parts)))
            else:
                log("    WARNING: no node matched pattern '^{}-.*-{}$'".format(
                    code, target_name))
                log("    loading ALL {} parts from STP".format(len(all_sub_parts)))
                sub_parts = all_sub_parts
        else:
            sub_parts = all_sub_parts

        for p in sub_parts:
            p["name"] = "{}_{:04d}__{}".format(code, i, p["name"])
            p["bomSource"] = bom_info
            p["bomCode"] = code
            p["bomRowIndex"] = i

        sub_parts = export_assembly_indexed(
            sub_parts, parts_dir,
            linear_deflection=args.mesh_deflection)

        for p in sub_parts:
            if "bomSource" not in p:
                p["bomSource"] = bom_info

        _prefix_hierarchy_roots(roots, "{}_{:04d}__".format(code, i))

        all_parts.extend(sub_parts)
        all_roots.extend(roots)

        log("    {} parts ({:.1f}s)".format(len(sub_parts), time.time() - t0))

    if not all_parts:
        log("ERROR: No parts loaded from any BOM entry")
        return 1

    # Verify uniqueness
    names = [p["name"] for p in all_parts]
    if len(names) != len(set(names)):
        dupes = [n for n in names if names.count(n) > 1]
        log("WARNING: duplicate part names after BOM prefix: {}".format(
            list(set(dupes))[:5]))

    log("  Total: {} parts".format(len(all_parts)))

    # ── Build BOM-grouped dag_parts (one compound shape per BOM entry) ──
    flat_parts = list(all_parts)
    for p in flat_parts:
        p["parent"] = p.get("bomSource", {}).get("name", "root")

    bom_groups = {}
    for p in all_parts:
        bs = p.get("bomSource", {})
        key = bs.get("name", "unknown")
        if key not in bom_groups:
            bom_groups[key] = {"name": key, "child_names": []}
        bom_groups[key]["child_names"].append(p["name"])

    bom_group_map = {}
    dag_parts = []
    for key, group in bom_groups.items():
        group_parts = [p for p in all_parts if p["name"] in group["child_names"]]
        if not group_parts:
            continue
        merged_shape = _merge_parts_to_compound(group_parts)
        dag_parts.append({
            "name": key,
            "shape": merged_shape,
            "parent": "root",
        })
        bom_group_map[key] = group["child_names"]

    log("  {} BOM units formed from {} leaf parts".format(
        len(dag_parts), len(flat_parts)))

    sub_assemblies = list(bom_groups.values())

    # Pre-compute collision data for dag_parts
    log("  Pre-computing mesh collision data...")
    t_mesh = time.time()
    dag_collision_data = prepare_collision_data(dag_parts)
    log("  {} meshes ready ({:.1f}s)".format(
        len(dag_collision_data), time.time() - t_mesh))

    # Contact detection on dag_parts
    log("[4/7] Detecting contacts (BOM-level)...")
    t0 = time.time()
    contacts = detect_contacts(dag_parts, intra_parent_only=False,
                               collision_data=dag_collision_data, parallel=True)
    log("  {} contact pairs ({:.1f}s)".format(len(contacts), time.time() - t0))

    # Fastener identification on dag_parts
    fasteners = identify_fasteners(dag_parts, contacts)
    if fasteners:
        log("  {} fasteners: {}".format(len(fasteners), ", ".join(fasteners[:10])))
    else:
        log("  No fasteners identified")

    # Compute directions on dag_parts
    log("[5/7] Computing outward directions...")
    t0 = time.time()
    directions = compute_all_directions(dag_parts, contacts, sub_assemblies)
    for part in dag_parts:
        part["direction"] = directions.get(part["name"], [0, 1, 0])
    log("  {} directions computed ({:.1f}s)".format(
        len(directions), time.time() - t0))

    centroids = _compute_centroids(dag_parts)
    assembly_centroid = _compute_assembly_centroid(dag_parts, centroids)

    chain_result = None

    if args.target_part:
        from pipeline.dependency_chain import compute_dependency_chain
        dag_target = None
        for key in bom_group_map:
            if args.target_part in bom_group_map[key] or args.target_part == key:
                dag_target = key
                break
        if not dag_target:
            dag_target = args.target_part
        log("[6/7] Computing dependency chain for target: {}".format(dag_target))
        t0 = time.time()
        stages, verified_dirs, dist_mults, details = compute_dependency_chain(
            dag_parts, directions, dag_collision_data, dag_target,
            max_distance=args.explosion_distance,
            assembly_centroid=assembly_centroid,
            sub_assemblies=sub_assemblies,
            optimize_direction=not args.no_optimize_dir,
            interference_tolerance=args.interference_tolerance)
        log("  {} stages in dependency chain ({:.1f}s)".format(
            len(stages), time.time() - t0))

        target_detail = None
        for d in details:
            if d.get("part") == dag_target:
                target_detail = d
                break
        if target_detail is None and details:
            target_detail = details[0]

        chain_result = {
            "mode": "dependency_chain",
            "target": args.target_part,
            "resolved_target": dag_target,
            "chain": [{"stage": i + 1, "parts": s} for i, s in enumerate(stages)],
            "feasible_count": sum(1 for d in details if d.get("feasible")),
            "blocked_count": sum(1 for d in details if not d.get("feasible")),
            "total_count": len(details),
            "optimize_direction": not args.no_optimize_dir,
            "chosen_direction": target_detail.get("chosen_direction") if target_detail else None,
            "considered_directions": target_detail.get("considered_directions", []) if target_detail else [],
            "expected_chain_cost": target_detail.get("expected_chain_cost") if target_detail else None,
        }
        log("CHAIN_RESULT_JSON: " + json.dumps(chain_result, ensure_ascii=False))
        _write_chain_log(stages, details, flat_parts,
                         args.bom, args.target_part, args.output_dir)
    else:
        if args.skip_collision:
            from pipeline.explosion_planner import build_explosion_plan
            log("[6/7] Building BOM-level explosion view (geometry-based)...")
            t0 = time.time()
            # Resolve center: user may pass leaf or BOM-unit name
            bom_center = None
            if args.center_part:
                if args.center_part in {p["name"] for p in dag_parts}:
                    bom_center = args.center_part
                else:
                    for key, children in bom_group_map.items():
                        if args.center_part in children or args.center_part == key:
                            bom_center = key
                            break
                    if not bom_center:
                        bom_center = args.center_part
            stages, verified_dirs, dist_mults, details = build_explosion_plan(
                dag_parts, directions, dag_collision_data,
                sub_assemblies=sub_assemblies,
                center_part=bom_center,
                max_distance=args.explosion_distance,
                base_explosion_distance=150.0)
            feasible = sum(1 for d in details if d.get("feasible"))
            blocked = len(details) - feasible
            log("  Explosion plan: {} stages, {}/{} BOM-units placed ({:.1f}s)".format(
                len(stages), feasible, len(details), time.time() - t0))
        else:
            log("[6/7] Building collision-driven disassembly plan (BOM-level)...")
            t0 = time.time()
            stages, verified_dirs, dist_mults, details = build_disassembly_dag_v2(
                dag_parts, directions, dag_collision_data, fasteners,
                max_distance=args.explosion_distance,
                assembly_centroid=assembly_centroid,
                sub_assemblies=sub_assemblies)

            feasible = sum(1 for d in details if d.get("feasible"))
            blocked = len(details) - feasible
            log("  {} BOM-unit stages, {}/{} units feasible ({:.1f}s)".format(
                len(stages), feasible, len(details), time.time() - t0))

    # ── Map dag-part stages/directions back to individual leaf parts ──
    dag_stage = {}
    for s_idx, s_parts in enumerate(stages):
        for name in s_parts:
            dag_stage[name] = s_idx + 1

    # Rebuild: one stage per BOM unit, containing all its leaf parts
    stages_by_unit = [[] for _ in range(max(dag_stage.values()) if dag_stage else 0)]
    for key, child_names in bom_group_map.items():
        s = dag_stage.get(key, 1)
        stages_by_unit[s - 1].extend(child_names)
    stages = [s for s in stages_by_unit if s]

    leaf_verified_dirs = {}
    for key, child_names in bom_group_map.items():
        if key in verified_dirs:
            for name in child_names:
                leaf_verified_dirs[name] = verified_dirs[key]

    # Propagate isExplosionCenter from BOM-unit details to leaf parts
    center_unit_names = {d["part"] for d in details if d.get("isExplosionCenter")}
    leaf_center_names = set()
    for key in center_unit_names:
        leaf_center_names.update(bom_group_map.get(key, []))

    for part in flat_parts:
        name = part["name"]
        if name in leaf_verified_dirs:
            part["direction"] = leaf_verified_dirs[name]
        if name in leaf_center_names:
            part["isExplosionCenter"] = True

    # Write report
    report_lines = []
    report_lines.append("=" * 60)
    report_lines.append("Disassembly Plan Report")
    report_lines.append("=" * 60)
    report_lines.append("Total parts: {}".format(len(details)))
    feasible = sum(1 for d in details if d.get("feasible"))
    blocked = len(details) - feasible
    report_lines.append("Feasible:    {}".format(feasible))
    report_lines.append("Blocked:     {}".format(blocked))
    report_lines.append("Stages:      {}".format(len(stages)))
    report_lines.append("-" * 60)
    for d in details:
        status = "OK" if d.get("feasible") else "BLOCKED"
        line = "  [{}] Stage {:2d} | {:20s} | dir=[{}] | safe: {:.1f}mm".format(
            status, d.get("stage", 0), d.get("part", ""),
            ",".join("{:.1f}".format(x) for x in d.get("direction", [0, 0, 0])),
            d.get("safe_distance", 0))
        if not d.get("feasible") and d.get("collision_with"):
            line += " | collision: {}".format(d["collision_with"])
        report_lines.append(line)
    report_lines.append("-" * 60)
    report = "\n".join(report_lines)
    report_path = os.path.join(args.output_dir, "report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    log(report)

    log("[7/7] Writing assembly.json...")
    t0 = time.time()
    assembly = build_assembly_json(
        flat_parts, stages, args.bom, contacts, fasteners,
        verified_directions=verified_dirs,
        distance_multipliers=dist_mults,
        chain_info=chain_result,
        roots=all_roots)
    json_path = os.path.join(args.output_dir, "assembly.json")
    write_assembly_json(assembly, json_path)
    log("  {} ({:.1f} KB, {:.1f}s)".format(
        json_path, os.path.getsize(json_path) / 1024, time.time() - t0))

    log("Done in {:.1f}s. Output: {}".format(
        time.time() - t_total, args.output_dir))
    return 0


def _run_clean(args):
    """数模清洗模式: STP + XLSX → 清洗后 glb + assembly.json"""
    if not args.clean_bom:
        log("ERROR: --clean 模式需要同时指定 --clean-bom <xlsx文件>")
        return 1

    from pipeline.model_cleaner import clean_model

    return clean_model(
        stp_path=args.input,
        xlsx_path=args.clean_bom,
        output_dir=args.output_dir,
        export_step=args.export_step,
        log_fn=log,
    )


def _run_compare(args):
    """数模对比模式: XLSX (Sheet3 A/B列) → 加载两STP → 整体对比 → 输出报告"""
    from pipeline.model_comparator import read_compare_xlsx, compare_assemblies

    compare_xlsx = args.compare
    if not os.path.exists(compare_xlsx):
        log("ERROR: 对比表格不存在: {}".format(compare_xlsx))
        return 1

    models_dir = args.models_dir or os.path.dirname(os.path.abspath(compare_xlsx))

    log("=== 数模对比 ===")
    log("对比表格: {}".format(compare_xlsx))
    log("模型目录: {}".format(models_dir))
    if args.diff_glb:
        log("差异模型: 已启用")

    pairs = read_compare_xlsx(compare_xlsx, models_dir)
    if not pairs:
        log("ERROR: Sheet3 中未找到有效的对比对（A列+B列）")
        return 1

    log("共 {} 组对比对".format(len(pairs)))

    results_dir = os.path.join(args.output_dir, "results")
    os.makedirs(results_dir, exist_ok=True)

    results = []
    ok_count = 0
    fail_count = 0

    for code_a, stp_a, code_b, stp_b, row_idx in pairs:
        pair_dir = os.path.join(results_dir, "{:03d}".format(row_idx))
        os.makedirs(pair_dir, exist_ok=True)

        result = compare_assemblies(stp_a, stp_b, code_a, code_b,
                                    pair_dir, log_fn=log, diff_glb=args.diff_glb)
        if result.get("error"):
            fail_count += 1
        else:
            ok_count += 1
        results.append(result)

    identical = sum(1 for r in results if r.get("classification") == "一致")
    minor = sum(1 for r in results if r.get("classification") == "细微差异")
    significant = sum(1 for r in results if r.get("classification") == "明显不一致")

    summary = {
        "total_pairs": len(pairs),
        "ok": ok_count,
        "failed": fail_count,
        "identical": identical,
        "minor_diff": minor,
        "significant_diff": significant,
        "pairs": results,
    }

    summary_path = os.path.join(args.output_dir, "compare_summary.json")
    import json as _json
    with open(summary_path, "w", encoding="utf-8") as f:
        _json.dump(summary, f, indent=2, ensure_ascii=False)

    _write_compare_csv(results, os.path.join(args.output_dir, "compare_summary.csv"), log)

    log("")
    log("=" * 60)
    log("对比汇总: 共 {} 对, 一致 {} 对, 细微差异 {} 对, 明显不一致 {} 对, 失败 {} 对".format(
        len(pairs), identical, minor, significant, fail_count))
    log("详细报告: {}".format(results_dir))
    log("汇总CSV:  {}".format(os.path.join(args.output_dir, "compare_summary.csv")))
    log("=" * 60)

    log("COMPARE_RESULT_JSON: " + _json.dumps(summary, ensure_ascii=False))

    return 0 if fail_count == 0 else 1


def _write_compare_csv(results, csv_path, log_fn):
    try:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            import csv
            w = csv.writer(f)
            w.writerow([
                "序号", "代号A", "代号B",
                "零件数A", "零件数B",
                "体积A(mm3)", "体积B(mm3)",
                "体积比", "网格匹配率", "综合相似度",
                "判定", "质心偏移X", "质心偏移Y", "质心偏移Z",
                "耗时(s)", "差异模型A", "差异模型B",
            ])
            for i, r in enumerate(results):
                g = r.get("geometric", {})
                s = r.get("structural", {})
                offset = g.get("align_offset", [0, 0, 0])
                w.writerow([
                    i + 1, r.get("code_a", ""), r.get("code_b", ""),
                    s.get("part_count_a", ""), s.get("part_count_b", ""),
                    g.get("volume_a", ""), g.get("volume_b", ""),
                    g.get("volume_ratio", ""),
                    g.get("mesh_similarity", ""),
                    g.get("similarity", ""),
                    r.get("classification", r.get("error", "?")),
                    offset[0] if len(offset) > 0 else "",
                    offset[1] if len(offset) > 1 else "",
                    offset[2] if len(offset) > 2 else "",
                    r.get("elapsed_s", ""),
                    g.get("diff_glb_a", ""), g.get("diff_glb_b", ""),
                ])
        log_fn("  CSV: {}".format(csv_path))
    except Exception as e:
        log_fn("  WARNING: CSV write failed: {}".format(e))


def _run_diag(args):
    """装配树诊断模式: STP → 层级误判报告"""
    from pipeline.stp_reader import read_stp_with_doc, verify_doc
    from pipeline.xcaf_utils import diagnose_assembly_tree

    log("=== 装配树诊断 ===")
    log("STP: {}".format(args.input))

    t0 = time.time()
    doc = read_stp_with_doc(args.input)
    summary = verify_doc(doc, filepath=args.input)
    log("  读取 (%.1fs), Root shapes: %d" % (
        time.time() - t0, summary["root_count"]))
    if not summary["valid"]:
        log("ERROR: 无有效形状")
        return 1

    diag = diagnose_assembly_tree(doc)

    # ── Format output ──
    lines = []
    sep = "-" * 60

    lines.append("[DIAG] 总节点: %d" % diag["total_nodes"])
    lines.append("[DIAG] Assembly节点: %d  |  Compound节点: %d  |  误判Compound: %d" % (
        diag["assembly_nodes"], diag["compound_nodes"],
        diag["misclassified_compounds"]))
    lines.append("[DIAG] 深度分布: %s" % ", ".join(
        "d%s=%d" % (k, diag["depth_distribution"][k])
        for k in sorted(diag["depth_distribution"].keys(),
                         key=lambda x: int(x))))
    lines.append("[DIAG] 形状类型: %s" % ", ".join(
        "%s=%d" % (k, diag["shape_type_distribution"][k])
        for k in sorted(diag["shape_type_distribution"].keys())))

    mc_list = diag["misclassified_details"]
    if mc_list:
        lines.append(sep)
        lines.append("[DIAG] 误判Compound节点 (会被_S001分裂, 共%d个):" % len(mc_list))
        for m in mc_list[:50]:
            lines.append("[DIAG]   %s d=%d solids=%d | %s  %s" % (
                m["entry"], m["depth"], m["solid_count"],
                m["name"], m["path"]))
        if len(mc_list) > 50:
            lines.append("[DIAG]   ... 另有 %d 个" % (len(mc_list) - 50))
    else:
        lines.append("[DIAG] 无误判Compound节点 - 层级不会丢失")

    lines.append(sep)

    diag_report = "\n".join(lines)

    # Print to stdout (captured by Electron pipeline-progress)
    log(diag_report)

    # Write to file
    output_path = os.path.join(args.output_dir, "diag_report.txt")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(diag_report)
    log("  report: %s (%.1f KB)" % (output_path, os.path.getsize(output_path) / 1024))

    return 0


def _run_pmi(args):
    """PMI诊断模式: STP → PMI标注内容 + 关联零件"""
    from pipeline.stp_reader import read_stp_with_doc, verify_doc
    from pipeline.pmi_diag import extract_pmi, extract_pmi_full, format_pmi_report

    log("=== PMI 标注诊断 ===")
    log("STP: {}".format(args.input))

    t0 = time.time()
    doc = read_stp_with_doc(args.input)
    summary = verify_doc(doc, filepath=args.input)
    log("  读取 (%.1fs), Root shapes: %d" % (
        time.time() - t0, summary["root_count"]))
    if not summary["valid"]:
        log("ERROR: 无有效形状")
        return 1

    pmi_data = extract_pmi_full(doc, stp_path=args.input, log_fn=log)

    from pipeline.pmi_diag import parse_pmi_text_from_step
    pmi_text = parse_pmi_text_from_step(args.input)
    log("  [DIAG] STP路径: %s" % args.input)
    log("  [DIAG] 文本扫描: %d 个标签 %s" % (len(pmi_text), list(pmi_text.keys())))
    log("  [DIAG] OCCT Datum=%d DimTol=%d | match_results exists=%s" % (
        pmi_data["summary"]["total_datums"],
        pmi_data["summary"]["total_dimtols"],
        "match_results" in pmi_data))

    report = format_pmi_report(pmi_data)
    log(report)

    output_path = os.path.join(args.output_dir, "pmi_report.txt")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)
    log("  report: %s (%.1f KB)" % (output_path, os.path.getsize(output_path) / 1024))

    match_results = pmi_data.get("match_results")
    if match_results:
        import json
        labels_json = os.path.join(args.output_dir, "pmi_labels.json")
        with open(labels_json, "w", encoding="utf-8") as f:
            json.dump({"labels": match_results}, f, ensure_ascii=False, indent=2)
        log("  pmi_labels.json: %d 个端子匹配" % len(match_results))
        log("PMI_MATCH_JSON: %s" % labels_json.replace("\\", "/"))

    return 0


def _merge_parts_to_compound(parts_list):
    """Merge multiple part shapes into a single TopoDS_Compound for DAG."""
    from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Builder
    comp = TopoDS_Compound()
    builder = TopoDS_Builder()
    builder.MakeCompound(comp)
    for p in parts_list:
        shape = p.get("shape")
        if shape is not None:
            builder.Add(comp, shape)
    return comp


def _write_chain_log(stages, details, parts, source_file, target_name, output_dir):
    """Write a human-readable dependency chain log as chain_log.txt."""
    if not stages:
        return

    part_map = {p["name"]: p for p in parts}
    detail_map = {}
    for d in details:
        detail_map[d.get("part", "")] = d

    lines = []
    sep = "=" * 72
    sub = "-" * 72
    lines.append(sep)
    lines.append("  依赖链拆卸日志")
    lines.append(sep)
    lines.append("  源文件:    {}".format(source_file))
    lines.append("  目标零件:  {}".format(target_name))
    feasible = sum(1 for s in stages
                   for n in s
                   if detail_map.get(n, {}).get("feasible", False))
    blocked = sum(1 for s in stages for n in s) - feasible
    lines.append("  链长:      {} 个零件 ({} 可行, {} 阻塞)".format(
        sum(len(s) for s in stages), feasible, blocked))
    lines.append(sub)
    lines.append("  {:>4s} | {:<42s} | {:>4s} | {:^17s} | {:>7s}".format(
        "顺序", "零件名称", "状态", "拆卸方向", "安全距离"))
    lines.append(sub)

    stage_idx = 0
    for s_idx, stage_parts in enumerate(stages):
        for name in stage_parts:
            stage_idx += 1
            info = detail_map.get(name, {})
            is_feasible = info.get("feasible", False)
            status = "OK" if is_feasible else "BLK"
            direction = info.get("direction", [0, 0, 0])
            dir_str = "({:+.0f},{:+.0f},{:+.0f})".format(
                direction[0], direction[1], direction[2])
            safe_d = info.get("safe_distance", 0.0)
            safe_str = "{:.1f}mm".format(safe_d)
            note = info.get("note", "")
            display_name = name
            part_entry = part_map.get(name)
            if part_entry:
                display_name = part_entry.get("name", name)
            line = "  {:4d} | {:<42s} | {:>4s} | {:^17s} | {:>7s}".format(
                stage_idx, display_name[:42], status, dir_str, safe_str)
            if not is_feasible and note:
                line += " | {}".format(note[:50])
            lines.append(line)

    lines.append(sub)
    lines.append("  图例: OK=可行  BLK=阻塞")
    lines.append("  方向格式: (X,Y,Z) 单位向量, +Y=上方")
    lines.append(sep)

    report = "\n".join(lines)
    report_path = os.path.join(output_dir, "chain_log.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    log(report)


def _prefix_hierarchy_roots(roots, prefix):
    """Recursively prefix node names in assembly tree roots for BOM uniqueness."""
    for node in roots:
        node["name"] = prefix + node.get("name", "")
        _prefix_hierarchy_roots(node.get("children", []), prefix)


if __name__ == "__main__":
    sys.exit(main())
