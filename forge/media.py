"""FFmpeg bulma ve ses çözme.

Whisper'ın kendi `load_audio` fonksiyonu ffmpeg'i PATH'ten çağırır ve elle
seçilmiş bir yolu kullanmaya izin vermez. Burada aynı işi yaparız ama ffmpeg
yolunu biz belirleriz ve hata mesajını okunur hâle getiririz.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import numpy as np

ORNEKLEME_HIZI = 16000

# Windows'ta alt süreçler konsol penceresi açmasın.
KONSOLSUZ = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


class SesCozmeHatasi(RuntimeError):
    pass


def ffmpeg_coz(elle: str | None = None) -> str | None:
    """Kullanılacak ffmpeg yolunu döndürür; bulunamazsa None.

    Elle bir yol verilmişse yalnızca o denenir — sessizce sisteme düşmek,
    kullanıcının seçtiği sürüm yerine başka bir sürümün çalışmasına yol açar.
    """
    elle = (elle or "").strip().strip('"').strip()
    if elle:
        return elle if os.path.isfile(elle) else None
    return shutil.which("ffmpeg")


def ffmpeg_surumu(yol: str) -> str | None:
    """`ffmpeg -version` çıktısının ilk satırı; çalıştırılamazsa None."""
    try:
        sonuc = subprocess.run([yol, "-version"], capture_output=True,
                               timeout=10, creationflags=KONSOLSUZ)
    except Exception:
        return None
    if sonuc.returncode != 0:
        return None
    satirlar = sonuc.stdout.decode(errors="replace").splitlines()
    return satirlar[0].strip() if satirlar else None


def ffmpeg_kisa_surum(surum_satiri: str) -> str:
    """Rozete sığacak kadar kısa sürüm metni.

    Tam satır "ffmpeg version 7.1-full_build-www.gyan.dev Copyright…" gibi
    olabiliyor; rozete yalnızca sürüm numarası girsin.
    """
    surum = surum_satiri.replace("ffmpeg version ", "").split(" Copyright")[0].strip()
    kisa = surum.split("-")[0]
    return kisa if kisa else surum[:14]


def sesi_coz(medya_yolu: str, ffmpeg_yolu: str) -> np.ndarray:
    """Medyayı 16 kHz mono float32 dizisine çevirir (whisper'ın beklediği biçim)."""
    komut = [
        ffmpeg_yolu, "-nostdin", "-threads", "0",
        "-i", medya_yolu,
        "-f", "s16le", "-ac", "1", "-acodec", "pcm_s16le",
        "-ar", str(ORNEKLEME_HIZI), "-",
    ]
    try:
        sonuc = subprocess.run(komut, capture_output=True, creationflags=KONSOLSUZ)
    except Exception as e:
        raise SesCozmeHatasi(f"FFmpeg çalıştırılamadı: {e}") from e

    if sonuc.returncode != 0:
        satirlar = sonuc.stderr.decode(errors="replace").strip().splitlines()
        son = "\n".join(satirlar[-5:]) if satirlar else "(çıktı yok)"
        raise SesCozmeHatasi(f"FFmpeg sesi çözemedi:\n{son}")

    if not sonuc.stdout:
        raise SesCozmeHatasi("FFmpeg boş ses döndürdü — dosyada ses akışı olmayabilir.")

    return np.frombuffer(sonuc.stdout, np.int16).flatten().astype(np.float32) / 32768.0


def geri_donusume_gonder(yol: str) -> tuple[bool, str]:
    """Dosyayı Windows Geri Dönüşüm Kutusu'na taşır; asla kalıcı silmez.

    `SHFileOperationW` + `FOF_ALLOWUNDO` kullanılır. Windows, Geri Dönüşüm
    Kutusu olmayan sürücülerde (USB bellek, ağ klasörü) aynı çağrıyla dosyayı
    KALICI siler; bu yüzden yalnızca yerel sabit disklerde çalışır, diğerlerinde
    dosyaya dokunmaz. (başarılı mı, açıklama) döner.
    """
    if os.name != "nt":
        return False, "yalnızca Windows'ta destekleniyor"
    if not os.path.isfile(yol):
        return False, "dosya bulunamadı"

    import ctypes
    from ctypes import wintypes

    tam_yol = os.path.abspath(yol)
    surucu = os.path.splitdrive(tam_yol)[0]
    if not surucu or surucu.startswith("\\\\"):
        return False, "ağ yolunda Geri Dönüşüm Kutusu yok"
    DRIVE_FIXED = 3
    if ctypes.windll.kernel32.GetDriveTypeW(surucu + "\\") != DRIVE_FIXED:
        return False, "bu sürücüde Geri Dönüşüm Kutusu yok (USB/ağ)"

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE = 0x0003
    FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 0x4, 0x10, 0x40, 0x400
    islem = SHFILEOPSTRUCTW()
    islem.wFunc = FO_DELETE
    islem.pFrom = tam_yol + "\0\0"           # çift NUL ile biten liste
    islem.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    sonuc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(islem))
    if sonuc != 0 or islem.fAnyOperationsAborted:
        return False, f"Windows işlemi reddetti (kod {sonuc})"
    if os.path.exists(tam_yol):
        return False, "dosya hâlâ yerinde"
    return True, "Geri Dönüşüm Kutusu'na taşındı"


def sure_metni(saniye: float) -> str:
    saniye = int(max(0, saniye))
    saat, kalan = divmod(saniye, 3600)
    dakika, saniye = divmod(kalan, 60)
    if saat:
        return f"{saat}sa {dakika}dk {saniye}sn"
    if dakika:
        return f"{dakika}dk {saniye}sn"
    return f"{saniye}sn"


def zaman_damgasi(saniye: float, ayirici: str = ",") -> str:
    """SRT/VTT biçiminde HH:MM:SS,mmm."""
    saniye = max(0.0, float(saniye))
    saat = int(saniye // 3600)
    dakika = int((saniye % 3600) // 60)
    kalan = saniye % 60
    milisaniye = int(round((kalan - int(kalan)) * 1000))
    tam = int(kalan)
    if milisaniye == 1000:
        tam += 1
        milisaniye = 0
    return f"{saat:02d}:{dakika:02d}:{tam:02d}{ayirici}{milisaniye:03d}"
