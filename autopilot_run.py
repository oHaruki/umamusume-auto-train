"""Entry point for the Independent Training autopilot.

  py -3.12 autopilot_run.py                      one client, from config.json
  py -3.12 autopilot_run.py --device 127.0.0.1:7555 --device 127.0.0.1:7565

With more than one device it drives them all at once, one process per client,
each with its own logs/<port>/ directory and its own tag on every console line.
Devices can also be listed once under "autopilot" -> "devices" in config.json
instead of being passed every time.

Press F10 at any time to stop. With one client the stop is checked inside every
click and screenshot, so it takes effect immediately; with several it is passed
to each client, which then stops the same way.
"""

import argparse
import multiprocessing
import sys
import threading

import core.bot as bot
from utils.log import info, init_logging

from autopilot import config as auto_config
from autopilot.loop import run
from autopilot.supervisor import new_stop_flag, run_devices


def parse_args(argv):
  parser = argparse.ArgumentParser(
    description="Run the Independent Training autopilot against one or more emulators.",
    # The repo-wide flags in utils/log.py (--debug, --device-debug, ...) are
    # parsed there off the same argv; unknown ones are left alone here too.
    allow_abbrev=False)
  parser.add_argument("--device", action="append", default=[], metavar="ADB_ID",
                      help="ADB id of a client to drive. Repeat for several. "
                           "Overrides the devices in config.json.")
  known, _unknown = parser.parse_known_args(argv)
  return known


def watch_for_stop(on_stop) -> None:
  try:
    import keyboard
  except Exception as e:  # pragma: no cover - depends on the host
    info(f"Hotkey unavailable ({e}); use Ctrl+C to stop.")
    return
  keyboard.wait("f10")
  info("F10 pressed - stopping.")
  on_stop()


def main() -> None:
  args = parse_args(sys.argv[1:])
  devices = args.device or auto_config.resolve_devices()

  if len(devices) > 1:
    # One shared flag for every client, raised by F10 here in the parent.
    stop = new_stop_flag()
    init_logging()
    info(f"Press F10 to stop all {len(devices)} clients.")
    threading.Thread(target=watch_for_stop,
                     args=(lambda: setattr(stop, "value", 1),), daemon=True).start()
    run_devices(devices, stop_event=stop)
    return

  device_id = devices[0] if devices else None
  if device_id:
    bot.device_id = device_id
  init_logging()
  bot.is_bot_running = True
  threading.Thread(target=watch_for_stop,
                   args=(lambda: setattr(bot, "is_bot_running", False),),
                   daemon=True).start()
  info("Press F10 to stop.")
  run(device_id=device_id)


if __name__ == "__main__":
  # Required on Windows: workers re-import this module, and without the guard
  # each of them would start its own set of workers.
  multiprocessing.freeze_support()
  main()
