"""Continuous Independent Training loop.

Each tick: read the screen, identify it against the table in screens.py, run
that screen's action, repeat. Nothing tracks "what step are we on", so an
unexpected popup, a slow transition or being started mid-cycle all recover on
their own - and the bot can be launched from any screen.

The loop only ever clicks the location of a template it has already matched.
On a screen it does not recognise it does nothing at all, which is what keeps
a misread from turning into a stray tap.
"""

from __future__ import annotations

import time

from PIL import Image

import core.bot as bot
import core.config as core_config
import utils.constants as constants
import utils.device_action_wrapper as device_action
from core.ocr import extract_number, get_reader
from core.recognizer import compare_brightness
from core.skill import buy_skill, init_skill_py
from utils.adb_actions import init_adb
from utils.device_action_wrapper import BotStopException
from utils.log import debug, error, info, warning
from utils.notifications import StopReason
from utils.screenshot import are_screenshots_same
from utils.tools import sleep

from autopilot import config as auto_config
from autopilot.borrow_match import duplicate_row_indexes, find_best, parse_rows
from autopilot.screens import (
  BORROW_ALLOWLIST, BORROW_LIST_LTRB, BORROW_ROW_X, BORROW_SCROLL_FROM,
  BORROW_RELOAD_BUTTON, BORROW_SCROLL_TO, DUPLICATE_BADGE, EDIT_AGENDA_BUTTON,
  FRIENDS_SLOT_EMPTY, LOAD_LIST_BUTTON,
  MATCH_THRESHOLD, MY_AGENDAS_BUTTON, NORMAL_MODE_LABEL, SCREENS,
  SKILL_POINTS_LTRB, TP_LTRB, TP_TIME_LTRB, SKILLS_BUTTON, START_BUTTON, START_CAREER_BUTTON,TP_EVENT_BUTTON, NEXT_BUTTON,
)

CLOSE_BUTTON = "assets/buttons/close_btn.png"
CONFIRM_BUTTON = "assets/buttons/confirm_btn.png"

# Consecutive unsettled checks before acting on the latest reading anyway.
FORCE_ACT_AFTER = 8

# Mean pixel difference below which the borrow list counts as stopped. Low,
# because a list that has genuinely stopped is pixel-identical between looks.
LIST_STILL_DIFF = 1.0

# Scroll passes when spending leftover points. Bounded so a skill screen that
# never reports reaching its end cannot loop forever.
MAX_LEFTOVER_PASSES = 15


