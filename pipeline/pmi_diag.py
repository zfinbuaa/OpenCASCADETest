"""
PMI (Product Manufacturing Information) diagnostic extraction.

Reads PMI annotations from AP242 STEP files via OCCT's XCAFDoc
DimTolTool, NotesTool, and ViewTool. Outputs every annotation
with its text content, type, and associated part name.

Used by: pipeline.py --pmi mode
"""

import logging

from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool
from OCC.Core.TDF import TDF_LabelSequence

logger = logging.getLogger(__name__)


def _label_name(label, shape_tool):
    """Get shape name from a TDF label (preserves Unicode)."""
    try:
        name = label.GetLabelName()
        if name and name.strip():
            return name
    except Exception:
        pass
    return "Part_%d" % label.Tag()


def _get_ref_shape_names(dim_tol_tool, gdt_label, shape_tool):
    """Get names of shapes referenced by a GDT label."""
    names = []
    try:
        first = TDF_LabelSequence()
        second = TDF_LabelSequence()
        dim_tol_tool.GetRefShapeLabel(gdt_label, first, second)
        for seq in (first, second):
            for i in range(seq.Length()):
                lab = seq.Value(i + 1)
                nm = _label_name(lab, shape_tool)
                if nm:
                    names.append(nm)
    except Exception:
        pass
    return names


def extract_pmi(doc):
    """
    Extract all PMI annotations from an XCAF document.

    Returns:
        dict with keys:
            datums: list of {name, description, identification, shapes}
            dim_tols: list of {kind, name, description, shapes}
            notes: list of {type, text, shapes}
            summary: {total_datums, total_dimtols, total_notes}
    """
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    dim_tol_tool = XCAFDoc_DocumentTool.DimTolTool(doc.Main())
    notes_tool = XCAFDoc_DocumentTool.NotesTool(doc.Main())

    result = {
        "datums": [],
        "dim_tols": [],
        "notes": [],
        "summary": {"total_datums": 0, "total_dimtols": 0, "total_notes": 0},
    }

    # ── Datums ──────────────────────────────────────────
    try:
        datum_labels = TDF_LabelSequence()
        dim_tol_tool.GetDatumLabels(datum_labels)
        result["summary"]["total_datums"] = datum_labels.Length()
        # Detail extraction skipped — GetDatum can hang.
        # Use match_pmi_by_proximity() instead for spatial matching.
    except Exception as e:
        logger.debug("GetDatumLabels failed: %s", e)

    # ── DimTols (dimensions + tolerances) ───────────────
    try:
        dmt_labels = TDF_LabelSequence()
        dim_tol_tool.GetDimTolLabels(dmt_labels)
        result["summary"]["total_dimtols"] = dmt_labels.Length()

        for i in range(min(dmt_labels.Length(), 20)):
            lab = dmt_labels.Value(i + 1)
            entry = {}
            try:
                ok, kind, name, description = dim_tol_tool.GetDimTol(lab, None)
                entry["kind"] = int(kind) if kind is not None else -1
                entry["name"] = str(name) if name else ""
                entry["description"] = str(description) if description else ""
            except Exception:
                entry["kind"] = -1
                entry["name"] = ""
                entry["description"] = ""
            entry["shapes"] = _get_ref_shape_names(dim_tol_tool, lab, shape_tool)
            result["dim_tols"].append(entry)
    except Exception as e:
        logger.debug("GetDimTolLabels failed: %s", e)

    # ── Notes (Comments + Balloons) ─────────────────────
    try:
        note_labels = TDF_LabelSequence()
        notes_tool.GetNotes(note_labels)
        result["summary"]["total_notes"] = note_labels.Length()

        for i in range(note_labels.Length()):
            lab = note_labels.Value(i + 1)
            entry = {"type": "unknown", "text": "", "shapes": []}

            try:
                from OCC.Core.XCAFDoc import XCAFDoc_NoteBalloon
                balloon = XCAFDoc_NoteBalloon.Get(lab)
                if balloon is not None:
                    entry["type"] = "balloon"
                    try:
                        entry["text"] = str(balloon.Comment())
                    except Exception:
                        pass
            except Exception:
                pass

            try:
                from OCC.Core.XCAFDoc import XCAFDoc_NoteComment
                if entry["text"] == "":
                    comment = XCAFDoc_NoteComment.Get(lab)
                    if comment is not None:
                        entry["type"] = "comment"
                        try:
                            entry["text"] = str(comment.Comment())
                        except Exception:
                            pass
            except Exception:
                pass

            entry["shapes"] = _get_ref_shape_names(dim_tol_tool, lab, shape_tool)
            result["notes"].append(entry)
    except Exception as e:
        logger.debug("GetNotes failed: %s", e)

    return result


