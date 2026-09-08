"""Rebuild devtools/supports.json from GameTora's data layer.

Replaces the Selenium support-card scraper in main.py, which cannot work any
more: the training-event outcomes used to live in tippy tooltips, and the site
now renders them in an accordion that ignores synthetic clicks, so there is no
DOM left to read. See gametora.py for the data layer this uses instead.

    py -3.12 devtools/scrape_supports.py --validate   # compare, write nothing
    py -3.12 devtools/scrape_supports.py --write      # rewrite supports.json

Validate first. It regenerates every card already in supports.json and diffs
against what is stored, which is the only real check on the renderer below -
the reward codes are a private encoding and the stored records are the only
statement of what they are supposed to come out as.

One deliberate difference from the old data. GameTora renders a card's event
values with its max-level event bonuses applied, and the old scraper captured
that for the twelve support cards that have such a bonus (the Tazuna/Aoi
Kiryuin/Sasami-style friend cards) while every other card kept its base
values. This writes base values throughout. That is self-consistent, it is
what the game gives before those bonuses are unlocked, and it does not change
which choice is better - the bonus scales a card's options together.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gametora  # noqa: E402

SUPPORTS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "supports.json")

RARITY = {1: "R", 2: "SR", 3: "SSR"}
TYPE = {"speed": "SPD", "stamina": "STA", "power": "POW", "guts": "GUTS",
        "intelligence": "WIT", "friend": "PAL", "group": "GRP"}

# Reward codes that name a stat, and the label the old data used for each.
STATS = {"sp": "Speed", "st": "Stamina", "po": "Power", "gu": "Guts",
         "in": "Wit", "en": "Energy", "pt": "Skill points", "mo": "Mood",
         "me": "Maximum Energy", "5s": "All stats"}

# Codes that split a choice into alternative outcomes rather than adding to it.
SPLITTERS = {"di", "nl"}

# Branch conditions rather than outcomes: they qualify the alternative that
# follows, which is already listed on its own, so they carry no text. The old
# data dropped these too.
SILENT = {"brg", "se_has", "other_cases",
          "both_previous_chain_good", "one_previous_chain_good",
          "both_previous_chain_bad"}

# Codes whose whole meaning is a fixed phrase.
FIXED = {
  "he": "Heal a negative status effect",
  "ha": "Heal all negative status effects",
  "fe": "Full energy recovery",
  "ee": "Event chain ended",
  # A badge GameTora draws beside the event title. The old scraper read the
  # title and the badge as one string, which is where keys like
  # "Enthusiastic PairDating starts" in the stored data came from.
  "ds": "Dating starts",
}

ORDINAL = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th"}

# Reward codes seen in a run that have no label here, counted for the report at
# the end. Better surfaced than quietly dropped: an unlabelled code is data the
# game has and this does not.
UNKNOWN_CODES: dict[str, int] = {}


def format_value(value, spaced: bool) -> str:
  """Level-dependent values arrive as "+5/+10".

  The old data spaced those out for stats ("+5 / +10") but not for skill hints
  or bond ("+1/+3", "-5/+5"); keeping that split means the validation diff
  shows real changes rather than punctuation.
  """
  text = str(value)
  if spaced and "/" in text:
    return " / ".join(part.strip() for part in text.split("/"))
  return text


class Lookups:
  """Id -> English name, for the things reward entries point at."""

  def __init__(self, mf=None):
    mf = mf if mf is not None else gametora.manifest()
    self.cards = gametora.data_file("support-cards", mf)
    skills = gametora.data_file("skills", mf)
    statuses = gametora.data_file("status-effects", mf)
    characters = gametora.data_file("characters", mf)

    self.skill = {s["id"]: s.get("name_en") or s.get("enname") for s in skills}
    self.status = {s["id"]: s.get("name_en") or s.get("name_en_eon") for s in statuses}
    # Bond points at a character. Support-only characters (Tazuna, the student
    # council, the reporters) are absent from the character list, so the cards
    # themselves fill those in.
    self.character = {c["char_id"]: c.get("en_name") for c in characters}
    for card in self.cards:
      self.character.setdefault(card["char_id"], card.get("char_name"))

  def skill_name(self, skill_id):
    return self.skill.get(skill_id) or f"skill {skill_id}"

  def status_name(self, status_id):
    return self.status.get(status_id) or f"status {status_id}"

  def character_name(self, char_id, fallback=""):
    return self.character.get(char_id) or fallback or f"character {char_id}"


def render_reward(reward: dict, lookups: Lookups, own_character: str) -> str | None:
  """One reward entry as the text the old data used, or None if it has none."""
  code = reward.get("t")
  value = reward.get("v")
  target = reward.get("d")

  if code in SILENT or code in SPLITTERS:
    return None

  if code in FIXED:
    text = FIXED[code]
  elif code in STATS:
    text = f"{STATS[code]} {format_value(value, spaced=True)}"
  elif code == "bo":
    text = f"{lookups.character_name(target, own_character)} bond {format_value(value, spaced=False)}"
  elif code == "sk":
    text = f"{lookups.skill_name(target)} hint {format_value(value, spaced=False)}"
  elif code == "sg":
    text = f"Obtain {lookups.skill_name(target)} skill"
  elif code == "se":
    text = f"Get {lookups.status_name(target)} status"
  elif code == "brf":
    text = f"※ Will affect the outcome of the {ORDINAL.get(target, str(target) + 'th')} event"
  elif code == "rs":
    count = target or 1
    text = f"{count} random stat{'s' if count != 1 else ''} {value}"
  elif code == "brp":
    # Prerequisite on an earlier choice in the same chain: v is which option,
    # d is which event.
    option, event = ORDINAL.get(value, f"{value}th"), ORDINAL.get(target, f"{target}th")
    text = f"※ Can only happen if you chose the {option} option during the {event} event"
  elif code == "sr":
    # A set of alternative skill hints carried in d.
    parts = [f"{lookups.skill_name(alt.get('d'))} hint {alt.get('v')}"
             for alt in (target or []) if isinstance(alt, dict)]
    text = " or ".join(parts) if parts else None
  else:
    # Unknown code: say so rather than inventing a label or dropping it
    # silently, so a new one shows up in the validation diff and in the report.
    UNKNOWN_CODES[code] = UNKNOWN_CODES.get(code, 0) + 1
    text = f"<{code}{' ' + str(value) if value else ''}>"

  if text and reward.get("r"):
    text = f"(random) {text}"
  return text


def render_choice(rewards: list, lookups: Lookups, own_character: str) -> str:
  """One choice's outcome text, joining alternative branches with "or"."""
  branches, current = [], []
  for reward in rewards:
    if reward.get("t") in SPLITTERS:
      branches.append(current)
      current = []
      continue
    text = render_reward(reward, lookups, own_character)
    if text:
      current.append(text)
  branches.append(current)

  rendered = [", ".join(b) for b in branches if b]
  if not rendered:
    return ""
  if len(rendered) == 1:
    return rendered[0]
  return "Randomly either " + " or ".join(rendered)


