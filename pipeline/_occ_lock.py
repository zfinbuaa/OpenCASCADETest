"""
Global lock for OCCT BRep operations that are NOT thread-safe.

OCCT BRepBuilderAPI_*, BRepAlgoAPI_*, BRepMesh_IncrementalMesh,
BRepGProp_*, BRepBndLib_* all share global state and must be
serialized when called from concurrent worker threads.

Usage:
    from pipeline._occ_lock import OCC_BREP_LOCK

    with OCC_BREP_LOCK:
        BRepAlgoAPI_Cut(...).Shape()

To disable (debug only):
    Set environment variable PIPELINE_BREP_PARALLEL=1
"""

import os
import threading


class _NoOpLock:
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def acquire(self, *args, **kwargs): return True
    def release(self): pass


if os.environ.get("PIPELINE_BREP_PARALLEL", "0") == "1":
    OCC_BREP_LOCK = _NoOpLock()
else:
    OCC_BREP_LOCK = threading.RLock()
