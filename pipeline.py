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
    parser.add_argument("--bom", default=None,
                        help="BOM Excel (.xlsx) 文件路径，启用多文件加载模式")
    parser.add_argument("--models-dir", default=None,
                        help="BOM 模式下 STP 文件所在目录（默认同 BOM 文件目录）")
    parser.add_argument("--target-part", default=None,
                        help="仅计算指定零件的拆卸依赖链（依赖链模式）")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

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
    parts, _sub_assemblies = flatten_assembly_tree(roots)
    log("  {} parts extracted".format(len(parts)))

    # Also try looking for sourceFile adjacent to the JSON
    if not source_file or not os.path.exists(source_file):
        adj = os.path.join(os.path.dirname(json_path), os.path.basename(source_file))
        if os.path.exists(adj):
            source_file = adj

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
            if not p["name"].startswith(code + "_"):
                p["name"] = "{}_{:04d}__{}".format(code, i, p["name"].split("__", 1)[-1] if "__" in p["name"] else p["name"])

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
            if not p["name"].startswith(code + "_"):
                p["name"] = "{}_{:04d}__{}".format(code, i, p["name"].split("__", 1)[-1] if "__" in p["name"] else p["name"])

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
            sub_assemblies=sub_assemblies)
        log("  {} stages in dependency chain ({:.1f}s)".format(
            len(stages), time.time() - t0))
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

    for part in flat_parts:
        name = part["name"]
        if name in leaf_verified_dirs:
            part["direction"] = leaf_verified_dirs[name]

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
        roots=all_roots)
    json_path = os.path.join(args.output_dir, "assembly.json")
    write_assembly_json(assembly, json_path)
    log("  {} ({:.1f} KB, {:.1f}s)".format(
        json_path, os.path.getsize(json_path) / 1024, time.time() - t0))

    log("Done in {:.1f}s. Output: {}".format(
        time.time() - t_total, args.output_dir))
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


def _prefix_hierarchy_roots(roots, prefix):
    """Recursively prefix node names in assembly tree roots for BOM uniqueness."""
    for node in roots:
        node["name"] = prefix + node.get("name", "")
        _prefix_hierarchy_roots(node.get("children", []), prefix)


if __name__ == "__main__":
    sys.exit(main())
