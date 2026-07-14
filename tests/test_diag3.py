import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.XCAFApp import XCAFApp_Application
from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.TDataStd import TDataStd_Name
from OCC.Core.TDF import TDF_AttributeIterator

app = XCAFApp_Application.GetApplication()
doc = TDocStd_Document("test")
app.InitDocument(doc)
shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())

# Test 1: Chinese name
box = BRepPrimAPI_MakeBox(10, 5, 3).Shape()
label = shape_tool.NewShape()
shape_tool.SetShape(label, box)
TDataStd_Name.Set(label, "\u5de6\u524d\u51cf\u632f\u5668\u652f\u67f1\u603b\u6210")
# = 左前减振器支柱总成

it = TDF_AttributeIterator(label)
found = False
while it.More():
    attr = it.Value()
    if attr.ID() == TDataStd_Name.GetID():
        tn = TDataStd_Name.DownCast(attr)
        ext_str = tn.Get()
        chars = [chr(int(ext_str.Value(i))) for i in range(1, ext_str.Length() + 1)]
        name = ''.join(chars)
        print(f"[PASS] Chinese: {name}")
        assert name == "左前减振器支柱总成"
        found = True
        break
    it.Next()
assert found, "Attribute not found!"

# Test 2: ASCII name
label2 = shape_tool.NewShape()
shape_tool.SetShape(label2, box)
TDataStd_Name.Set(label2, "TestPart_001")

it2 = TDF_AttributeIterator(label2)
found2 = False
while it2.More():
    attr = it2.Value()
    if attr.ID() == TDataStd_Name.GetID():
        tn = TDataStd_Name.DownCast(attr)
        ext_str = tn.Get()
        chars = [chr(int(ext_str.Value(i))) for i in range(1, ext_str.Length() + 1)]
        name = ''.join(chars)
        print(f"[PASS] ASCII: {name}")
        assert name == "TestPart_001"
        found2 = True
        break
    it2.Next()
assert found2

# Test 3: Mixed name (BOM-style)
label3 = shape_tool.NewShape()
shape_tool.SetShape(label3, box)
TDataStd_Name.Set(label3, "BYDQ83811_2F61H_002_\u91d1\u5c5e\u5361\u6263")
# = BTDQ83811_2F61H_002_金属卡扣

it3 = TDF_AttributeIterator(label3)
found3 = False
while it3.More():
    attr = it3.Value()
    if attr.ID() == TDataStd_Name.GetID():
        tn = TDataStd_Name.DownCast(attr)
        ext_str = tn.Get()
        chars = [chr(int(ext_str.Value(i))) for i in range(1, ext_str.Length() + 1)]
        name = ''.join(chars)
        print(f"[PASS] Mixed: {name}")
        assert name == "BYDQ83811_2F61H_002_金属卡扣"
        found3 = True
        break
    it3.Next()
assert found3

# Test 4: Fallback when no attribute
label4 = shape_tool.NewShape()
shape_tool.SetShape(label4, box)
# No TDataStd_Name set

it4 = TDF_AttributeIterator(label4)
found4 = False
while it4.More():
    if it4.Value().ID() == TDataStd_Name.GetID():
        found4 = True
        break
    it4.Next()
assert not found4, "Should not find TDataStd_Name"
print(f"[PASS] Fallback: label.Tag()={label4.Tag()}")

print("\nALL 4 TESTS PASSED")
