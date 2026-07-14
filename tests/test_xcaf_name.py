"""
Test for get_shape_name with Unicode (Chinese) names.
Verifies that chr(int(ext_str.Value(i))) correctly decodes UCS-2 names.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from OCC.Core.TDocStd import TDocStd_Document
    from OCC.Core.XCAFApp import XCAFApp_Application
    from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.TDataStd import TDataStd_Name

    from pipeline.xcaf_utils import get_shape_name, set_shape_name
except ImportError as e:
    print(f"[SKIP] OCCT not available: {e}")
    sys.exit(0)

def test_ascii_name():
    """Test simple ASCII name extraction."""
    app = XCAFApp_Application.GetApplication()
    doc = TDocStd_Document("test")
    app.InitDocument(doc)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())

    box = BRepPrimAPI_MakeBox(10, 5, 3).Shape()
    label = shape_tool.NewShape()
    shape_tool.SetShape(label, box)
    set_shape_name(label, "TestPart_001")

    name = get_shape_name(label, shape_tool)
    assert name == "TestPart_001", f"Expected TestPart_001, got {name}"
    print(f"  [PASS] ASCII name: {name}")


def test_chinese_name():
    """Test Chinese (Unicode) name extraction via TDataStd_Name."""
    app = XCAFApp_Application.GetApplication()
    doc = TDocStd_Document("test")
    app.InitDocument(doc)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())

    box = BRepPrimAPI_MakeBox(10, 5, 3).Shape()
    label = shape_tool.NewShape()
    shape_tool.SetShape(label, box)
    # Set a Chinese name that would appear in STEP as \X2\5DE6...\
    set_shape_name(label, "左前减振器支柱总成")

    name = get_shape_name(label, shape_tool)
    assert name == "左前减振器支柱总成", f"Expected 左前减振器支柱总成, got {repr(name)}"
    print(f"  [PASS] Chinese name: {name}")


def test_mixed_ascii_chinese_name():
    """Test mixed ASCII + Chinese name like BOM-part-number_ChineseName_catiaID."""
    app = XCAFApp_Application.GetApplication()
    doc = TDocStd_Document("test")
    app.InitDocument(doc)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())

    box = BRepPrimAPI_MakeBox(5, 5, 5).Shape()
    label = shape_tool.NewShape()
    shape_tool.SetShape(label, box)
    # Typical BOM name: code_chineseName_internalID
    set_shape_name(label, "BYDQ83811_2F61H_002_金属卡扣")

    name = get_shape_name(label, shape_tool)
    assert name == "BYDQ83811_2F61H_002_金属卡扣", \
        f"Expected mixed name, got {repr(name)}"
    print(f"  [PASS] Mixed name: {name}")


def test_fallback_for_unnamed_label():
    """Test fallback to Part_NNN when no name attribute exists."""
    app = XCAFApp_Application.GetApplication()
    doc = TDocStd_Document("test")
    app.InitDocument(doc)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())

    box = BRepPrimAPI_MakeBox(10, 5, 3).Shape()
    label = shape_tool.NewShape()
    shape_tool.SetShape(label, box)
    # No TDataStd_Name set — should fall back to Part_NNN

    name = get_shape_name(label, shape_tool)
    tag = label.Tag()
    expected = f"Part_{tag}"
    assert name == expected, f"Expected {expected}, got {repr(name)}"
    print(f"  [PASS] Fallback name: {name}")


def main():
    print("=" * 50)
    print("get_shape_name Unicode Tests")
    print("=" * 50)
    try:
        test_ascii_name()
        test_chinese_name()
        test_mixed_ascii_chinese_name()
        test_fallback_for_unnamed_label()
        print("-" * 50)
        print("ALL 4 TESTS PASSED")
        return 0
    except Exception as e:
        print(f"\n[FAIL] {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
