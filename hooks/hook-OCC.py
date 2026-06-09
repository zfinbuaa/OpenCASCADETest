from PyInstaller.utils.hooks import collect_submodules, collect_dynamic_libs
import os, glob, sys

def _keep(name):
    if name.startswith('OCC.Display'):
        return False
    return True

hiddenimports = [m for m in collect_submodules('OCC') if _keep(m)]

binaries = collect_dynamic_libs('OCC')

env_root = os.path.dirname(sys.executable)
bin_dir = os.path.join(env_root, 'Library', 'bin')
if os.path.isdir(bin_dir):
    for dll in glob.glob(os.path.join(bin_dir, '*.dll')):
        name = os.path.basename(dll).lower()
        if name.startswith(('qt5', 'qt6', 'pyside', 'pyqt', 'opencv_videoio_ffmpeg',
                            'avcodec', 'avformat', 'avutil', 'avfilter',
                            'swscale', 'swresample', 'avdevice')):
            continue
        binaries.append((dll, 'OCC/Core'))
