import sys
from pathlib import Path

ROOT = Path(__file__).parent

version = Path(ROOT, "VERSION.txt").read_text(encoding="utf-8").strip()
va = version.split(".")
assert len(va) >= 3, f"VERSION must be x.y.z, got: {version}"
v0, v1, v2 = va[0], va[1], va[2]

content = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({v0}, {v1}, {v2}, 0),
    prodvers=({v0}, {v1}, {v2}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
    ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        u'080404b0',
        [StringStruct(u'CompanyName', u'AutoModel'),
        StringStruct(u'FileDescription', u'AutoModel Pipeline Engine'),
        StringStruct(u'FileVersion', u'{version}'),
        StringStruct(u'ProductVersion', u'{version}'),
        StringStruct(u'InternalName', u'AutoModel'),
        StringStruct(u'LegalCopyright', u'LGPL-3.0')])
      ]),
    VarFileInfo([VarStruct(u'Translation', [0x0804, 0x04b0])])
  ]
)
"""

out = Path(ROOT, "version_info.txt")
out.write_text(content, encoding="utf-8")
print(f"[OK] Written: {out}  (version={version})")
