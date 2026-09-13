# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller yapılandırması — Whispera.

Derlemek için:

    .venv\\Scripts\\pyinstaller.exe Whispera.spec --noconfirm

Sonuç `dist\\Whispera\\` klasörüne çıkar; `Whispera.exe` ile açılır.

Neden `onedir`: ROCm çalışma zamanı tek başına ~1,3 GB DLL taşıyor. `onefile`
her açılışta bunların tamamını geçici klasöre açardı — açılış dakikalar sürer ve
disk boşa yazılır. `onedir` klasörünü olduğu gibi zipleyip dağıtabilirsiniz.
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

sys.path.insert(0, os.path.abspath("."))
from forge.version import SURUM, UYGULAMA_ADI     # noqa: E402
from forge.config import eski_ayarlari_tasi        # noqa: E402

# Derleme `dist/Whispera/` klasörünü silecek. İçinde eski sürümün yazdığı
# ayarlar (API anahtarları) varsa, silinmeden önce kullanıcı profiline taşı.
_tasima = eski_ayarlari_tasi(os.path.abspath("."))
if _tasima:
    print(f"[Whispera] {_tasima}")

SITE = Path(SPECPATH) / ".venv" / "Lib" / "site-packages"


# --------------------------------------------------------------------------
# ROCm çalışma zamanı
# --------------------------------------------------------------------------
#
# `rocm_sdk.find_libraries()` DLL'leri `importlib.import_module("_rocm_sdk_core")`
# sonucunun klasöründen çözüyor. Bu yüzden bu iki paketin klasör düzeni
# bozulmadan taşınmalı — dosyaları tek tek `datas` olarak veriyoruz.
#
# Nokta ile başlayan klasörler (.kpack, .info, .devel_links) GPU'ya özel çekirdek
# paketlerini taşıyor ve hiçbir otomatik toplayıcı bunları görmüyor; elle
# eklenmezse uygulama GPU'da çalışmaz.

def klasoru_topla(kok: Path, hedef_kok: str, atla=()):
    """(kaynak, hedef_klasör) çiftleri üretir; `atla` göreli yol önekleridir."""
    toplanan = []
    for dizin, _alt, dosyalar in os.walk(kok):
        goreli = Path(dizin).relative_to(kok).as_posix()
        if goreli == ".":
            goreli = ""
        if any(goreli == a or goreli.startswith(a + "/") for a in atla):
            continue
        if "__pycache__" in goreli.split("/"):
            continue
        hedef = f"{hedef_kok}/{goreli}" if goreli else hedef_kok
        for ad in dosyalar:
            toplanan.append((os.path.join(dizin, ad), hedef))
    return toplanan


rocm_datas = []

# Derleyici takımı (clang/lld/flang, ~1,9 GB) yalnızca kaynaktan HIP derlemek
# için gerekli. Çalışma anındaki JIT amd_comgr.dll + amdgcn bitcode üzerinden
# yürüdüğü için bu klasör dışarıda bırakılıyor.
rocm_datas += klasoru_topla(
    SITE / "_rocm_sdk_core", "_rocm_sdk_core",
    atla=("lib/llvm/bin", "include", "libexec", "share"),
)
rocm_datas += klasoru_topla(SITE / "_rocm_sdk_libraries", "_rocm_sdk_libraries")

# hipify-clang gibi geliştirici araçları da gereksiz.
rocm_datas = [(k, h) for k, h in rocm_datas
              if not (h == "_rocm_sdk_core/bin" and k.lower().endswith(".exe"))]


# --------------------------------------------------------------------------
# Diğer veri dosyaları
# --------------------------------------------------------------------------

datas = list(rocm_datas)

# whisper: mel süzgeçleri ve tokenizer sözlükleri (paketin içinden okunuyor)
datas += collect_data_files("whisper", includes=["assets/*"])

# Uygulama simgesi (pencere ikonu için çalışma anında da aranıyor)
if os.path.isfile(os.path.join(SPECPATH, "icon.ico")):
    datas += [(os.path.join(SPECPATH, "icon.ico"), ".")]


hiddenimports = [
    # tiktoken kodlamaları eklenti keşfiyle yükleniyor; statik analiz görmüyor.
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
    # ROCm yükleyicisi bunları isimle import ediyor.
    "rocm_sdk",
    "rocm_sdk._dist_info",
    "_rocm_sdk_core",
    "_rocm_sdk_libraries",
    "torch._rocm_init",
]
hiddenimports += collect_submodules("forge")


# Kullanılmayan ağır bağımlılıklar — hepsi PySide6/torch üzerinden dolaylı
# çekilebiliyor ve derlemeyi gigabaytlarca şişiriyor.
excludes = [
    # NOT: `unittest` hariç tutulamaz — torch.utils._config_module onu modül
    # düzeyinde import ediyor, çıkarılırsa torch hiç yüklenmiyor.
    "tkinter", "test", "pydoc_data",
    "matplotlib", "IPython", "notebook", "jupyter", "pytest",
    "torchvision", "torchaudio",
    "transformers",          # yerel çeviri kurulu değil; varsa da isteğe bağlı
    # Qt'nin bu modülleri kullanılmıyor:
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtWebView",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.QtQml", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtSerialBus", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtHelp", "PySide6.QtDesigner",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSpatialAudio",
    "PySide6.QtTextToSpeech", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtStateMachine", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
]


# --------------------------------------------------------------------------
# Windows sürüm kaynağı (dosya özelliklerinde görünen bilgiler)
# --------------------------------------------------------------------------

def surum_kaynagi():
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable,
        VarFileInfo, VarStruct, VSVersionInfo,
    )

    parcalar = [int(p) for p in SURUM.split(".")] + [0, 0, 0, 0]
    dortlu = tuple(parcalar[:4])

    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=dortlu, prodvers=dortlu,
                          mask=0x3F, flags=0x0, OS=0x40004,
                          fileType=0x1, subtype=0x0, date=(0, 0)),
        kids=[
            StringFileInfo([StringTable("041F04B0", [      # 041F = Türkçe
                StringStruct("CompanyName", ""),
                StringStruct("FileDescription", "Whisper tabanlı altyazı üretici"),
                StringStruct("FileVersion", SURUM),
                StringStruct("InternalName", UYGULAMA_ADI),
                StringStruct("OriginalFilename", f"{UYGULAMA_ADI}.exe"),
                StringStruct("ProductName", UYGULAMA_ADI),
                StringStruct("ProductVersion", SURUM),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x041F, 1200])]),
        ],
    )


a = Analysis(
    ["Whispera.py"],
    pathex=[SPECPATH],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=UYGULAMA_ADI,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                    # UPX torch/ROCm DLL'lerini bozabiliyor
    console=False,                # pencereli uygulama
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(SPECPATH, "icon.ico") if os.path.isfile(
        os.path.join(SPECPATH, "icon.ico")) else None,
    version=surum_kaynagi(),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=UYGULAMA_ADI,
)
