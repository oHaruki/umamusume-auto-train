"""Detect-only probe: what does the bot think it is looking at?

Reads the screen over ADB and reports which entry of the screen table matches
and what the loop would do there. It never clicks, taps or swipes, so it is
safe to leave running while you play through a cycle by hand.

Run from the repo root:

  py -3.12 autopilot/tools/whereami.py

Every rule that matches is listed, not just the winner, so ambiguity between
screens is visible rather than hidden.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from adbutils import adb

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from autopilot.screens import (  # noqa: E402
  FRIENDS_SLOT_EMPTY, HOME_TP_TEXT_LTRB, MATCH_THRESHOLD, SCREENS, SKILLS_BUTTON,
  TP_AMOUNT_PLUS, TP_CARATS_ITEM, TP_USE_BUTTON,
)
from utils import display  # noqa: E402


def default_device() -> str:
  """The first client from config.json, so these tools point where the bot does.

  Prefers the Device ID on the Set-Up tab, which is the one client a single-
  client setup uses and the one most people mean by "the emulator"; the
  Autopilot tab's Clients list is only a fallback, and picking its first entry
  meant these tools pointed at whichever client happened to be listed first.

  A hardcoded default is worse than either: 127.0.0.1:5555 is BlueStacks' port,
  and on a MuMu setup it names an emulator that does not exist, which fails as
  "device not found" rather than as "you forgot --device".
  """
  try:
    import json
    with open(REPO_ROOT / "config.json", "r", encoding="utf-8") as f:
      single = str(json.load(f).get("device_id", "")).strip()
    if single:
      return single
  except Exception:
    pass
  try:
    from autopilot import config as auto_config
    devices = auto_config.resolve_devices(str(REPO_ROOT / "config.json"))
    if devices:
      return devices[0]
  except Exception:
    pass
  return "127.0.0.1:7555"


OK_BUTTON = "assets/buttons/ok_btn.png"

# Set by --tp. Off by default because reading the counter means loading
# easyocr and torch, which turns a two-second probe into a twenty-second one.
read_tp_enabled = False

# A missing template is cached as None, so its warning is printed once rather
# than once per poll - this runs in a loop, and templates the bot ships
# without (the Recover TP set) are legitimately absent until they are cut.
_cache: dict[str, np.ndarray | None] = {}


def template(rel_path: str) -> np.ndarray | None:
  if rel_path not in _cache:
    img = cv2.imread(str(REPO_ROOT / rel_path), cv2.IMREAD_COLOR)
    if img is None:
      print(f"[WARN] missing template: {rel_path}")
    _cache[rel_path] = img
  return _cache[rel_path]


def best_score(frame: np.ndarray, rel_path: str,
               region: tuple[int, int, int, int] | None = None) -> tuple[float, tuple[int, int]]:
  tpl = template(rel_path)
  if tpl is None:
    return 0.0, (0, 0)

  offset_x, offset_y = 0, 0
  if region:
    left, top, right, bottom = region
    frame = frame[top:bottom, left:right]
    offset_x, offset_y = left, top

  if tpl.shape[0] > frame.shape[0] or tpl.shape[1] > frame.shape[1]:
    return 0.0, (0, 0)
  res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
  _, score, _, loc = cv2.minMaxLoc(res)
  return float(score), (loc[0] + offset_x, loc[1] + offset_y)


def grab(device) -> np.ndarray:
  img = display.screenshot(device)
  # screencap gives RGB; templates are read as BGR, so match in BGR throughout.
  return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def read_tp(frame: np.ndarray) -> str:
  """Home's TP counter as the bot reads it, for checking the crop lines up."""
  import re

  from PIL import Image

  from core.ocr import extract_allowed_text

  left, top, right, bottom = HOME_TP_TEXT_LTRB
  # The frame is BGR here; the bot reads RGB, and the recogniser wants RGB.
  crop = cv2.cvtColor(frame[top:bottom, left:right], cv2.COLOR_BGR2RGB)
  pil = Image.fromarray(crop)
  pil = pil.resize((pil.width * 3, pil.height * 3), Image.BICUBIC)
  text = extract_allowed_text(pil, allowlist="0123456789/")
  found = re.search(r"(\d+)/(\d+)", text) or re.search(r"(\d+)/(\d+)", text.replace(" ", ""))
  if not found:
    return f"TP unreadable, the crop said {text!r} - check HOME_TP_TEXT_LTRB"
  return f"TP {found.group(1)}/{found.group(2)}"


