"""
PMI Regex Diagnostic Tool (pure stdlib, no OCCT dependency).

Parses an AP242 STEP file with regex to trace PMI annotation
linking chains and determine whether PMI annotations can be
associated with specific parts.

Usage:
    python tests/test_pmi_regex.py nist_ftc_09_asme1_ap242-e1.stp
    python tests/test_pmi_regex.py nist_ftc_09_asme1_ap242-e1.stp --verbose
"""

import sys
import os
import re
import argparse
from collections import Counter, OrderedDict

_pat = re.compile(
    r'^#(\d+)=(?:(\w+)|\w+)\s*\((.*)\)\s*;\s*$', re.DOTALL)


def _parse_entity(line):
    m = _pat.match(line)
    if m is None:
        return None, None, None
    eid = m.group(1)
    etype = m.group(2)
    body = m.group(3)
    if etype is None:
        inner = re.match(r'^(\w+)', body)
        if inner:
            etype = inner.group(1)
    return eid, etype, body


def _find_ids(body):
    """Extract all entity IDs from a STEP body string."""
    return [m.group(1) for m in re.finditer(r'#(\d+)', body)]


def _collect_nested_ids(body):
    """Parse the body to find all IDs including those in nested
    parenthesized lists.  Returns a flat list of ID strings."""
    ids = []
    for token in re.split(r'([\(\),])', body):
        token = token.strip()
        if not token:
            continue
        m = re.match(r'#(\d+)$', token)
        if m:
            ids.append(m.group(1))
    return ids


_STR_RE = re.compile(r"'((?:[^'\\]|\\.)*)'")


def _extract_strings(body):
    return [m.group(1) for m in _STR_RE.finditer(body)]


def _read_file(path):
    """Stream-read a STEP file and build entity map + type counts."""
    entities = {}
    type_counts = Counter()
    total = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        buf = ""
        for raw in f:
            buf += raw
            while ";" in buf:
                idx = buf.index(";")
                line = buf[:idx + 1].strip()
                buf = buf[idx + 1:]
                if not line or line.startswith("/"):
                    continue
                eid, etype, body = _parse_entity(line)
                if eid is not None:
                    total += 1
                    entities[eid] = (etype, body)
                    type_counts[etype] += 1
        if buf.strip():
            line = buf.strip()
            if line and not line.startswith("/"):
                eid, etype, body = _parse_entity(line)
                if eid is not None:
                    total += 1
                    entities[eid] = (etype, body)
                    type_counts[etype] += 1
    return total, type_counts, entities


