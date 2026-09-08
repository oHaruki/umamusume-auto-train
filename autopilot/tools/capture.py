"""
Frame capture tool for the autopilot fork.

Grabs frames through the SAME ADB path the bot uses (adbutils
`device.screenshot()`, see utils/adb_actions.py), and writes them so that
`cv2.imread()` + the channel swap in device_action_wrapper.match_template()
reproduces the exact pixels the bot compares against at runtime. Frames saved
here can be cropped straight into assets/ and used as templates.

Run from the repo root:

  py -3.12 autopilot/tools/capture.py --session start_of_run
  py -3.12 autopilot/tools/capture.py --session end_of_run

Default mode watches the screen and saves a frame whenever it changes AND
settles. Animated screens (the auto-training loop) never settle, so they don't
spam the output; a real screen transition does settle and gets captured.

Keys while running:
  F9   force-save the current frame
  F10  quit
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
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


DEFAULT_OUT = REPO_ROOT / "autopilot" / "captures"


def connect(device_id: str):
  adb.connect(device_id)
  device = adb.device(device_id)
  screen, complaint = display.pin(device)
  if complaint:
    print(f"[WARN] {complaint}")
  print(f"[OK] Capturing {screen}")
  return device


def grab(device) -> np.ndarray:
  """Identical to utils/adb_actions.screenshot() minus the region crop: RGB uint8."""
  return np.array(display.screenshot(device))


def fingerprint(frame: np.ndarray, scale: int = 4) -> np.ndarray:
  """Downscaled grayscale view used for change detection.

  Downscaling is what keeps small in-game animations (sparkles, ticking
  numbers) from reading as a screen change.
  """
  h, w = frame.shape[:2]
  small = cv2.resize(frame, (max(1, w // scale), max(1, h // scale)), interpolation=cv2.INTER_AREA)
  if small.ndim == 3:
    small = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
  return small


def mean_diff(a: np.ndarray | None, b: np.ndarray | None) -> float:
  """Mean absolute difference, same metric as utils/screenshot.are_screenshots_same()."""
  if a is None or b is None or a.shape != b.shape:
    return 255.0
  return float(np.mean(cv2.absdiff(a, b)))


def save_frame(frame: np.ndarray, path: Path) -> None:
  # adbutils hands us RGB. cv2.imwrite expects BGR, and the bot's
  # match_template() does imread(BGR) -> RGB2BGR swap -> RGB. Writing BGR here
  # is what makes the round trip land back on the original RGB pixels.
  cv2.imwrite(str(path), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))


def main() -> int:
  p = argparse.ArgumentParser(description="Capture Umamusume frames over ADB for template building.")
  p.add_argument("--device", default=default_device(),
                 help="ADB device id (default: the Device ID from Set-Up, "
                      "currently %(default)s)")
  p.add_argument("--session", default=None, help="Session name; defaults to a timestamp")
  p.add_argument("--out", default=str(DEFAULT_OUT), help="Output root (default: autopilot/captures)")
  p.add_argument("--poll", type=float, default=1.0, help="Seconds between polls (default: %(default)s)")
  p.add_argument("--change-threshold", type=float, default=8.0,
                 help="Mean-abs-diff vs last saved frame that counts as a new screen (default: %(default)s)")
  p.add_argument("--stable-threshold", type=float, default=1.5,
                 help="Mean-abs-diff between consecutive polls below which the screen counts as settled (default: %(default)s)")
  p.add_argument("--stable-frames", type=int, default=2,
                 help="Consecutive settled polls required before saving (default: %(default)s)")
  p.add_argument("--min-interval", type=float, default=3.0,
                 help="Minimum seconds between auto saves (default: %(default)s)")
  p.add_argument("--heartbeat", type=float, default=300.0,
                 help="Save a frame regardless every N seconds, 0 to disable (default: %(default)s)")
  p.add_argument("--max-frames", type=int, default=400, help="Stop after N frames (default: %(default)s)")
  p.add_argument("--manual", action="store_true", help="Disable auto-capture; only F9 saves")
  args = p.parse_args()

  session = args.session or datetime.now().strftime("%Y%m%d_%H%M%S")
  out_dir = Path(args.out) / session
  out_dir.mkdir(parents=True, exist_ok=True)

  try:
    device = connect(args.device)
    probe = grab(device)
  except Exception as e:
    print(f"[ERROR] Could not connect to {args.device}: {e}")
    print("        Is the emulator running? Check the device id matches config.json.")
    return 1

  h, w = probe.shape[:2]
  orientation = "portrait" if h > w else "landscape"
  print(f"[OK] Connected to {args.device} - frame is {w}x{h} ({orientation})")
  # The framebuffer follows the game's own orientation, so this flips between
  # screens (lobby is portrait, races are landscape). Frames of both are wanted;
  # just note which orientation a template came from when cropping it.
  print(f"[OK] Saving to {out_dir}")
  print(f"[OK] Mode: {'manual only' if args.manual else 'auto on change + settle'}   F9 = force save, F10 = quit")

  manual_request = {"save": False, "quit": False}
  try:
    import keyboard

    keyboard.on_press_key("f9", lambda _: manual_request.__setitem__("save", True))
    keyboard.on_press_key("f10", lambda _: manual_request.__setitem__("quit", True))
  except Exception as e:
    print(f"[WARN] Hotkeys unavailable ({e}); running without F9/F10. Ctrl+C to stop.")

  manifest_path = out_dir / "manifest.csv"
  manifest = manifest_path.open("w", newline="", encoding="utf-8")
  writer = csv.writer(manifest)
  writer.writerow(["index", "filename", "timestamp", "elapsed_s", "trigger", "diff_vs_last_saved", "label"])

  start = time.time()
  last_saved_fp: np.ndarray | None = None
  prev_fp: np.ndarray | None = None
  stable_count = 0
  last_save_t = 0.0
  index = 0

  try:
    while index < args.max_frames:
      loop_t = time.time()
      frame = grab(device)
      fp = fingerprint(frame)

      d_prev = mean_diff(fp, prev_fp)
      d_saved = mean_diff(fp, last_saved_fp)
      prev_fp = fp

      stable_count = stable_count + 1 if d_prev <= args.stable_threshold else 0

      if manual_request["quit"]:
        print("\n[OK] F10 pressed, stopping.")
        break

      trigger = None
      if manual_request["save"]:
        manual_request["save"] = False
        trigger = "manual"
      elif not args.manual and (
        d_saved > args.change_threshold
        and stable_count >= args.stable_frames
        and (loop_t - last_save_t) >= args.min_interval
      ):
        trigger = "change"
      elif args.heartbeat and (loop_t - last_save_t) >= args.heartbeat:
        trigger = "heartbeat"

      if trigger:
        index += 1
        elapsed = loop_t - start
        name = f"{index:04d}_{trigger}_{int(elapsed):05d}s.png"
        save_frame(frame, out_dir / name)
        writer.writerow([index, name, datetime.now().isoformat(timespec="seconds"),
                         f"{elapsed:.1f}", trigger, f"{d_saved:.2f}", ""])
        manifest.flush()
        last_saved_fp = fp
        last_save_t = loop_t
        print(f"[{index:04d}] {trigger:9s} t+{elapsed:7.1f}s  diff={d_saved:6.2f}  -> {name}")
      else:
        print(f"\r      watching  t+{loop_t - start:7.1f}s  diff={d_saved:6.2f}  stable={stable_count}   ",
              end="", flush=True)

      time.sleep(max(0.0, args.poll - (time.time() - loop_t)))
  except KeyboardInterrupt:
    print("\n[OK] Interrupted, stopping.")
  finally:
    manifest.close()

  print(f"\n[DONE] {index} frames in {out_dir}")
  print(f"       Fill in the 'label' column of {manifest_path.name} to say what each screen is.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
