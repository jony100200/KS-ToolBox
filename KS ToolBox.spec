# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('assets', 'assets')]
binaries = []
hiddenimports = []
# Ship the tools, but not their smoke tests (dev-only, never imported at runtime).
hiddenimports += [m for m in collect_submodules('tools') if not m.endswith('.test_smoke')]
tmp_ret = collect_all('customtkinter')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# Drop build-only assets from the shipped bundle: the 1.5 MB icon SOURCE
# (KSToolBox_1024.png, used only to regenerate the .ico) — the runtime icon is
# the .ico + the 256px .png, which stay. Also drop any stray test files.
a.datas = [d for d in a.datas
           if 'KSToolBox_1024' not in d[0] and not d[0].endswith('test_smoke.py')]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='KS ToolBox',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets\\KSToolBox.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='KS ToolBox',
)