def _trace_pmi_chains(entities):
    """Trace ANNOTATION_PLANE -> DRAUGHTING_CALLOUT -> SHAPE_ASPECT -> PRODUCT."""

    string_cache = {}
    def _strings(eid):
        if eid not in string_cache:
            if eid in entities:
                string_cache[eid] = _extract_strings(entities[eid][1])
            else:
                string_cache[eid] = []
        return string_cache[eid]

    id_cache = {}
    def _ids(eid):
        if eid not in id_cache:
            if eid in entities:
                id_cache[eid] = _find_ids(entities[eid][1])
            else:
                id_cache[eid] = []
        return id_cache[eid]

    ann_planes = {}
    for eid, (etype, body) in entities.items():
        if etype == "ANNOTATION_PLANE":
            strs = _strings(eid)
            ids = _ids(eid)
            name = strs[0] if strs else ""
            ref_ids = ids[2:] if len(ids) > 2 else []
            ann_planes[eid] = {"name": name, "ref_ids": ref_ids}

    dr_calls = {}
    for eid, (etype, body) in entities.items():
        if etype == "DRAUGHTING_CALLOUT" or etype == "DRAUGHTING_CALLOUT_RELATIONSHIP":
            strs = _strings(eid)
            dr_name = strs[0] if strs else ""
            dr_calls[eid] = dr_name

    sar_map = {}
    for eid, (etype, body) in entities.items():
        if etype == "SHAPE_ASPECT_RELATIONSHIP":
            strs = _strings(eid)
            name = strs[0] if strs else ""
            ids = _ids(eid)
            if len(ids) >= 2:
                sar_map[name] = {"eid": eid, "shape_aspect_id": ids[0],
                                 "datum_or_datum_feature_id": ids[1]}

    shape_aspects = {}
    for eid, (etype, body) in entities.items():
        if etype == "SHAPE_ASPECT" or etype == "COMPOSITE_SHAPE_ASPECT":
            ids = _ids(eid)
            if ids:
                shape_aspects[eid] = ids[0]

    pds_map = {}
    for eid, (etype, body) in entities.items():
        if etype == "PRODUCT_DEFINITION_SHAPE":
            ids = _ids(eid)
            if ids:
                pds_map[eid] = ids[0]

    pd_map = {}
    for eid, (etype, body) in entities.items():
        if etype == "PRODUCT_DEFINITION":
            ids = _ids(eid)
            if ids:
                pd_map[eid] = ids[0]

    products = {}
    for eid, (etype, body) in entities.items():
        if etype == "PRODUCT":
            strs = _strings(eid)
            if len(strs) >= 2 and strs[1]:
                products[eid] = strs[1]
            elif len(strs) >= 1 and strs[0]:
                products[eid] = strs[0]
            else:
                products[eid] = "(unnamed)"

    datum_to_pds = {}
    for eid, (etype, body) in entities.items():
        if etype == "DATUM" or etype == "DATUM_FEATURE":
            ids = _ids(eid)
            if len(ids) >= 1 and ids[0] in pds_map:
                datum_to_pds[eid] = ids[0]

    def _resolve_to_product(shape_aspect_id):
        if shape_aspect_id in shape_aspects:
            pds_id = shape_aspects[shape_aspect_id]
        elif shape_aspect_id in pds_map:
            pds_id = shape_aspect_id
        elif shape_aspect_id in datum_to_pds:
            pds_id = datum_to_pds[shape_aspect_id]
        else:
            return "(unlinked)"

        if pds_id is None:
            return "(unlinked)"
        if pds_id in pds_map:
            pd_id = pds_map[pds_id]
            prod_id = pd_map.get(pd_id)
            if prod_id:
                return products.get(prod_id, "(unnamed product)")
        return "(unlinked after PDS)"

    results = []
    for ap_id, ap_info in ann_planes.items():
        for ref_id in ap_info["ref_ids"]:
            dr_name = dr_calls.get(ref_id)
            if dr_name is None:
                continue
            if dr_name in sar_map:
                sa_id = sar_map[dr_name]["shape_aspect_id"]
                part = _resolve_to_product(sa_id)
                results.append({
                    "annotation_plane": ap_id,
                    "ap_name": ap_info["name"],
                    "drafting_callout": ref_id,
                    "callout_name": dr_name,
                    "shape_aspect": sa_id,
                    "part": part,
                    "status": "linked",
                })
            else:
                results.append({
                    "annotation_plane": ap_id,
                    "ap_name": ap_info["name"],
                    "drafting_callout": ref_id,
                    "callout_name": dr_name,
                    "shape_aspect": "",
                    "part": "(no SHAPE_ASPECT_RELATIONSHIP match)",
                    "status": "partial",
                })

    if not results and ann_planes:
        for ap_id, ap_info in ann_planes.items():
            results.append({
                "annotation_plane": ap_id,
                "ap_name": ap_info["name"],
                "drafting_callout": "",
                "callout_name": "",
                "shape_aspect": "",
                "part": "(no DRAUGHTING_CALLOUT found in refs)",
                "status": "unlinked",
            })

    return results


