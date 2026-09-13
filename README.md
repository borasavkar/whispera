# Whispera 2

Saf [openai-whisper](https://github.com/openai/whisper) üzerine kurulu, PySide6
arayüzlü altyazı üretici. Whisper CLI'ının bütün parametreleri arayüzden
erişilebilir; üstüne toplu iş kuyruğu, canlı metin akışı ve isteğe bağlı Türkçe
çeviri gelir.

## Çalıştırma

```bash
.venv\Scripts\python.exe Whispera.py
```

Konsolsuz başlatmak için `Calistir.bat` dosyasına çift tıklayın.

Gereksinimler: `openai-whisper`, `torch`, `PySide6`, `requests`,
`deep-translator` ve PATH'te (ya da ayarlardan seçilmiş) bir `ffmpeg`.

## Neler var

**Arayüz**

- Dosyaları pencereye sürükleyip bırakma (klasör bırakılırsa içindeki tüm
  medya dosyaları alınır).
- Kuyruk: birden çok dosya sırayla işlenir, her satırda durum işareti bulunur.
  Biten dosya kuyruktan düşer — yoksa yeni bir dosya eklenip «Başlat»a
  basıldığında eskiler baştan işlenirdi. Başarısız olanlar ✕ ile listede kalır.
- İş bitince kutu ile özet verilir ve görev çubuğu simgesi yanıp söner; uzun
  işlerde başka bir şeyle uğraşırken de fark edilir.
- **Canlı akış** sekmesi: whisper her pencereyi bitirdiğinde ürettiği altyazı
  satırları zaman damgasıyla anında akar.
- Gerçek yüzde ve kalan süre tahmini — whisper'ın kendi kare sayacından okunur.
- **Günlük** sekmesi: renklendirilmiş, kopyalanabilir terminal çıktısı.
- Pencere konumu ve tüm ayarlar `forge_settings.json` içinde saklanır.

**Whisper parametreleri**

`Temel` sekmesinde model, dil, görev, çıktı formatı/klasörü, işlem birimi;
`Gelişmiş` sekmesinde kod çözme (sıcaklık, beam/best-of, sabır, uzunluk cezası,
bastırılan token'lar, başlangıç istemi), eşikler (sıkıştırma oranı, log
olasılık, konuşma yok, halüsinasyon sessizliği), kelime zaman damgaları
(vurgulama, satır genişliği/sayısı, satır başına kelime, noktalama kuralları)
ve diğerleri (CPU iş parçacığı, işlenecek aralıklar, model klasörü).

Kelime zaman damgası gerektiren alanlar, o seçenek kapalıyken erişime kapalıdır
ve «Başlat»a basıldığında gereken düzeltmeler günlüğe yazılarak uygulanır.

**Ek işlemler**

- **Türkçe çeviri** — üç kademeli: cümle bütünlüğü korunarak toplu istek →
  satır satır yedek yol → internetsiz yerel Marian modeli. Orijinal altyazı
  `Dosya_EN.srt`, Türkçesi `Dosya.srt` olarak yazılır.
- **Sansürsüz mod** — dile uygun bir başlangıç istemiyle küfür/argonun
  yumuşatılmasını engeller; çeviride yıldızlanan satırları tekrar ister.
- **Dil oylaması** — whisper yalnızca ilk 30 saniyeye bakar; bu seçenek dosyanın
  geneline yayılmış pencerelerden oy toplar.
- **Tekrar filtresi** — modelin aynı cümleyi arka arkaya ürettiği halüsinasyon
  döngülerini ayıklar.

## Zamanlama ve bölümleme

Whisper'ın kendi segmentleri konuşma tanıma için uygun ama altyazı için uzun:
8-10 saniyelik bloklara birkaç cümle sığıyor ve ekranda tek satır hâlinde
akıyor. `word_timestamps` açıkken (varsayılan) her kelimenin başlangıç/bitiş anı
çıkarılıyor ve bloklar bundan yeniden kuruluyor.

Bölme kararının öncelik sırası: cümle sonu noktalaması → uzun duraklama →
süre/karakter sınırı → blok yarıdan doluysa virgül. **Blok bitişi son kelimenin
bittiği andır**, bir sonraki bloğun başlangıcı değil; böylece konuşma olmayan
boşluklar altyazıyla doldurulmaz.

Whisper'ın kendi `--max_line_width` + `--max_line_count` ikilisi bunu yapmıyor:
ikisi birden verildiğinde `preserve_segments` kapanıyor ve karakter kotası
açgözlüce dolduruluyor, sonuç cümle ortasından bölünmüş bloklar oluyor
(«Gelin bu gizemin peşine» / «birlikte düşelim»). Akıllı bölümleme tam da bunu
düzeltmek için var; kapatılırsa o iki ayar whisper'a olduğu gibi aktarılır.

Ayarlar Gelişmiş sekmesinde: blok başına karakter (84 = 2×42), blok başına
saniye (6), bölen sessizlik (0,7 sn), satır genişliği (42).

`highlight_words` (karaoke etkisi) açıkken akıllı bölümleme devre dışı kalır —
o çıktı kelime zamanlarının blokta durmasını gerektiriyor.

## Çeviri motorları

Türkçe çeviri sırayla şu yolları dener; her biri bir öncekinin bıraktığı
satırları doldurur:

| Sıra | Motor | Anahtar | Sınır |
| --- | --- | --- | --- |
| 0 | **Gemini** | gerekli (ücretsiz katman) | dakikada/günde istek kotası |
| 1 | **DeepL** | gerekli (ücretsiz katman) | ayda 500.000 karakter |
| 2 | Google `translate_a` | yok | IP başına istek limiti (HTTP 429) |
| 3 | Google (deep_translator) | yok | farklı uç nokta, ayrı limit |
| 4 | **MyMemory** | yok | günde ~5.000 karakter (anonim) |
| 5 | Helsinki-NLP Marian | yok | **etkin değil** — aşağıya bakın |

### Argo ve küfür: neden Gemini

Ölçüldü — İspanyolca bir filmde kaynak transkripsiyon Faster-Whisper-XXL ile
**birebir aynı** derecede sansürsüz (`polla`×3, `puta`×10, `chupa`×8,
`nabos`×1). Fark tamamen çeviri katmanında:

| İspanyolca | Google Translate | Gemini |
| --- | --- | --- |
| `¿Se llama polla?` | Buna **horoz** mu denir? | Adı **yarrak** mı? |
| `¿Cuántos nabos habrás tocado?` | Kaç tane **şalgama** dokundun? | Kaç **yarağa** dokunmuşsundur |
| `Qué puta eres` | Sen nasıl bir **fahişesin** | Ne **orospusun** |
| `Chupa` (emir) | **Emmek** (mastar) | **Em** |

Google argoyu sözlük anlamıyla çeviriyor (`nabos` = şalgam) ve kaydı
yumuşatıyor; tek kelimelik satırlarda kipi de kaçırıyor. Dil modeli bağlamı
görüyor ve sertlik derecesini koruyor.

Gemini anahtarı Temel sekmesinden girilir (aistudio.google.com, ücretsiz).
Yönergede küfrün yumuşatılmaması açıkça isteniyor ve güvenlik süzgeçleri
kapatılıyor — yoksa müstehcen satırlar boş dönüyor. Satır hizası
numaralandırmayla korunur: model bir satırı atlar ya da birleştirirse sonuç
kaymaz, o satır çevrilmemiş sayılıp alttaki motorlara devredilir.

**Ücretsiz kota.** Gemini'nin ücretsiz katmanı model başına *günlük istek
sayısıyla* sınırlı (Eylül 2026'da gemini-3.6-flash için günde 20 istek). Bu
yüzden satırlar 150'lik paketlerle gönderiliyor — 95 cümlelik bir film tek
istek tutuyor. Bir modelin günlük kotası dolunca program beklemeden kotası
ayrı tutulan sıradaki modele geçiyor (3.6-flash → 3.5-flash → 2.5-flash).
Hepsi dolarsa kalan satırlar Google'a, o da limitteyse orijinal dilde kalır;
günlükte `⚠️ N cümle çevrilemedi` satırının altında sebebi yazar. Kotalar her
gün sıfırlanır.

**Hangi motorun çevirdiği günlükte yazıyor.** İş bitince
`🔧 Çeviriyi yapan motorlar → Gemini: 78 · Google Translate: 4` gibi bir satır
düşer. Anahtar girilmediyse çeviri başlarken bir uyarı çıkar; böylece "çeviri
neden böyle" sorusunun cevabı tahmin gerektirmez.


### Çeviri blokları nasıl kuruluyor

Çeviri **cümle** bazında yapılır (bağlam korunsun diye), sonra Türkçe metin
kaynak blokların zaman aralığına yayılır — kaynak blok *sınırlarına*
paylaştırılmaz.

Eskiden paylaştırılıyordu ve şu çıkıyordu:

```
9   Zaman kaybetme, tedavi edilemez
    olduğumu biliyorsun, değil
10  mi?
```

Türkçe kelime sırası kaynak dilden farklı olduğu için «hangi Türkçe kelime hangi
kaynak bloğa ait» sorusunun doğru cevabı yok; karakter oranıyla bölmek yetim
parçalar üretiyordu. Akıllı bölümleme blokları kısaltınca (her cümle daha çok
bloğa yayıldığı için) sorun iyice belirginleşti. Artık cümle bir bütün olarak
ele alınıyor; tek bloğa sığmazsa **Türkçe metnin kendi noktalamasından**
bölünüyor ve süre parça uzunluklarıyla orantılı dağıtılıyor.

DeepL anahtarı Temel sekmesinden girilir; boşsa hiç denenmez. Türkçe çevirisi
Google'dan belirgin biçimde daha akıcıdır ve bir filmin altyazısı tipik olarak
30-50 bin karakter olduğu için ücretsiz katman ayda on kadar filme yeter.

### Beşinci basamak neden kapalı

Yerel Marian modelleri `transformers` + `sentencepiece` gerektiriyor; ikisi de
kurulu değil ve `Whispera.spec` `transformers`'ı bilerek hariç tutuyor.
Yani bu basamak hem kaynakta hem exe'de sessizce atlanıyor (günlüğe
`ℹ️ Yerel çeviri kullanılamıyor` satırı düşer).

Bilinçli bir tercih: modeller ilk kullanımda birkaç yüz MB ile 1 GB arası
indiriyor, HuggingFace'in kendi önbelleğine (taşınabilir `models` klasörüne
değil) yazıyor ve çeviri kalitesi Google/DeepL'in altında kalıyor. Kota derdine
karşı asıl çözüm DeepL anahtarı; MyMemory de aradaki boşluğu dolduruyor.

