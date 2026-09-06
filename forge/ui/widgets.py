"""Yeniden kullanılan arayüz parçaları.

Buradaki her sınıf tek bir görsel işi yapar ve iş mantığı taşımaz; ana pencere
bunları birleştirir.
"""

from __future__ import annotations

import html
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import MEDYA_UZANTILARI
from .theme import ESIT_YAZI, GUNLUK_RENKLERI, R


# --------------------------------------------------------------------------
# Küçük yapı taşları
# --------------------------------------------------------------------------

def ozellik_yenile(widget: QWidget, ad: str, deger) -> None:
    """QSS'teki `[ad="deger"]` seçicilerinin yeniden değerlendirilmesini sağlar."""
    widget.setProperty(ad, deger)
    stil = widget.style()
    stil.unpolish(widget)
    stil.polish(widget)
    widget.update()


class Rozet(QLabel):
    """Başlık şeridindeki durum etiketi (GPU, FFmpeg, aşama…)."""

    def __init__(self, metin: str = "", durum: str = "notr", parent=None):
        super().__init__(metin, parent)
        self.setObjectName("rozet")
        self.setProperty("durum", durum)
        self.setAlignment(Qt.AlignCenter)

    def guncelle(self, metin: str, durum: str = "notr") -> None:
        self.setText(metin)
        ozellik_yenile(self, "durum", durum)