def describe(frame: np.ndarray) -> tuple[str, str, list[str]]:
  """Return (dedup key, display line, every matching rule).

  The key deliberately excludes match scores, which drift by a few thousandths
  as screens animate; keying on them would reprint the same screen endlessly.
  """
  matches = []
  winner = None
  for screen in SCREENS:
    score, loc = best_score(frame, screen.identify, screen.identify_region)
    if score >= MATCH_THRESHOLD:
      matches.append(f"{score:.3f}  {screen.name:20s} at {loc}")
      if winner is None:
        winner = (screen, score, loc)

  if winner is None:
    return "none", "unrecognised screen - nothing would be clicked", matches

  screen, score, loc = winner
  detail, state = "", ""
  if screen.handler == "formation":
    empty, _ = best_score(frame, FRIENDS_SLOT_EMPTY)
    state = "empty" if empty >= MATCH_THRESHOLD else "filled"
    detail = (f" -> borrow slot EMPTY ({empty:.3f}), would open the borrow list"
              if state == "empty"
              else f" -> borrow slot filled ({empty:.3f}), would press Start Career!")
  elif screen.handler == "complete_career":
    skills, sloc = best_score(frame, SKILLS_BUTTON)
    state = "skills" if skills >= MATCH_THRESHOLD else "no_skills"
    detail = (f" -> Skills button found ({skills:.3f}) at {sloc}"
              if state == "skills" else " -> no Skills button visible")
  elif screen.handler == "home" and read_tp_enabled:
    state = "tp"
    detail = f" -> {read_tp(frame)}"
  elif screen.handler == "recover_tp":
    # Which of the three stacked dialogs is up, decided the same way the
    # handler decides it, so a wrong reading shows up here rather than in a
    # tap that spends the wrong thing.
    ok, _ = best_score(frame, OK_BUTTON)
    use, _ = best_score(frame, TP_USE_BUTTON)
    carats, _ = best_score(frame, TP_CARATS_ITEM)
    plus, _ = best_score(frame, TP_AMOUNT_PLUS)
    if ok >= MATCH_THRESHOLD:
      state = "amount"
      detail = (f" -> amount dialog (OK {ok:.3f}), would press + {plus:.3f} then OK"
                if plus >= MATCH_THRESHOLD
                else f" -> amount dialog (OK {ok:.3f}) but no + ({plus:.3f}), would Cancel")
    elif use >= MATCH_THRESHOLD and carats >= MATCH_THRESHOLD:
      state = "list"
      detail = f" -> item list, would press Use ({use:.3f}) on the Carats row ({carats:.3f})"
    else:
      state = "close"
      detail = (f" -> nothing to press (Use {use:.3f}, Carats {carats:.3f}), would Close")

  return (f"{screen.name}|{state}",
          f"{screen.name}  ({score:.3f})  would: {screen.action}{detail}",
          matches)


def main() -> int:
  p = argparse.ArgumentParser(description="Report the detected screen. Never clicks.")
  p.add_argument("--device", default=default_device(),
                 help="ADB device id (default: the Device ID from Set-Up, "
                      "currently %(default)s)")
  p.add_argument("--poll", type=float, default=1.0)
  p.add_argument("--all", action="store_true", help="List every matching rule, not just the winner")
  p.add_argument("--tp", action="store_true",
                 help="Also read Home's TP counter. Slow to start: loads easyocr.")
  args = p.parse_args()

  global read_tp_enabled
  read_tp_enabled = args.tp

  try:
    adb.connect(args.device)
    device = adb.device(args.device)
    probe = grab(device)
  except Exception as e:
    print(f"[ERROR] could not connect to {args.device}: {e}")
    return 1

  print(f"[OK] {args.device}, frame {probe.shape[1]}x{probe.shape[0]}")
  print("[OK] Detect only - this never clicks. Ctrl+C to stop.\n")

  last = None
  try:
    while True:
      key, line, matches = describe(grab(device))
      if key != last:
        print(f"{time.strftime('%H:%M:%S')}  {line}")
        if args.all and len(matches) > 1:
          for m in matches[1:]:
            print(f"          also matched: {m}")
        last = key
      time.sleep(args.poll)
  except KeyboardInterrupt:
    print("\n[OK] stopped")
  return 0


if __name__ == "__main__":
  sys.exit(main())
