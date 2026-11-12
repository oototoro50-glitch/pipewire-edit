# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['pipewire-dac-gui'],
    pathex=[],
    binaries=[
        ('/usr/lib/gtk-4.0/4.0.0/immodules/libim-fcitx5.so', 'lib/gtk-4.0/4.0.0/immodules'),
    ],
    datas=[('share/pipewire-dac-gui.desktop', 'share')],
    hiddenimports=[
        'gi.repository.Gdk',
        'gi.repository.Gio',
        'gi.repository.GLib',
        'gi.repository.Gtk',
        'gi.repository.GObject',
        'gi.repository.Gsk',
        'gi.repository.Graphene',
        'gi.repository.Pango',
        'gi.repository.cairo',
    ],
    hookspath=[],
    hooksconfig={
        'gi': {
            'icons': ['Adwaita'],
            'themes': ['Adwaita'],
            'languages': ['ja'],
            'module-versions': {
                'Gtk': '4.0',
                'Gdk': '4.0',
            },
        },
    },
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
    name='pipewire-dac-gui',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