def main():
    parser = argparse.ArgumentParser(
        description="PMI Regex Diagnostic Tool (pure stdlib)")
    parser.add_argument("stp_file", help="Path to .stp file")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show every PMI chain detail")
    parser.add_argument("--max-lines", type=int, default=None,
                        help="Show only top N PMI chains (default: all)")
    args = parser.parse_args()

    if not os.path.exists(args.stp_file):
        print("[FATAL] File not found: %s" % args.stp_file)
        return 1

    fsize_kb = os.path.getsize(args.stp_file) / 1024.0

    print("=" * 60)
    print("  PMI Regex Diagnostic")
    print("=" * 60)
    print("File: %s (%.1f KB)" % (args.stp_file, fsize_kb))

    total, type_counts, entities = _read_file(args.stp_file)

    print("Lines / Entities parsed: %d" % total)
    print()

    pm_key_types = [
        "ANNOTATION_PLANE", "DRAUGHTING_CALLOUT",
        "SHAPE_ASPECT_RELATIONSHIP", "SHAPE_ASPECT",
        "COMPOSITE_SHAPE_ASPECT", "DATUM_FEATURE", "DATUM",
        "PRODUCT_DEFINITION_SHAPE", "PRODUCT_DEFINITION", "PRODUCT",
        "PROPERTY_DEFINITION_REPRESENTATION",
        "CHARACTERIZED_ITEM_WITHIN_REPRESENTATION",
        "DRAUGHTING_MODEL",
    ]

    print("[Struct] PMI-related entity counts:")
    for t in pm_key_types:
        if type_counts.get(t, 0) > 0:
            print("  %-45s %d" % (t + ":", type_counts[t]))

    print()

    chains = _trace_pmi_chains(entities)

    linked = [c for c in chains if c["status"] == "linked"]
    partial = [c for c in chains if c["status"] == "partial"]
    unlinked = [c for c in chains if c["status"] == "unlinked"]

    print("[PMI] Chain summary:")
    print("  Total ANNOTATION_PLANE entities: %d" % type_counts.get("ANNOTATION_PLANE", 0))
    print("  Fully linked (to part):           %d" % len(linked))
    print("  Partially linked:                 %d" % len(partial))
    print("  Unlinked:                         %d" % len(unlinked))

    if linked:
        print()
        print("[PMI] Linked annotations (%d):" % len(linked))
        shown = 0
        for c in linked:
            if args.max_lines is not None and shown >= args.max_lines:
                print("  ... and %d more" % (len(linked) - shown))
                break
            print("  [%s] AP=%s  DC='%s'  →  Part: %s" % (
                c["status"], c["ap_name"], c["callout_name"], c["part"]))
            shown += 1

    if partial:
        print()
        print("[PMI] Partial links (%d):" % len(partial))
        for c in partial[:20]:
            print("  [%s] AP=%s  DC='%s'  →  %s" % (
                c["status"], c["ap_name"], c["callout_name"], c["part"]))
        if len(partial) > 20:
            print("  ... and %d more" % (len(partial) - 20))

    if args.verbose:
        print()
        print("[PMI] All chains detail:")
        for c in chains:
            print("  AP=%s(%s) DC=%s '%s' SA=%s → %s" % (
                c["annotation_plane"], c["ap_name"],
                c["drafting_callout"], c["callout_name"],
                c["shape_aspect"], c["part"]))

    print()
    print("-" * 60)
    if linked:
        print("CONCLUSION: PMI-to-part linking IS available.")
        print("   %d annotations are fully traceable to parts." % len(linked))
    elif partial:
        print("CONCLUSION: PMI links exist but incomplete.")
        print("   %d DRAUGHTING_CALLOUTs found but no SHAPE_ASPECT_RELATIONSHIP match." % len(partial))
    else:
        print("CONCLUSION: PMI-to-part linking NOT available in this file.")
        print("   Annotation planes exist but cannot be linked to parts via text.")
        print("   UG may need to export with semantic PMI associations enabled.")
    print("-" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
