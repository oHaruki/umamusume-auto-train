import sys
import atexit
import signal
import subprocess
import warnings
warnings.filterwarnings(
  "ignore",
  category=UserWarning,
  module=r"torch\.utils\.data\.dataloader"
)
MIN = (3, 10)
MAX = (3, 14)

if not (MIN <= sys.version_info < MAX):
  # ask the launcher what it has
  out = subprocess.check_output(
    ["py", "--list"],
    text=True,
    stderr=subprocess.DEVNULL
  )

  candidates = []
  for line in out.splitlines():
    line = line.strip()
    if line.startswith("-V:"):
      v = line.split()[0][3:]
      try:
        major, minor = map(int, v.split("."))
        if (major, minor) >= MIN and (major, minor) < MAX:
          candidates.append(v)
      except ValueError:
        pass

  if not candidates:
    raise RuntimeError("No compatible Python 3.10-3.13 installed")

  best = sorted(candidates)[-1]

  p = subprocess.Popen(
    ["py", f"-{best}", *sys.argv],
    stdin=sys.stdin,
    stdout=sys.stdout,
    stderr=sys.stderr
  )
  p.wait()
  sys.exit(p.returncode)

from utils.tools import sleep
import pygetwindow as gw
import threading
import uvicorn
import keyboard

import time
import sys
import socket

import utils.constants as constants
from utils.log import info, warning, error, debug, args, init_logging

from core.skeleton import career_lobby
import core.config as config
import core.bot as bot
from server.main import app
from update_config import update_config
from utils.notifications import on_started
from utils.win_job import kill_child_when_we_exit

bot.windows_window = None

def focus_umamusume():
  if bot.use_adb:
    info("Using ADB no need to focus window.")
    constants.adjust_constants_x_coords(offset=-155)
    return True
  try:
    import pyautogui
    from utils.pyautogui_actions import screen_to_world_conversion_init
    win = gw.getWindowsWithTitle("Umamusume")
    target_window = next((w for w in win if w.title.strip() == "Umamusume"), None)
    if not target_window:
      info(f"Couldn't get the steam version window, trying {config.WINDOW_NAME}.")
      if not config.WINDOW_NAME:
        error("Window name cannot be empty! Please set window name in the config.")
        return False
      win = gw.getWindowsWithTitle(config.WINDOW_NAME)
      target_window = next((w for w in win if w.title.strip() == config.WINDOW_NAME), None)
      if not target_window:
        error(f"Couldn't find target window named \"{config.WINDOW_NAME}\". Please double check your window name config.")
        return False

      constants.adjust_constants_x_coords()
      if target_window.isMinimized:
        target_window.restore()
      else:
        target_window.minimize()
        sleep(0.2)
        target_window.restore()
        sleep(0.5)
      pyautogui.press("esc")
      pyautogui.press("f11")
      time.sleep(5)
      close_btn = pyautogui.locateCenterOnScreen("assets/buttons/bluestacks/close_btn.png", confidence=0.8, minSearchTime=2)
      if close_btn:
        pyautogui.click(close_btn)
      return True

    if target_window.width < 1920 or target_window.height < 1080:
      error(f"Your resolution is {target_window.width} x {target_window.height}. Minimum expected size is 1920 x 1080.")
      return
    if target_window.isMinimized:
      target_window.restore()
    else:
      target_window.minimize()
      sleep(0.2)
      target_window.restore()
      sleep(0.5)
    bot.windows_window = target_window
    if target_window.width > 1920 or target_window.height > 1080:
      info("Screen bigger than standard 1080p. Initializing screen space conversions.")
      screen_to_world_conversion_init()
  except Exception as e:
    error(f"Error focusing window: {e}")
    return False
  return True

def main():
  print("Uma Auto!")
  config.reload_config()

  if args.use_adb:
    bot.use_adb = True
    bot.device_id = args.use_adb
  else:
    bot.use_adb = config.USE_ADB
    if config.DEVICE_ID and config.DEVICE_ID != "":
      bot.device_id = config.DEVICE_ID
  if focus_umamusume():
    on_started()
    info(f"Config: {config.CONFIG_NAME}")
    debug(f"Config:")
    for name, value in vars(config).items():
      if not name.startswith("__"):
          debug(f"{name} = {value}")
    career_lobby(args.dry_run_turn)
  else:
    error("Failed to focus Umamusume window")

def hotkey_listener():
  while True:
    keyboard.wait(bot.hotkey)
    if not bot.is_bot_running:
      print("[BOT] Starting...")
      bot.is_bot_running = True
      t = threading.Thread(target=main, daemon=True)
      t.start()
    else:
      print("[BOT] Stopping...")
      bot.is_bot_running = False
    sleep(0.5)

# The running multi-client fleet, and the job object holding it to this
# process's lifetime. Module level so the atexit hook can reach them.
_fleet = None
_fleet_job = None


