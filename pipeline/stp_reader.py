"""
STP file reader - STEP format import via OpenCASCADE.

Uses STEPCAFControl_Reader to read STEP files into XCAF documents
with full assembly hierarchy, colors, and B-Rep geometry.
"""

import re

from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.XCAFApp import XCAFApp_Application
from OCC.Core.STEPCAFControl import STEPCAFControl_Reader


_UNIT_SCALE_TO_MM = {
    "M": 1000.0,
    "METER": 1000.0,
    "METRE": 1000.0,
    "INCH": 25.4,
    "IN": 25.4,
    "FT": 304.8,
    "FOOT": 304.8,
    "FEET": 304.8,
    "CM": 10.0,
    "CENTIMETRE": 10.0,
    "CENTIMETER": 10.0,
    "MM": 1.0,
    "MILLIMETRE": 1.0,
    "MILLIMETER": 1.0,
    "MICROINCH": 0.0000254,
    "MIL": 0.0254,
    "THOU": 0.0254,
}


def read_step_units(filepath):
    """
    Read the length unit from a STEP file header and return a scale factor to mm.

    Parses the HEADER section for LENGTH_UNIT and SI_UNIT patterns.
    Common units and their scale factors:
        millimetre -> 1.0  (default)
        metre      -> 1000.0
        inch       -> 25.4
        centimetre -> 10.0

    Args:
        filepath: path to the STEP (.stp/.step) file.

    Returns:
        float: scale factor to convert file units to millimetres.
               Returns 1.0 (assume mm) if the unit cannot be determined.
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            in_header = False
            header_lines = []
            for line in f:
                stripped = line.strip()
                if stripped.upper().startswith("HEADER"):
                    in_header = True
                if in_header:
                    header_lines.append(stripped.upper())
                    if stripped.upper().startswith("ENDSEC"):
                        break
    except (OSError, UnicodeDecodeError):
        return 1.0

    header_text = " ".join(header_lines)

    si_unit_match = re.search(
        r'SI_UNIT\s*\(\s*\.?META?\s*,\s*\.?CENTI?\s*\)\s*LENGTH_UNIT', header_text)
    if si_unit_match:
        return 10.0

    si_unit_milli = re.search(
        r'SI_UNIT\s*\(\s*\.?META?\s*,\s*\.?MILLI?\s*\)\s*LENGTH_UNIT', header_text)
    if si_unit_milli:
        return 1.0

    for unit_name, scale in sorted(_UNIT_SCALE_TO_MM.items(), key=lambda x: -len(x[0])):
        if unit_name in header_text and "LENGTH_UNIT" in header_text:
            return scale

    return 1.0


def read_stp(filepath):
    """
    Read a STEP (.stp) file and return a list of root shapes.

    Returns:
        list[TopoDS_Shape]: Root shapes extracted from the STEP file.
    """
    from OCC.Extend.DataExchange import read_step_file
    shapes = read_step_file(filepath)
    return shapes if isinstance(shapes, list) else [shapes]


def read_stp_with_doc(filepath):
    """
    Read a STEP file into an XCAF document, preserving assembly tree,
    part names, colors, and transformations.

    Uses STEPCAFControl_Reader for full XCAF import with all metadata.

    Returns:
        TDocStd_Document: XCAF document containing the full model.
    """
    # Create XCAF document
    app = XCAFApp_Application.GetApplication()
    doc = TDocStd_Document("MDTV-CAF")
    app.InitDocument(doc)

    # Read STEP file with full metadata
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.SetColorMode(True)
    reader.SetLayerMode(True)
    reader.SetPropsMode(True)
    reader.SetGDTMode(True)
    reader.SetMatMode(True)
    reader.SetViewMode(True)

    status = reader.ReadFile(filepath)
    if status != 1:  # IFSelect_RetDone
        raise IOError("Failed to read STEP file: {}".format(filepath))

    if not reader.Transfer(doc):
        raise RuntimeError("Failed to transfer STEP data to document")

    return doc


def verify_doc(doc, filepath=None):
    """
    Verify that an XCAF document is valid and contains shapes.
    Returns summary dict with part count, root labels, and unit scale.

    Args:
        doc: TDocStd_Document to verify.
        filepath: optional STEP file path; if provided, unit scale is detected.
    """
    from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool
    from OCC.Core.TDF import TDF_LabelSequence

    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    free_shapes = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_shapes)

    unit_scale = 1.0
    if filepath:
        try:
            unit_scale = read_step_units(filepath)
        except Exception:
            unit_scale = 1.0

    return {
        "root_count": free_shapes.Length(),
        "valid": free_shapes.Length() > 0,
        "unit_scale_to_mm": unit_scale,
    }
