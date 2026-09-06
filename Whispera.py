"""Whispera — giriş noktası.

Çalıştırmak için:  python Whispera.py
"""

from __future__ import annotations

import os
import sys
import traceback

class _BosAkis:
    """Konsolsuz (pencereli) exe'de sys.stdout None olabiliyor.

    Whisper ilerlemeyi ve segmentleri `print` ile yazıyor; akış None olursa
    kütüphanenin içinden AttributeError gelir. Yutan bir akış koyarak bunu
    baştan kesiyoruz.
    """

    encoding = "utf-8"

    def write(self, metin):
        return len(metin or "")

    def flush(self):
        pass

    def isatty(self):
        return False


if sys.stdout is None:
    sys.stdout = _BosAkis()
if sys.stderr is None:
    sys.stderr = _BosAkis()

# Türkçe karakterler Windows konsolunda cp1254'e takılmasın.
for _akis in (sys.stdout, sys.stderr):
    try:
        _akis.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Donmuş exe'de __file__ paketin içindeki sanal yolu gösterir; raporlar
# kullanıcının göreceği yere — exe'nin yanına — yazılmalı.
_KOK = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(__file__)))
if _KOK not in sys.path:
    sys.path.insert(0, _KOK)


def _hata_yakalayici(tur, deger, izleme):
    """Yakalanmayan hatalar sessizce kaybolmasın."""
    metin = "".join(traceback.format_exception(tur, deger, izleme))
    try:
        with open(os.path.join(_KOK, "HATA_RAPORU.txt"), "w", encoding="utf-8") as f:
            f.write(metin)
    except Exception:
        pass
    print(metin, file=sys.stderr)


def kendini_sina(medya_yolu: str | None) -> int:
    """Derlemenin sağlam olduğunu arayüz açmadan doğrular.

    Donmuş (exe) sürümde asıl risk paketlemenin ROCm çalışma zamanını doğru
    taşıyıp taşımadığı; bunu ancak gerçekten model yükleyip bakarak anlarız.
    Medya yolu verilirse baştan sona bir transkripsiyon da yapılır.

        Whispera.exe --kendini-sina [dosya.mp4]
    """
    rapor_yolu = os.path.join(_KOK, "SINAMA_RAPORU.txt")
    satirlar: list[str] = []

    def yaz(metin: str = "") -> None:
        # Pencereli exe'de konsol yok; rapor dosyaya da düşsün.
        satirlar.append(metin)
        print(metin)
        try:
            with open(rapor_yolu, "w", encoding="utf-8") as f:
                f.write("\n".join(satirlar) + "\n")
        except Exception:
            pass

    def bitir(kod: int, sonuc: str) -> int:
        yaz()
        yaz(f"SONUÇ: {sonuc}")
        yaz(f"(bu rapor: {rapor_yolu})")
        return kod

    from forge import media
    from forge.config import Ayarlar
    from forge.version import SURUM
    from forge.whisper_engine import WhisperMotoru

    yaz(f"Whispera v{SURUM} — kendini sınama")
    yaz(f"donmuş paket   : {getattr(sys, 'frozen', False)}")
    yaz(f"kök klasör     : {_KOK}")

    ffmpeg = media.ffmpeg_coz("")
    yaz(f"ffmpeg         : {ffmpeg or 'BULUNAMADI'}")

    motor = WhisperMotoru(log=lambda m: yaz(f"  {m}"))
    try:
        motor.kutuphaneleri_yukle()
        import torch
    except Exception as e:
        yaz(f"HATA: kütüphaneler yüklenemedi — {e.__class__.__name__}: {e}")
        yaz(traceback.format_exc())
        return bitir(2, "KÜTÜPHANE YÜKLENEMEDİ")

    yaz(f"torch          : {torch.__version__}")
    try:
        gpu_var = bool(torch.cuda.is_available())
    except Exception as e:
        gpu_var = False
        yaz(f"GPU sorgusu hata verdi: {e}")
    yaz(f"donanım        : {motor.donanim_bilgisi()}")
    yaz(f"GPU kullanıyor : {gpu_var}")

    if not medya_yolu:
        yaz()
        yaz("Medya verilmedi; yalnızca kütüphane/donanım yüklemesi denendi.")
        return bitir(0 if gpu_var else 1, "YÜKLEME TAMAM" if gpu_var else "GPU YOK (CPU'ya düşer)")

    try:
        ayarlar = Ayarlar()
        ayarlar.model = "tiny"
        ayarlar.dil = "en"
        cihaz = motor.modeli_hazirla(ayarlar)

        ses = media.sesi_coz(medya_yolu, ffmpeg)
        yaz(f"ses            : {media.sure_metni(len(ses) / media.ORNEKLEME_HIZI)}")

        sayac = {"segment": 0, "ilerleme": 0}
        sonuc = motor.cevir_metne(
            ses, ayarlar, "en", None, cihaz,
            ilerleme=lambda o: sayac.__setitem__("ilerleme", sayac["ilerleme"] + 1),
            segment_geldi=lambda b, s, m: (
                sayac.__setitem__("segment", sayac["segment"] + 1),
                yaz(f"  [{b:6.2f}] {m[:70]}")),
            bilgi_satiri=lambda s: None,
            iptal=lambda: False,
        )
    except Exception as e:
        yaz(f"HATA: {e.__class__.__name__}: {e}")
        yaz(traceback.format_exc())
        return bitir(2, "TRANSKRİPSİYON ÇÖKTÜ")

    segmentler = sonuc.get("segments") or []
    yaz()
    yaz(f"cihaz          : {cihaz.upper()}")
    yaz(f"segment        : {len(segmentler)}")
    yaz(f"canlı akış     : {sayac['segment']} satır")
    yaz(f"ilerleme tiki  : {sayac['ilerleme']}")
    return bitir(0 if segmentler else 3, "TAMAM" if segmentler else "METİN ÇIKMADI")


def main() -> int:
    sys.excepthook = _hata_yakalayici

    if "--kendini-sina" in sys.argv:
        kalan = [a for a in sys.argv[1:] if a != "--kendini-sina"]
        return kendini_sina(kalan[0] if kalan else None)

    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("PySide6 kurulu değil.\n\n    pip install PySide6\n", file=sys.stderr)
        return 1

    # Yüksek DPI ekranlarda kesirli ölçekleme düzgün yuvarlanısın.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    from forge.ui.main_window import uygulamayi_calistir
    return uygulamayi_calistir()


if __name__ == "__main__":
    raise SystemExit(main())
