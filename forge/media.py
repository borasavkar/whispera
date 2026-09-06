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
