"""Renk paleti ve uygulama genelinde geçerli stil sayfası.

Tek bir koyu tema var: kömür grisi zemin üzerine "ocak" turuncusu vurgu —
uygulamanın adına da yakışıyor. Renkler yalnızca burada tanımlanır; widget'lar
`R` sözlüğünden okur ya da QSS'e bırakır.
"""

from __future__ import annotations

import os
import tempfile

# --------------------------------------------------------------------------
# Palet
# --------------------------------------------------------------------------

R = {
    # zeminler
    "zemin":        "#0e1116",
    "zemin_alt":    "#12161d",
    "yuzey":        "#1a1f28",
    "yuzey_ust":    "#222833",
    "yuzey_vurgu":  "#2a313e",

    # çizgiler
    "cizgi":        "#252c38",
    "cizgi_guclu":  "#374050",

    # yazı
    "metin":        "#e9edf5",
    "metin_soluk":  "#98a2b6",
    "metin_silik":  "#626d82",

    # vurgu (ocak turuncusu)
    "vurgu":        "#ff7a2f",
    "vurgu_parlak": "#ff9a5c",
    "vurgu_koyu":   "#d95e18",
    "vurgu_zemin":  "#2a1a12",

    # durum
    "basari":       "#4ade80",
    "uyari":        "#fbbf24",
    "hata":         "#f87171",
    "bilgi":        "#60a5fa",
    "mor":          "#a78bfa",
}

# Günlükteki satırların baş harfine göre renklendirme.
GUNLUK_RENKLERI = {
    "❌": R["hata"],
    "⛔": R["hata"],
    "⚠️": R["uyari"],
    "🚧": R["uyari"],
    "🚫": R["uyari"],
    "✅": R["basari"],
    "🎉": R["basari"],
    "🏁": R["basari"],
    "💾": R["bilgi"],
    "🇹🇷": R["bilgi"],
    "🛑": R["uyari"],
}

YAZI_TIPI = '"Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif'
ESIT_YAZI = '"Cascadia Mono", "Consolas", "JetBrains Mono", monospace'


def _ok_simgesi(ad: str, yon: str, renk: str, genislik: int = 9, yukseklik: int = 6) -> str:
    """Küçük bir üçgen ok PNG'si üretip yolunu döndürür.

    Qt'nin QSS'i `::down-arrow` gibi alt denetimlerde CSS üçgeni numarasını
    doğru çizmiyor; en temizi gerçek bir görsel vermek. Görseller geçici
    klasöre bir kez yazılır ve sonraki açılışlarda yeniden kullanılır.
    """
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QBrush, QColor, QPainter, QPixmap, QPolygonF

    klasor = os.path.join(tempfile.gettempdir(), "subtitleforge_simge")
    os.makedirs(klasor, exist_ok=True)
    yol = os.path.join(klasor, f"{ad}.png")
    if os.path.isfile(yol):
        return yol.replace("\\", "/")

    resim = QPixmap(genislik, yukseklik)
    resim.fill(Qt.transparent)

    if yon == "asagi":
        noktalar = [QPointF(0, 0), QPointF(genislik, 0), QPointF(genislik / 2, yukseklik)]
    else:
        noktalar = [QPointF(0, yukseklik), QPointF(genislik, yukseklik), QPointF(genislik / 2, 0)]

    boyaci = QPainter(resim)
    boyaci.setRenderHint(QPainter.Antialiasing, True)
    boyaci.setPen(Qt.NoPen)
    boyaci.setBrush(QBrush(QColor(renk)))
    boyaci.drawPolygon(QPolygonF(noktalar))
    boyaci.end()

    resim.save(yol, "PNG")
    return yol.replace("\\", "/")


def _oklar() -> dict[str, str]:
    """QSS'e gömülecek ok görsellerinin yolları; üretilemezse boş sözlük."""
    try:
        return {
            "asagi": _ok_simgesi("asagi", "asagi", R["metin_soluk"], 9, 6),
            "yukari": _ok_simgesi("yukari", "yukari", R["metin_soluk"], 7, 5),
            "kucuk_asagi": _ok_simgesi("kucuk_asagi", "asagi", R["metin_soluk"], 7, 5),
            "asagi_soluk": _ok_simgesi("asagi_soluk", "asagi", R["metin_silik"], 9, 6),
            "yukari_soluk": _ok_simgesi("yukari_soluk", "yukari", R["metin_silik"], 7, 5),
            "kucuk_asagi_soluk": _ok_simgesi("kucuk_asagi_soluk", "asagi", R["metin_silik"], 7, 5),
        }
    except Exception:
        return {}


