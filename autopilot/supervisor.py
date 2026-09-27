"""Run an autopilot against several emulators at once, one process each.

Separate processes, not threads. Almost everything the bot touches is module
state shared by the whole interpreter - the ADB handle and its screenshot
cache in utils/adb_actions.py, the pinned display in utils/display.py, the
easyocr reader in core/ocr.py, `bot.is_bot_running`, the root logger. Two
clients in one interpreter would take turns corrupting all of it: a frame
captured from one emulator read as the other's, taps sent to whichever device
was connected last. A process per client gives each one its own copy of the
lot, with no changes needed anywhere else.

The cost is real - each worker loads torch and easyocr, so budget on the order
of a gigabyte per client - and the emulators themselves want a core each.

A single configured device skips all of this and runs in the calling process,
which is what it has always done.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import re
import sys
import threading
import time

# Workers start with `spawn`, so this module is imported fresh in a bare
# interpreter whose working directory is inherited but whose sys.path may not
# include the repo root. utils/log.py reads version.txt relative to the cwd.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# How long a worker gets to notice the stop flag and wind down on its own
# before it is killed. A tick is at most a couple of seconds of screenshots.
SHUTDOWN_GRACE = 20.0

# How often a worker looks at the stop flag.
STOP_POLL = 0.25

# A worker that dies on its own is started again: a dropped ADB connection or
# an emulator that restarted should cost one client a few minutes, not the rest
# of an unattended night. Capped and spaced out, so a device that is genuinely
# gone stops being retried instead of spinning on it.
MAX_RESTARTS = 5
RESTART_BACKOFF = 30.0

# A client that stayed up this long before dying was not in a crash loop, so
# its restart budget starts over. Without this, five unrelated hiccups spread
# across a night used up the budget and left the client stopped for the rest
# of it.
HEALTHY_RUN = 15 * 60.0

# A client whose device has not answered, and which has not deliberately
# waited, for this long is frozen rather than slow. Every ADB command is
# bounded well inside this, so nothing legitimate gets near it.
WATCHDOG_TIMEOUT = 300.0

# How a client's process ends, as the supervisor reads it.
EXIT_FINISHED = 0     # done on purpose: stop hotkey, run limit, borrow gave up
EXIT_CRASHED = 1      # kept failing; a fresh process may do better
EXIT_NO_RESTART = 2   # cannot run as configured; restarting would only repeat it
EXIT_FROZEN = 3       # the watchdog ended it; worth a restart like any crash


def new_stop_flag(ctx=None):
  """A one-way "everybody stop" flag that is safe to share with the workers.

  A RawValue rather than the obvious multiprocessing.Event, because an Event
  is a lock and a worker holds that lock every time it checks. The check runs
  on a daemon thread, daemon threads are frozen mid-instruction when their
  interpreter shuts down, and one frozen inside Event.wait() leaves the shared
  semaphore acquired for good - after which the parent's own set() blocks for
  ever and the supervisor never gets to clean up. That deadlock is reliable
  enough to hit on the first client that exits. A RawValue has no lock at all;
  a byte written by one process and read by the others cannot wedge.
  """
  ctx = ctx or mp.get_context("spawn")
  return ctx.RawValue("b", 0)


def claim_device(label: str):
  """Take an exclusive lock on one client, or return None if it is already taken.

  Restarting the fleet before the previous one has finished exiting would put
  two bots on one emulator, both tapping the same screen and undoing each
  other. It is not a theoretical race: stopping waits on the supervisor, and a
  worker can outlive it by a moment, which is all it takes.

  The lock is an open file handle held for the life of the process rather than
  a pid written to disk, so a killed worker leaves nothing stale behind - the
  operating system drops the lock when the process goes.

  Returns the handle to keep alive, or None if another process holds it.
  """
  return _lock(os.path.join(REPO_ROOT, "logs", label, "client.lock"))


def claim_emulator(identity: str, serial: str):
  """Lock one emulator by its Android id. Returns (handle, holder).

  claim_device() locks a port, and two ports can reach one emulator - MuMu's
  7555 alias forwards to one of the real instance ports. Locking the machine
  behind the port is what actually keeps two bots off one screen. `holder` is
  the serial of whoever has it when the lock is refused, for the error.
  """
  lock_dir = os.path.join(REPO_ROOT, "logs", ".emulators")
  name = re.sub(r"[^A-Za-z0-9_-]", "_", identity)
  owner = os.path.join(lock_dir, f"{name}.owner")
  handle = _lock(os.path.join(lock_dir, f"{name}.lock"))
  if handle is None:
    try:
      with open(owner, "r", encoding="utf-8") as f:
        return None, f.read().strip() or "another client"
    except OSError:
      return None, "another client"
  try:
    with open(owner, "w", encoding="utf-8") as f:
      f.write(serial)
  except OSError:
    pass
  return handle, None


def _lock(path: str):
  os.makedirs(os.path.dirname(path), exist_ok=True)
  try:
    handle = open(path, "w")
  except OSError:
    # Opening for write truncates, which Windows refuses while another
    # process holds a lock on the file - so this is a held lock too.
    return None
  try:
    if os.name == "nt":
      import msvcrt
      msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
      import fcntl
      fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
  except OSError:
    handle.close()
    return None
  except ImportError:
    pass                   # no locking available; carry on unprotected
  return handle


def label_for(device_id: str) -> str:
  """A short tag for logs and console lines: 127.0.0.1:7555 -> 7555."""
  host_port = re.match(r"^.*:(\d+)$", device_id.strip())
  if host_port:
    return host_port.group(1)
  return re.sub(r"[^A-Za-z0-9._-]", "_", device_id.strip()) or "device"


def _worker(device_id: str, label: str, stop_event) -> None:
  """One client, in its own interpreter. Runs until the loop or `stop_event` ends it."""
  os.chdir(REPO_ROOT)
  if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

  import core.bot as bot
  from utils.log import error, info, init_logging

  bot.is_bot_running = True
  bot.heartbeat = time.time()
  # Its own logs/<label>/ and its own tag on every console line, so two
  # clients writing to the same terminal stay tellable apart.
  init_logging(subdir=label, prefix=label)

  main_thread = threading.main_thread()

  def watch() -> None:
    while bot.is_bot_running:
      if stop_event.value:
        info("Stop requested, winding down.")
        bot.is_bot_running = False
        return
      silent = time.time() - bot.heartbeat
      if silent > WATCHDOG_TIMEOUT:
        # Where it is stuck is the one thing that makes the next freeze
        # fixable, and it is lost the moment the process goes.
        import traceback
        frame = sys._current_frames().get(main_thread.ident)
        where = "".join(traceback.format_stack(frame)) if frame else "(no frame)"
        error(f"No response for {silent:.0f}s - this client has frozen. Restarting it. "
              f"It was stuck here:\n{where}")
        os._exit(EXIT_FROZEN)
      time.sleep(STOP_POLL)

  threading.Thread(target=watch, daemon=True).start()

  from autopilot.loop import run
  sys.exit(run(device_id=device_id) or EXIT_FINISHED)


def run_devices(device_ids: list[str], stop_event=None) -> None:
  """Start one autopilot per device and block until they are all done.

  `stop_event` is an optional flag from new_stop_flag() the caller can raise to
  ask every client to stop; Ctrl+C does the same thing.
  """
  if not device_ids:
    from utils.log import error
    error('No devices configured. Set "devices" under "autopilot" in config.json, '
          "or device_id for a single client.")
    return

  # Two emulators on the same port of different hosts both label as that port,
  # and sharing a label would mean sharing a log directory. Listing the same
  # device twice would mean two clients fighting over one emulator, so that
  # goes too.
  unique = list(dict.fromkeys(d.strip() for d in device_ids if d.strip()))
  if len(unique) < len(device_ids):
    print(f"[AUTOPILOT] Ignoring {len(device_ids) - len(unique)} repeated device(s).")
  device_ids = unique

  ctx = mp.get_context("spawn")
  stop = stop_event if stop_event is not None else new_stop_flag(ctx)

  started_at = {}

  def spawn(device_id: str, label: str):
    process = ctx.Process(target=_worker, args=(device_id, label, stop),
                          name=f"autopilot-{label}", daemon=False)
    process.start()
    started_at[label] = time.time()
    return process

  # [label, device_id, process]. A list, not the tuple it once was: a restart
  # replaces the process while the other two stay put.
  workers = []
  taken = set()
  for device_id in device_ids:
    label = label_for(device_id)
    if label in taken:
      label = f"{label}_{len(taken) + 1}"
    taken.add(label)
    process = spawn(device_id, label)
    workers.append([label, device_id, process])
    print(f"[AUTOPILOT] {label}: started on {device_id} (pid {process.pid}), "
          f"logging to logs/{label}/")

  # One client failing does not stop the others, so its exit is called out
  # here. Otherwise it only shows up in that client's own log, and a set of
  # two quietly becomes a set of one.
  reported = set()
  restarts = {}
  retry_at = {}
  try:
    while not stop.value:
      pending = False
      for entry in workers:
        label, device_id, process = entry
        if process.is_alive():
          pending = True
          continue

        # Exit 0 is the client deciding it was finished - a run limit reached,
        # or the stop hotkey. Only a crash is worth starting again.
        if process.exitcode == EXIT_FINISHED:
          if label not in reported:
            reported.add(label)
            print(f"[AUTOPILOT] {label}: finished on its own. "
                  f"See logs/{label}/log.txt.")
          continue

        # A setup problem - another client already on that emulator, a port
        # that wanders between instances - would fail identically every time.
        if process.exitcode == EXIT_NO_RESTART:
          if label not in reported:
            reported.add(label)
            print(f"[AUTOPILOT] {label}: cannot run as configured, not restarting it. "
                  f"The reason is at the end of logs/{label}/log.txt.")
          continue

        if label not in retry_at and time.time() - started_at.get(label, 0) >= HEALTHY_RUN:
          restarts[label] = 0

        if restarts.get(label, 0) >= MAX_RESTARTS:
          if label not in reported:
            reported.add(label)
            print(f"[AUTOPILOT] {label}: died again after {MAX_RESTARTS} restarts "
                  f"(exit {process.exitcode}), leaving it stopped. "
                  f"See logs/{label}/log.txt.")
          continue

        # First sighting of the corpse schedules the restart rather than doing
        # it here, so an emulator still coming back up is given a moment.
        if label not in retry_at:
          retry_at[label] = time.time() + RESTART_BACKOFF
          why = "froze" if process.exitcode == EXIT_FROZEN else f"died (exit {process.exitcode})"
          print(f"[AUTOPILOT] {label}: {why}, restarting in "
                f"{RESTART_BACKOFF:.0f}s "
                f"(attempt {restarts.get(label, 0) + 1} of {MAX_RESTARTS}). "
                f"See logs/{label}/log.txt.")
          pending = True
          continue
        if time.time() < retry_at[label]:
          pending = True
          continue

        restarts[label] = restarts.get(label, 0) + 1
        del retry_at[label]
        entry[2] = spawn(device_id, label)
        print(f"[AUTOPILOT] {label}: restarted on {device_id} (pid {entry[2].pid}).")
        pending = True

      # Once every client has stopped for good there is nothing left to
      # supervise, so this returns rather than waiting on a stop that is
      # never coming.
      if not pending:
        break
      time.sleep(0.5)
  except KeyboardInterrupt:
    print("\n[AUTOPILOT] Interrupted, stopping every client.")
  finally:
    stop.value = 1
    deadline = time.time() + SHUTDOWN_GRACE
    for label, _device_id, process in workers:
      process.join(timeout=max(0.0, deadline - time.time()))
      if process.is_alive():
        print(f"[AUTOPILOT] {label}: did not stop in {SHUTDOWN_GRACE:.0f}s, killing it.")
        process.terminate()
        process.join(timeout=5.0)
    print("[AUTOPILOT] All clients stopped.")