def build_record(card: dict, payload: dict, lookups: Lookups) -> dict:
  """One supports.json record: the card's metadata plus event -> outcomes."""
  support_id = str(card["support_id"])
  rarity = RARITY.get(card["rarity"], str(card["rarity"]))
  type_code = TYPE.get(card["type"], card["type"])
  own = card.get("char_name", "")

  record = {
    "id": support_id,
    "name": f"{own} ({rarity}) ({type_code})",
    "image_url": f"https://gametora.com/images/umamusume/supports/support_card_s_{support_id}.png",
    "rarity": rarity,
    "type": type_code,
  }

  for group in gametora.event_data(payload).values():
    for event in group:
      name = (event.get("n") or "").strip()
      if not name:
        continue
      outcomes = [render_choice(choice.get("r") or [], lookups, own)
                  for choice in event.get("c") or []]
      if outcomes:
        record[name] = outcomes

  record["implemented"] = card.get("release_en")
  return record


def build_all(cards, lookups, build, log_every=25):
  records, failures = {}, []
  for i, card in enumerate(cards, 1):
    try:
      payload = gametora.card_payload(card["url_name"], build)
      records[str(card["support_id"])] = build_record(card, payload, lookups)
    except Exception as e:
      failures.append((card["url_name"], f"{type(e).__name__}: {e}"))
    if log_every and i % log_every == 0:
      print(f"  {i}/{len(cards)} cards")
  return records, failures


def normalise(name: str) -> str:
  """Event key as the old scraper would have left it, for comparison only.

  Those records carry its artefacts - a leading space where an arrow prefix was
  stripped, and unstripped arrows past the third, because its cleanup list only
  covered one, two and three. core/events.py matches event names fuzzily and
  strips parenthesised runs first, so neither form matters at runtime; this
  exists so validation compares outcomes rather than old bugs.
  """
  import re
  return re.sub(r"\s+", " ", re.sub(r"\(❯+\)", "", name)).strip()


