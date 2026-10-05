# Run inside embedded Python 3.7 (host37): compiles src/*.py into dist/rsim.ts4script and copies the JSON files
import os, py_compile, shutil, zipfile
ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, 'dist')
# Empty dist/ instead of deleting it: the folder may be open in Explorer.
os.makedirs(DIST, exist_ok=True)
for old in os.listdir(DIST):
    path = os.path.join(DIST, old)
    if os.path.isfile(path):
        os.remove(path)
out = os.path.join(DIST, 'rsim.ts4script')
with zipfile.ZipFile(out, 'w', zipfile.ZIP_STORED) as z:
    for name in ('rsim_engine', 'rsim_preview'):
        pyc = os.path.join(DIST, name + '.pyc')
        py_compile.compile(os.path.join(ROOT, 'src', name + '.py'), cfile=pyc, dfile=name + '.py', doraise=True)
        z.write(pyc, name + '.pyc')
        os.remove(pyc)
    # Data files live inside the .ts4script: CurseForge rejects .json files in Mods archives.
    for name in ('rsim_profiles.json', 'rsim_tones.json'):
        z.write(os.path.join(ROOT, 'data', name), name)
for name in ('rsim.cfg',):
    if os.path.exists(os.path.join(ROOT, 'data', name)):
        shutil.copy(os.path.join(ROOT, 'data', name), DIST)
