#!/usr/bin/env python3
"""
KENDI TERMINALINDEN calistir (Claude Code gibi bir ajanin tool-call
kisitlamalarini tamamen atlamak icin -- bkz. README.md "Why"). Antigravity
sidebar'inda proje proje / chat chat native CDP mouse click
(Input.dispatchMouseEvent -- Puppeteer/Playwright'in kullandigi ayni
mekanizma) ile tiklar.

Icerik YAKALAMA islemini bu script YAPMAZ -- o is ayrica calisan
watch_and_archive.py arka plan izleyicisine ait (~/antigravity_chat_archive/
klasorune yaziyor). Bu script sadece: satir bul -> tikla -> izleyicinin
yakalamasini bekle -> sonraki satir. Gerekirse "See all (N)" butonlarini
genisletir ve sidebar'i asagi kaydirir.

Kullanim:
  python3 watch_and_archive.py &          # once izleyiciyi baslat (ayri terminal/arka plan)
  python3 click_through_all.py            # once TEK satirla test (varsayilan)
  python3 click_through_all.py --live     # tum listeyi gercekten gez
  python3 click_through_all.py --live --max 20   # ilk 20 ile sinirla
"""
import argparse
import asyncio
import json
import os
import time
from pathlib import Path
import urllib.request
import websockets

PORT = 9223
ARCHIVE_ROOT = Path.home() / "antigravity_chat_archive"
CURRENT_RUN_POINTER = ARCHIVE_ROOT / "_current_run.txt"

ROW_PATTERN_JS = r"const timePattern = /^(\d+[smhdw]|\d+mo|\d+y|now|just now|yesterday)$/i;"

FIND_ROWS_EXPR = r"""
(function() {
  // Her satir artik data-cascade-id (gercek conv_id, UUID) tasiyan stabil bir
  // data-testid uzerinden bulunuyor -- innerText satir-sayisi sezgisi DEGIL.
  // Kasitli olarak eski sezgiye FALLBACK YOK: o sezgi zaten Kritik #1'in
  // (baslik carpismasinda sessiz veri kaybi) kaynagiydi, sessizce geri donmek
  // ayni riski gizlice geri getirir. Bu selector hic eslesmezse asagidaki
  // ana dongudeki stale-limit/"yeni satir yok" mekanizmasi zaten gorunur
  // sekilde durur.
  const rows = [];
  for (const row of document.querySelectorAll('[data-testid="conversation-row-sidebar"]')) {
    const convId = row.getAttribute('data-cascade-id');
    const a = row.querySelector('a[aria-label]');
    if (!convId || !a) continue;
    const lines = (row.innerText || '').trim().split('\n').map(s => s.trim()).filter(Boolean);
    rows.push({convId: convId, title: a.getAttribute('aria-label') || '', time: lines[1] || ''});
  }
  return JSON.stringify(rows);
})()
"""

# Bir satiri BULUR, gercekten gorunur olmasi icin scrollIntoView yapar,
# yerlesim/scroll'un oturmasini bekler, SONRA koordinatlarini olcer.
# getBoundingClientRect() ekran-disi/ust-container'i tarafindan kirpilmis
# bir elementte de gecerli (ama tiklanamaz) bir deger dondurur -- bu yuzden
# olcumden once scrollIntoView SART.
FIND_SCROLL_MEASURE_EXPR_TMPL = r"""
(async function() {{
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const wantConvId = {conv_id_json};
  // data-cascade-id ile DOGRUDAN eslestirme -- ayni baslik+zaman'a sahip
  // farkli sohbetler arasinda artik belirsizlik yok (eskiden innerText
  // metnini yeniden tarayip title+time'a gore ariyordu).
  const target = Array.from(document.querySelectorAll('[data-testid="conversation-row-sidebar"]'))
    .find(row => row.getAttribute('data-cascade-id') === wantConvId);
  if (!target) return JSON.stringify({{found: false}});
  target.scrollIntoView({{block: 'center', inline: 'nearest', behavior: 'instant'}});
  await sleep(400);
  const r = target.getBoundingClientRect();
  const visible = r.width > 0 && r.height > 0 &&
    r.top >= 0 && r.left >= 0 &&
    r.bottom <= window.innerHeight && r.right <= window.innerWidth;
  return JSON.stringify({{found: true, visible: visible, x: r.x + r.width/2, y: r.y + r.height/2,
                          top: r.top, left: r.left, bottom: r.bottom, right: r.right}});
}})()
"""

