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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def log(msg):
    """Print with flush for live progress."""
    print(msg, flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="整车数模自动拆装方案生成管线"
    )
    parser.add_argument("input", help="STEP (.stp) 或 assembly.json (配合 --validate)")
    parser.add_argument("--output-dir", default="./output")
    parser.add_argument("--mesh-deflection", type=float, default=1.0)
    parser.add_argument("--explosion-distance", type=float, default=500.0)
    parser.add_argument("--skip-collision", action="store_true")
    parser.add_argument("--preview", action="store_true",
                        help="仅导入 STP → 网格化 → 导出 glb + JSON (跳过分析)")
    parser.add_argument("--validate", action="store_true",
                        help="仅对已有 assembly.json 运行碰撞验证")
    parser.add_argument("--export-body", action="store_true",
                        help="将 STP 转换为单个车壳 .glb（不拆分零件）")
    parser.add_argument("--root-node", default=None,
                        help="仅处理指定子装配节点下的零件（层级选择）")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

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
    summary = verify_doc(doc)
    log("  Root shapes: {}".format(summary["root_count"]))
    if not summary["valid"]:
        log("ERROR: No valid shapes found")
        return 1

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
    log("[7/8] Building collision-driven disassembly plan...")
    t0 = time.time()

    centroids = _compute_centroids(parts)
    assembly_centroid = _compute_assembly_centroid(parts, centroids)

    stages, verified_dirs, dist_mults, details = build_disassembly_dag_v2(
        parts, directions, collision_data, fasteners,
        max_distance=args.explosion_distance,
        assembly_centroid=assembly_centroid,
        sub_assemblies=sub_assemblies)

    for part in parts:
        name = part["name"]
        if name in verified_dirs:
            part["direction"] = verified_dirs[name]

    feasible = sum(1 for d in details if d.get("feasible"))
    blocked = len(details) - feasible
    log("  {} stages, {}/{} parts feasible ({:.1f}s)".format(
        len(stages), feasible, len(details), time.time() - t0))

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
    summary = verify_doc(doc)
    if not summary["valid"]:
        log("ERROR: No valid shapes found")
        return 1

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

    # Write minimal assembly.json (no stage/contact data)
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
    parts = flatten_assembly_tree(roots)
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


if __name__ == "__main__":
    sys.exit(main())
