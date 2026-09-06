"""Kelime zaman damgalarından okunabilir altyazı blokları üretir.

Whisper'ın kendi segmentleri konuşma tanıma için uygun ama altyazı için uzun:
8-10 saniyelik bloklara birkaç cümle sığıyor ve ekranda tek satır hâlinde
akıyor. Whisper'ın `--max_line_width` seçeneği ise `--max_line_count` ile
birlikte verildiğinde segment sınırlarını tamamen yok sayıp karakter kotasını
açgözlüce dolduruyor; sonuç cümle ortasından bölünmüş bloklar oluyor
(«Gelin bu gizemin peşine» / «birlikte düşelim»).

Burada `word_timestamps` çıktısındaki kelime kelime zamanları kullanarak kendi
bölümlememizi yapıyoruz. Öncelik sırası:

1. Cümle sonu noktalaması — en güçlü sınır.
2. Uzun duraklama — konuşmacı zaten ara vermiş.
3. Süre ya da karakter sınırı — okunabilirlik için.
4. Ara noktalama (virgül, noktalı virgül) — blok yeterince dolduysa.

Blok bitişi son kelimenin bittiği andır, bir sonraki bloğun başlangıcı değil;
böylece konuşma olmayan boşluklar altyazıyla doldurulmaz.
"""

from __future__ import annotations

from typing import Any

# Cümleyi bitiren noktalama.
CUMLE_SONU = (".", "!", "?", "…", "。", "！", "？")
# Cümleyi bitirmeyen ama bölmek için uygun yerler.
ARA_NOKTALAMA = (",", ";", ":", "—", "–", "،", "؛")
# Sondaki kapatma işaretleri; noktalama denetiminden önce kırpılır.
KAPATANLAR = "\"'»)]}”’"


def _kelime_akisi(segmentler: list[dict]) -> list[dict]:
    """Bütün segmentlerdeki kelimeleri tek bir sıralı listeye indirger."""
    akis: list[dict] = []
    for segment in segmentler:
        for kelime in segment.get("words") or []:
            metin = (kelime.get("word") or "")
            if not metin.strip():
                continue
            akis.append({
                "metin": metin,
                "bas": float(kelime.get("start", 0.0)),
                "son": float(kelime.get("end", 0.0)),
            })
    return akis


def _cumle_bitti_mi(metin: str) -> bool:
    return metin.rstrip().rstrip(KAPATANLAR).rstrip().endswith(CUMLE_SONU)


def _ara_noktalama_mi(metin: str) -> bool:
    return metin.rstrip().rstrip(KAPATANLAR).rstrip().endswith(ARA_NOKTALAMA)