# Sidebar'i ONCE dogrulanmis data-testid ile bul (bkz. DONE.md 2026-09-21).
# Bulunamazsa (UI degismis olabilir) eski "en az 3 sohbet-satiri iceren
# scrollable" sezgisine DUS -- row-detection'in aksine burada sessiz fallback
# kabul edilebilir, cunku en kotu ihtimalle scroll yanlis/hic konteynere
# gitmez (gorunur, zararsiz), Kritik #1'deki gibi sessiz veri kaybina yol
# acmaz. Uc cagiran yer de (asagida) bunu ortak kullaniyor -- eskiden ayni
# sezgi 3 kopya halinde tekrarlaniyordu.
FIND_SIDEBAR_JS = r"""
function findSidebarContainers() {
  const direct = document.querySelector('[data-testid="conversation-list-sidebar"]');
  if (direct) return [direct];
  """ + ROW_PATTERN_JS + r"""
  function countRowDescendants(container) {
    let count = 0;
    for (const el of container.querySelectorAll('div,li,a,button')) {
      const t = (el.innerText || '').trim();
      const lines = t.split('\n').map(s => s.trim()).filter(Boolean);
      if (lines.length === 2 && timePattern.test(lines[1])) {
        count++;
        if (count >= 3) return true;
      }
    }
    return false;
  }
  return Array.from(document.querySelectorAll('*'))
    .filter(e => e.scrollHeight > e.clientHeight + 50)
    .filter(countRowDescendants);
}
"""

EXPAND_AND_SCROLL_EXPR = r"""
(function(){
  """ + FIND_SIDEBAR_JS + r"""
  const seeAlls = Array.from(document.querySelectorAll('div,li,a,button'))
    .filter(el => /^See all \(\d+\)$/.test((el.innerText||'').trim()));
  let clicked = 0;
  for (const el of seeAlls) { el.click(); clicked++; }

  const sidebarLike = findSidebarContainers();
  for (const e of sidebarLike) { e.scrollTop += 600; }
  const maxScrollTop = Math.max(0, ...sidebarLike.map(e => e.scrollTop));
  const maxPossible = Math.max(0, ...sidebarLike.map(e => e.scrollHeight - e.clientHeight));
  return JSON.stringify({seeAllClicked: clicked, sidebarContainers: sidebarLike.length,
                          scrollTop: maxScrollTop, maxPossible: maxPossible});
})()
"""

SCROLL_SIDEBAR_TO_TOP_EXPR = r"""
(async function(){
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  """ + FIND_SIDEBAR_JS + r"""
  // Uygulama, aktif/son-tiklanan sohbeti sidebar'da gorunur tutmak icin
  // otomatik geri kaydiriyor olabilir -- tek seferlik reset bunu yenemez.
  // Birkac kez, aralarla, tekrar tekrar sifirla.
  let lastCount = -1;
  for (let i = 0; i < 10; i++) {
    const sidebarLike = findSidebarContainers();
    for (const e of sidebarLike) { e.scrollTop = 0; }
    lastCount = sidebarLike.length;
    await sleep(350);
  }
  return JSON.stringify({resetContainers: lastCount, rounds: 10});
})()
"""

# Tiklamadan sonra uygulama, az onceki aktif/tiklanan sohbeti gorunur tutmak
# icin sidebar'i KENDI KAFASINA GORE bir yere kaydirabiliyor -- bu bizim
# ilerleme takibimizi (nerede kaldik) bozuyordu. Her tarama oncesi kendi
# takip ettigimiz "cursor" pozisyonunu YENIDEN DAYATIYORUZ, uygulamanin
# scroll'una guvenmiyoruz.
SET_SIDEBAR_SCROLL_TMPL = r"""
(function(){
  """ + FIND_SIDEBAR_JS + r"""
  const sidebarLike = findSidebarContainers();
  for (const e of sidebarLike) { e.scrollTop = __SCROLL_TARGET__; }
  return JSON.stringify({containers: sidebarLike.length});
})()
"""


def list_targets():
    with urllib.request.urlopen(f"http://localhost:{PORT}/json/list", timeout=5) as r:
        return json.load(r)


def current_run_dir():
    if CURRENT_RUN_POINTER.exists():
        return Path(CURRENT_RUN_POINTER.read_text().strip())
    return None


