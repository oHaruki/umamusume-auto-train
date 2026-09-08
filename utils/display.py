"""Pick the Android display the game runs on, for both capture and input.

MuMu's Android 15 (Nx) image gives every app its own virtual screen, so one
device exposes several displays and the game is usually not on display 0. Two
things break if that goes unnoticed:

  screencap -p  without -d prints a "Multiple displays were found" warning onto
                stdout ahead of the PNG bytes, so PIL rejects the frame.
  input tap     defaults to display 0, the launcher, so every tap misses the
                game and the bot looks alive while doing nothing at all.

The two commands want different ids for the same screen: screencap takes the
SurfaceFlinger physical id, input takes the logical display id. Both come out of
the same lookup, which costs two dumpsys calls and so runs once per device
rather than once per frame.
"""

import io
import re

from PIL import Image
from adbutils import adb
from adbutils._utils import escape_special_characters

# Matched as a substring against the focused window, so regional builds resolve
# without their exact package name having to be listed here.
GAME_HINT = "umamusume"

# How long to wait on the ADB server re-establishing a dropped connection.
RECONNECT_TIMEOUT = 5.0

class Display:
  """The ids one Android screen answers to, and the commands aimed at it."""

  def __init__(self, logical=None, physical=None):
    self.logical = logical    # what `input -d` wants
    self.physical = physical  # what `screencap -d` wants

  def screenshot(self, device):
    cmd = ["screencap", "-p"]
    if self.physical:
      cmd += ["-d", self.physical]
    img = Image.open(io.BytesIO(device.shell(cmd, encoding=None)))
    return img.convert("RGB") if img.mode == "RGBA" else img

  def input(self, device, *params):
    cmd = ["input"]
    if self.logical is not None:
      cmd += ["-d", str(self.logical)]
    return device.shell(cmd + [str(p) for p in params])

  def __str__(self):
    return "the default display" if self.logical is None else f"display {self.logical}"

pinned = {}

def focus_by_display(device):
  """[(logical id, focused window)] for every display, focused or not."""
  found = []
  for block in re.split(r"(?=Display: mDisplayId=)", device.shell("dumpsys window displays")):
    header = re.match(r"Display: mDisplayId=(\d+)", block)
    if not header:
      continue
    focus = re.search(r"mCurrentFocus=(.+)", block)
    found.append((int(header.group(1)), focus.group(1).strip() if focus else "null"))
  return found

def physical_by_display(device):
  """logical id -> SurfaceFlinger physical id, the form `screencap -d` takes."""
  mapping = {}
  for logical, physical in re.findall(
      r"mDisplayId=(\d+)\s*\n\s*mPrimaryDisplayDevice=[^(\n]*\(local:(\d+)\)",
      device.shell("dumpsys display")):
    mapping.setdefault(int(logical), physical)
  return mapping

def resolve(device, hint=GAME_HINT):
  """Locate the game's screen. Returns (Display, complaint or None)."""
  focus = focus_by_display(device)
  physical = physical_by_display(device)

  complaint = None
  candidates = [logical for logical, window in focus if hint.lower() in window.lower()]
  if not candidates:
    # Fall back to whatever currently holds window focus: a build whose package
    # does not contain the hint still lands on the right screen this way.
    candidates = [logical for logical, window in focus if window != "null"]
    complaint = f"No focused window mentioning '{hint}' - is the game open?"

  # Last resort ordering: the plain command first, since a single-display device
  # answers it happily, then every display the system admits to having.
  fallbacks = [(None, None)] + sorted(physical.items())

  for logical in candidates:
    screen = Display(logical, physical.get(logical))
    try:
      screen.screenshot(device)
      return screen, complaint
    except Exception:
      continue

  for logical, phys in fallbacks:
    screen = Display(logical, phys)
    try:
      screen.screenshot(device)
      return screen, complaint or "Could not find the game on any display."
    except Exception:
      continue

  return Display(), complaint or "No display returned a readable screencap."

def pin(device, hint=GAME_HINT):
  """Resolve this device's display and remember it."""
  screen, complaint = resolve(device, hint)
  pinned[device.serial] = screen
  return screen, complaint

def get(device):
  screen = pinned.get(device.serial)
  return screen if screen is not None else pin(device)[0]

def reconnect(device):
  """Re-establish a dropped connection to a networked device. True if it took.

  MuMu retires its 127.0.0.1:<port> connection every so often, and from then on
  every command raises "device offline" until somebody reconnects - the
  emulator itself is usually still running. Only network serials can be
  reconnected by address; a usb or emulator-NNNN serial is left alone.

  The answer comes from the device list rather than from connect(), which is
  not the test it looks like: an emulator whose VM is up but whose Android has
  stopped serving adb still accepts the socket, and connect() reports "already
  connected" for a transport that is sitting there offline. Only the state the
  server reports says whether commands will actually run.
  """
  serial = getattr(device, "serial", "") or ""
  if ":" not in serial:
    return False
  try:
    adb.disconnect(serial)
  except Exception:
    pass                     # already gone is the state we want it in
  try:
    adb.connect(serial, timeout=RECONNECT_TIMEOUT)
    return any(d.serial == serial and d.state == "device" for d in adb.list())
  except Exception:
    return False

def screenshot(device):
  """Full-screen RGB frame of the game's display, as a PIL image."""
  try:
    return get(device).screenshot(device)
  except Exception:
    pass

  # A restarted or rotated emulator can retire the display we pinned, so take
  # one shot at finding where the game went before giving up.
  try:
    return pin(device)[0].screenshot(device)
  except Exception:
    # Re-pinning is itself a handful of shell commands, so a connection that
    # has dropped fails it exactly as it failed the capture above, and the
    # second failure escapes uncaught. Reconnect and re-pin once more - the
    # display ids are worth looking up again, since a device that went away
    # and came back may not lay them out the way it did before.
    if not reconnect(device):
      raise
  return pin(device)[0].screenshot(device)

def tap(device, x, y):
  return get(device).input(device, "tap", int(x), int(y))

def swipe(device, x1, y1, x2, y2, duration=0.3):
  return get(device).input(device, "swipe", int(x1), int(y1), int(x2), int(y2), int(duration * 1000))

def text(device, content):
  return get(device).input(device, "text", escape_special_characters(content))