def satirlara_boluk(metin: str, satir_genisligi: int, max_satir: int) -> str:
    """Metni dengeli biçimde en fazla `max_satir` satıra böler.

    Satırların uzunluğunu eşitlemeye çalışır: tek kelimelik ikinci satır
    («dul satır») okunurluğu bozuyor.
    """
    kelimeler = metin.split()
    if not kelimeler:
        return ""
    if len(metin) <= satir_genisligi or max_satir < 2:
        return metin

    hedef_satir = min(max_satir, max(2, -(-len(metin) // satir_genisligi)))
    hedef_satir = min(hedef_satir, len(kelimeler))
    hedef_uzunluk = len(metin) / hedef_satir

    satirlar: list[str] = []
    mevcut: list[str] = []
    for kelime in kelimeler:
        if not mevcut:
            mevcut.append(kelime)
            continue
        simdiki = " ".join(mevcut)
        aday = f"{simdiki} {kelime}"
        son_satirda_miyiz = len(satirlar) == hedef_satir - 1
        if not son_satirda_miyiz and abs(len(aday) - hedef_uzunluk) > abs(len(simdiki) - hedef_uzunluk):
            satirlar.append(simdiki)
            mevcut = [kelime]
        else:
            mevcut.append(kelime)
    if mevcut:
        satirlar.append(" ".join(mevcut))
    return "\n".join(satirlar)


def bloklara_bol(segmentler: list[dict], *,
                 max_karakter: int = 84,
                 max_sure: float = 6.0,
                 max_bosluk: float = 0.7,
                 satir_genisligi: int = 42,
                 max_satir: int = 2,
                 min_sure: float = 0.7) -> tuple[list[dict], int] | None:
    """Kelime zamanlarından altyazı blokları üretir.

    Döndürür: (bloklar, elenen_sayısı). Kelime zamanı yoksa None döner;
    çağıran taraf whisper'ın kendi segmentlerini kullanmaya devam eder.
    """
    akis = _kelime_akisi(segmentler)
    if not akis:
        return None

    bloklar: list[dict] = []
    mevcut: list[dict] = []

    def kapat() -> None:
        if not mevcut:
            return
        metin = "".join(k["metin"] for k in mevcut).strip()
        if metin:
            bloklar.append({
                "start": mevcut[0]["bas"],
                "end": mevcut[-1]["son"],
                "text": satirlara_boluk(" ".join(metin.split()),
                                        satir_genisligi, max_satir),
            })
        mevcut.clear()

    for kelime in akis:
        if mevcut:
            bosluk = kelime["bas"] - mevcut[-1]["son"]
            uzunluk = len("".join(k["metin"] for k in mevcut).strip())
            sure = kelime["son"] - mevcut[0]["bas"]
            onceki = mevcut[-1]["metin"]

            yeni_blok = (
                _cumle_bitti_mi(onceki)                       # cümle bitti
                or bosluk > max_bosluk                        # konuşmacı duraksadı
                or uzunluk + len(kelime["metin"]) > max_karakter
                or sure > max_sure
                # Blok yarıdan fazla doluysa virgülde de bölmek okumayı kolaylaştırır.
                or (_ara_noktalama_mi(onceki) and uzunluk > max_karakter * 0.55)
            )
            if yeni_blok:
                kapat()

        mevcut.append(kelime)

    kapat()
    bloklar, atilan = olcusuz_bloklari_ele(bloklar)
    bloklar = _uzun_bloklari_kirp(bloklar, max_sure)
    bloklar = _kisa_bloklari_duzelt(bloklar, min_sure)
    return bloklar, atilan


# Gerçek konuşma kabaca 12-20 karakter/saniye akar. Bu sınırların dışına çıkan
# bloklar konuşma değildir: ya sessizliğe iliştirilmiş bir halüsinasyon, ya da
# whisper'ın saçmalayan kelime zamanlaması.
YAVAS_SINIR = 2.0        # karakter/saniye — altı "sessizlik" sayılır
YAVAS_ASGARI_SURE = 4.0  # yalnızca bu kadar uzun bloklara bakılır
HIZLI_SINIR = 35.0       # karakter/saniye — üstü fiziksel olarak konuşulamaz


def olcusuz_bloklari_ele(bloklar: list[dict]) -> tuple[list[dict], int]:
    """Konuşma hızı akla yatkın olmayan blokları atar.

    Örnek (gerçek bir dosyadan): 17,7 saniye süren ve içinde tek bir «bizim»
    kelimesi olan blok — saniyede 0,28 karakter. Ya da 0,46 saniyede 29
    karakter, yani saniyede 63 karakter. İkisi de konuşma olamaz; ilki
    sessizliğe iliştirilmiş halüsinasyon, ikincisi bozuk zamanlama.

    Bu ölçüt içerikten ve dilden bağımsız çalışır — kara liste tutmaya göre
    büyük avantajı bu.
    """
    temiz: list[dict] = []
    for blok in bloklar:
        sure = max(1e-6, blok["end"] - blok["start"])
        uzunluk = len(blok["text"].replace("\n", " ").strip())
        hiz = uzunluk / sure
        cok_yavas = sure >= YAVAS_ASGARI_SURE and hiz < YAVAS_SINIR
        cok_hizli = hiz > HIZLI_SINIR
        if cok_yavas or cok_hizli:
            continue
        temiz.append(blok)
    return temiz, len(bloklar) - len(temiz)


def _uzun_bloklari_kirp(bloklar: list[dict], max_sure: float) -> list[dict]:
    """Tek bir kelimenin uzun sürdüğü durumlarda bloğu makul süreye çeker.

    Bölme kararı kelime eklerken veriliyor; blokta tek kelime varsa bölünecek
    bir yer yok ve o kelimenin zamanı saçmaysa blok da saçma uzunlukta kalıyor.
    """
    for blok in bloklar:
        if blok["end"] - blok["start"] > max_sure:
            blok["end"] = blok["start"] + max_sure
    return bloklar


def _kisa_bloklari_duzelt(bloklar: list[dict], min_sure: float) -> list[dict]:
    """Göz kırpması kadar kısa blokları biraz uzatır.

    Tek kelimelik bloklar («Hadi bakalım.») kimi zaman 0,2 saniye sürüyor ve
    okunamadan kayboluyor. Sonraki bloğa taşmadan bitişi öteliyoruz.
    """
    for i, blok in enumerate(bloklar):
        sure = blok["end"] - blok["start"]
        if sure >= min_sure:
            continue
        istenen = blok["start"] + min_sure
        sonraki_bas = bloklar[i + 1]["start"] if i + 1 < len(bloklar) else istenen
        blok["end"] = max(blok["end"], min(istenen, sonraki_bas))
    return bloklar


def _metni_parcala(metin: str, max_karakter: int) -> list[str]:
    """Metni sınırı aşmayan parçalara böler; önce noktalama, sonra kelime.

    Bir cümlenin çevirisi tek bloğa sığmadığında nereden bölüneceğine karar
    verir. Virgül/noktalı virgül gibi doğal duraklar varsa oradan böler,
    yoksa kelime sınırından.
    """
    metin = " ".join((metin or "").split())
    if len(metin) <= max_karakter:
        return [metin] if metin else []

    parcalar: list[str] = []
    kalan = metin
    while len(kalan) > max_karakter:
        pencere = kalan[:max_karakter + 1]

        # Sınıra en yakın doğal durağı ara (yarıdan sonrasında olsun ki
        # parçalar çok dengesiz olmasın).
        kesim = -1
        for isaret in ARA_NOKTALAMA:
            yer = pencere.rfind(isaret)
            if yer > max_karakter * 0.4:
                kesim = max(kesim, yer + 1)

        if kesim <= 0:
            kesim = pencere.rfind(" ")
        if kesim <= 0:
            kesim = max_karakter          # tek uzun kelime: mecburen kes

        parcalar.append(kalan[:kesim].strip())
        kalan = kalan[kesim:].strip()

    if kalan:
        parcalar.append(kalan)
    return [p for p in parcalar if p]


def metni_zamana_yay(bas: float, son: float, metin: str, *,
                     max_karakter: int = 84,
                     max_sure: float = 6.0,
                     satir_genisligi: int = 42,
                     max_satir: int = 2) -> list[dict]:
    """Bir cümlenin çevirisini kendi zaman aralığına yayar.

    Çeviriyi kaynak bloklara paylaştırmak yerine bunu yapıyoruz. Sebep: Türkçe
    kelime sırası kaynak dilden farklı olduğu için «hangi Türkçe kelime hangi
    kaynak bloğa ait» sorusunun doğru cevabı yok. Karakter oranına göre
    paylaştırmak «…biliyorsun, değil» / «mi?» gibi kopuk parçalar üretiyordu.

    Burada cümle bir bütün olarak ele alınır; sığmıyorsa Türkçe metnin kendi
    noktalamasından bölünür ve süre parça uzunluklarıyla orantılı dağıtılır.
    """
    metin = " ".join((metin or "").split())
    if not metin:
        return []

    toplam_sure = max(0.001, son - bas)
    # Kaç parça gerekiyor: hem karakter hem süre sınırını sağlamalı.
    gereken = max(1, -(-len(metin) // max_karakter), int(toplam_sure // max_sure) + (
        1 if toplam_sure % max_sure > 0.01 else 0))
    hedef_uzunluk = max(1, -(-len(metin) // gereken))

    parcalar = _metni_parcala(metin, max(hedef_uzunluk, min(max_karakter, len(metin))))
    if not parcalar:
        return []

    toplam_karakter = sum(len(p) for p in parcalar) or 1
    bloklar: list[dict] = []
    imlec = bas
    for i, parca in enumerate(parcalar):
        pay = toplam_sure * len(parca) / toplam_karakter
        bitis = son if i == len(parcalar) - 1 else min(son, imlec + pay)
        bloklar.append({
            "start": imlec,
            "end": max(bitis, imlec + 0.2),
            "text": satirlara_boluk(parca, satir_genisligi, max_satir),
        })
        imlec = bloklar[-1]["end"]
    return bloklar


def sonuca_uygula(sonuc: dict[str, Any], **secenekler) -> tuple[dict, bool, int]:
    """Sonuç sözlüğündeki segmentleri yeniden bölümler.

    Döndürür: (yeni_sonuç, uygulandı_mı, elenen_blok_sayısı). Kelime zamanı
    yoksa sonuç olduğu gibi geri verilir.
    """
    sonuc_ikili = bloklara_bol(sonuc.get("segments") or [], **secenekler)
    if sonuc_ikili is None:
        return sonuc, False, 0
    bloklar, elenen = sonuc_ikili
    if not bloklar:
        return sonuc, False, elenen

    yeni = dict(sonuc)
    yeni["segments"] = [
        {"id": i, "seek": 0, "start": b["start"], "end": b["end"], "text": b["text"]}
        for i, b in enumerate(bloklar)
    ]
    yeni["text"] = " ".join(b["text"].replace("\n", " ") for b in bloklar)
    return yeni, True, elenen