Kod yerinde duruyor — ileride gerekirse `transformers` kurulup spec'teki
`excludes` listesinden çıkarılması yeterli.

## Halüsinasyona karşı savunma

Konuşmanın seyrek olduğu, müziğin baskın olduğu dosyalarda whisper uydurmaya
başlıyor: ya başlangıç istemini transkript sanıp tekrarlıyor, ya da eğitim
verisinden gelen jenerik satırlar üretiyor (`Transcription by CastingWords`,
`Subtitles by the Amara.org community`). `condition_on_previous_text` açıkken bu
döngü bir daha kırılmıyor ve saatlik bir film tek cümlenin tekrarı olarak
çıkabiliyor.

Dört kademeli savunma var:

1. **Kısa istem.** Sansürsüz kalıplar tek cümleciğe indirildi. Uzun bir talimat
   metni — özellikle sesin diliyle aynı dildeyse — modelin onu metnin devamı
   sanmasına yol açıyor. Ölçüldü: 3 cümlelik Almanca kalıp + Almanca ses =
   7 segmentin 7'si de istemin kendisi; kısa hâliyle 60 segment / 40 benzersiz.
2. **Eko yakalayıcı.** Üst üste üç blok istemin tekrarıysa işlem orada kesilip
   istemsiz baştan başlatılır. Saatlerce sürecek bir işin çöp çıkmasını
   dakikasında engeller.
