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
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

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
    from pipeline.dag_builder import build_disassembly_dag
    from pipeline.direction_calc import compute_all_directions
    from pipeline.assembly_json import build_assembly_json, write_assembly_json

    t_total = time.time()

    # Step 1
    log("[1/7] Reading STEP: {}".format(args.input))
    t0 = time.time()
    doc = read_stp_with_doc(args.input)
    log("  Read in {:.1f}s".format(time.time() - t0))
    summary = verify_doc(doc)
    log("  Root shapes: {}".format(summary["root_count"]))
    if not summary["valid"]:
        log("ERROR: No valid shapes found")
        return 1

    # Step 2
    log("[2/7] Extracting assembly tree...")
    t0 = time.time()
    roots = extract_assembly_tree(doc)
    parts = flatten_assembly_tree(roots)
    log("  {} leaf parts extracted ({:.1f}s)".format(len(parts), time.time() - t0))
    if len(parts) == 0:
        log("ERROR: No parts found")
        return 1

    # Step 3
    log("[3/7] Meshing + exporting glb (deflection={}mm)...".format(args.mesh_deflection))
    t0 = time.time()
    parts = export_assembly_indexed(parts, parts_dir)
    log("  {} glb files written ({:.1f}s)".format(len(parts), time.time() - t0))

    # Step 4
    log("[4/7] Detecting contacts ({} pairs)...".format(len(parts) * (len(parts) - 1) // 2))
    t0 = time.time()
    contacts = detect_contacts(parts)
    log("  {} contact pairs ({:.1f}s)".format(len(contacts), time.time() - t0))

    fasteners = identify_fasteners(parts, contacts)
    if fasteners:
        log("  {} fasteners: {}".format(len(fasteners), ", ".join(fasteners[:10])))
    else:
        log("  No fasteners identified")

    # Step 5
    log("[5/7] Building disassembly DAG...")
    t0 = time.time()
    directions = compute_all_directions(parts, contacts)
    stages = build_disassembly_dag(parts, contacts, fasteners, directions)
    log("  {} disassembly stages ({:.1f}s)".format(len(stages), time.time() - t0))

    # Attach direction to each part dict so it appears in assembly.json
    for part in parts:
        part["direction"] = directions.get(part["name"], [0, 1, 0])

    # Step 6
    if not args.skip_collision:
        log("[6/7] Validating disassembly paths...")
        from pipeline.path_validator import (
            validate_disassembly_plan, generate_report
        )
        from pipeline.collision_check import prepare_collision_data
        t0 = time.time()
        log("  Pre-computing collision mesh data...")
        collision_data = prepare_collision_data(parts)
        validation = validate_disassembly_plan(
            parts, stages, directions, max_distance=args.explosion_distance,
            collision_data=collision_data,
            progress_callback=lambda done, total, name: log(
                "    collision {}/{}: {}".format(done, total, name))
        )
        report = generate_report(validation)
        report_path = os.path.join(args.output_dir, "report.txt")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        log(report)
        valid_str = "PASS" if validation["valid"] else "PARTIAL"
        log("  Validation: {} ({}/{} parts, {:.1f}s)".format(
            valid_str, validation["feasible_parts"],
            validation["total_parts"], time.time() - t0))
    else:
        log("[6/7] Collision check skipped")

    # Step 7
    log("[7/7] Writing assembly.json...")
    t0 = time.time()
    assembly = build_assembly_json(parts, stages, args.input, contacts, fasteners)
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
    parts = flatten_assembly_tree(roots)
    log("  {} leaf parts ({:.1f}s)".format(len(parts), time.time() - t0))
    if len(parts) == 0:
        log("ERROR: No parts found")
        return 1

    log("[3/3] Meshing + exporting glb...")
    t0 = time.time()
    parts = export_assembly_indexed(parts, parts_dir)
    log("  {} glb files ({:.1f}s)".format(len(parts), time.time() - t0))

    # Write minimal assembly.json (no stage/contact data)
    assembly = build_assembly_json(parts, [], args.input)
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
        contacts = detect_contacts(parts)
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


if __name__ == "__main__":
    sys.exit(main())