def load_captured_conv_ids(cross_run=True):
    # Varsayilan (cross_run=True): TUM run_*/ klasorlerinin _seen.json'larinin
    # BIRLESIMINE bakar -- bir onceki tasarim SADECE aktif run'a bakiyordu, bu
    # da (watch_and_archive.py kod-duzeltmeleri yuzunden birden fazla kez
    # restart edilince) daha ONCEKI bir run'da zaten yakalanmis bir sohbetin
    # bu run'da hala "yeni" gorunup TEKRAR tiklanmasina yol aciyordu (gozlem:
    # "bastan basladi" -- GitLab Merge Request Review gibi ilk run'da zaten
    # yakalanmis basliklar tekrar tiklandi). Artik gecmiste HANGI run'da
    # olursa olsun bir kere yakalanmis her conv_id'yi "zaten yapildi" sayiyoruz.
    #
    # Eskiden BASLIK metnine gore tekillestiriyordu (v["title"]) -- bu, farkli
    # projelerdeki ayni basligi tasiyan sohbetlerin (Kritik #1) sessizce hic
    # yedeklenmemesine yol aciyordu (bkz. DONE.md 2026-09-21: canli DOM
    # incelemesiyle dogrulandi, sidebar satirlarinda gercek conv_id mevcut).
    # _seen.json zaten conv_id (UUID) ile anahtarlanmis oldugu icin
    # (watch_and_archive.py) dogrudan dict key'lerini kullanmak yeterli.
    #
    # cross_run=False (--fresh bayragi): SADECE aktif run'a bakar -- kullanici
    # bilerek TAM YENI bir yedekleme turu istediginde (her seyi, daha once
    # baska run'larda alinmis olsa bile, yeniden cekmek icin) kullanilir.
    conv_ids = set()
    if not ARCHIVE_ROOT.exists():
        return conv_ids

    if cross_run:
        run_dirs = list(ARCHIVE_ROOT.glob("run_*"))
    else:
        only = current_run_dir()
        run_dirs = [only] if only else []

    for run_dir in run_dirs:
        seen_file = run_dir / "_seen.json"
        if not seen_file.exists():
            continue
        try:
            data = json.loads(seen_file.read_text())
            conv_ids.update(data.keys())
        except Exception:
            continue
    return conv_ids


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.next_id = 1

    async def send(self, method, params, timeout=15.0):
        mid = self.next_id
        self.next_id += 1
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))

        async def _wait_for_reply():
            while True:
                resp = json.loads(await self.ws.recv())
                if resp.get("id") == mid:
                    return resp

        return await asyncio.wait_for(_wait_for_reply(), timeout=timeout)

    async def eval(self, expr, await_promise=False):
        resp = await self.send("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": await_promise,
        })
        return resp.get("result", {}).get("result", {}).get("value")

    async def click_at(self, x, y):
        await self.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        await self.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        await asyncio.sleep(0.05)
        await self.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})

    async def find_scroll_measure(self, conv_id):
        expr = FIND_SCROLL_MEASURE_EXPR_TMPL.format(conv_id_json=json.dumps(conv_id))
        raw = await self.eval(expr, await_promise=True)
        return json.loads(raw) if raw else {"found": False}


async def get_ws_url():
    targets = [t for t in await asyncio.to_thread(list_targets) if t.get("type") == "page"]
    if not targets:
        raise RuntimeError("Antigravity page target bulunamadi (uygulama --remote-debugging-port ile acik mi?)")
    if len(targets) > 1:
        print(f"[UYARI] {len(targets)} adet 'page' target bulundu, ilki kullanilacak "
              f"(targets[0] varsayimi hala kirilgan -- yanlis pencereye baglanirsak "
              f"asagidaki listeden dogrusunu ayirt et):")
        for t in targets:
            print(f"    - id={t.get('id')} title={t.get('title')!r} url={t.get('url')!r}")
    return targets[0]["webSocketDebuggerUrl"]