3. **Çeşitlilik denetimi.** İş bitince benzersiz metin oranına bakılır; %20'nin
   altındaysa model kilitlenmiş demektir ve istemsiz, `condition_on_previous_text`
   kapalı olarak yeniden denenir (whisper'ın kendi belgelerinde önerdiği çıkış
   yolu).
4. **Artık filtresi.** Bilinen jenerik satırlar çıktıdan ayıklanır. Kara liste
   hiçbir zaman tam olamayacağı için bu son savunma hattıdır, ilki değil.

**5. Konuşma hızı denetimi.** Akıllı bölümleme açıkken her blok karakter/saniye
oranıyla sınanır. Gerçek konuşma kabaca 12-20 karakter/saniye akar; bunun çok
dışındaki bloklar konuşma değildir:

* 17,7 saniye süren ve içinde tek bir kelime olan blok (0,28 krk/sn) —
  sessizliğe iliştirilmiş uydurma;
* 0,46 saniyede 29 karakter (63 krk/sn) — fiziksel olarak konuşulamaz, bozuk
  zamanlama.

Bu ölçüt dilden ve içerikten bağımsız çalışır, kara liste tutmaya göre avantajı
budur. Ayrıca tek kelimenin saçma uzunlukta olduğu bloklar «blok başına saniye»
değerine kırpılır.