class Autopilot:
  def __init__(self, cfg: auto_config.AutopilotConfig):
    self.cfg = cfg
    self.runs_completed = 0
    self.skill_visits = 0
    self.skills_done = False
    self.list_pass_done = False
    self.agenda_loaded = False
    self.last_screen = None
    self.idle_streak = 0
    self.settling_streak = 0
    self.repeat_count = 0

  # --- screen reading -------------------------------------------------
  def identify(self):
    """First matching entry of the table, or (None, None)."""
    for screen in SCREENS:
      pos = device_action.locate(screen.identify, confidence=MATCH_THRESHOLD,
                                 region_ltrb=screen.identify_region)
      if pos:
        return screen, pos
    return None, None

  def identify_settled(self):
    """Identify twice, reporting only a reading that holds across both.

    Screens fade in and their parts do not arrive together: entering Trainee
    Select draws its Next button before its header, so for a frame or two it
    looks like a generic result screen. Acting then clicks whatever happens to
    sit under that button. Confirming across two reads costs a fraction of a
    second and removes the whole class of transitional misfires.

    Returns (screen, pos, status) where status is "stable" or "settling".
    A stable reading of nothing is (None, None, "stable").
    """
    first, _ = self.identify()
    sleep(0.4)
    device_action.flush_screenshot_cache()
    second, pos = self.identify()

    first_name = first.name if first else None
    second_name = second.name if second else None
    if first_name != second_name:
      debug(f"Screen still settling ({first_name} -> {second_name}), waiting.")
      # The second reading is still returned so the caller can fall back to it
      # rather than stalling forever on a screen that never settles.
      return second, pos, "settling"
    return second, pos, "stable"

  def read_skill_points(self) -> int:
    """Skill point total on the Learn screen, or -1 if unreadable."""
    crop = device_action.screenshot(region_ltrb=SKILL_POINTS_LTRB)
    pil = Image.fromarray(crop)
    pil = pil.resize((pil.width * 3, pil.height * 3), Image.BICUBIC)
    return extract_number(pil)

  def read_current_tp(self):
    """ check current tp"""
    crop = device_action.screenshot(region_ltrb=TP_LTRB)
    pil = Image.fromarray(crop)
    pil = pil.resize((pil.width * 3, pil.height * 3), Image.BICUBIC)
    return extract_number(pil)
  
  def read_tp_refresh_time(self):
    """get remaining tp time in seconds"""
    crop = device_action.screenshot(region_ltrb=TP_TIME_LTRB)
    pil = Image.fromarray(crop)
    pil = pil.resize((pil.width * 3, pil.height * 3), Image.BICUBIC)
    rawtime = extract_number(pil)
    # raw time is what is directly read, eg 1000 -> 10 min
    minutes = rawtime // 100
    seconds = rawtime % 100
    return minutes * 60 + seconds
  
  def duplicate_badge_ys(self) -> list[int]:
    """Y positions of every "Duplicate Support" badge currently on screen."""
    frame = device_action.screenshot()
    boxes = device_action.match_template(DUPLICATE_BADGE, frame, threshold=MATCH_THRESHOLD)
    return [y for _x, y, _w, _h in boxes]

  def wait_for_borrow_list_still(self, timeout: float = 5.0, poll: float = 0.25) -> bool:
    """Block until the borrow list stops moving.

    A swipe keeps gliding after the finger lifts. Reading rows mid-glide gives
    positions that are already stale by the time the tap lands, which picks the
    wrong card. A fixed sleep cannot win here - too short and it still moves,
    too long and every page costs that wait - so watch until two consecutive
    looks match.
    """
    deadline = time.time() + timeout
    previous = None
    while time.time() < deadline:
      device_action.flush_screenshot_cache()
      current = device_action.screenshot(region_ltrb=BORROW_LIST_LTRB)
      if previous is not None and are_screenshots_same(previous, current,
                                                       diff_threshold=LIST_STILL_DIFF):
        return True
      previous = current
      sleep(poll)
    warning("Borrow list was still moving after "
            f"{timeout}s; reading it anyway, the tap may be off.")
    return False

  def read_borrow_rows(self):
    crop = device_action.screenshot(region_ltrb=BORROW_LIST_LTRB)
    result = get_reader().readtext(crop, allowlist=BORROW_ALLOWLIST)
    return parse_rows(result, y_offset=BORROW_LIST_LTRB[1], x_offset=BORROW_LIST_LTRB[0])

  # --- handlers -------------------------------------------------------
  def do_scenario_select(self,screen):
    tp_cost = 30
    if device_action.locate(TP_EVENT_BUTTON,confidence=MATCH_THRESHOLD):
      info("TP Event Active")
      tp_cost = 15
    current_tp = self.read_current_tp()
    info(f"Current TP is {current_tp} and cost is {tp_cost}")
    #continue if enough tp, otherwise wait or quit depending on config
    if(current_tp>=tp_cost):
      info("Enough TP, selecting scenario.")
      device_action.locate_and_click(NEXT_BUTTON, confidence=MATCH_THRESHOLD)
    else:
      info("Not enough TP")
      if self.cfg.wait_when_out_of_tp:
        sleeptime = (tp_cost-current_tp-1)*10*60 + self.read_tp_refresh_time()
        info(f"Waiting for {sleeptime} seconds ")
        sleep(sleeptime)
      else:
        info("Wait for TP set to false, exiting")
        bot.is_bot_running = False
  
  def do_formation(self, screen):
    """Borrow first if the slot is still empty, otherwise start the career."""
    if device_action.locate(FRIENDS_SLOT_EMPTY, confidence=MATCH_THRESHOLD):
      info("Borrow slot empty, opening the borrow list.")
      device_action.locate_and_click(FRIENDS_SLOT_EMPTY, confidence=MATCH_THRESHOLD)
    else:
      info("Support formation ready, starting the career.")
      device_action.locate_and_click(START_CAREER_BUTTON, confidence=MATCH_THRESHOLD)

  def do_career_mode(self, screen):
    """Pick Normal Mode on the event-time popup, then confirm.

    Deliberately does not fall back to confirming blind. Pressing Confirm
    without having found the row starts whichever mode the game has
    highlighted, and there is no way to tell afterwards - the TP is spent and
    the career is running. Sitting on the popup instead is loud (step() warns
    about repeats) and costs nothing but time.
    """
    if not device_action.locate_and_click(NORMAL_MODE_LABEL, confidence=MATCH_THRESHOLD):
      warning("Normal Mode row not found on the career mode popup. Not confirming, "
              "because that would start whichever mode is currently highlighted.")
      return

    # Let the selection move before Confirm reads it.
    sleep(0.4)
    info("Normal Mode selected, confirming.")
    device_action.locate_and_click(CONFIRM_BUTTON, confidence=MATCH_THRESHOLD)

  def do_borrow(self, screen):
    targets = self.cfg.borrow_card_targets
    if not targets:
      error('No borrow_card_targets configured. Add them under "autopilot" in config.json.')
      device_action.stop_bot(StopReason.STUCK)
      return

    for reload_count in range(self.cfg.borrow_max_reloads + 1):
      if self.scan_borrow_list(targets):
        return
      if reload_count >= self.cfg.borrow_max_reloads:
        break
      # The reload button swaps in a different set of friends rather than
      # paging, so it is the only way to see candidates this list never had.
      if not device_action.locate_and_click(BORROW_RELOAD_BUTTON, confidence=MATCH_THRESHOLD):
        warning("Reload button not found; cannot look at a different set of friends.")
        break
      info(f"Not in this list, reloading it ({reload_count + 1} of "
           f"{self.cfg.borrow_max_reloads}).")
      sleep(1.5)

    if self.cfg.borrow_required:
      error(f"None of {targets} found. Stopping rather than borrowing something unintended.")
      device_action.stop_bot(StopReason.STUCK)
    else:
      warning(f"None of {targets} found. Continuing without a borrowed card.")
      device_action.locate_and_click(CLOSE_BUTTON, confidence=MATCH_THRESHOLD)

  def scan_borrow_list(self, targets: list[str]) -> bool:
    """Scroll the current list looking for a target. True if one was tapped."""
    for attempt in range(self.cfg.borrow_max_scrolls + 1):
      # Must settle before reading: row positions are only valid if the list
      # is where it will still be when the tap lands.
      self.wait_for_borrow_list_still()
      rows = self.read_borrow_rows()
      debug(f"Borrow list page {attempt + 1}: {[r.card for r in rows]}")

      duplicates = duplicate_row_indexes(rows, self.duplicate_badge_ys())
      if duplicates:
        debug(f"Duplicate support on rows {sorted(duplicates)}: "
              f"{[rows[i].card for i in sorted(duplicates)]}")
      usable = [row for index, row in enumerate(rows) if index not in duplicates]

      row, target, score = find_best(usable, targets, self.cfg.borrow_match_threshold)
      if row:
        info(f"Borrowing {target!r} - matched {row.card!r} ({score:.3f}) from {row.friend!r}.")
        device_action.click((BORROW_ROW_X, row.center_y))
        return True

      device_action.flush_screenshot_cache()
      device_action.swipe(BORROW_SCROLL_FROM, BORROW_SCROLL_TO)
      # Only long enough for the glide to start; the settle check does the rest.
      sleep(0.3)
    return False

  def do_final_confirmation(self, screen):
    """Load the saved agenda first if asked, then start the run."""
    if self.cfg.use_agenda and not self.agenda_loaded:
      if device_action.locate(EDIT_AGENDA_BUTTON, confidence=MATCH_THRESHOLD):
        info("Loading the saved agenda before starting.")
        device_action.locate_and_click(EDIT_AGENDA_BUTTON, confidence=MATCH_THRESHOLD)
        return
      # Not finding Edit must not become a loop back to this same screen.
      warning("Edit button not found; starting without loading an agenda.")
      self.agenda_loaded = True

    info("Starting the run.")
    device_action.locate_and_click(START_BUTTON, confidence=MATCH_THRESHOLD)

  def do_agenda(self, screen):
    """Open the saved agendas, or close once one has been loaded."""
    if self.agenda_loaded:
      info("Agenda loaded, closing the dialog.")
      device_action.locate_and_click(CLOSE_BUTTON, confidence=MATCH_THRESHOLD)
    else:
      device_action.locate_and_click(MY_AGENDAS_BUTTON, confidence=MATCH_THRESHOLD)

  def do_my_agendas(self, screen):
    """Load the first saved agenda.

    Load List repeats once per saved agenda; locate() returns the topmost
    match, which is the first entry in the list.
    """
    if device_action.locate_and_click(LOAD_LIST_BUTTON, confidence=MATCH_THRESHOLD):
      self.agenda_loaded = True
      info("Loaded the first saved agenda.")
      return
    warning("No Load List button found; closing without loading an agenda.")
    self.agenda_loaded = True
    device_action.locate_and_click(CLOSE_BUTTON, confidence=MATCH_THRESHOLD)

  def do_complete_career(self, screen):
    """Spend skill points while any remain, then finish the career."""
    if not self.skills_done and self.skill_visits < self.cfg.max_skill_visits:
      if device_action.locate(SKILLS_BUTTON, confidence=MATCH_THRESHOLD):
        self.skill_visits += 1
        info(f"Opening the skill screen (visit {self.skill_visits}).")
        device_action.locate_and_click(SKILLS_BUTTON, confidence=MATCH_THRESHOLD)
        return
    info("Done buying skills, completing the career.")
    device_action.locate_and_click(screen.identify, confidence=MATCH_THRESHOLD)

  def do_buy_skills(self, screen):
    """Hand off to upstream's buy_skill(), which works on this screen as-is.

    It opens by looking for the career lobby's skills button, which is absent
    here (0.539), so that lookup harmlessly finds nothing before it starts
    scanning. init_skill_py() resets its turn gate, which paces buying during
    a career and is meaningless once the career is over.
    """
    if not core_config.IS_AUTO_BUY_SKILL:
      warning('"Auto Buy Skills" is off in the Skills settings, so nothing will be '
              "bought. Turn it on to have the autopilot spend skill points.")
      self.leave_skill_screen()
      return

    sp = self.read_skill_points()
    if sp < 0:
      warning("Could not read the skill point total; leaving the skill screen.")
      self.leave_skill_screen()
      return

    if not self.list_pass_done:
      info(f"Skill points: {sp} (threshold {core_config.SKILL_PTS_CHECK}, "
           f"{len(core_config.SKILL_LIST)} skill(s) on the buy list).")
      init_skill_py()
      if buy_skill({"current_stats": {"sp": sp}}, core_config.SKILL_CHECK_TURNS) is False:
        warning(f"Skill buying declined: {sp} points against a "
                f"{core_config.SKILL_PTS_CHECK} threshold. Not returning here this career.")
        self.leave_skill_screen()
        return
      # buy_skill backs out on its way through, so the next visit reopens the
      # screen at the top of the list - no scrolling back up needed.
      self.list_pass_done = True
      if not self.cfg.buy_leftover_skills:
        self.skills_done = True
      return

    info(f"List exhausted with {sp} points left, spending them on whatever is affordable.")
    if self.buy_any_affordable_skills() == 0:
      info("Nothing else affordable.")
      self.skills_done = True
      device_action.locate_and_click("assets/buttons/back_btn.png",
                                     region_ltrb=constants.SCREEN_BOTTOM_BBOX)

  def buy_any_affordable_skills(self) -> int:
    """Buy every affordable skill, ignoring the configured list.

    No arithmetic needed: the game greys out what the remaining points cannot
    cover, and compare_brightness reads that, so clicking every icon that is
    still lit and rescanning converges on its own.
    """
    bought = 0
    x1, y1 = constants.SCROLLING_SKILL_SCREEN_BBOX[:2]

    for _pass in range(MAX_LEFTOVER_PASSES):
      before = device_action.screenshot(region_ltrb=constants.SCROLLING_SKILL_SCREEN_BBOX)
      for x, y, w, h in device_action.match_template("assets/icons/buy_skill.png",
                                                     before, threshold=0.9):
        wx, wy = x + x1, y + y1
        icon = device_action.screenshot(region_xywh=(wx, wy, w, h))
        if compare_brightness(template_path="assets/icons/buy_skill.png", other=icon,
                              brightness_diff_threshold=0.20):
          device_action.click(target=(wx + 5, wy + 5), duration=0.15)
          bought += 1

      sleep(0.5)
      device_action.swipe(constants.SKILL_SCROLL_BOTTOM_MOUSE_POS,
                          constants.SKILL_SCROLL_TOP_MOUSE_POS)
      # Tap to kill the fling, same as upstream's skill scrolling does.
      device_action.click(constants.SKILL_SCROLL_TOP_MOUSE_POS, duration=0)
      sleep(0.25)
      after = device_action.screenshot(region_ltrb=constants.SCROLLING_SKILL_SCREEN_BBOX)
      if are_screenshots_same(before, after, diff_threshold=5):
        break

    if bought:
      info(f"Adding {bought} extra skill(s).")
      device_action.locate_and_click("assets/buttons/confirm_btn.png", min_search_time=3)
      sleep(0.5)
      device_action.locate_and_click("assets/buttons/learn_btn.png", min_search_time=3)
      sleep(0.5)
      device_action.locate_and_click("assets/buttons/close_btn.png", min_search_time=4)
      device_action.locate_and_click("assets/buttons/back_btn.png", min_search_time=3,
                                     region_ltrb=constants.SCREEN_BOTTOM_BBOX)
    return bought

  def leave_skill_screen(self) -> None:
    """Back out of the Learn screen and stop revisiting it this career."""
    self.skills_done = True
    device_action.locate_and_click("assets/buttons/back_btn.png",
                                   region_ltrb=constants.SCREEN_BOTTOM_BBOX)

  # --- one tick -------------------------------------------------------
  def step(self) -> str:
    """Act once. Returns "acted", "settling" or "idle"."""
    device_action.flush_screenshot_cache()
    screen, _, status = self.identify_settled()

    if status == "settling":
      self.settling_streak += 1
      # Two reads never agreeing is not a transition, it is a screen whose
      # animation keeps changing what matches. Waiting quietly forever is the
      # worst outcome, so give it a few tries and then act on what we last saw.
      if self.settling_streak < FORCE_ACT_AFTER or screen is None:
        return "settling"
      warning(f"Screen never settled after {self.settling_streak} checks; "
              f"acting on {screen.name} anyway. Run with --debug for detail.")
    self.settling_streak = 0

    if screen is None:
      if self.last_screen is not None:
        info("Nothing actionable on screen - waiting (training, loading, or a cutscene).")
        self.last_screen = None
      self.idle_streak += 1
      return "idle"

    self.idle_streak = 0

    if screen.name != self.last_screen:
      info(f"Screen: {screen.name} - {screen.action}")
      self.last_screen = screen.name
      self.repeat_count = 0
    else:
      self.repeat_count += 1
      # Acting on the same screen over and over means the click is landing but
      # doing nothing, or is not landing at all.
      if self.repeat_count % 5 == 0:
        warning(f"Still on {screen.name} after {self.repeat_count} actions - "
                "the click may not be registering.")

    # A career's skill budget resets when its results first appear.
    if screen.name == "training_log":
      self.skill_visits = 0
      self.skills_done = False
      self.list_pass_done = False
    # Home is the start of a fresh cycle, so the next run needs its own agenda.
    if screen.name == "home":
      self.agenda_loaded = False

    if screen.handler:
      getattr(self, f"do_{screen.handler}")(screen)
    else:
      debug(f"Clicking {screen.click} for {screen.name}.")
      if not device_action.locate_and_click(screen.click, confidence=MATCH_THRESHOLD):
        warning(f"Could not find {screen.click} to click on {screen.name}.")

    if screen.name == "career_complete":
      self.runs_completed += 1
      info(f"Run finished. Completed this session: {self.runs_completed}.")

    return "acted"


