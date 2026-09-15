#!/usr/bin/env python3
"""
Antigravity IDE conversation watcher/archiver.

Kullanici Antigravity sidebar'inda proje proje / chat chat tiklarken, bu
script CDP (port 9223) uzerinden her yeni acilan konusmayi algilar, mesaj
panelini "No more older messages" gorunene / yukseklik sabitlenene kadar
en uste scroll eder (lazy-load'i tetiklemek icin), sonra tam sayfa metnini
kaydeder. Tiklama/navigasyon YAPMAZ -- sadece: (a) sayfa metni okuma,
(b) scrollTop mutasyonu. Tiklama tarafi icin bkz. click_through_all.py.

Varsayilan olarak her calistirma AYRI, zaman-damgali bir klasore yazar
(backup gibi -- onceki run'lardaki kayitlari GORMEZ, ayni sohbet tekrar
acilirsa tekrar yakalar). `--resume` verilirse yeni klasor ACMAZ, en son
run klasorune devam eder (zaten kaydedilmisleri atlar) -- kod duzeltmesi
sonrasi yeniden baslatirken bastan baslamamak icin. Cikti:
~/antigravity_chat_archive/run_<YYYYMMDD_HHMMSS>/<baslik>__<conv_id8>.txt
(repo DISINDA, kapsam kullanicinin tum portfoyunu icerdigi icin). Aktif
run'in yolu, click_through_all.py'nin bulabilmesi icin
~/antigravity_chat_archive/_current_run.txt dosyasina yazilir. Durum
(sadece bu run icin, ayni sohbeti bu run icinde iki kere yakalamamak
icin): <run_klasoru>/_seen.json.
"""
import argparse
import asyncio
import html
import json
import os
import re
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

PORT = 9223
ARCHIVE_ROOT = Path.home() / "antigravity_chat_archive"
CURRENT_RUN_POINTER = ARCHIVE_ROOT / "_current_run.txt"
POLL_INTERVAL = 1.5
# SCROLL_AND_CAPTURE_EXPR icindeki JS, cok uzun sohbetlerde bulut-senkron
# lazy-load nedeniyle ~150 * 1.8sn + 4sn + 1.5sn ~ 280sn'ye kadar surebilir --
# CDP yaniti icin zaman asimi bunun rahatca ustunde olmali (asagidaki
# capture()'daki asyncio.wait_for).
CDP_RESPONSE_TIMEOUT = 300.0

# main()'de --resume'a gore belirlenir (yeni run mi, en son run'a devam mi).
RUN_DIR = None
SEEN_FILE = None


def find_latest_run_dir():
    if not ARCHIVE_ROOT.exists():
        return None
    runs = sorted(p for p in ARCHIVE_ROOT.iterdir() if p.is_dir() and p.name.startswith("run_"))
    return runs[-1] if runs else None


_other_runs_seen_cache = None
_other_runs_cache_dirs = None


def captured_in_other_run(conv_id):
    # Onceki run'lar (RUN_DIR disindakiler) BACKUP olarak dokunulmadan kalir,
    # ama bu konusma DAHA ONCE (herhangi bir run'da) zaten yakalanmissa
    # tekrar capture etmeye (uzun sohbetlerde onlarca saniye) gerek yok --
    # click_through_all.py'nin capture-oncesi dedup'iyla ayni prensip,
    # burada da (kullanicinin sohbeti elle tekrar acmasi gibi durumlar icin).
    #
    # Diger run'larin _seen.json icerigi bu process boyunca degismez (onlar
    # "dokunulmadan kalan backup") -- bu yuzden her 1.5sn'lik pollde hepsini
    # diskten yeniden okuyup json.loads etmek yerine, bellek-ici bir kumeye
    # BIR KERE cikarip onbelleklendiriyoruz. Sadece yeni bir run klasoru
    # ortaya cikarsa (ki bu process suresince normalde olmaz) yeniden tariyoruz.
    global _other_runs_seen_cache, _other_runs_cache_dirs
    if not ARCHIVE_ROOT.exists():
        return False
    current_dirs = frozenset(p for p in ARCHIVE_ROOT.glob("run_*") if p != RUN_DIR)
    if _other_runs_seen_cache is None or current_dirs != _other_runs_cache_dirs:
        cache = set()
        for run_dir in current_dirs:
            seen_file = run_dir / "_seen.json"
            if not seen_file.exists():
                continue
            try:
                cache.update(json.loads(seen_file.read_text()).keys())
            except Exception:
                continue
        _other_runs_seen_cache = cache
        _other_runs_cache_dirs = current_dirs
    return conv_id in _other_runs_seen_cache

