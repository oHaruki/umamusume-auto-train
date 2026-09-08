"""List the emulators the autopilot could drive, and say which are usable.

Every emulator instance listens on its own ADB port, and the defaults differ by
product - MuMu's first two instances are usually 127.0.0.1:7555 and
127.0.0.1:16416, BlueStacks uses 5555 and 5565 - so guessing a port is the
usual reason a second client starts and immediately dies with "device not
found".

Two things this catches that a plain `adb devices` does not:

  * The same emulator often answers to more than one serial (a 127.0.0.1:PORT
    form and an emulator-NNNN form). Listing both as separate clients would
    start two bots fighting over one screen, so instances are identified by
    their Android id and duplicates are grouped.
  * A reachable device is not necessarily usable. The frame has to be 800x1080
    or no template matches, and the game has to actually be running.

Read-only: it screenshots and reads properties, and never taps.

    py -3.12 autopilot/tools/devices.py
    py -3.12 autopilot/tools/devices.py --scan     # also probe common ports
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

import numpy as np
from adbutils import adb

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from utils import display  # noqa: E402

# Ports worth trying when nothing is connected yet. MuMu Player spaces its
# instances 32 apart from 16384; the 75x5 pair and the BlueStacks 55x5 pair are
# the other common defaults.
COMMON_PORTS = [7555, 7565, 7575, 5555, 5565, 5575,
                16384, 16416, 16448, 16480, 21503, 62001]

GAME_HINT = "umamusume"
WANTED_SIZE = (800, 1080)


def configured_serials() -> set:
  """Serials already named in config.json.

  Preferred when one machine answers to several, so this never tells you to
  rename a client that is already working - and `adb connect` is sticky, so
  once --scan has touched an alias, "which one did ADB already know" stops
  telling the two apart.
  """
  try:
    from autopilot import config as auto_config
    cfg = auto_config.load(str(REPO_ROOT / "config.json"))
    found = set(cfg.devices)
  except Exception:
    return set()
  try:
    import json
    with open(REPO_ROOT / "config.json", "r", encoding="utf-8") as f:
      single = str(json.load(f).get("device_id", "")).strip()
    if single:
      found.add(single)
  except Exception:
    pass
  return found


def port_of(serial: str) -> int:
  """The port in host:port, or a large number so unported serials sort last."""
  try:
    return int(serial.rsplit(":", 1)[1])
  except (IndexError, ValueError):
    return 1 << 30


def save_shot(serial: str, out_dir: Path):
  """Save this device's current screen as <port>.png. Read-only.

  Which ADB port is which emulator window is not something either end tells
  you, so the reliable answer is to look: the account on screen identifies the
  instance at a glance.
  """
  try:
    import cv2
    adb.connect(serial)
    device = adb.device(serial)
    screen, _ = display.resolve(device)
    frame = np.array(screen.screenshot(device))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (serial.replace(":", "_").replace("/", "_") + ".png")
    # screencap gives RGB; cv2 writes BGR.
    cv2.imwrite(str(path), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    return path
  except Exception:
    return None


def port_open(port: int, timeout: float = 0.3) -> bool:
  with socket.socket() as s:
    s.settimeout(timeout)
    return s.connect_ex(("127.0.0.1", port)) == 0


def inspect(serial: str, known: bool = True) -> dict:
  """Everything worth knowing about one serial. Never raises.

  `known` records whether ADB already had this serial, as opposed to it being
  turned up by --scan; the two are equally valid addresses for the same
  machine, and preferring the known one keeps an existing config working.
  """
  info = {"serial": serial, "ok": False, "error": None, "identity": None,
          "size": None, "screen": None, "game": False, "known": known}
  try:
    adb.connect(serial)
    device = adb.device(serial)
    # Identity, so two serials for one emulator can be collapsed.
    info["identity"] = (device.shell("settings get secure android_id").strip()
                        or device.shell("cat /proc/sys/kernel/random/boot_id").strip())
    screen, _complaint = display.resolve(device)
    info["screen"] = str(screen)
    frame = np.array(screen.screenshot(device))
    info["size"] = (frame.shape[1], frame.shape[0])
    info["game"] = any(GAME_HINT in window.lower()
                       for _logical, window in display.focus_by_display(device))
    info["ok"] = True
  except Exception as e:
    info["error"] = f"{type(e).__name__}: {e}"
  return info


def main() -> int:
  parser = argparse.ArgumentParser(description="List usable emulators. Never taps.")
  parser.add_argument("--scan", action="store_true",
                      help="Also try the common emulator ports, not just what ADB knows")
  parser.add_argument("--shot", nargs="?", const="device_shots", metavar="DIR",
                      help="Save a screenshot per instance, named by port, so you can "
                           "see which emulator window each one is (default: device_shots/)")
  args = parser.parse_args()

  serials = []
  try:
    serials = [d.serial for d in adb.device_list()]
  except Exception as e:
    print(f"[WARN] could not list ADB devices: {e}")

  known = set(serials)
  if args.scan:
    for port in COMMON_PORTS:
      candidate = f"127.0.0.1:{port}"
      if candidate not in serials and port_open(port):
        serials.append(candidate)

  if not serials:
    print("No emulators found. Start one, turn on ADB debugging, and try --scan.")
    return 1

  results = [inspect(s, known=s in known) for s in serials]
  configured = configured_serials()

  # One entry per real machine; extra serials for it are listed as aliases.
  instances: dict = {}
  for info in results:
    if not info["ok"]:
      continue
    key = info["identity"] or info["serial"]
    instances.setdefault(key, []).append(info)

  print(f"\n{len(instances)} emulator instance(s):\n")
  usable = []
  for number, (_identity, group) in enumerate(instances.items(), 1):
    # What config.json already uses wins, so a working setup is never renamed
    # out from under you. Then the host:port form, which survives a restart
    # where an emulator-NNNN serial may not, and finally the lowest port.
    group.sort(key=lambda i: (i["serial"] not in configured,
                              not i["known"],
                              ":" not in i["serial"],
                              port_of(i["serial"])))
    best = group[0]
    width, height = best["size"]
    size_ok = (width, height) == WANTED_SIZE

    in_config = "  (in your config)" if best["serial"] in configured else ""
    print(f"  {number}. {best['serial']}{in_config}")
    for alias in group[1:]:
      print(f"       also answers to {alias['serial']} (same machine - list only one)")
    print(f"       {width}x{height}"
          + ("" if size_ok else "  ** must be 800x1080, or nothing will match **"))
    print(f"       {best['screen']}, game {'running' if best['game'] else 'NOT running'}")
    if args.shot:
      saved = save_shot(best["serial"], Path(args.shot))
      print(f"       screenshot: {saved}" if saved else "       screenshot failed")
    if size_ok and best["game"]:
      usable.append(best["serial"])
    print()

  for info in results:
    if not info["ok"]:
      print(f"  unreachable: {info['serial']} - {info['error']}")

  if usable:
    print("Ready to drive. Put these in Clients on the Autopilot tab, or pass them "
          "on the command line:\n")
    print("  " + ", ".join(usable))
    print("\n  py -3.12 autopilot_run.py " + " ".join(f"--device {s}" for s in usable))
  else:
    print("Nothing usable yet: check the resolution is 800x1080 and the game is open.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
