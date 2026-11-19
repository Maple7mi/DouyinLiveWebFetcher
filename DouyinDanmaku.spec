# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('sign.js', '.'), ('a_bogus.js', '.'), ('webmssdk.js', '.'), ('assets/ui_badges', 'assets/ui_badges'), ('assets/dy_emoji', 'assets/dy_emoji'), ('.venv/Lib/site-packages/py_mini_racer/mini_racer.dll', '.'), ('.venv/Lib/site-packages/py_mini_racer/icudtl.dat', '.'), ('.venv/Lib/site-packages/py_mini_racer/snapshot_blob.bin', '.')]
binaries = []
hiddenimports = ['betterproto', 'execjs', 'websocket', 'site_builder', 'site_server', 'browser_login']
tmp_ret = collect_all('py_mini_racer')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['aurora_gui.py'],
    pathex=['.'],
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
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DouyinDanmaku',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
