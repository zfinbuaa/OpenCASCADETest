import sys, os, traceback
print('[DBG] sys.path:', sys.path, flush=True)
print('[DBG] cwd:', os.getcwd(), flush=True)
print('[DBG] _MEIPASS:', getattr(sys,'_MEIPASS','?'), flush=True)
try:
    import OCC
    print('[DBG] OCC ok:', OCC.__file__, flush=True)
except Exception as e:
    traceback.print_exc()
try:
    import OCC.Core
    print('[DBG] OCC.Core ok:', OCC.Core.__file__, flush=True)
except Exception as e:
    traceback.print_exc()
try:
    from OCC.Core import _Standard
    print('[DBG] _Standard ok:', _Standard.__file__, flush=True)
except Exception as e:
    traceback.print_exc()
try:
    from OCC.Core import _XCAFApp
    print('[DBG] _XCAFApp ok:', _XCAFApp.__file__, flush=True)
except Exception as e:
    traceback.print_exc()
