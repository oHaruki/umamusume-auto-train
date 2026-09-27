"""What the emulator itself says about its instances, for catching bad serials.

Only MuMu is covered, because MuMu is where it bites. Each MuMu instance has
its own ADB port (16384 + 32 x index), and 127.0.0.1:7555 is a legacy alias on
top of those that MuMu forwards to whichever instance it chooses. With one
instance that is harmless. With two it is a coin toss per connection: a client
set up for one account on 7555 has been seen driving the other account's
emulator, which a second client was already driving, and the two undid each
other's taps for hours.

Everything here is best effort. No MuMu, or a MuMuManager that does not answer,
means no opinion rather than an error.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess

# MuMu's forwarded alias. Not an instance of its own.
MUMU_ALIAS_PORT = 7555


def _mumu_managers() -> list[str]:
  """Paths to MuMuManager.exe, from the uninstall registry and the usual folders."""
  found = []
  try:
    import winreg
    for hive, key in ((winreg.HKEY_LOCAL_MACHINE,
                       r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                      (winreg.HKEY_LOCAL_MACHINE,
                       r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")):
      try:
        with winreg.OpenKey(hive, key) as root:
          for i in range(winreg.QueryInfoKey(root)[0]):
            try:
              with winreg.OpenKey(root, winreg.EnumKey(root, i)) as app:
                name = winreg.QueryValueEx(app, "DisplayName")[0]
                if "mumu" not in str(name).lower():
                  continue
                location = winreg.QueryValueEx(app, "InstallLocation")[0]
                found += glob.glob(os.path.join(location, "*", "MuMuManager.exe"))
            except OSError:
              continue
      except OSError:
        continue
  except ImportError:
    return []

  for env in ("ProgramFiles", "ProgramFiles(x86)"):
    root = os.environ.get(env)
    if root:
      found += glob.glob(os.path.join(root, "Netease", "*", "*", "MuMuManager.exe"))
  return list(dict.fromkeys(os.path.normcase(os.path.abspath(p)) for p in found))


def mumu_instances() -> list[dict]:
  """Running MuMu instances as [{"name", "port"}], or [] if unknown."""
  for manager in _mumu_managers():
    try:
      out = subprocess.run([manager, "info", "-v", "all"], capture_output=True,
                           text=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
      data = json.loads(out)
    except Exception:
      continue
    # One instance comes back as a flat object, several as {index: object}.
    entries = [data] if "adb_port" in data else list(data.values())
    running = [{"name": e.get("name", f"#{e.get('index', '?')}"),
                "port": int(e["adb_port"])}
               for e in entries
               if isinstance(e, dict) and e.get("adb_port") and e.get("is_android_started")]
    if running:
      return running
  return []


def alias_problem(serial: str) -> str | None:
  """Why `serial` cannot be trusted to reach one fixed emulator, or None.

  Only speaks up for MuMu's 7555 alias while MuMu has more than one instance
  running - the one case where the port is known to wander.
  """
  host, _, port = serial.strip().rpartition(":")
  if host not in ("127.0.0.1", "localhost") or port != str(MUMU_ALIAS_PORT):
    return None
  instances = mumu_instances()
  if len(instances) < 2:
    return None
  listing = ", ".join(f"127.0.0.1:{i['port']} ({i['name']})" for i in instances)
  return (f"{serial} is MuMu's shared alias, not an emulator of its own. With "
          f"{len(instances)} MuMu instances running it forwards to whichever one "
          f"MuMu picks, so this client can end up on another client's emulator. "
          f"Use the instance's own port instead: {listing}.")