def format_pmi_report(pmi_data, max_shapes=5):
    """
    Format PMI extraction results as a human-readable report.

    Returns:
        str: Multi-line report text.
    """
    lines = []
    sep = "-" * 60

    s = pmi_data["summary"]
    lines.append("[PMI] PMI 标注探测结果")
    lines.append("[PMI] Datum: %d  |  DimTol: %d  |  Note: %d" % (
        s["total_datums"], s["total_dimtols"], s["total_notes"]))

    # Datums
    if pmi_data["datums"]:
        lines.append("")
        lines.append(sep)
        lines.append("[PMI] Datum 标注 (%d):" % len(pmi_data["datums"]))
        for d in pmi_data["datums"]:
            shapes_str = ", ".join(d["shapes"][:max_shapes])
            if len(d["shapes"]) > max_shapes:
                shapes_str += ", ...(%d more)" % (len(d["shapes"]) - max_shapes)
            if not shapes_str:
                shapes_str = "(无关联零件)"
            lines.append("[PMI]   name='%s' desc='%s' id='%s' → %s" % (
                d["name"], d["description"], d["identification"], shapes_str))

    # DimTols
    if pmi_data["dim_tols"]:
        lines.append("")
        lines.append(sep)
        lines.append("[PMI] DimTol 标注 (%d):" % len(pmi_data["dim_tols"]))
        for d in pmi_data["dim_tols"]:
            shapes_str = ", ".join(d["shapes"][:max_shapes])
            if len(d["shapes"]) > max_shapes:
                shapes_str += ", ...(%d more)" % (len(d["shapes"]) - max_shapes)
            if not shapes_str:
                shapes_str = "(无关联零件)"
            lines.append("[PMI]   kind=%d name='%s' desc='%s' → %s" % (
                d["kind"], d["name"], d["description"], shapes_str))

    # Notes
    if pmi_data["notes"]:
        lines.append("")
        lines.append(sep)
        lines.append("[PMI] Note 标注 (%d):" % len(pmi_data["notes"]))
        for n in pmi_data["notes"]:
            shapes_str = ", ".join(n["shapes"][:max_shapes])
            if len(n["shapes"]) > max_shapes:
                shapes_str += ", ...(%d more)" % (len(n["shapes"]) - max_shapes)
            if not shapes_str:
                shapes_str = "(无关联零件)"
            lines.append("[PMI]   [%s] '%s' → %s" % (
                n["type"], n["text"][:80], shapes_str))

    lines.append("")
    lines.append(sep)
    return "\n".join(lines)


def parse_pmi_text_from_step(filepath):
    r"""
    Scan a STEP file for PMI labels in DESCRIPTIVE_REPRESENTATION_ITEM.

    Pattern: 'FCF\w\X2\...\X0\\wLABEL\w'
    Extracts text after \X0\\w, keeps labels containing at least one digit.
    Returns ordered dict: {"T01": line, "T04": line, ...}
    """
    import re
    from collections import OrderedDict
    pmi_map = OrderedDict()

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            prev = ""
            for line in f:
                raw = line
                stripped = line.strip()

                # Mode A: /*PMI:LABEL:'pattern'*/ comment blocks (test files)
                cm = re.search(r"/\*PMI:([^:]+):'([^']+)'\*/", stripped)
                if cm:
                    label = cm.group(1)
                    if label == "IGNORE":
                        continue
                    if re.search(r'\d', label):
                        pmi_map[label] = stripped
                    continue

                # Mode B: DESCRIPTIVE_REPRESENTATION_ITEM (production files)
                if prev and ("'FCF" in stripped or stripped.startswith("FCF")):
                    combined = prev + " " + stripped
                    m = re.search(
                        r"DESCRIPTIVE_REPRESENTATION_ITEM\('equivalent unicode string',\s*'FCF.*?X0\\*w([A-Za-z0-9_]+)\\",
                        combined)
                    if m:
                        label = m.group(1)
                        if len(label) <= 10 and re.search(r'\d', label):
                            pmi_map[label] = combined
                prev = raw.strip() if "DESCRIPTIVE_REPRESENTATION_ITEM" in stripped else ""
    except Exception:
        pass

    return pmi_map