def run() -> None:
  cfg = auto_config.load()
  core_config.reload_config()

  bot.use_adb = core_config.USE_ADB
  if core_config.DEVICE_ID:
    bot.device_id = core_config.DEVICE_ID
  if not bot.use_adb:
    error("Autopilot supports ADB only. Set use_adb in config.json.")
    return

  # Same shift main.py applies for ADB, which puts GAME_WINDOW_BBOX at
  # (0,0,800,1080) and lines the constants up with the emulator frame.
  constants.adjust_constants_x_coords(offset=-155)
  if not init_adb():
    error("Could not reach the device over ADB.")
    return

  pilot = Autopilot(cfg)
  info(f"Autopilot started. Borrow targets: {cfg.borrow_card_targets or '(none configured)'}")

  # A short unrecognised gap is a screen transition or a load, and resolves in
  # seconds; a sustained one is the 50 minutes of training, where polling hard
  # is pointless. Back off rather than paying the long wait at every transition.
  BRIEF_IDLES = 5

  def rest(seconds: float) -> None:
    """Sleep in slices so a stop request is noticed promptly.

    The long training poll is 20s; sleeping it in one go means the hotkey
    appears dead for that long after being pressed.
    """
    end = time.time() + seconds
    while bot.is_bot_running:
      remaining = end - time.time()
      if remaining <= 0:
        return
      sleep(min(0.5, remaining))

  try:
    while bot.is_bot_running:
      status = pilot.step()
      if status == "acted":
        rest(1.0)
      elif status == "settling":
        rest(0.3)
      else:
        rest(1.0 if pilot.idle_streak <= BRIEF_IDLES else cfg.idle_poll_seconds)
  except BotStopException as e:
    info(f"{e}")
  except KeyboardInterrupt:
    info("Interrupted.")
  finally:
    bot.is_bot_running = False
    info(f"Autopilot stopped after {pilot.runs_completed} completed run(s).")
