"""
STP file reader - STEP format import via OpenCASCADE.

Uses STEPCAFControl_Reader to read STEP files into XCAF documents
with full assembly hierarchy, colors, and B-Rep geometry.
"""

from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.XCAFApp import XCAFApp_Application
from OCC.Core.STEPCAFControl import STEPCAFControl_Reader


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


def verify_doc(doc):
    """
    Verify that an XCAF document is valid and contains shapes.
    Returns summary dict with part count and root labels.
    """
    from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool
    from OCC.Core.TDF import TDF_LabelSequence

    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    free_shapes = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_shapes)

    return {
        "root_count": free_shapes.Length(),
        "valid": free_shapes.Length() > 0,
    }