def stil_sayfasi() -> str:
    """Uygulamaya uygulanacak QSS.

    QApplication kurulduktan sonra çağrılmalı — ok görselleri QPixmap ile
    üretiliyor.
    """
    oklar = _oklar()
    ok_qss = ""
    if oklar:
        ok_qss = f"""
QComboBox::down-arrow {{
    image: url({oklar['asagi']});
    width: 9px; height: 6px;
    margin-right: 9px;
}}
QComboBox::down-arrow:disabled {{ image: url({oklar['asagi_soluk']}); }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url({oklar['yukari']}); width: 7px; height: 5px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url({oklar['kucuk_asagi']}); width: 7px; height: 5px;
}}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {{
    image: url({oklar['yukari_soluk']});
}}
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{
    image: url({oklar['kucuk_asagi_soluk']});
}}
"""

    return ok_qss + f"""
/* ---------- genel ---------- */
QWidget {{
    background-color: {R['zemin']};
    color: {R['metin']};
    font-family: {YAZI_TIPI};
    font-size: 10pt;
}}
QLabel {{ background: transparent; }}
QToolTip {{
    background-color: {R['yuzey_ust']};
    color: {R['metin']};
    border: 1px solid {R['cizgi_guclu']};
    border-radius: 6px;
    padding: 6px 8px;
}}

/* ---------- başlık şeridi ---------- */
#baslikSerit {{
    background-color: {R['zemin_alt']};
    border-bottom: 1px solid {R['cizgi']};
}}
#baslikAd {{
    font-size: 16pt;
    font-weight: 700;
    color: {R['metin']};
}}
#baslikAd[vurgulu="true"] {{ color: {R['vurgu']}; }}
#surumRozeti {{
    color: {R['metin_silik']};
    font-size: 9pt;
    padding-bottom: 4px;
}}

/* ---------- kartlar ---------- */
#kart {{
    background-color: {R['yuzey']};
    border: 1px solid {R['cizgi']};
    border-radius: 12px;
}}
#kartBaslik {{
    color: {R['metin_soluk']};
    font-size: 9pt;
    font-weight: 700;
    letter-spacing: 1px;
}}

/* ---------- bırakma alanı ---------- */
#birakmaAlani {{
    background-color: {R['yuzey']};
    border: 2px dashed {R['cizgi_guclu']};
    border-radius: 12px;
}}
#birakmaAlani[aktif="true"] {{
    background-color: {R['vurgu_zemin']};
    border: 2px dashed {R['vurgu']};
}}
#birakmaBaslik {{ font-size: 11pt; font-weight: 600; color: {R['metin']}; }}
#birakmaAlt    {{ font-size: 9pt; color: {R['metin_silik']}; }}

/* ---------- rozetler ---------- */
#rozet {{
    background-color: {R['yuzey_ust']};
    border: 1px solid {R['cizgi']};
    border-radius: 11px;
    padding: 3px 10px;
    font-size: 9pt;
    color: {R['metin_soluk']};
}}
#rozet[durum="iyi"]  {{ color: {R['basari']}; border-color: #24452f; background-color: #122119; }}
#rozet[durum="kotu"] {{ color: {R['hata']};   border-color: #4a2626; background-color: #221315; }}
#rozet[durum="mesgul"] {{ color: {R['vurgu']}; border-color: #4a2f19; background-color: {R['vurgu_zemin']}; }}

/* ---------- düğmeler ---------- */
QPushButton {{
    background-color: {R['yuzey_ust']};
    color: {R['metin']};
    border: 1px solid {R['cizgi_guclu']};
    border-radius: 8px;
    padding: 7px 14px;
}}
QPushButton:hover   {{ background-color: {R['yuzey_vurgu']}; border-color: #465164; }}
QPushButton:pressed {{ background-color: {R['cizgi']}; }}
QPushButton:disabled {{
    background-color: {R['zemin_alt']};
    color: {R['metin_silik']};
    border-color: {R['cizgi']};
}}

QPushButton#anaDugme {{
    background-color: {R['vurgu']};
    color: #16100a;
    border: none;
    border-radius: 9px;
    padding: 12px 22px;
    font-size: 11pt;
    font-weight: 700;
}}
QPushButton#anaDugme:hover   {{ background-color: {R['vurgu_parlak']}; }}
QPushButton#anaDugme:pressed {{ background-color: {R['vurgu_koyu']}; }}
QPushButton#anaDugme:disabled {{
    background-color: {R['yuzey_ust']};
    color: {R['metin_silik']};
}}

QPushButton#iptalDugme {{
    background-color: transparent;
    color: {R['hata']};
    border: 1px solid #4a2626;
    border-radius: 9px;
    padding: 12px 22px;
    font-size: 11pt;
    font-weight: 600;
}}
QPushButton#iptalDugme:hover {{ background-color: #221315; }}
QPushButton#iptalDugme:disabled {{
    color: {R['metin_silik']};
    border-color: {R['cizgi']};
}}

QPushButton#kucukDugme {{
    padding: 5px 10px;
    font-size: 9pt;
    border-radius: 7px;
}}
QPushButton#bagDugme {{
    background: transparent;
    border: none;
    color: {R['vurgu']};
    padding: 4px 6px;
    text-align: left;
}}
QPushButton#bagDugme:hover {{ color: {R['vurgu_parlak']}; }}

/* ---------- giriş alanları ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {R['zemin_alt']};
    color: {R['metin']};
    border: 1px solid {R['cizgi_guclu']};
    border-radius: 8px;
    padding: 6px 9px;
    selection-background-color: {R['vurgu']};
    selection-color: #16100a;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {R['vurgu']};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    color: {R['metin_silik']};
    background-color: #0f1319;
}}
QLineEdit[readOnly="true"] {{ color: {R['metin_soluk']}; }}

QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background-color: {R['yuzey']};
    color: {R['metin']};
    border: 1px solid {R['cizgi_guclu']};
    border-radius: 8px;
    padding: 4px;
    outline: none;
    selection-background-color: {R['vurgu']};
    selection-color: #16100a;
}}

QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    background-color: {R['yuzey_ust']};
    border: none;
    width: 16px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ border-top-right-radius: 7px; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ border-bottom-right-radius: 7px; }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {R['yuzey_vurgu']};
}}

/* ---------- onay kutusu ---------- */
QCheckBox {{ spacing: 9px; padding: 3px 0; }}
QCheckBox::indicator {{
    width: 17px; height: 17px;
    border: 1px solid {R['cizgi_guclu']};
    border-radius: 5px;
    background-color: {R['zemin_alt']};
}}
QCheckBox::indicator:hover {{ border-color: {R['vurgu']}; }}
QCheckBox::indicator:checked {{
    background-color: {R['vurgu']};
    border-color: {R['vurgu']};
    image: none;
}}
QCheckBox:disabled {{ color: {R['metin_silik']}; }}

/* ---------- sekmeler ---------- */
QTabWidget::pane {{
    border: 1px solid {R['cizgi']};
    border-radius: 12px;
    background-color: {R['yuzey']};
    top: -1px;
}}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent;
    color: {R['metin_silik']};
    border: none;
    padding: 8px 16px;
    margin-right: 2px;
    font-size: 10pt;
    font-weight: 600;
}}
QTabBar::tab:hover    {{ color: {R['metin_soluk']}; }}
QTabBar::tab:selected {{
    color: {R['vurgu']};
    border-bottom: 2px solid {R['vurgu']};
}}

/* ---------- listeler ---------- */
QListWidget {{
    background-color: {R['zemin_alt']};
    border: 1px solid {R['cizgi']};
    border-radius: 9px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{
    padding: 7px 9px;
    border-radius: 7px;
    color: {R['metin_soluk']};
}}
QListWidget::item:hover    {{ background-color: {R['yuzey_ust']}; }}
QListWidget::item:selected {{
    background-color: {R['vurgu_zemin']};
    color: {R['vurgu_parlak']};
}}

/* ---------- ilerleme ---------- */
QProgressBar {{
    background-color: {R['zemin_alt']};
    border: 1px solid {R['cizgi']};
    border-radius: 7px;
    height: 12px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    border-radius: 6px;
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                      stop:0 {R['vurgu_koyu']}, stop:1 {R['vurgu_parlak']});
}}

/* ---------- kaydırma çubuğu ---------- */
QScrollBar:vertical {{
    background: transparent; width: 11px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {R['cizgi_guclu']};
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: #4a5568; }}
QScrollBar:horizontal {{
    background: transparent; height: 11px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {R['cizgi_guclu']};
    border-radius: 5px;
    min-width: 28px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* ---------- ayırıcı ---------- */
QSplitter::handle {{ background-color: transparent; }}
QSplitter::handle:horizontal {{ width: 10px; }}
QSplitter::handle:hover {{ background-color: {R['cizgi']}; }}

/* ---------- günlük / akış ---------- */
#gunluk, #akis {{
    background-color: #0a0d12;
    border: none;
    border-radius: 8px;
    font-family: {ESIT_YAZI};
    font-size: 9pt;
    padding: 10px;
}}

/* ---------- alt şerit ---------- */
#altSerit {{
    background-color: {R['zemin_alt']};
    border-top: 1px solid {R['cizgi']};
}}
#durumMetni  {{ color: {R['metin_soluk']}; font-size: 9pt; }}
#yuzdeMetni  {{ color: {R['vurgu']}; font-size: 9pt; font-weight: 700; }}

/* ---------- form etiketleri ---------- */
#formEtiket   {{ color: {R['metin_soluk']}; }}
#yardimMetni  {{ color: {R['metin_silik']}; font-size: 8pt; }}
#bolumBaslik  {{
    color: {R['vurgu']};
    font-size: 9pt;
    font-weight: 700;
    letter-spacing: 1px;
    padding-top: 6px;
}}
#ayiracCizgi {{ background-color: {R['cizgi']}; max-height: 1px; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
"""
