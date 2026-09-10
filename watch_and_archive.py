#!/usr/bin/env python3
"""
Antigravity IDE conversation watcher/archiver.

Kullanici Antigravity sidebar'inda proje proje / chat chat tiklarken, bu
script CDP (port 9223) uzerinden her yeni acilan konusmayi algilar, mesaj
panelini "No more older messages" gorunene / yukseklik sabitlenene kadar
en uste scroll eder (lazy-load'i tetiklemek icin), sonra tam sayfa metnini
kaydeder. Tiklama/navigasyon YAPMAZ -- sadece: (a) sayfa metni okuma,
(b) scrollTop mutasyonu. Tiklama tarafi icin bkz. click_through_all.py.

Her calistirma AYRI, zaman-damgali bir klasore yazar (backup gibi --
onceki run'lardaki kayitlari GORMEZ, ayni sohbet tekrar acilirsa tekrar
yakalar). Cikti: ~/antigravity_chat_archive/run_<YYYYMMDD_HHMMSS>/
<baslik>__<conv_id8>.txt (repo DISINDA, kapsam kullanicinin tum
portfoyunu icerdigi icin). Aktif run'in yolu, click_through_all.py'nin
bulabilmesi icin ~/antigravity_chat_archive/_current_run.txt dosyasina
yazilir. Durum (sadece bu run icin, ayni sohbeti bu run icinde iki kere
yakalamamak icin): <run_klasoru>/_seen.json.
"""
import asyncio
import json
import re
import time
import urllib.request
from pathlib import Path

import websockets

PORT = 9223
ARCHIVE_ROOT = Path.home() / "antigravity_chat_archive"
RUN_DIR = ARCHIVE_ROOT / f"run_{time.strftime('%Y%m%d_%H%M%S')}"
CURRENT_RUN_POINTER = ARCHIVE_ROOT / "_current_run.txt"
SEEN_FILE = RUN_DIR / "_seen.json"
POLL_INTERVAL = 1.5

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
  return document.body.innerText;
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
        while True:
            resp = json.loads(await ws.recv())
            if resp.get("id") == 1:
                result = resp.get("result", {}).get("result", {})
                return result.get("value", "")


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s)[:100] or "untitled"


def load_seen():
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text())
    return {}


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(seen, indent=2, ensure_ascii=False))


async def main():
    ARCHIVE_ROOT.mkdir(exist_ok=True)
    RUN_DIR.mkdir(exist_ok=True)
    CURRENT_RUN_POINTER.write_text(str(RUN_DIR))
    seen = load_seen()
    print(f"[{time.strftime('%H:%M:%S')}] YENI RUN: port {PORT} -> {RUN_DIR}", flush=True)
    print(f"[{time.strftime('%H:%M:%S')}] bu run icin {len(seen)} konusma kaydedilmis (onceki run'lar ayri, gorulmuyor)", flush=True)

    while True:
        try:
            targets = [t for t in list_targets() if t.get("type") == "page"]
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
                current_targets = {x.get("id"): x.get("url", "") for x in list_targets()}
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
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