def trace_pmi_positions(filepath, log_fn=None):
    """
    Trace PMI annotation plane positions from STEP text.

    Chains:  DESCRIPTIVE_REPRESENTATION_ITEM → DRAUGHTING_CALLOUT
             ANNOTATION_PLANE → DRAUGHTING_CALLOUT (same ID)
             ANNOTATION_PLANE → PLANE → AXIS2_PLACEMENT_3D → CARTESIAN_POINT

    Returns list of (label, x, y, z) per traced PMI.
    """
    _log = log_fn or (lambda x: None)
    import re
    results = []

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        return results

    id_to_xy = {}

    for m in re.finditer(
            r'#(\d+)=CARTESIAN_POINT\(.*?\(\s*([-.\dE+]+)\s*,\s*([-.\dE+]+)\s*,\s*([-.\dE+]+)\s*\)',
            content):
        eid = m.group(1)
        try:
            x = float(m.group(2))
            y = float(m.group(3))
            z = float(m.group(4))
            id_to_xy[(eid, "p")] = (x, y, z)
        except Exception:
            pass

    for m in re.finditer(r'#(\d+)=AXIS2_PLACEMENT_3D\(.*?#(\d+)\)', content):
        aid = m.group(1)
        pt_id = m.group(2)
        key = (pt_id, "p")
        if key in id_to_xy:
            id_to_xy[(aid, "a")] = id_to_xy[key]

    p2a = {}
    for m in re.finditer(r'#(\d+)=PLANE\(.*?,#(\d+)\)', content):
        pid = m.group(1)
        aid = m.group(2)
        key = (aid, "a")
        if key in id_to_xy:
            p2a[pid] = id_to_xy[key]

    ap_to_dc = {}
    for m in re.finditer(
            r'#(\d+)=ANNOTATION_PLANE\(\'PMI PLANE\',\(#\d+\),#(\d+),\(([^)]+)\)\)',
            content):
        ap_id = m.group(1)
        plane_id = m.group(2)
        cart_ids_raw = m.group(3)
        for cm in re.finditer(r'#(\d+)', cart_ids_raw):
            dcall_id = cm.group(1)
            if plane_id in p2a:
                ap_to_dc[dcall_id] = p2a[plane_id]

    dc_records = {}
    for m in re.finditer(
            r"#(\d+)=DESCRIPTIVE_REPRESENTATION_ITEM\('equivalent unicode string',\s*'FCF.*?X0\\*w([A-Za-z0-9_]+)\\",
            content):
        dc_records[m.group(1)] = m.group(2)

    for rep_m in re.finditer(
            r"#(\d+)=REPRESENTATION\('',\(([^)]+)\),#(\d+)\)",
            content):
        rep_id = rep_m.group(1)
        inner_ids = re.findall(r'#(\d+)', rep_m.group(2))
        for desc_id in inner_ids:
            if desc_id in dc_records:
                label = dc_records[desc_id]
                if len(label) <= 10 and re.search(r'\d', label):
                    for pdr_m in re.finditer(
                            r'#(\d+)=PROPERTY_DEFINITION_REPRESENTATION\(#(\d+),#' +
                            rep_id + r'\)',
                            content):
                        pd_id = pdr_m.group(2)
                        for pd_m in re.finditer(
                                r'#' + pd_id + r'=PROPERTY_DEFINITION\([^)]*,#(\d+)\)',
                                content):
                            ciwr_id = pd_m.group(1)
                            for ciwr_m in re.finditer(
                                    r'#' + ciwr_id + r'=CHARACTERIZED_ITEM_WITHIN_REPRESENTATION\([^,]*,[^,]*,#(\d+),',
                                    content):
                                dcall_id = ciwr_m.group(1)
                                if dcall_id in ap_to_dc:
                                    x, y, z = ap_to_dc[dcall_id]
                                    results.append((label, x, y, z, dcall_id))
                                    break
                            break
                        break
                    break

    if results:
        _log("[DIAG-TRACE] traced %d PMI positions, samples: %s" % (
            len(results), [(r[0], round(r[1], 1), round(r[2], 1), round(r[3], 1))
                           for r in results[:8]]))
    else:
        _log("[DIAG-TRACE] NO PMI positions traced from STEP text")

    return results