SCROLL_AND_CAPTURE_EXPR = """
(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  function findScrollables() {
    return Array.from(document.querySelectorAll('*'))
      .filter(e => e.scrollHeight > e.clientHeight + 50);
  }
  function hasEndMarker() {
    const t = document.body.innerText;
    // "No more older messages" = normal bitis. Bazi cok uzun sohbetlerde
    // sunucu en eski mesajlari zaten silmis oldugu icin bu hic gelmez,
    // bunun yerine "server cleared a prefix" gorunur. Ikisi de esit
    // gecerli "artik daha fazla yuklenecek yok" isareti.
    return t.indexOf('No more older messages') !== -1 ||
           t.indexOf('The server cleared a prefix of the conversation') !== -1;
  }
  let lastHeight = -1;
  let stableCount = 0;
  for (let i = 0; i < 150; i++) {
    const els = findScrollables();
    if (els.length === 0 && i === 0) break;
    let maxHeight = 0;
    for (const e of els) {
      e.scrollTop = 0;
      maxHeight = Math.max(maxHeight, e.scrollHeight);
    }
    if (hasEndMarker()) break;
    // bulut-senkron lazy-load ARADA DURAKLAYIP sonra devam edebiliyor --
    // sadece birkac ardisik ayni-yukseklik olcumune guvenip erken "bitti"
    // dememek icin: once uzunca bir sabitlik ister (8 x 1.8s ~ 14sn), SONRA
    // 4 saniyelik ayri bir dogrulama bekleyisiyle GERCEKTEN degismedigini
    // teyit eder -- degismisse sayaci sifirlayip devam eder.
    if (maxHeight === lastHeight) {
      stableCount++;
      if (stableCount >= 8) {
        await sleep(4000);
        for (const e of findScrollables()) { e.scrollTop = 0; }
        await sleep(500);
        if (hasEndMarker()) break;
        const confirmHeight = Math.max(0, ...findScrollables().map(e => e.scrollHeight));
        if (confirmHeight === lastHeight) {
          break; // dogrulandi: gercekten bitti
        } else {
          stableCount = 0;
          lastHeight = confirmHeight;
          continue;
        }
      }
    } else {
      stableCount = 0;
    }
    lastHeight = maxHeight;
    await sleep(1800);
  }
  // son bir guvenlik payi -- tam bu sirada yuklenmekte olan bir sayfa varsa
  await sleep(1500);
  for (const e of findScrollables()) { e.scrollTop = 0; }
  await sleep(500);
  const capturedText = document.body.innerText;
  // Metni ONCE al, SONRA (kullanicinin ekranda gordugu sohbeti en eski
  // mesajlarda birakmamak icin) panel(ler)i tekrar en alta dondur. Sirayi
  // TERSINE cevirme -- innerText'ten SONRA scroll edersek, olasi bir
  // virtualization (uzak mesajlarin DOM'dan cikmasi) yeni okunan metni
  // eksiltebilir; onceden yakalanan metin guvende kalir.
  for (const e of findScrollables()) { e.scrollTop = e.scrollHeight; }
  return capturedText;
})()
"""


def list_targets():
    with urllib.request.urlopen(f"http://localhost:{PORT}/json/list", timeout=5) as r:
        return json.load(r)


async def capture(ws_url):
    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": SCROLL_AND_CAPTURE_EXPR,
                "returnByValue": True,
                "awaitPromise": True,
            },
        }))

        async def _wait_for_reply():
            while True:
                resp = json.loads(await ws.recv())
                if resp.get("id") == 1:
                    result = resp.get("result", {}).get("result", {})
                    return result.get("value", "")

        return await asyncio.wait_for(_wait_for_reply(), timeout=CDP_RESPONSE_TIMEOUT)


def safe_name(s):
    s = html.unescape(s)
    return re.sub(r'[\\/:*?"<>|\x00]+', "_", s).strip()[:100] or "untitled"


def load_seen():
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text())
    return {}


def atomic_write_text(path, content):
    # click_through_all.py ayni anda bu dosyalari (SEEN_FILE, CURRENT_RUN_POINTER)
    # okuyabiliyor -- dogrudan write_text() bir crash/SIGINT ile yarida kesilirse
    # ya da okuyucu tam yazma sirasinda rastlarsa dosya 0 bayt/bozuk JSON kalabilir.
    # Once gecici dosyaya yaz, sonra os.replace() (atomic rename) ile degistir --
    # okuyucu her zaman ya eski-tam ya da yeni-tam icerigi gorur.
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def save_seen(seen):
    atomic_write_text(SEEN_FILE, json.dumps(seen, indent=2, ensure_ascii=False))