async def run(live, max_clicks, fresh=False, timeout=320.0):
    ws_url = await get_ws_url()
    clicked_conv_ids = set()
    total = 0
    cross_run = not fresh

    async with websockets.connect(ws_url, max_size=None) as ws:
        cdp = CDP(ws)

        reset = await cdp.eval(SCROLL_SIDEBAR_TO_TOP_EXPR, await_promise=True)
        print(f"[{time.strftime('%H:%M:%S')}] sidebar en tepeye sifirlandi (tekrarli): {reset}")
        print(f"[{time.strftime('%H:%M:%S')}] mod: {'--fresh (sadece aktif run, gecmis gormezden gelinir)' if fresh else 'normal (tum run gecmisi birlesimi)'}")

        stale_rounds = 0
        last_scroll_top = -1
        # Kendi ilerleme takibimiz. Uygulama, her tiklamadan SONRA az onceki
        # aktif sohbeti gorunur tutmak icin sidebar'i KENDI KAFASINA GORE
        # bir yere kaydirabiliyor -- bu bizim "nerede kaldik" bilgimizi
        # bozuyor (gozlem: "cok atladi, direkt secili sohbete gitti"). Bu
        # yuzden her tarama ONCESINDE kendi cursor'umuzu YENIDEN DAYATIYORUZ,
        # uygulamanin son biraktigi pozisyona guvenmiyoruz.
        sidebar_cursor = 0
        # "yeni satir yok" TEK BASINA pes etme sebebi degil -- virtualized liste
        # scroll GERCEKTEN ilerlerken bile DOM'a henuz yeni satirlari render
        # etmemis olabilir, bu da bir tarama turunde gecici olarak "hepsi zaten
        # yakalanmis/hic satir yok" gibi gorunmesine yol acabiliyor. Bu yuzden
        # scrollTop'un KENDISI de ilerliyor mu diye ayrica takip ediyoruz:
        # pozisyon hala degisiyorsa sayaci SIFIRLA, gercekten dibe vurunca
        # (pozisyon da sabitlenince) pes et. (Eskiden burada ayrica baslik+zaman
        # cift-carpismasi da bir sebep olarak sayiliyordu -- artik dedup gercek
        # conv_id'ye gore oldugu icin (bkz. DONE.md 2026-09-21) o senaryo artik
        # gecerli degil.)
        STALE_LIMIT = 40

        while total < max_clicks and stale_rounds < STALE_LIMIT:
            try:
                # Kendi cursor'umuzu yeniden dayat -- tiklama sonrasi
                # uygulamanin sidebar'i baska bir yere kaydirmis olma
                # ihtimaline karsi.
                await cdp.eval(SET_SIDEBAR_SCROLL_TMPL.replace("__SCROLL_TARGET__", str(sidebar_cursor)))
                captured_conv_ids = load_captured_conv_ids(cross_run)
                rows_raw = await cdp.eval(FIND_ROWS_EXPR)
                rows = json.loads(rows_raw) if rows_raw else []
            except Exception as e:
                print(f"[{time.strftime('%H:%M:%S')}] HATA (satir taramasi, tekrar denenecek): {type(e).__name__}: {e}")
                await asyncio.sleep(3.0)
                continue

            todo = [r for r in rows
                    if r["convId"] not in clicked_conv_ids
                    and r["convId"] not in captured_conv_ids]

            if not todo:
                try:
                    exp = await cdp.eval(EXPAND_AND_SCROLL_EXPR)
                    exp_data = json.loads(exp) if exp else {}
                except Exception as e:
                    print(f"[{time.strftime('%H:%M:%S')}] HATA (genislet/scroll): {type(e).__name__}: {e}")
                    exp_data = {}

                cur_top = exp_data.get("scrollTop")
                max_top = exp_data.get("maxPossible")
                if cur_top is not None and cur_top != last_scroll_top:
                    # pozisyon hala ilerliyor -- "yeni satir yok" gorunse de bu
                    # GERCEK bir dip degil, virtualized liste henuz render
                    # etmemis olabilir. Sayaci sifirla, pes etme.
                    stale_rounds = 0
                    last_scroll_top = cur_top
                    sidebar_cursor = cur_top
                else:
                    stale_rounds += 1
                remaining = (max_top - cur_top) if (cur_top is not None and max_top is not None) else "?"
                print(f"[{time.strftime('%H:%M:%S')}] yeni satir yok, genislet/scroll (stale {stale_rounds}/{STALE_LIMIT}, scrollTop={cur_top}, kalan~{remaining}px): {exp_data}")
                await asyncio.sleep(2.2)
                continue

            stale_rounds = 0
            row = todo[0]
            clicked_conv_ids.add(row["convId"])
            print(f"[{time.strftime('%H:%M:%S')}] hedef: {row['title']} ({row['time']}) conv_id={row['convId'][:8]}")

            try:
                measured = await cdp.find_scroll_measure(row["convId"])
                if not measured.get("found"):
                    print("    UYARI: scrollIntoView sirasinda element kayboldu (virtualized liste), atlaniyor")
                    continue
                if not measured.get("visible"):
                    print(f"    UYARI: scrollIntoView sonrasi hala tam gorunur degil (rect={measured}), yine de deneniyor")
                x, y = measured["x"], measured["y"]
                print(f"    dogrulanmis koordinat: ({x:.0f},{y:.0f})")

                if not live:
                    print("  --test modu: sadece bu 1 satiri BULDUM+olcTUM, tiklamadan cikiyorum.")
                    print("  Gercekten tiklamasini istiyorsan: python3 click_through_all.py --live")
                    return

                await cdp.click_at(x, y)
                total += 1

                # izleyici (watch_and_archive.py) uzun sohbetlerde eski mesajlari
                # bulut-senkron lazy-load ile yukledigi icin tam 130sn'ye kadar
                # surebilir (bkz. izleyicinin kendi SCROLL_AND_CAPTURE_EXPR'i) --
                # erken vazgecip sonrakine gecmemek icin ayni mertebede bekleriz.
                # NOT: eger bu satir ONCEKI bir --live calistirmasinda tiklanip
                # izleyici henuz yetismeden script kapatildiysa, burada TEKRAR
                # tiklanir (zararsiz, ayni sohbet zaten acik) ve izleyicinin
                # o eski islemi bitirmesi beklenir -- bu "takili kalmis gibi"
                # gorunebilir, asagidaki kalp-atisi bunu ayirt etmeye yarar.
                waited = 0.0
                caught = False
                while waited < timeout:
                    await asyncio.sleep(2.0)
                    waited += 2.0
                    if row["convId"] in load_captured_conv_ids(cross_run):
                        print(f"    izleyici yakaladi ({waited:.1f}sn)")
                        caught = True
                        break
                    if int(waited) % 20 == 0:
                        print(f"    ... hala bekleniyor ({waited:.0f}sn, canli -- izleyici muhtemelen uzun bir sohbeti scroll ediyor)")
                if not caught:
                    print(f"    UYARI: izleyici {waited:.1f}sn'de yakalamadi -- tiklama basarisiz olmus olabilir, devam ediliyor (bir sonraki --live calistirmasinda otomatik yeniden denenecek)")
            except Exception as e:
                print(f"    HATA (bu satir atlaniyor, script devam ediyor): {type(e).__name__}: {e}")
                continue

        print(f"\nBitti. Bu calistirmada tiklanan: {total}, dur-nedeni: {'max limit' if total>=max_clicks else 'yeni satir kalmadi'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="gercekten tikla (yoksa sadece ilk satiri BULUR, tiklamaz)")
    ap.add_argument("--max", type=int, default=500, help="en fazla kac tiklama")
    ap.add_argument("--fresh", action="store_true",
                     help="gecmisi (diger run klasorlerini) gormezden gel -- daha once baska "
                          "bir run'da yakalanmis olsa bile HER SEYI yeniden tikla. Bunu "
                          "watch_and_archive.py'yi (--resume OLMADAN) yeni bir run acmis "
                          "haldeyken kullan.")
    ap.add_argument("--port", type=int, default=int(os.environ.get("CDP_PORT", PORT)),
                     help="CDP hata ayiklama portu (varsayilan: env CDP_PORT ya da 9223)")
    ap.add_argument("--archive-dir", type=str, default=os.environ.get("ARCHIVE_DIR", ""),
                     help="arsiv kok dizini (varsayilan: env ARCHIVE_DIR ya da ~/antigravity_chat_archive)")
    ap.add_argument("--timeout", type=float, default=float(os.environ.get("CAPTURE_TIMEOUT", 320)),
                     help="izleyicinin bir sohbeti yakalamasini bekleme azami suresi, saniye (varsayilan: 320)")
    args = ap.parse_args()

    PORT = args.port
    if args.archive_dir:
        ARCHIVE_ROOT = Path(args.archive_dir).expanduser()
        CURRENT_RUN_POINTER = ARCHIVE_ROOT / "_current_run.txt"

    asyncio.run(run(args.live, args.max, args.fresh, timeout=args.timeout))
