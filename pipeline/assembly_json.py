"""
Assembly JSON output generator.

Produces the assembly.json file that the Three.js frontend consumes.
Contains part list, groups, and disassembly stages.
"""

import json


def build_assembly_json(parts, stages, source_file, contacts=None, fasteners=None):
    """
    Build the assembly.json data structure.

    Args:
        parts: list of part dicts with name, glbFile, color, transform.
        stages: list of stage lists from build_disassembly_dag().
        source_file: original STP file path (for metadata).
        contacts: (optional) contact list for stats.
        fasteners: (optional) list of fastener names.

    Returns:
        dict: assembly.json compatible structure.
    """
    if fasteners is None:
        fasteners = []

    # Part entries
    part_entries = []
    part_index = {}
    for idx, part in enumerate(parts):
        entry = build_part_entry(part, idx)
        part_entries.append(entry)
        part_index[part["name"]] = entry

    # Assign stage numbers
    stage_by_part = {}
    for stage_idx, stage_parts in enumerate(stages):
        for name in stage_parts:
            stage_by_part[name] = stage_idx + 1

    # Update stage numbers in part entries
    for entry in part_entries:
        entry["disassemblyStage"] = stage_by_part.get(entry["name"], 0)

    # Mark fasteners
    for entry in part_entries:
        entry["isFastener"] = entry["name"] in fasteners

    # Build groups (parts without explicit groups are solo groups)
    groups = []
    for entry in part_entries:
        groups.append({
            "id": "group_{}".format(entry["id"]),
            "name": entry["name"],
            "members": [entry["id"]],
            "stage": entry["disassemblyStage"],
        })

    # Stage descriptions
    stage_descriptions = []
    for stage_idx, stage_parts in enumerate(stages):
        desc = "Stage {}".format(stage_idx + 1)
        if stage_idx == 0 and fasteners:
            desc = "Remove fasteners"
        elif len(stage_parts) == 1:
            desc = "Remove {}".format(stage_parts[0])

        stage_descriptions.append({
            "stage": stage_idx + 1,
            "description": desc,
            "parts": stage_parts,
        })

    result = {
        "name": source_file.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].replace(".stp", "").replace(".step", ""),
        "sourceFile": source_file,
        "parts": part_entries,
        "groups": groups,
        "stages": stage_descriptions,
        "stats": {
            "totalParts": len(parts),
            "totalStages": len(stages),
            "totalContacts": len(contacts) if contacts else 0,
            "totalFasteners": len(fasteners),
        },
    }

    return result


def build_part_entry(part, index):
    """
    Build a single part entry for assembly.json.

    Args:
        part: dict with name, glbFile, color, transform, parent.
        index: sequential part index.

    Returns:
        dict: part entry for assembly.json.
    """
    entry = {
        "id": part.get("name", "part_{}".format(index)).replace(" ", "_"),
        "name": part.get("name", "part_{}".format(index)),
        "glbFile": part.get("glbFile", ""),
        "isFastener": False,
        "disassemblyStage": 0,
        "distanceMultiplier": 1.0,
    }

    if part.get("direction"):
        entry["direction"] = part["direction"]

    if part.get("directionConfidence"):
        entry["directionConfidence"] = part["directionConfidence"]

    if part.get("color"):
        entry["color"] = part["color"]

    if part.get("parent"):
        entry["parent"] = part["parent"]

    return entry


def write_assembly_json(assembly, output_path):
    """
    Write assembly data to a JSON file.

    Args:
        assembly: dict from build_assembly_json().
        output_path: file path to write.

    Returns:
        str: output file path.
    """
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(assembly, f, indent=2, ensure_ascii=False)
    return output_path
