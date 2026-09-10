# agy2_0_ide_backup

*English · [Türkçe](README_TR.md)*

Two small scripts that back up your own **Antigravity IDE** (Google's Gemini-based "Visual
Agent Orchestration Platform," v2.8+) chat history to local `.txt` files, by driving the
already-running app over the Chrome DevTools Protocol (CDP).

## Why this exists

Antigravity IDE has **no bulk conversation-export feature**, and — this took some digging to
confirm — **no persistent local copy of your chat history either**. We checked four
independent ways: `state.vscdb`'s chat-related keys are always empty, no new file appears on
disk during live use, all network traffic goes out over TLS to Google's own IPs, and a direct
dump of the renderer's `IndexedDB`/`localStorage` came back empty. The architecture is
cloud-sync only — a conversation's content exists locally only for as long as its tab is open
and rendered.

That leaves exactly one way to get your own history out: open each conversation (so it
renders), and read `document.body.innerText` off the live page while it's up. `watch_and_archive.py`
does the reading. But *opening every single conversation, one at a time, across every
project* by hand is exactly the kind of tedious, repetitive task you'd normally hand to an
coding agent — so `click_through_all.py` does the clicking, automatically, via CDP's native
`Input.dispatchMouseEvent` (the same mechanism Puppeteer/Playwright use).

**Why two scripts, and why does one of them insist on being run by *you*, not by an agent?**
Coding-agent harnesses (this was built with one) reasonably restrict what an agent can
automate inside a *different* live GUI application — clicking around in a third-party app
blurs the line between "helping you" and "an agent driving software on your behalf without
you watching," which is a fair thing to be cautious about by default, even when you *do* want
it and say so. That restriction applies to the agent's own tool calls, not to a script you
run yourself in your own terminal — so `click_through_all.py` is written to be run directly
by you, sidestepping the question entirely rather than trying to talk an agent's safety layer
into an exception. `watch_and_archive.py` only *reads* the page, which is a much lower-stakes
action, and ran fine either way.

## Requirements

- Antigravity IDE, started with remote debugging enabled:
  ```bash
  pkill -f antigravity/antigravity   # if already running
  /path/to/antigravity --remote-debugging-port=9223 --remote-allow-origins=* &
  ```
- Python 3.9+, `pip install -r requirements.txt`

## Usage

```bash
# terminal 1 — starts watching, creates a new timestamped run folder
python3 watch_and_archive.py

# terminal 2 — test first (finds+measures one row, does NOT click)
python3 click_through_all.py
# looks sane? now actually click through everything:
python3 click_through_all.py --live
```

Output lands in `~/antigravity_chat_archive/run_<YYYYMMDD_HHMMSS>/`, one `.txt` file per
conversation, plus a `_seen.json` index. **Every run gets its own folder** — re-running
`click_through_all.py --live` against a fresh `watch_and_archive.py` run re-clicks and
re-captures everything again (a full new backup pass), it does not try to diff against
previous runs. Use `python3 watch_and_archive.py --resume` instead when you're restarting
after an interruption (a crash, a code fix) and want to pick up where the *same* pass left
off rather than starting a whole new one — it continues writing into the most recent
`run_*/` folder and skips whatever it already captured there. The archive folder lives
outside this repo and is never committed anywhere; treat it as sensitive — it contains the
full text of whatever conversations you had open, across whatever projects you were using
the IDE for.

## How the two pieces coordinate

- `watch_and_archive.py` polls the CDP target list. When a new conversation URL appears, it
  repeatedly scrolls the message pane to the top (lazy-loading older messages from the cloud,
  which can pause mid-load — the stability check waits out a false "done" before believing
  it), then saves `document.body.innerText`.
- `click_through_all.py` finds sidebar rows by a simple structural heuristic (an element whose
  own text is exactly two lines: a title, then a relative-time label like `13m`/`2mo`),
  `scrollIntoView`s the one it's about to click (a plain `getBoundingClientRect()` can return
  coordinates for an element that's scrolled out of view — click there and you hit whatever
  else happens to be on screen), clicks it via native CDP mouse events, then waits for the
  watcher to confirm it caught the new conversation before moving to the next row.

## Known limitations

- Row detection is a text-shape heuristic, not a real DOM selector — it's what was reachable
  without a working element inspector at the time (see the private companion repo's build log
  for why). It has worked reliably in practice but isn't guaranteed against every possible
  sidebar layout.
- Duplicate `(title, relative-time)` pairs across different projects could in principle
  collide; not observed in practice but not structurally ruled out either.
- Everything here is specific to Antigravity IDE's current (2026) DOM structure and CDP
  exposure. If the app changes its UI, the heuristics may need updating.

MIT licensed — see [`LICENSE`](LICENSE).