def stop_fleet_on_exit():
  """Ask the fleet to wind down when the server exits for any reason.

  The job object would kill it regardless, but that is a kernel-level
  execution and gives the clients no chance to finish the tap they are in the
  middle of. Trying the polite route first costs a few seconds.
  """
  global _fleet
  if _fleet is not None and _fleet.poll() is None:
    print("[AUTOPILOT] Server exiting - stopping the clients.")
    stop_fleet(_fleet)
    _fleet = None


def stop_fleet(fleet):
  """Stop a multi-client autopilot and everything it started.

  terminate() would kill only the supervisor and leave its workers tapping
  away at the emulators with nothing left to stop them. Ctrl+Break reaches the
  whole process group, and Python turns it into the KeyboardInterrupt both the
  supervisor and each worker already shut down cleanly on.
  """
  if fleet is None or fleet.poll() is not None:
    return
  try:
    fleet.send_signal(signal.CTRL_BREAK_EVENT)
    fleet.wait(timeout=30)
    return
  except Exception as e:
    warning(f"Could not stop the autopilot clients gracefully ({e}); killing them.")
  # Last resort: take down the tree by pid, since the group signal did not land.
  subprocess.run(["taskkill", "/F", "/T", "/PID", str(fleet.pid)],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

def autopilot_listener():
  global _fleet, _fleet_job
  # Shares bot.is_bot_running with the F1 bot, so only one runs at a time.
  from autopilot.loop import run as run_autopilot
  from autopilot import config as auto_config
  worker = None
  fleet = None
  while True:
    keyboard.wait(bot.autopilot_hotkey)
    running = (worker is not None and worker.is_alive()) or (fleet is not None and fleet.poll() is None)
    # Clearing the flag does not stop the thread instantly, so track the thread
    # itself. Going by the flag alone starts a second autopilot whenever the
    # key is pressed again while the previous one is still winding down.
    if running:
      print("[AUTOPILOT] Stopping...")
      bot.is_bot_running = False
      stop_fleet(fleet)
      fleet = None
      _fleet = None
    elif bot.is_bot_running:
      print(f"[AUTOPILOT] The bot is running. Press '{bot.hotkey}' to stop it first.")
    else:
      devices = auto_config.resolve_devices()
      if len(devices) > 1:
        # Several clients means several processes, and spawning those from a
        # thread of a server that has already imported uvicorn, pygame and
        # torch is asking for trouble. A plain child process running the
        # standalone entry point keeps the web UI out of it entirely.
        print(f"[AUTOPILOT] Starting {len(devices)} clients: {', '.join(devices)}")
        print(f"[AUTOPILOT] Their output goes to logs/<port>/. Press "
              f"'{bot.autopilot_hotkey}' again to stop them.")
        # Its own process group, so a stop can send it Ctrl+Break without
        # also stopping this server. That does mean console Ctrl+C no longer
        # reaches it, which is what the job object below is for: it ties the
        # fleet's life to ours so no route out of here can leave the clients
        # running, and orphaned bots keep tapping the game for ever.
        fleet = subprocess.Popen([sys.executable, "autopilot_run.py"],
                                 creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        _fleet = fleet
        _fleet_job = kill_child_when_we_exit(fleet)
        if _fleet_job is None:
          warning("Could not tie the clients to this process; if this window is "
                  "killed rather than stopped, stop them with F10 or Task Manager.")
      else:
        print("[AUTOPILOT] Starting...")
        bot.is_bot_running = True
        worker = threading.Thread(target=run_autopilot, daemon=True)
        worker.start()
    sleep(0.5)

def is_port_available(host, port):
  try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((host, port))
    sock.close()
    return True
  except OSError:
    return False

def start_server():
  host = "127.0.0.1"
  start_port = 8000
  end_port = 8010
  for port in range(start_port, end_port):
    if is_port_available(host, port):
      bot.instance = port - start_port + 1
      bot.hotkey = f"f{bot.instance}"
      break
    else:
      print(f"[INFO] Port {port} is already in use. Trying {port + 1}...")

  bot.autopilot_hotkey = f"f{bot.instance + 1}"

  threading.Thread(target=hotkey_listener, daemon=True).start()
  threading.Thread(target=autopilot_listener, daemon=True).start()
  server_config = uvicorn.Config(app, host=host, port=port, workers=1, log_level="warning")
  server = uvicorn.Server(server_config)
  init_logging()
  info(f"Press '{bot.hotkey}' to start/stop the bot.")
  info(f"Press '{bot.autopilot_hotkey}' to start/stop the autopilot.")
  info(f"[SERVER] Open http://{host}:{port} to configure the bot.")
  server.run()

if __name__ == "__main__":
  atexit.register(stop_fleet_on_exit)
  update_config()
  config.reload_config()
  start_server()