def match_pmi_by_proximity(doc, pmi_text_map, stp_path=None, log_fn=None):
    """
    Match PMI labels to closest parts using traced plane positions.

    1. Trace PMI plane positions from STEP text
    2. For each label with a known position, find closest part via
       BRepExtrema_DistShapeShape.
    3. Output all labels (unmatched have part="" and leader_pos=[0,0,0]).

    Returns list of {label, part, dist, leader_pos}.
    """
    _log = log_fn or (lambda x: None)
    import numpy as np
    from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape
    from OCC.Core.Bnd import Bnd_Box
    from OCC.Core.BRepBndLib import brepbndlib
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCC.Core.gp import gp_Pnt

    from pipeline.xcaf_utils import extract_assembly_tree, flatten_assembly_tree

    roots = extract_assembly_tree(doc)
    parts, _ = flatten_assembly_tree(roots)
    if not parts:
        return [{"label": k, "part": "", "dist": 0.0,
                 "leader_pos": [0.0, 0.0, 0.0]}
                for k in pmi_text_map]

    part_shapes = []
    for p in parts:
        s = p.get("shape")
        if s is not None:
            try:
                bbox = Bnd_Box()
                brepbndlib.Add(s, bbox)
                xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
                center = np.array([
                    (xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2])
                size = max(xmax - xmin, ymax - ymin, zmax - zmin)
                part_shapes.append((p["name"], s, center, size))
            except Exception:
                part_shapes.append((p["name"], s, np.zeros(3), 0.0))

    positions = {}
    if stp_path:
        traced = trace_pmi_positions(stp_path, log_fn=_log)
        for label, x, y, z, dcall_id in traced:
            if label in pmi_text_map:
                positions[label] = (x, y, z)

    label_order = list(pmi_text_map.keys())
    results = []
    for label in label_order:
        if label in positions:
            px, py, pz = positions[label]
            leader_pos = [px, py, pz]
            try:
                vtx = BRepBuilderAPI_MakeVertex(
                    gp_Pnt(float(px), float(py), float(pz))).Shape()
            except Exception:
                results.append({"label": label, "part": "", "dist": 0.0,
                                "leader_pos": [0.0, 0.0, 0.0]})
                continue

            min_dist = float('inf')
            closest_part = ""
            for name, pshape, center, size in part_shapes:
                if pshape is None:
                    continue
                if np.linalg.norm(np.array([px, py, pz]) - center) > size + 20.0:
                    continue
                try:
                    extrema = BRepExtrema_DistShapeShape(vtx, pshape)
                    extrema.Perform()
                    if extrema.IsDone():
                        d = extrema.Value()
                        if d < min_dist:
                            min_dist = d
                            closest_part = name
                except Exception:
                    continue

            results.append({
                "label": label,
                "part": closest_part,
                "dist": round(min_dist, 4) if min_dist != float('inf') else 0.0,
                "leader_pos": [round(float(v), 4) for v in leader_pos],
            })
            _log("[DIAG-MATCH] %s pos=(%.1f,%.1f,%.1f) → part=%s dist=%.2f" % (
                label, px, py, pz, closest_part[:40] if closest_part else "NONE",
                min_dist if min_dist != float('inf') else -1))
        else:
            results.append({
                "label": label, "part": "", "dist": 0.0,
                "leader_pos": [0.0, 0.0, 0.0],
            })

    return results


def extract_pmi_full(doc, stp_path=None, log_fn=None):
    """
    Full PMI extraction: try OCCT API first, fall back to text
    parsing + spatial matching via traced PMI plane positions.

    Returns dict compatible with format_pmi_report() but also
    includes 'match_results' for JSON/UI consumption.
    """
    result = extract_pmi(doc)

    if stp_path and result["summary"]["total_datums"] == 0 \
            and result["summary"]["total_dimtols"] == 0:
        pmi_text = parse_pmi_text_from_step(stp_path)
        if pmi_text:
            matches = match_pmi_by_proximity(doc, pmi_text, stp_path=stp_path,
                                              log_fn=log_fn)
            if not matches:
                matches = [{"label": k, "part": "", "dist": 0.0,
                            "leader_pos": [0.0, 0.0, 0.0]}
                           for k in pmi_text]
            result["match_results"] = matches
            result["summary"]["pmi_text_labels"] = len(matches)

    return result
