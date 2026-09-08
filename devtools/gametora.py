"""Client for GameTora's data layer.

The site keeps its game data in plain JSON files behind a manifest that maps a
logical path to a content hash:

    https://gametora.com/data/manifests/umamusume.json
    -> {"support-cards": "d6dfa084", "skills": "11a58047", ...}
    https://gametora.com/data/umamusume/support-cards.d6dfa084.json

Reading those is what the pages themselves do, and it replaces the browser
scraping this repo used to rely on. That mattered because the old approach
stopped working entirely: GameTora replaced the tippy tooltips that held the
training-event outcomes with an accordion that does not respond to synthetic
clicks, so no amount of selector fixing could bring it back. The data layer has
no markup to rot, needs no browser, and answers in ten requests what used to
take a page load per card.

Per-card detail (English event names, and the events' choices) is not in those
files - it comes from the page's own Next.js payload:

    https://gametora.com/_next/data/<buildId>/umamusume/supports/<url_name>.json

which is one request per card. Everything is cached under devtools/.gtcache/
so re-runs and offline work cost nothing.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".gtcache")
CARD_CACHE = os.path.join(CACHE_DIR, "cards")

MANIFEST_URL = "https://gametora.com/data/manifests/umamusume.json"
DATA_URL = "https://gametora.com/data/umamusume/{path}.{digest}.json"
PAGE_URL = "https://gametora.com/umamusume/supports"
CARD_URL = "https://gametora.com/_next/data/{build}/umamusume/supports/{url_name}.json"

# Plain browser UA. The data layer is public and unauthenticated, but a bare
# urllib UA gets refused.
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36"}

# Pause between per-card requests. This is someone else's server and a full run
# is a couple of hundred fetches.
CARD_DELAY = 0.35


def _get(url: str) -> str:
  request = urllib.request.Request(url, headers=UA)
  with urllib.request.urlopen(request, timeout=60) as response:
    return response.read().decode("utf-8", "replace")


def _cached(path: str, produce):
  """Read `path` if it exists, otherwise write what `produce()` returns."""
  if os.path.exists(path):
    with open(path, "r", encoding="utf-8") as f:
      return json.load(f)
  value = produce()
  os.makedirs(os.path.dirname(path), exist_ok=True)
  with open(path, "w", encoding="utf-8") as f:
    json.dump(value, f, ensure_ascii=False)
  return value


def manifest(refresh: bool = False) -> dict:
  """The path -> content-hash map. Refreshing it invalidates nothing else.

  Data files are cached under their logical name rather than the hashed one,
  so a refreshed manifest alone will not re-download them - clear the cache
  directory for that.
  """
  path = os.path.join(CACHE_DIR, "_manifest.json")
  if refresh and os.path.exists(path):
    os.remove(path)
  return _cached(path, lambda: json.loads(_get(MANIFEST_URL)))


def data_file(logical_path: str, mf: dict | None = None):
  """One data-layer file, e.g. "support-cards", "skills", "status-effects"."""
  mf = mf if mf is not None else manifest()
  if logical_path not in mf:
    raise KeyError(f"{logical_path!r} is not in the manifest "
                   f"({len(mf)} entries; has the site reorganised?)")
  local = os.path.join(CACHE_DIR, logical_path.replace("/", "__") + ".json")
  return _cached(
    local,
    lambda: json.loads(_get(DATA_URL.format(path=logical_path, digest=mf[logical_path]))))


def build_id() -> str:
  """The Next.js build id, which the per-card payload URLs are keyed by.

  Not cached: it changes on every site deploy, and a stale one 404s every card.
  """
  found = re.search(r'"buildId"\s*:\s*"([^"]+)"', _get(PAGE_URL))
  if not found:
    raise RuntimeError("No buildId on the supports page; the site layout changed.")
  return found.group(1)


def card_payload(url_name: str, build: str) -> dict:
  """One card's pageProps: itemData plus eventData with English event names."""
  local = os.path.join(CARD_CACHE, url_name + ".json")
  if os.path.exists(local):
    with open(local, "r", encoding="utf-8") as f:
      return json.load(f)

  value = json.loads(_get(CARD_URL.format(build=build, url_name=url_name)))["pageProps"]
  os.makedirs(CARD_CACHE, exist_ok=True)
  with open(local, "w", encoding="utf-8") as f:
    json.dump(value, f, ensure_ascii=False)
  time.sleep(CARD_DELAY)
  return value


def event_data(payload: dict, language: str = "en") -> dict:
  """The events off a card payload, as {"random": [...], "arrows": [...], ...}.

  The field is sometimes a JSON string and sometimes already parsed, depending
  on how the page serialised it.
  """
  raw = (payload.get("eventData") or {}).get(language)
  if not raw:
    return {}
  return json.loads(raw) if isinstance(raw, str) else raw
