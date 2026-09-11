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
import time
from pathlib import Path
import urllib.request
import websockets

PORT = 9223
ARCHIVE_ROOT = Path.home() / "antigravity_chat_archive"

ROW_PATTERN_JS = r"const timePattern = /^(\d+[smhd]|\d+mo|\d+y)$/;"

FIND_ROWS_EXPR = r"""
(function() {
  """ + ROW_PATTERN_JS + r"""
  const all = document.querySelectorAll('div,li,a,button');
  const rows = [];
  for (const el of all) {
    const t = (el.innerText || '').trim();
    const lines = t.split('\n').map(s => s.trim()).filter(Boolean);
    if (lines.length === 2 && timePattern.test(lines[1]) && lines[0].length > 1) {
      rows.push({title: lines[0], time: lines[1]});
    }
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
  """ + ROW_PATTERN_JS + r"""
  const wantTitle = {title_json};
  const wantTime = {time_json};
  const all = document.querySelectorAll('div,li,a,button');
  let target = null;
  for (const el of all) {{
    const t = (el.innerText || '').trim();
    const lines = t.split('\n').map(s => s.trim()).filter(Boolean);
    if (lines.length === 2 && lines[0] === wantTitle && lines[1] === wantTime) {{ target = el; break; }}
  }}
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

# Scroll edilecek container'i "en az 3 sohbet-satiri iceren" olarak sec --
# boylece yanlislikla sag taraftaki chat panelini degil, gercekten sidebar
# listesini kaydiriyoruz.
EXPAND_AND_SCROLL_EXPR = r"""
(function(){
  const timePattern = /^(\d+[smhd]|\d+mo|\d+y)$/;
  const seeAlls = Array.from(document.querySelectorAll('div,li,a,button'))
    .filter(el => /^See all \(\d+\)$/.test((el.innerText||'').trim()));
  let clicked = 0;
  for (const el of seeAlls) { el.click(); clicked++; }

  function countRowDescendants(container) {
    let count = 0;
    const cand = container.querySelectorAll('div,li,a,button');
    for (const el of cand) {
      const t = (el.innerText || '').trim();
      const lines = t.split('\n').map(s => s.trim()).filter(Boolean);
      if (lines.length === 2 && timePattern.test(lines[1])) {
        count++;
        if (count >= 3) return true;
      }
    }
    return false;
  }

  const candidates = Array.from(document.querySelectorAll('*'))
    .filter(e => e.scrollHeight > e.clientHeight + 50);
  const sidebarLike = candidates.filter(countRowDescendants);
  for (const e of sidebarLike) { e.scrollTop += 600; }
  const maxScrollTop = Math.max(0, ...sidebarLike.map(e => e.scrollTop));
  const maxPossible = Math.max(0, ...sidebarLike.map(e => e.scrollHeight - e.clientHeight));
  return JSON.stringify({seeAllClicked: clicked, sidebarContainers: sidebarLike.length,
                          totalScrollables: candidates.length, scrollTop: maxScrollTop,
                          maxPossible: maxPossible});
})()
"""

SCROLL_SIDEBAR_TO_TOP_EXPR = r"""
(async function(){
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const timePattern = /^(\d+[smhd]|\d+mo|\d+y)$/;
  function countRowDescendants(container) {
    let count = 0;
    const cand = container.querySelectorAll('div,li,a,button');
    for (const el of cand) {
      const t = (el.innerText || '').trim();
      const lines = t.split('\n').map(s => s.trim()).filter(Boolean);
      if (lines.length === 2 && timePattern.test(lines[1])) {
        count++;
        if (count >= 3) return true;
      }
    }
    return false;
  }
  // Uygulama, aktif/son-tiklanan sohbeti sidebar'da gorunur tutmak icin
  // otomatik geri kaydiriyor olabilir -- tek seferlik reset bunu yenemez.
  // Birkac kez, aralarla, tekrar tekrar sifirla.
  let lastCount = -1;
  for (let i = 0; i < 10; i++) {
    const candidates = Array.from(document.querySelectorAll('*'))
      .filter(e => e.scrollHeight > e.clientHeight + 50);
    const sidebarLike = candidates.filter(countRowDescendants);
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
  const timePattern = /^(\d+[smhd]|\d+mo|\d+y)$/;
  function countRowDescendants(container) {
    let count = 0;
    const cand = container.querySelectorAll('div,li,a,button');
    for (const el of cand) {
      const t = (el.innerText || '').trim();
      const lines = t.split('\n').map(s => s.trim()).filter(Boolean);
      if (lines.length === 2 && timePattern.test(lines[1])) {
        count++;
        if (count >= 3) return true;
      }
    }
    return false;
  }
  const candidates = Array.from(document.querySelectorAll('*'))
    .filter(e => e.scrollHeight > e.clientHeight + 50);
  const sidebarLike = candidates.filter(countRowDescendants);
  for (const e of sidebarLike) { e.scrollTop = __SCROLL_TARGET__; }
  return JSON.stringify({containers: sidebarLike.length});
})()
"""


def list_targets():
    with urllib.request.urlopen(f"http://localhost:{PORT}/json/list", timeout=5) as r:
        return json.load(r)


def load_captured_titles():
    # TUM run_*/ klasorlerinin _seen.json'larinin BIRLESIMINE bakar -- bir
    # onceki tasarim SADECE aktif run'a bakiyordu, bu da (watch_and_archive.py
    # kod-duzeltmeleri yuzunden birden fazla kez restart edilince) daha ONCEKI
    # bir run'da zaten yakalanmis bir sohbetin bu run'da hala "yeni" gorunup
    # TEKRAR tiklanmasina yol aciyordu (gozlem: "bastan basladi" -- GitLab
    # Merge Request Review gibi ilk run'da zaten yakalanmis basliklar tekrar
    # tiklandi). Artik gecmiste HANGI run'da olursa olsun bir kere
    # yakalanmis her basligi "zaten yapildi" sayiyoruz.
    titles = set()
    if not ARCHIVE_ROOT.exists():
        return titles
    for run_dir in ARCHIVE_ROOT.glob("run_*"):
        seen_file = run_dir / "_seen.json"
        if not seen_file.exists():
            continue
        try:
            data = json.loads(seen_file.read_text())
            titles.update(v["title"] for v in data.values())
        except Exception:
            continue
    return titles


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.next_id = 1

    async def send(self, method, params):
        mid = self.next_id
        self.next_id += 1
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            resp = json.loads(await self.ws.recv())
            if resp.get("id") == mid:
                return resp

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

    async def find_scroll_measure(self, title, time_label):
        expr = FIND_SCROLL_MEASURE_EXPR_TMPL.format(
            title_json=json.dumps(title), time_json=json.dumps(time_label),
        )
        raw = await self.eval(expr, await_promise=True)
        return json.loads(raw) if raw else {"found": False}


async def get_ws_url():
    targets = [t for t in list_targets() if t.get("type") == "page"]
    if not targets:
        raise RuntimeError("Antigravity page target bulunamadi (uygulama --remote-debugging-port ile acik mi?)")
    return targets[0]["webSocketDebuggerUrl"]


async def run(live, max_clicks):
    ws_url = await get_ws_url()
    clicked_keys = set()
    total = 0

    async with websockets.connect(ws_url, max_size=None) as ws:
        cdp = CDP(ws)

        reset = await cdp.eval(SCROLL_SIDEBAR_TO_TOP_EXPR, await_promise=True)
        print(f"[{time.strftime('%H:%M:%S')}] sidebar en tepeye sifirlandi (tekrarli): {reset}")

        stale_rounds = 0
        last_scroll_top = -1
        # Kendi ilerleme takibimiz. Uygulama, her tiklamadan SONRA az onceki
        # aktif sohbeti gorunur tutmak icin sidebar'i KENDI KAFASINA GORE
        # bir yere kaydirabiliyor -- bu bizim "nerede kaldik" bilgimizi
        # bozuyor (gozlem: "cok atladi, direkt secili sohbete gitti"). Bu
        # yuzden her tarama ONCESINDE kendi cursor'umuzu YENIDEN DAYATIYORUZ,
        # uygulamanin son biraktigi pozisyona guvenmiyoruz.
        sidebar_cursor = 0
        # "yeni satir yok" TEK BASINA pes etme sebebi degil -- ayni (baslik,zaman)
        # cifti farkli projelerde tekrar edebiliyor (ornek: "GitLab Merge Request
        # Review" birden fazla projede), bu da scroll GERCEKTEN ilerlerken bile
        # bir sure "hepsi zaten yakalanmis" gibi gorunmesine yol acabiliyor.
        # Bu yuzden scrollTop'un KENDISI de ilerliyor mu diye ayrica takip
        # ediyoruz: pozisyon hala degisiyorsa sayaci SIFIRLA, gercekten dibe
        # vurunca (pozisyon da sabitlenince) pes et.
        STALE_LIMIT = 40

        while total < max_clicks and stale_rounds < STALE_LIMIT:
            try:
                # Kendi cursor'umuzu yeniden dayat -- tiklama sonrasi
                # uygulamanin sidebar'i baska bir yere kaydirmis olma
                # ihtimaline karsi.
                await cdp.eval(SET_SIDEBAR_SCROLL_TMPL.replace("__SCROLL_TARGET__", str(sidebar_cursor)))
                captured_titles = load_captured_titles()
                rows_raw = await cdp.eval(FIND_ROWS_EXPR)
                rows = json.loads(rows_raw) if rows_raw else []
            except Exception as e:
                print(f"[{time.strftime('%H:%M:%S')}] HATA (satir taramasi, tekrar denenecek): {type(e).__name__}: {e}")
                await asyncio.sleep(3.0)
                continue

            todo = [r for r in rows
                    if (r["title"] + "|" + r["time"]) not in clicked_keys
                    and r["title"] not in captured_titles]

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
                    # pozisyon hala ilerliyor -- "yeni satir yok" gorunse de
                    # bu GERCEK bir dip degil, sadece baslik-eslesmesi kacirmis
                    # olabilir. Sayaci sifirla, pes etme.
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
            key = row["title"] + "|" + row["time"]
            clicked_keys.add(key)
            print(f"[{time.strftime('%H:%M:%S')}] hedef: {row['title']} ({row['time']})")

            try:
                measured = await cdp.find_scroll_measure(row["title"], row["time"])
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
                prev_count = len(load_captured_titles())
                caught = False
                while waited < 320:
                    await asyncio.sleep(2.0)
                    waited += 2.0
                    if len(load_captured_titles()) > prev_count:
                        print(f"    izleyici yakaladi ({waited:.1f}sn)")
                        caught = True
                        break
                    if int(waited) % 20 == 0:
                        print(f"    ... hala bekleniyor ({waited:.0f}sn, canli -- izleyici muhtemelen uzun bir sohbeti scroll ediyor)")
                if not caught:
                    print(f"    UYARI: izleyici {waited:.1f}sn'de yakalamadi -- tiklama isteFAILmis olabilir, devam ediliyor (bir sonraki --live calistirmasinda otomatik yeniden denenecek)")
            except Exception as e:
                print(f"    HATA (bu satir atlaniyor, script devam ediyor): {type(e).__name__}: {e}")
                continue

        print(f"\nBitti. Bu calistirmada tiklanan: {total}, dur-nedeni: {'max limit' if total>=max_clicks else 'yeni satir kalmadi'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="gercekten tikla (yoksa sadece ilk satiri BULUR, tiklamaz)")
    ap.add_argument("--max", type=int, default=500, help="en fazla kac tiklama")
    args = ap.parse_args()
    asyncio.run(run(args.live, args.max))