Tekrar limiti 4'tür: aynı metin arka arkaya dörtten fazla geçemez.

### Kalan halüsinasyonlar için

Konuşmanın hiç olmadığı açılışlarda (müzik, ortam sesi) whisper hâlâ kısa,
makul görünen cümleler uydurabiliyor — «Musica», «Grazie a tutti.» gibi. Buna
karşı en etkili kol **«Konuşma yok eşiği»**dir. Bir filmde ölçüldü:

| Ayar | 86 sn öncesi uydurma blok |
| --- | --- |
| Whisper varsayılanı (0,60) | 3 |
| Halüsinasyon sessizlik eşiği 2,0 | 5 — **kötüleşti** |
| Konuşma yok eşiği 0,40 | 3 |
| Konuşma yok eşiği 0,30 | 2 |

**Bu uygulamanın varsayılanı 0,30'dur** — whisper'ınki 0,60. Kanıt tek dosyadan
geliyor, ama bu arşivin tamamı aynı türden (konuşma seyrek, müzik baskın) ve
0,60 orada sistematik olarak uyduruyor. Konuşmanın yoğun olduğu kayıtlarda
0,60'a çıkarmak daha iyi olabilir: eşik yükseldikçe model sessizlik kararını
daha kolay verir, alçaldıkça daha çok konuşma arar.

`hallucination_silence_threshold` (arayüzde «Halüsinasyon sessizliği») bu
dosyada işe yaramadı, hatta uydurmayı 3'ten 5'e çıkardı — kapalı bırakın.

## Modeller

Uygulamanın yanında bir `models` klasörü varsa modeller oradan okunur ve oraya
indirilir; yoksa whisper'ın varsayılanı (`~/.cache/whisper`) kullanılır.
Gelişmiş sekmesindeki «Model klasörü» alanı bunu geçersiz kılar.

Derlenmiş sürümde `dist/Whispera/models`, proje kökündeki `models`
klasörüne bağlanan bir NTFS kavşağıdır (`Derle.bat` oluşturur) — anında oluşur
ve yer kaplamaz. Başka bir makineye taşırken kavşağı gerçek kopyayla
değiştirin.

### GPU belleği

Whisper ağırlıkları her zaman fp32 kurar, dosya fp16 olsa bile. fp16 seçeneği
açıkken bu uygulama ağırlıkları da yarı hassasiyete çevirir:

