"""Cut a template straight out of a live frame, by dragging a box over it.

The bot matches templates pixel for pixel against frames captured over ADB, so
a template has to come from that exact path: a crop off a phone screenshot, a
resized image or anything a screen recorder touched will score far below the
0.85 threshold and simply never match.

Run from the repo root, with the game showing the screen you want:

  py -3.12 autopilot/tools/cut_template.py assets/autopilot/recover_tp_header.png

A window opens on the current frame. Drag a box, press ENTER to save, or C to
grab a fresh frame and try again. ESC quits without saving.

The whole Recover TP flow can be walked through in one go, which prompts for
each template in turn and tells you which screen to be on:

  py -3.12 autopilot/tools/cut_template.py --recover-tp

What to aim for: something that only appears on the screen you are identifying,
that does not light up, grey out or animate, and that has an edge or two in it.
Flat colour matches everywhere; a button whose label changes with state matches
nothing once it changes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
from adbutils import adb

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
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


WINDOW = "Drag a box - ENTER save, C new frame, ESC quit"

# The Recover TP handler needs all four. Order follows the flow, so walking
# through the dialogs by hand lines up with the prompts.
RECOVER_TP_STEPS = (
  ("assets/autopilot/recover_tp_header.png",
   "the Recover TP dialog (tap + beside the TP bar on Home)",
   'the words "Recover TP" in the green header bar, text only, clear of the '
   "rounded corners"),
  ("assets/autopilot/tp_carats_item.png",
   "the same Recover TP item list",
   "the Carats thumbnail - the rainbow carrot picture on the left of the first "
   "row, not its name"),
  ("assets/autopilot/tp_use_btn.png",
   "the same Recover TP item list",
   'one "Use" button, tight to the button\'s own border'),
  ("assets/autopilot/tp_amount_plus_btn.png",
   'the amount dialog (press Use on the Carats row) while it still reads 0 added',
   "the round + button on the right of the amount row, while it is bright - not "
   "the greyed-out one"),
)


def connect(device_id: str):
  adb.connect(device_id)
  device = adb.device(device_id)
  screen, complaint = display.pin(device)
  if complaint:
    print(f"[WARN] {complaint}")
  return device


def grab(device) -> np.ndarray:
  """RGB frame, exactly what utils/adb_actions.screenshot() returns."""
  return np.array(display.screenshot(device))


def save(frame_rgb: np.ndarray, box, path: Path) -> None:
  x, y, w, h = (int(v) for v in box)
  crop = frame_rgb[y:y + h, x:x + w]
  path.parent.mkdir(parents=True, exist_ok=True)
  # adbutils hands over RGB; cv2.imwrite wants BGR, and the bot's
  # match_template() does imread(BGR) -> RGB2BGR -> RGB. Writing BGR here is
  # what makes that round trip land on the pixels we just cut.
  cv2.imwrite(str(path), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
  print(f"[OK] {path.relative_to(REPO_ROOT)}  {w}x{h} at ({x}, {y})")


def cut_one(device, out_path: Path, hint: str | None = None) -> bool:
  """Show a frame, let the user drag a box, save it. False if they quit."""
  while True:
    frame = grab(device)
    if hint:
      print(f"      cut: {hint}")
    # BGR for display, because that is what imshow expects.
    box = cv2.selectROI(WINDOW, cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
                        showCrosshair=False, fromCenter=False)
    key = cv2.waitKey(1) & 0xFF
    cv2.destroyAllWindows()

    if box[2] > 0 and box[3] > 0:
      save(frame, box, out_path)
      return True
    if key == ord("c"):
      continue
    print("[SKIP] nothing selected.")
    return False


def main() -> int:
  p = argparse.ArgumentParser(description="Cut bot templates out of a live ADB frame.")
  p.add_argument("output", nargs="?", help="Where to write the template, "
                                           "e.g. assets/autopilot/foo.png")
  p.add_argument("--device", default=default_device(),
                 help="ADB device id (default: the Device ID from Set-Up, "
                      "currently %(default)s)")
  p.add_argument("--recover-tp", action="store_true",
                 help="Walk through every template the TP recovery needs")
  args = p.parse_args()

  if not args.output and not args.recover_tp:
    p.error("give an output path, or --recover-tp")

  try:
    device = connect(args.device)
    probe = grab(device)
  except Exception as e:
    print(f"[ERROR] could not connect to {args.device}: {e}")
    return 1

  h, w = probe.shape[:2]
  print(f"[OK] {args.device}, frame {w}x{h}")
  if (w, h) != (800, 1080):
    print("[WARN] The bot's templates were all cut at 800x1080. Anything cut at "
          "another size will not match at runtime.")

  if not args.recover_tp:
    cut_one(device, REPO_ROOT / args.output)
    return 0

  print("\nWalking through the Recover TP templates. Put the game on the screen "
        "each step names, then drag a box round what it describes.\n")
  for rel_path, screen, what in RECOVER_TP_STEPS:
    target = REPO_ROOT / rel_path
    state = "overwrite" if target.exists() else "new"
    print(f"--- {rel_path}  ({state})")
    print(f"    be on: {screen}")
    try:
      input("    press ENTER when the screen is showing (Ctrl+C to stop) ")
    except (KeyboardInterrupt, EOFError):
      print("\n[OK] stopped.")
      return 0
    if not cut_one(device, target, hint=what):
      print("[WARN] skipped; TP recovery stays off until every template exists.")

  missing = [rel for rel, _s, _w in RECOVER_TP_STEPS if not (REPO_ROOT / rel).exists()]
  if missing:
    print(f"\n[WARN] still missing: {', '.join(missing)}")
  else:
    print("\n[DONE] All four cut. Turn on Auto Recover TP in the Autopilot tab, then "
          "check it with autopilot/tools/whereami.py on each of the dialogs.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