def compare_to_stored(records: dict, stored: dict) -> int:
  """Diff regenerated records against the stored ones. Returns a mismatch count."""
  meta = ("id", "name", "image_url", "rarity", "type", "implemented")
  shared = sorted(set(records) & set(stored), key=int)
  print(f"\ncards: regenerated={len(records)} stored={len(stored)} "
        f"comparable={len(shared)} new={len(set(records) - set(stored))} "
        f"missing={len(set(stored) - set(records))}")

  name_bad = event_bad = outcome_bad = 0
  exact_cards = 0
  samples = []
  for support_id in shared:
    new, old = records[support_id], stored[support_id]
    card_ok = True
    if new["name"] != old.get("name"):
      name_bad += 1
      card_ok = False
      if len(samples) < 6:
        samples.append(f"  {support_id} name: {old.get('name')!r} -> {new['name']!r}")

    new_events = {normalise(k): v for k, v in new.items() if k not in meta}
    old_events = {normalise(k): v for k, v in old.items() if k not in meta}
    if set(new_events) != set(old_events):
      event_bad += 1
      card_ok = False
      if len(samples) < 6:
        only_old = sorted(set(old_events) - set(new_events))[:2]
        only_new = sorted(set(new_events) - set(old_events))[:2]
        samples.append(f"  {support_id} events: only-old={only_old} only-new={only_new}")

    for key in set(new_events) & set(old_events):
      if new_events[key] != old_events[key]:
        outcome_bad += 1
        card_ok = False
        if len(samples) < 6:
          samples.append(f"  {support_id} {key!r}\n      old={old_events[key]}\n      new={new_events[key]}")
    if card_ok:
      exact_cards += 1

  print(f"cards identical: {exact_cards}/{len(shared)}")
  print(f"  name mismatches:     {name_bad}")
  print(f"  event-set mismatches:{event_bad}")
  print(f"  outcome mismatches:  {outcome_bad}")
  if samples:
    print("\nsamples:")
    for s in samples:
      print(s)
  return name_bad + event_bad + outcome_bad


def refresh(write: bool = False, validate: bool = False,
            refresh_manifest: bool = False) -> int:
  """Rebuild every EN support card. Returns a process-style exit code.

  Kept separate from main() so devtools/main.py can ask for a real write.
  Calling main() from there would re-parse sys.argv, find no --write, and
  quietly do a dry run - leaving convert_all to rebuild events.json from the
  supports.json it had not actually updated.
  """
  args = argparse.Namespace(write=write, validate=validate,
                            refresh_manifest=refresh_manifest)

  mf = gametora.manifest(refresh=args.refresh_manifest)
  lookups = Lookups(mf)
  cards = [c for c in lookups.cards if c.get("release_en")]
  cards.sort(key=lambda c: c["support_id"])
  print(f"support cards released on the English client: {len(cards)}")

  build = gametora.build_id()
  print(f"buildId: {build}")

  records, failures = build_all(cards, lookups, build)
  print(f"built {len(records)} records, {len(failures)} failed")
  if UNKNOWN_CODES:
    print("unlabelled reward codes (written as <code value>, add them to this "
          "file once you know what they render as):")
    for code, count in sorted(UNKNOWN_CODES.items(), key=lambda kv: -kv[1]):
      print(f"  {code}: {count}")
  for url_name, why in failures[:10]:
    print(f"  !! {url_name}: {why}")

  with open(SUPPORTS_JSON, "r", encoding="utf-8") as f:
    stored = json.load(f)

  if args.validate or not args.write:
    mismatches = compare_to_stored(records, stored)
    if not args.write:
      print("\nDry run - nothing written. Pass --write to update supports.json.")
      return 0 if not failures else 1
    if mismatches:
      print(f"\n{mismatches} mismatch(es); writing anyway because --write was given.")

  if failures:
    print("\nRefusing to write: some cards failed to build, and a partial "
          "supports.json would silently lose their events.")
    return 1

  # Newest first, which is the order the old scraper left behind.
  ordered = {k: records[k] for k in sorted(records, key=int, reverse=True)}
  with open(SUPPORTS_JSON, "w", encoding="utf-8") as f:
    json.dump(ordered, f, ensure_ascii=False, indent=2)
  print(f"\nWrote {len(ordered)} cards to {SUPPORTS_JSON}")
  print("Now regenerate data/events.json (convert_all in devtools/main.py).")
  return 0


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--write", action="store_true",
                      help="Rewrite devtools/supports.json (default is a dry run)")
  parser.add_argument("--validate", action="store_true",
                      help="Diff every regenerated card against the stored one")
  parser.add_argument("--refresh-manifest", action="store_true",
                      help="Re-read the manifest before starting")
  args = parser.parse_args()
  return refresh(write=args.write, validate=args.validate,
                 refresh_manifest=args.refresh_manifest)


if __name__ == "__main__":
  sys.exit(main())
