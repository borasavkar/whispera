"""Akıllı bölümleme: kelime zamanlarından altyazı blokları."""

from forge import segment


def kelime(metin, bas, son):
    return {"word": metin, "start": bas, "end": son}


def test_cumle_sonu_kapatan_isaretle_de_taninir():
    assert segment._cumle_bitti_mi("dedi.»")
    assert segment._cumle_bitti_mi('"Gel!"')
    assert not segment._cumle_bitti_mi("gel,")
    assert segment._ara_noktalama_mi("gel,")


def test_kelime_zamani_yoksa_none_doner():
    assert segment.bloklara_bol([{"text": "merhaba", "start": 0, "end": 1}]) is None


def test_cumle_sonunda_bolunur_ve_blok_son_kelimede_biter():
    segmentler = [{"words": [
        kelime(" Merhaba", 0.0, 0.5), kelime(" dünya.", 0.5, 1.0),
        kelime(" Nasılsın", 1.1, 1.6), kelime(" bugün?", 1.6, 2.1),
    ]}]
    bloklar, elenen = segment.bloklara_bol(segmentler)
    assert elenen == 0
    assert [b["text"] for b in bloklar] == ["Merhaba dünya.", "Nasılsın bugün?"]
    # Bitiş, sonraki bloğun başlangıcı değil son kelimenin bitişidir.
    assert bloklar[0]["end"] == 1.0
    assert bloklar[1]["start"] == 1.1


def test_uzun_duraklamada_bolunur():
    segmentler = [{"words": [
        kelime(" bir", 0.0, 0.4), kelime(" iki", 0.4, 0.8),
        kelime(" üç", 2.0, 2.4), kelime(" dört", 2.4, 2.8),
    ]}]
    bloklar, _ = segment.bloklara_bol(segmentler, max_bosluk=0.7)
    assert [b["text"] for b in bloklar] == ["bir iki", "üç dört"]


def test_karakter_siniri_asilinca_bolunur():
    kelimeler = [kelime(f" kelime{i}", i * 0.3, i * 0.3 + 0.25) for i in range(12)]
    bloklar, _ = segment.bloklara_bol([{"words": kelimeler}],
                                      max_karakter=30, max_sure=100)
    assert len(bloklar) > 1
    for b in bloklar:
        assert len(b["text"].replace("\n", " ")) <= 30


def test_satirlara_boluk_dengeli_iki_satir_uretir():
    metin = "Gelin bu gizemin peşine birlikte düşelim ve sonuna kadar gidelim"
    sonuc = segment.satirlara_boluk(metin, 42, 2)
    satirlar = sonuc.split("\n")
    assert len(satirlar) == 2
    assert " ".join(satirlar) == metin
    assert abs(len(satirlar[0]) - len(satirlar[1])) < 15


def test_satirlara_boluk_kisa_metne_dokunmaz():
    assert segment.satirlara_boluk("Kısa satır", 42, 2) == "Kısa satır"


def test_olcusuz_bloklar_elenir():
    bloklar = [
        {"start": 0.0, "end": 17.7, "text": "bizim"},                        # çok yavaş
        {"start": 20.0, "end": 20.46, "text": "x" * 29},                     # çok hızlı
        {"start": 30.0, "end": 32.0, "text": "Normal bir konuşma satırı."},  # makul
    ]
    temiz, elenen = segment.olcusuz_bloklari_ele(bloklar)
    assert elenen == 2
    assert [b["text"] for b in temiz] == ["Normal bir konuşma satırı."]


def test_metni_zamana_yay_araligi_tam_kaplar():
    metin = ("Bu çok uzun bir çeviri cümlesi, iki bloğa bölünmesi gerekiyor; "
             "çünkü tek blokta seksen dört karakteri rahatça aşıyor.")
    bloklar = segment.metni_zamana_yay(10.0, 16.0, metin)
    assert len(bloklar) >= 2
    assert bloklar[0]["start"] == 10.0
    assert bloklar[-1]["end"] == 16.0
    for onceki, sonraki in zip(bloklar, bloklar[1:]):
        assert onceki["end"] <= sonraki["start"] + 1e-9
    assert " ".join(b["text"].replace("\n", " ") for b in bloklar) == metin


def test_sonuca_uygula_segmentleri_yeniden_kurar():
    sonuc = {"text": "eski", "segments": [{"words": [
        kelime(" Selam.", 0.0, 0.8), kelime(" Görüşürüz.", 1.0, 1.9)]}]}
    yeni, uygulandi, elenen = segment.sonuca_uygula(sonuc)
    assert uygulandi and elenen == 0
    assert [s["text"] for s in yeni["segments"]] == ["Selam.", "Görüşürüz."]
    assert yeni["text"] == "Selam. Görüşürüz."
    assert sonuc["text"] == "eski"          # özgün sözlük değişmez