| Model | fp32 (whisper'ın varsayılanı) | fp16 (bu uygulama) |
| --- | --- | --- |
| large-v3 | 5,95 GB | 2,88 GB |
| turbo | ~3,2 GB | ~1,6 GB |

Çıktı birebir aynıdır (sınandı). LayerNorm katmanları fp32 bırakılır — whisper'ın
LayerNorm'u girdiyi `x.float()` ile çevirdiği için ağırlığı fp16 yapmak
«expected scalar type Float but found Half» hatası verir, ayrıca sayısal
kararlılık için de fp32 tercih edilir. Bu katmanların parametreleri toplamın
binde birinden azdır, yani bellek kazancı bozulmaz.

GPU belleği yine de yetmezse uygulama önbelleği temizleyip bir kez daha dener,
sonra yığın izi yerine ne yapılabileceğini anlatan bir liste gösterir.

### Kapanışta kaynak bırakma

Uygulama kapandığında arkada hiçbir şey kalmaz. Kapanış sırası:

1. Ayarlar diske yazılır (sonraki adımlar takılsa bile kaybolmasın diye önce).
2. Pencere hemen gizlenir — temizlik sürerken donmuş görünmesin.
3. Süren iş varsa iptal edilir ve en fazla 15 saniye beklenir. İptal ancak iki
   kod çözme penceresi arasında görülebiliyor; `large-v3` gibi büyük modellerde
   tek pencere on saniyeyi bulabildiği için süre cömert tutulmuştur.
4. Model bellekten atılır, GPU önbelleği boşaltılır.
5. Olay döngüsü bittikten sonra süreç `os._exit` ile sonlandırılır.

Son adım şundan dolayı: iptal edilmiş bir işçi hâlâ torch'un içindeyse ya da bir
çeviri isteği zaman aşımını bekliyorsa, `concurrent.futures` atexit sırasında
havuz iş parçacıklarını join etmeye çalışır ve süreç görünmez biçimde ayakta
kalabilir — GPU belleği de onunla birlikte. Ayarlar zaten yazıldığı için süreci
doğrudan sonlandırmak güvenlidir.

Ölçülen kapanış süreleri (`large-v3`, RX 9070 XT):

| Durum | Süre | Kapanıştan sonra |
| --- | --- | --- |
| Boşta | 0,06 sn | iş parçacığı yok, GPU 0 GB |
| Açılışta (torch yüklenirken) | 1,2 sn | iş parçacığı yok, GPU 0 GB |
| Transkripsiyon ortasında | ~9 sn | iş parçacığı yok, GPU 3,47 → 0,07 GB |

Model, kuyruk biter bitmez bellekten atılır. Bellekte tutmak arka arkaya
yapılan işleri hızlandırırdı (`large-v3` için ~13 saniye) ama uygulama boşta
dururken gigabaytlarca VRAM'i tutmasına değmiyor. Kuyruk içindeki dosyalar
arasında model yeniden yüklenmez; yalnızca kuyruğun sonunda bırakılır.

## Exe derleme

```bash
.venv/Scripts/pyinstaller.exe Whispera.spec --noconfirm --clean
```

Kısayol: `Derle.bat`. Sonuç `dist/Whispera/Whispera.exe`
(~2,3 GB, derleme ~2,5 dakika). Dağıtılacak olan `dist/Whispera` klasörünün
tamamıdır; `build/` yalnızca ara dosyalardır, silinebilir.

Boyutun dağılımı: ROCm kitaplıkları 1,2 GB (çoğu MIOpen — evrişim çekirdekleri,
whisper'ın kodlayıcısı `Conv1d` kullandığı için gerekli), torch 581 MB, ROCm
çekirdeği 210 MB, llvmlite 115 MB, PySide6 92 MB.

`onedir` (tek klasör) kullanılıyor, `onefile` değil: ROCm çalışma zamanı tek
başına ~1,3 GB DLL taşıyor ve `onefile` bunların hepsini her açılışta geçici
klasöre açardı. Klasörü olduğu gibi zipleyip dağıtabilirsiniz.

Spec dosyasında üç şey elle yapılıyor, üçü de otomatik toplayıcıların
kaçırdığı ya da yanlış yaptığı yerler:

- **ROCm klasör düzeni korunuyor.** `rocm_sdk.find_libraries()` DLL'leri
  `importlib.import_module("_rocm_sdk_core")` sonucunun klasöründen çözüyor, bu
  yüzden dosyalar aynı ağaç yapısıyla taşınmalı. GPU'ya özel çekirdek paketleri
  `.kpack` / `.info` gibi **nokta ile başlayan** klasörlerde duruyor; elle
  eklenmezse uygulama sessizce CPU'ya düşer.
- **LLVM derleyici takımı dışarıda bırakılıyor** (`lib/llvm/bin`, ~1,9 GB).
  clang/lld/flang yalnızca kaynaktan HIP derlemek için gerekli; çalışma anındaki
  JIT `amd_comgr.dll` ve `amdgcn/bitcode` üzerinden yürüyor.
- **`unittest` hariç tutulamaz.** Kulağa gereksiz gelse de
  `torch.utils._config_module` onu modül düzeyinde import ediyor; listeye
  eklenirse torch donmuş pakette hiç yüklenmiyor.

### Derlemeyi doğrulama

```bash
dist/Whispera/Whispera.exe --kendini-sina
```

Arayüz açmadan torch/ROCm yüklenmesini ve GPU'nun görünüp görünmediğini
sınar. Sonuna bir medya dosyası yolu eklerseniz `tiny` modeliyle gerçek bir
transkripsiyon da yapar. Pencereli exe'nin konsolu olmadığı için rapor exe'nin
yanına `SINAMA_RAPORU.txt` olarak yazılır.

## Klasör yapısı

| Yol | İş |
| --- | --- |
| [Whispera.py](Whispera.py) | Giriş noktası, hata yakalayıcı |
| [forge/config.py](forge/config.py) | `Ayarlar` veri sınıfı, doğrulama, kalıcılık |
| [forge/media.py](forge/media.py) | FFmpeg bulma, ses çözme, zaman biçimleme |
| [forge/whisper_engine.py](forge/whisper_engine.py) | Model önbelleği, ilerleme kancaları, dil oylaması, segment temizliği |
| [forge/subtitles.py](forge/subtitles.py) | Çıktı yazımı (whisper yazıcıları), satır kırma |
| [forge/translate.py](forge/translate.py) | Üç kademeli Türkçe çeviri motoru |
| [forge/worker.py](forge/worker.py) | Kuyruğu yürüten `QThread`, sinyaller |
| [forge/ui/theme.py](forge/ui/theme.py) | Palet ve QSS |
| [forge/ui/widgets.py](forge/ui/widgets.py) | Bırakma alanı, günlük/akış görünümleri, parametre denetimleri |
| [forge/ui/main_window.py](forge/ui/main_window.py) | Pencere düzeni, form ↔ ayar aktarımı |

## Nasıl çalışıyor: canlı geri bildirim

Whisper `verbose=False` iken ilerleme çubuğu, `verbose=True` iken segment
metinleri basar — ikisi aynı anda olmaz. Her ikisini birden almak için:

1. `whisper.transcribe` **modülünün** `tqdm` adı geçici olarak `disable`
   bayrağını yok sayan bir sınıfla değiştirilir; böylece `verbose` ne olursa
   olsun kare bazlı gerçek yüzde alınır. (Dikkat: `whisper.transcribe` paket
   ad alanında *fonksiyonu* gösterir, modülü değil — yama
   `importlib.import_module("whisper.transcribe")` ile alınan modüle uygulanır.)
2. `verbose=True` ile basılan segment satırları, yalnızca çalışan iş
   parçacığının yazdıklarını yakalayan bir `sys.stdout` vekiliyle okunur; diğer
   iş parçacıkları etkilenmez.

İkisi de `finally` içinde eski hâline döndürülür. Kanca kurulamazsa işlem yine
çalışır, sadece ilerleme kabalaşır.

## Lisans

Whispera **MIT** lisanslıdır — bkz. [LICENSE](LICENSE).

Kaynak koddan çalıştırmak için başka bir yükümlülük yok. Hazır `.exe`
dağıtacaksanız, pakete giren üçüncü taraf bileşenlerin lisansları
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) dosyasında listelenmiştir.
Dikkat gerektiren tek bileşen **Qt/PySide6 (LGPL-3.0)**: PyInstaller'ın
`onedir` kipinde Qt kütüphaneleri `_internal/PySide6/` altında ayrı `.dll`
dosyaları olarak kaldığı için LGPL'in «kullanıcı kütüphaneyi
değiştirebilmeli» şartı sağlanıyor. **Onefile kipine geçmeyin**, bu uyumu
bozar.

ffmpeg pakete dahil değildir; sistemdeki ffmpeg çağrılır.