class Kart(QFrame):
    """Başlıklı, yuvarlak köşeli yüzey."""

    def __init__(self, baslik: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("kart")
        self.duzen = QVBoxLayout(self)
        self.duzen.setContentsMargins(14, 12, 14, 14)
        self.duzen.setSpacing(10)

        self.baslik_satiri = QHBoxLayout()
        self.baslik_satiri.setSpacing(8)
        if baslik:
            etiket = QLabel(baslik.upper())
            etiket.setObjectName("kartBaslik")
            self.baslik_satiri.addWidget(etiket)
        self.baslik_satiri.addStretch(1)
        self.duzen.addLayout(self.baslik_satiri)

    def ekle(self, widget: QWidget, *args) -> None:
        self.duzen.addWidget(widget, *args)

    def duzen_ekle(self, duzen) -> None:
        self.duzen.addLayout(duzen)


class Ayirac(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ayiracCizgi")
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)


class BolumBaslik(QLabel):
    def __init__(self, metin: str, parent=None):
        super().__init__(metin.upper(), parent)
        self.setObjectName("bolumBaslik")


# --------------------------------------------------------------------------
# Dosya bırakma alanı
# --------------------------------------------------------------------------

class BirakmaAlani(QFrame):
    """Sürükle-bırak hedefi; tıklayınca da dosya seçtirir."""

    dosyalar_birakildi = Signal(list)
    tiklandi = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("birakmaAlani")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(104)

        duzen = QVBoxLayout(self)
        duzen.setContentsMargins(16, 14, 16, 14)
        duzen.setSpacing(3)
        duzen.addStretch(1)

        self._baslik = QLabel("Medya dosyalarını buraya sürükleyin")
        self._baslik.setObjectName("birakmaBaslik")
        self._baslik.setAlignment(Qt.AlignCenter)

        self._alt = QLabel("veya tıklayıp seçin  ·  video ve ses dosyaları")
        self._alt.setObjectName("birakmaAlt")
        self._alt.setAlignment(Qt.AlignCenter)

        duzen.addWidget(self._baslik)
        duzen.addWidget(self._alt)
        duzen.addStretch(1)

    # --- sürükle-bırak ---------------------------------------------------

    @staticmethod
    def _yollari_al(olay) -> list[str]:
        yollar = []
        for url in olay.mimeData().urls():
            yol = url.toLocalFile()
            if not yol:
                continue
            if os.path.isdir(yol):
                yollar.extend(_klasordeki_medya(yol))
            elif yol.lower().endswith(MEDYA_UZANTILARI):
                yollar.append(yol)
        return yollar

    def dragEnterEvent(self, olay):
        if olay.mimeData().hasUrls() and self._yollari_al(olay):
            olay.acceptProposedAction()
            ozellik_yenile(self, "aktif", "true")
        else:
            olay.ignore()

    def dragLeaveEvent(self, olay):
        ozellik_yenile(self, "aktif", "false")
        super().dragLeaveEvent(olay)

    def dropEvent(self, olay):
        yollar = self._yollari_al(olay)
        ozellik_yenile(self, "aktif", "false")
        if yollar:
            olay.acceptProposedAction()
            self.dosyalar_birakildi.emit(yollar)
        else:
            olay.ignore()

    def mousePressEvent(self, olay):
        if olay.button() == Qt.LeftButton:
            self.tiklandi.emit()
        super().mousePressEvent(olay)


def _klasordeki_medya(klasor: str) -> list[str]:
    try:
        adlar = sorted(os.listdir(klasor))
    except OSError:
        return []
    return [os.path.join(klasor, ad) for ad in adlar
            if ad.lower().endswith(MEDYA_UZANTILARI)
            and os.path.isfile(os.path.join(klasor, ad))]


# --------------------------------------------------------------------------
# Metin görünümleri
# --------------------------------------------------------------------------

class GunlukGorunumu(QPlainTextEdit):
    """Renklendirilmiş, otomatik kaydırmalı terminal görünümü.

    Uzun kuyruklarda bellek şişmesin diye satır sayısı sınırlıdır; kullanıcı
    yukarı kaydırmışsa yeni satır geldiğinde aşağı zıplamayız.
    """

    def __init__(self, parent=None, azami_satir: int = 6000):
        super().__init__(parent)
        self.setObjectName("gunluk")
        self.setReadOnly(True)
        self.setMaximumBlockCount(azami_satir)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setFont(QFont(ESIT_YAZI.split(",")[0].strip('"'), 9))

    def _dipte_mi(self) -> bool:
        cubuk = self.verticalScrollBar()
        return cubuk.value() >= cubuk.maximum() - 4

    def satir_ekle(self, metin: str) -> None:
        dipteydi = self._dipte_mi()

        renk = R["metin_soluk"]
        for isaret, deger in GUNLUK_RENKLERI.items():
            if isaret in metin[:6]:
                renk = deger
                break
        else:
            if metin.startswith("="):
                renk = R["cizgi_guclu"]
            elif metin.startswith("   "):
                renk = R["metin_silik"]

        self.appendHtml(
            f'<span style="color:{renk}; white-space:pre">{html.escape(metin)}</span>')

        if dipteydi:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def temizle(self) -> None:
        self.clear()


class AkisGorunumu(QPlainTextEdit):
    """Whisper metni ürettikçe akan canlı altyazı listesi."""

    def __init__(self, parent=None, azami_satir: int = 20000):
        super().__init__(parent)
        self.setObjectName("akis")
        self.setReadOnly(True)
        self.setMaximumBlockCount(azami_satir)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.sayac = 0

    def _dipte_mi(self) -> bool:
        cubuk = self.verticalScrollBar()
        return cubuk.value() >= cubuk.maximum() - 4

    def segment_ekle(self, baslangic: float, bitis: float, metin: str) -> None:
        dipteydi = self._dipte_mi()
        self.sayac += 1
        damga = _kisa_damga(baslangic)
        self.appendHtml(
            f'<span style="color:{R["vurgu"]}">{damga}</span>'
            f'<span style="color:{R["metin_silik"]}">  ·  </span>'
            f'<span style="color:{R["metin"]}">{html.escape(metin)}</span>'
        )
        if dipteydi:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def basliga_yaz(self, metin: str) -> None:
        self.appendHtml(
            f'<span style="color:{R["metin_silik"]}">— {html.escape(metin)} —</span>')

    def temizle(self) -> None:
        self.clear()
        self.sayac = 0


def _kisa_damga(saniye: float) -> str:
    saniye = int(max(0, saniye))
    saat, kalan = divmod(saniye, 3600)
    dakika, sn = divmod(kalan, 60)
    if saat:
        return f"{saat}:{dakika:02d}:{sn:02d}"
    return f"{dakika:02d}:{sn:02d}"


# --------------------------------------------------------------------------
# Parametre denetimleri
# --------------------------------------------------------------------------

class SecimKutusu(QComboBox):
    """(değer, etiket) çiftleriyle çalışan açılır liste."""

    def __init__(self, secenekler: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        for deger, etiket in secenekler:
            self.addItem(etiket, deger)

    def deger(self):
        return self.currentData()

    def degeri_ayarla(self, deger) -> None:
        indeks = self.findData(deger)
        if indeks >= 0:
            self.setCurrentIndex(indeks)


class IstegeBagliSayi(QDoubleSpinBox):
    """«Kapalı/varsayılan» durumu olabilen ondalık alan.

    En küçük değer özel metinle gösterilir ve `deger()` orada None döner —
    whisper'ın `None` kabul eden parametreleri için (patience, eşikler…).
    """

    def __init__(self, en_az: float, en_cok: float, adim: float = 0.1,
                 ondalik: int = 2, ozel_metin: str = "kapalı", parent=None):
        super().__init__(parent)
        self._bos_deger = en_az - adim
        self.setRange(self._bos_deger, en_cok)
        self.setSingleStep(adim)
        self.setDecimals(ondalik)
        self.setSpecialValueText(ozel_metin)
        self.setValue(self._bos_deger)

    def deger(self) -> float | None:
        return None if self.value() <= self._bos_deger + 1e-9 else float(self.value())

    def degeri_ayarla(self, deger) -> None:
        self.setValue(self._bos_deger if deger is None else float(deger))


class IstegeBagliTamSayi(QSpinBox):
    """`IstegeBagliSayi`'nın tam sayı sürümü (satır genişliği, kelime sayısı…)."""

    def __init__(self, en_az: int, en_cok: int, ozel_metin: str = "sınırsız", parent=None):
        super().__init__(parent)
        self._bos_deger = en_az - 1
        self.setRange(self._bos_deger, en_cok)
        self.setSpecialValueText(ozel_metin)
        self.setValue(self._bos_deger)

    def deger(self) -> int | None:
        return None if self.value() <= self._bos_deger else int(self.value())

    def degeri_ayarla(self, deger) -> None:
        self.setValue(self._bos_deger if deger is None else int(deger))


class Onay(QCheckBox):
    def __init__(self, metin: str, ipucu: str = "", parent=None):
        super().__init__(metin, parent)
        if ipucu:
            self.setToolTip(ipucu)

    def deger(self) -> bool:
        return self.isChecked()

    def degeri_ayarla(self, deger) -> None:
        self.setChecked(bool(deger))