async def main(resume):
    global RUN_DIR, SEEN_FILE
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)

    if resume:
        RUN_DIR = find_latest_run_dir()
        if RUN_DIR is None:
            print(f"[{time.strftime('%H:%M:%S')}] --resume verildi ama hic run klasoru yok, yeni acilyor", flush=True)
            resume = False
    if not resume:
        RUN_DIR = ARCHIVE_ROOT / f"run_{time.strftime('%Y%m%d_%H%M%S')}"

    RUN_DIR.mkdir(exist_ok=True)
    SEEN_FILE = RUN_DIR / "_seen.json"
    atomic_write_text(CURRENT_RUN_POINTER, str(RUN_DIR))
    seen = load_seen()
    tag = "DEVAM (resume)" if resume else "YENI RUN"
    print(f"[{time.strftime('%H:%M:%S')}] {tag}: port {PORT} -> {RUN_DIR}", flush=True)
    print(f"[{time.strftime('%H:%M:%S')}] bu run icin {len(seen)} konusma kaydedilmis"
          + ("" if resume else " (onceki run'lar ayri, gorulmuyor)"), flush=True)

    while True:
        try:
            targets = [t for t in await asyncio.to_thread(list_targets) if t.get("type") == "page"]
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] list_targets hata: {e}", flush=True)
            await asyncio.sleep(3)
            continue

        for t in targets:
            url = t.get("url", "")
            m = re.search(r"/c/([0-9a-fA-F-]+)", url)
            if not m:
                continue  # konusma disi sayfa (dashboard vb.)
            conv_id = m.group(1)
            if conv_id in seen:
                continue
            if captured_in_other_run(conv_id):
                continue

            title = t.get("title", "untitled")
            target_id = t.get("id")
            print(f"[{time.strftime('%H:%M:%S')}] yeni konusma: {title} ({conv_id[:8]})", flush=True)
            await asyncio.sleep(1.0)  # ilk yuklemeye kisa pay
            try:
                text = await capture(t["webSocketDebuggerUrl"])
            except Exception as e:
                print(f"    okuma hatasi: {e}", flush=True)
                continue

            if not text or len(text) < 30:
                print("    icerik cok kisa, sonraki turda tekrar denenecek", flush=True)
                continue

            # Yaris-durumu korumasi: capture() suresince (uzun sohbetlerde
            # onlarca saniye surebilir) kullanici baska bir konusmaya
            # tiklamis olabilir -- ayni target, farkli URL. Kaydetmeden
            # once hedefin hala AYNI konusmada oldugunu dogrula.
            try:
                current_targets = {x.get("id"): x.get("url", "") for x in await asyncio.to_thread(list_targets)}
            except Exception:
                current_targets = {}
            now_url = current_targets.get(target_id, "")
            if conv_id not in now_url:
                print(f"    ATLANDI: yakalama surerken baska konusmaya gecilmis ({title}) -- tekrar tiklarsan yeniden denenecek", flush=True)
                continue

            fname = RUN_DIR / f"{safe_name(title)}__{conv_id[:8]}.txt"
            fname.write_text(text, encoding="utf-8")
            seen[conv_id] = {"title": title, "file": fname.name, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"), "chars": len(text)}
            save_seen(seen)
            print(f"    kaydedildi: {fname.name} ({len(text)} karakter)", flush=True)

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume", action="store_true",
                     help="yeni run acma, en son run klasorune devam et (zaten kaydedilmisleri atlar)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("CDP_PORT", PORT)),
                     help="CDP hata ayiklama portu (varsayilan: env CDP_PORT ya da 9223)")
    ap.add_argument("--archive-dir", type=str, default=os.environ.get("ARCHIVE_DIR", ""),
                     help="arsiv kok dizini (varsayilan: env ARCHIVE_DIR ya da ~/antigravity_chat_archive)")
    ap.add_argument("--poll-interval", type=float, default=float(os.environ.get("POLL_INTERVAL", POLL_INTERVAL)),
                     help="CDP hedef listesini kac saniyede bir tarayacagi (varsayilan: 1.5)")
    args = ap.parse_args()

    PORT = args.port
    if args.archive_dir:
        ARCHIVE_ROOT = Path(args.archive_dir).expanduser()
        CURRENT_RUN_POINTER = ARCHIVE_ROOT / "_current_run.txt"
    POLL_INTERVAL = args.poll_interval

    try:
        asyncio.run(main(args.resume))
    except KeyboardInterrupt:
        pass
