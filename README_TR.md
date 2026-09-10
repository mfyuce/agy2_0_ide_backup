# agy2_0_ide_backup

*[English](README.md) · Türkçe*

Kendi **Antigravity IDE** (Google'ın Gemini tabanlı "Visual Agent Orchestration Platform"u,
v2.8+) sohbet geçmişinizi, hâlihazırda çalışan uygulamayı Chrome DevTools Protocol (CDP)
üzerinden yöneterek yerel `.txt` dosyalarına yedekleyen iki küçük script.

## Neden var

Antigravity IDE'nin **toplu sohbet dışa-aktarma özelliği yok**, ve — bunu doğrulamak biraz
araştırma gerektirdi — **sohbet geçmişinin yerelde kalıcı bir kopyası da yok**. Dört bağımsız
yöntemle kontrol edildi: `state.vscdb`'nin sohbetle ilgili anahtarları hep boş, canlı kullanım
sırasında diskte yeni bir dosya belirmiyor, tüm ağ trafiği Google'ın kendi IP'lerine TLS
üzerinden gidiyor, ve renderer'ın `IndexedDB`/`localStorage`'ının doğrudan dökümü de boş
çıktı. Mimari tamamen bulut-senkron — bir konuşmanın içeriği yerelde sadece o sekme açık ve
render edilmişken var oluyor.

Bu da geriye tek bir yol bırakıyor: her konuşmayı tek tek açıp (render olsun diye), sayfa
açıkken `document.body.innerText`'i okumak. `watch_and_archive.py` okuma kısmını yapıyor. Ama
*her projede, her konuşmayı tek tek elle açmak* tam olarak normalde bir kodlama ajanına
devredeceğiniz türden sıkıcı, tekrarlayan bir iş — o yüzden `click_through_all.py` tıklama
kısmını CDP'nin native `Input.dispatchMouseEvent`'i (Puppeteer/Playwright'ın kullandığı aynı
mekanizma) ile otomatik yapıyor.

**Neden iki ayrı script, ve neden biri bir ajan tarafından değil, mutlaka *sizin* tarafınızdan
çalıştırılmakta ısrarlı?** Kodlama-ajanı altyapıları (bu araç da böyle biriyle inşa edildi)
bir ajanın *başka* canlı bir GUI uygulaması içinde neleri otomatikleştirebileceğini makul
şekilde kısıtlıyor — üçüncü parti bir uygulamada tıklayıp durmak, "size yardım etmek" ile "bir
ajanın siz izlemeden sizin adınıza yazılım kullanması" arasındaki çizgiyi bulanıklaştırıyor;
bunu siz isteseniz ve açıkça söyleseniz bile, varsayılan olarak temkinli davranmak makul bir
şey. Bu kısıtlama ajanın kendi tool-call'larına uygulanıyor, kendi terminalinizden
çalıştırdığınız bir script'e değil — o yüzden `click_through_all.py` doğrudan sizin
çalıştırmanız için yazıldı, bir ajanın güvenlik katmanını istisna için ikna etmeye
çalışmak yerine sorunu tamamen bypass ediyor. `watch_and_archive.py` sadece sayfayı *okuyor*,
çok daha düşük riskli bir eylem, ve her iki durumda da sorunsuz çalıştı.

## Gereksinimler

- Antigravity IDE, uzaktan debug açık şekilde başlatılmış:
  ```bash
  pkill -f antigravity/antigravity   # zaten çalışıyorsa
  /path/to/antigravity --remote-debugging-port=9223 --remote-allow-origins=* &
  ```
- Python 3.9+, `pip install -r requirements.txt`

## Kullanım

```bash
# terminal 1 — izlemeyi başlatır, yeni zaman-damgalı bir run klasörü açar
python3 watch_and_archive.py

# terminal 2 — önce test et (bir satır bulur+ölçer, TIKLAMAZ)
python3 click_through_all.py
# mantıklı görünüyor mu? şimdi gerçekten her şeyi tıkla:
python3 click_through_all.py --live
```

Çıktı `~/antigravity_chat_archive/run_<YYYYMMDD_HHMMSS>/` içine düşer, her konuşma için bir
`.txt` dosyası + bir `_seen.json` indeksi. **Her çalıştırma kendi klasörünü alır** — taze bir
`watch_and_archive.py` çalıştırmasına karşı `click_through_all.py --live`'ı tekrar çalıştırmak
her şeyi yeniden tıklayıp yeniden yakalar (tam yeni bir yedekleme turu), önceki run'larla
diff almaya çalışmaz. Bir kesinti sonrası (çökme, kod düzeltmesi) AYNI turdan devam etmek
istiyorsan (yeni bir tur başlatmak yerine) `python3 watch_and_archive.py --resume` kullan —
en son `run_*/` klasörüne yazmaya devam eder, orada zaten yakalanmış olanları atlar. Arşiv
klasörü bu repo'nun dışında yaşar ve hiçbir yere commit edilmez; hassas veri gibi ele alın —
IDE'yi hangi projeler için kullandıysanız, o projelerin açık olan konuşmalarının tam metnini
içerir.

## İki parça nasıl koordine oluyor

- `watch_and_archive.py` CDP target listesini periyodik kontrol eder. Yeni bir konuşma URL'i
  görününce, mesaj panelini tekrar tekrar en üste kaydırır (eski mesajları buluttan lazy-load
  ettirmek için — bu arada duraklayabilir, sabitlik kontrolü erken bir yanlış "bitti"
  sonucuna aldanmadan önce bekler), sonra `document.body.innerText`'i kaydeder.
- `click_through_all.py` sidebar satırlarını basit bir yapısal sezgiyle bulur (kendi metni
  tam olarak iki satır olan bir eleman: bir başlık, sonra `13m`/`2mo` gibi göreli-zaman
  etiketi), tıklamadan önce hedefi `scrollIntoView` yapar (düz bir `getBoundingClientRect()`
  ekran dışına kaymış bir elementte de geçerli görünen koordinatlar döndürebilir — oraya
  tıklarsanız o an ekranda ne varsa ona tıklamış olursunuz), native CDP mouse event'leriyle
  tıklar, sonra izleyicinin yeni konuşmayı yakaladığını doğrulayana kadar bekleyip sonraki
  satıra geçer.

## Bilinen sınırlamalar

- Satır tespiti gerçek bir DOM selector'ü değil, metin-şekli sezgisi — o anda çalışan bir
  element inspector olmadan ulaşılabilen buydu (nedeni için özel eşlik eden repo'nun build
  günlüğüne bakın). Pratikte güvenilir çalıştı ama her olası sidebar düzenine karşı garanti
  değil.
- Farklı projelerde aynı `(başlık, göreli-zaman)` çifti teorik olarak çakışabilir; pratikte
  gözlemlenmedi ama yapısal olarak da dışlanmış değil.
- Buradaki her şey Antigravity IDE'nin şu anki (2026) DOM yapısına ve CDP maruziyetine özel.
  Uygulama arayüzünü değiştirirse sezgilerin güncellenmesi gerekebilir.

MIT lisanslı — bkz. [`LICENSE`](LICENSE).
