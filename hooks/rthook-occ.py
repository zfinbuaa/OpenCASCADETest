import os
import sys

if hasattr(sys, '_MEIPASS') and os.name == 'nt':
    _occ_core = os.path.join(sys._MEIPASS, 'OCC', 'Core')
    os.environ['PATH'] = _occ_core + os.pathsep + sys._MEIPASS + os.pathsep + os.environ.get('PATH', '')
    os.environ['OCCT_ESSENTIALS_ROOT'] = _occ_core
    try:
        os.add_dll_directory(sys._MEIPASS)
    except (OSError, AttributeError):
        pass
    if os.path.isdir(_occ_core):
        try:
            os.add_dll_directory(_occ_core)
        except (OSError, AttributeError):
            pass
