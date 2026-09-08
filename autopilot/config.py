"""Autopilot settings, read from the "autopilot" block of config.json.

Read straight from the JSON rather than through core/config.py, whose
reload_config() raises on any key it does not find. Keeping these optional
means an existing config.json keeps working untouched, and merges from
upstream never collide over a config key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


DEFAULTS = {
  # The emulators to drive, one autopilot each, all at once. Empty falls back
  # to the single device_id from the main config, which is what a one-client
  # setup has always used.
  #
  # An entry is either an id on its own, or an object carrying that id plus
  # any settings from this same block to use for that client only:
  #
  #   ["127.0.0.1:5555",
  #    {"id": "127.0.0.1:7555", "borrow_card_targets": ["Kitasan Black"]}]
  #
  # Anything a client does not override it takes from the shared settings, so
  # a two-account setup can differ only in the card it borrows.
  "devices": [],
  # Support cards to borrow, best first. Written as they appear in game;
  # matching normalises punctuation away, so "[Q!=0] Agnes Tachyon" and the
  # real "[Q<>0] Agnes Tachyon" both work.
  "borrow_card_targets": [],
  # Stop rather than borrow something unintended when no target is found.
  "borrow_required": True,
  # How many times to scroll the borrow list before giving up.
  "borrow_max_scrolls": 8,
  # How many times to reload the borrow list with a different set of friends
  # when none of the targets are in it.
  "borrow_max_reloads": 3,
  # Levenshtein ratio a row must reach to count as the wanted card.
  "borrow_match_threshold": 0.80,
  # Load a saved race agenda before starting each run. Always takes the first
  # entry under My Agendas.
  "use_agenda": False,
  # Once the configured skill list is exhausted, spend whatever points remain
  # on any affordable skill rather than leaving them unused.
  "buy_leftover_skills": False,
  # Cap on Skills visits per career, so a career can never loop forever
  # between Complete Career and the Learn screen.
  "max_skill_visits": 5,
  # Wait and retry when TP is too low, instead of stopping.
  "wait_when_out_of_tp": True,
  # Seconds between polls while waiting for the training to finish.
  "idle_poll_seconds": 20.0,
  # Spend carats on TP from Home when there is not enough for another run.
  # Off by default: it costs real currency.
  "auto_recover_tp": False,
  # TP to keep on hand. Below this, Home refills before opening Career.
  "tp_min": 30,
  # How many times to press + in the recovery dialog. One press is 10 carats
  # for 30 TP; TP over the cap is banked, so more than one is not wasted.
  "tp_recover_uses": 1,
  # Consecutive refills allowed without TP coming back up. Purely a runaway
  # guard - every one of these spends carats, so a misread must not loop.
  "tp_recover_max_attempts": 3,
}


@dataclass
class AutopilotConfig:
  devices: list[str] = field(default_factory=list)
  # Per-client settings, keyed by device id. Kept beside `devices` rather than
  # inside it so everything that reads a config still sees plain fields.
  device_overrides: dict = field(default_factory=dict)
  borrow_card_targets: list[str] = field(default_factory=list)
  borrow_required: bool = True
  borrow_max_scrolls: int = 8
  borrow_max_reloads: int = 3
  borrow_match_threshold: float = 0.80
  use_agenda: bool = False
  buy_leftover_skills: bool = False
  max_skill_visits: int = 5
  wait_when_out_of_tp: bool = True
  idle_poll_seconds: float = 20.0
  auto_recover_tp: bool = False
  tp_min: int = 30
  tp_recover_uses: int = 1
  tp_recover_max_attempts: int = 3


# Settings that describe the fleet as a whole. A client cannot override these:
# a per-client "devices" list would be a fleet inside a fleet.
FLEET_ONLY = ("devices", "device_overrides")

# Config is re-read several times per process, and a typo in it would
# otherwise be reported on every read.
_warned_unknown: set = set()


def split_devices(entries) -> tuple[list[str], dict]:
  """Separate the device list into ids and per-client overrides.

  Accepts either form of entry - a bare id, or an object with an "id" and any
  settings to override - so a config written before per-client settings
  existed still loads unchanged.
  """
  ids: list[str] = []
  overrides: dict = {}
  for entry in entries or []:
    if isinstance(entry, dict):
      device_id = str(entry.get("id", "")).strip()
      patch = {k: v for k, v in entry.items()
               if k != "id" and k in DEFAULTS and k not in FLEET_ONLY}
      unknown = [k for k in entry if k != "id" and k not in patch]
      if unknown and (device_id, tuple(sorted(unknown))) not in _warned_unknown:
        # Warned about rather than dropped silently: a typo here looks exactly
        # like a setting that is being ignored for no reason.
        _warned_unknown.add((device_id, tuple(sorted(unknown))))
        print(f"[WARN] Ignoring unknown per-client setting(s) for {device_id or '?'}: "
              f"{', '.join(sorted(unknown))}")
    else:
      device_id, patch = str(entry).strip(), {}
    if not device_id:
      continue
    ids.append(device_id)
    if patch:
      overrides[device_id] = patch
  return ids, overrides


def load(path: str = "config.json") -> AutopilotConfig:
  try:
    with open(path, "r", encoding="utf-8") as f:
      block = json.load(f).get("autopilot", {})
  except (OSError, json.JSONDecodeError):
    block = {}

  values = {key: block.get(key, default) for key, default in DEFAULTS.items()}
  values["devices"], values["device_overrides"] = split_devices(values["devices"])
  return AutopilotConfig(**values)


def load_for(device_id: str | None, path: str = "config.json") -> AutopilotConfig:
  """The shared settings with one client's own overrides laid over the top."""
  cfg = load(path)
  patch = cfg.device_overrides.get(device_id) if device_id else None
  if not patch:
    return cfg
  for key, value in patch.items():
    setattr(cfg, key, value)
  return cfg


def resolve_devices(path: str = "config.json") -> list[str]:
  """The devices to drive, in order, falling back to the single device_id.

  Kept out of load() so the supervisor can work out how many processes to
  start without every worker having to repeat the fallback.
  """
  cfg = load(path)
  if cfg.devices:
    return cfg.devices
  try:
    with open(path, "r", encoding="utf-8") as f:
      fallback = str(json.load(f).get("device_id", "")).strip()
  except (OSError, json.JSONDecodeError):
    fallback = ""
  return [fallback] if fallback else []
