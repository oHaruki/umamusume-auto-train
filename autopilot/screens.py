"""The screen table for the Independent Training cycle.

Order matters: entries are tried top to bottom and the first match wins, so
specific screens come before the generic catch-alls. Several screens share a
button - close_btn appears on Borrow Card, Skills Learned and Umamusume
Details; next_btn on Trainee Select and all three result screens - so the
ambiguous ones are identified by a header crop first, and whatever is left
falls through to a generic rule whose action is the same anyway.

Never match against all of assets/: some templates there are tiny enough to
correlate with almost anything (assets/ui/energy_bar_right_end_part.png scores
0.87-0.98 on every screen tested). Only the templates named here are used.
"""

from __future__ import annotations

from dataclasses import dataclass

AUTO = "assets/autopilot"
BTN = "assets/buttons"


@dataclass(frozen=True)
class Screen:
  name: str
  # Template proving we are on this screen.
  identify: str
  # What the bot does here, for logs and the dry-run probe.
  action: str
  # Template to click. None means `handler` decides.
  click: str | None = None
  # Name of a special routine in the loop (borrowing, skill buying, branches).
  handler: str | None = None
  # Restricts where `identify` may match. Only needed where a shared button
  # would otherwise make two different screens look alike.
  identify_region: tuple[int, int, int, int] | None = None


SCREENS: tuple[Screen, ...] = (
  # --- start of a run -------------------------------------------------
  Screen("borrow_card", f"{AUTO}/borrow_card_header.png",
         "find and tap the wanted support card", handler="borrow"),
  # Sits between Home and Trainee Select. Its header bar and its Next button
  # are both in the same place as Trainee Select's, so the header text is the
  # only thing telling them apart: this template scores 0.663 on Trainee
  # Select and theirs scores 0.656 here.
  # Clicks the below-ribbon crop, not the whole Next button: while an event is
  # running the game hangs an "Event Underway" banner over the button's top
  # edge, and that is enough to sink the full template from 0.953 to 0.59 -
  # under the threshold, so the bot saw the screen and then sat there unable to
  # press anything. The crop starts below the banner and scores the same either
  # way. The header is unaffected, so identification never needed changing.
  Screen("scenario_select", f"{AUTO}/scenario_select_header.png",
         "keep the shown scenario and continue",
         handler="scenario_select"),
  # While an event is running, Next opens this instead of going straight on to
  # Trainee Select. The generic "confirmation" rule at the bottom would already
  # clear it - Normal Mode is highlighted when the popup opens, so pressing
  # Confirm happens to do the right thing - but only for as long as that stays
  # true. Whatever is highlighted is what gets started, and an Aptitude Test
  # run costs the same 15 TP as the one that was actually wanted, so the mode
  # is picked here rather than inherited.
  Screen("career_mode", f"{AUTO}/career_mode_header.png",
         "choose Normal Mode, then confirm", handler="career_mode"),
  Screen("trainee_select", f"{AUTO}/trainee_select_header.png",
         "keep the selected trainee and continue", click=f"{BTN}/next_btn.png"),
  Screen("support_formation", f"{AUTO}/start_career_btn.png",
         "open the borrow list if the slot is empty, else start",
         handler="formation"),
  # Must rank above "agenda": the Agenda header template also scores 0.940 on
  # this dialog, while this one is unique at 1.000.
  Screen("my_agendas", f"{AUTO}/my_agendas_header.png",
         "load the first saved agenda", handler="my_agendas"),
  Screen("agenda", f"{AUTO}/agenda_header.png",
         "open the saved agendas, or close once one is loaded", handler="agenda"),
  Screen("final_confirmation", f"{AUTO}/start_btn.png",
         "load the agenda if wanted, then spend TP and begin training",
         handler="final_confirmation"),
  Screen("home", f"{AUTO}/career_btn.png",
         "open Career", click=f"{AUTO}/career_btn.png"),

  # --- end of a run ---------------------------------------------------
  Screen("training_log", f"{BTN}/ok_btn.png",
         "dismiss the training results", click=f"{BTN}/ok_btn.png"),
  Screen("complete_career_confirm", f"{AUTO}/finish_btn.png",
         "confirm finishing the playthrough", click=f"{AUTO}/finish_btn.png"),
  Screen("complete_career", f"{BTN}/complete_career_btn.png",
         "buy skills while points remain, else complete the career",
         handler="complete_career"),
  # Identified by the Skill Points bar, NOT by its Confirm button: the Sparks
  # screen and the Sparks confirmation carry the same Confirm, and treating
  # either as Learn would click beside "Reroll Sparks (Consumes 30 TP)".
  Screen("learn", f"{AUTO}/skill_points_label.png",
         "pick skills and confirm", handler="buy_skills"),
  Screen("learn_confirmation", f"{BTN}/learn_btn.png",
         "confirm learning the chosen skills", click=f"{BTN}/learn_btn.png"),
  Screen("sparks", f"{AUTO}/reroll_sparks_btn.png",
         "accept the inheritance sparks without rerolling",
         click=f"{BTN}/confirm_btn.png"),
  # Appears sometimes after a career, offering to follow whoever lent the
  # support card. Declined. Identified by its header rather than its Cancel
  # button, which is shared with Final Confirmation and the two confirmation
  # dialogs - a blanket "press Cancel" rule would cancel the run itself.
  Screen("follow_trainer", f"{AUTO}/follow_trainer_header.png",
         "decline following the trainer who lent support",
         click=f"{BTN}/cancel_btn.png"),
  Screen("career_complete", f"{AUTO}/to_home_btn.png",
         "return to Home and close the loop", click=f"{AUTO}/to_home_btn.png"),

  # --- generic fallbacks, same action wherever they appear ------------
  # Reached only after the specific Confirm screens above have been ruled out.
  Screen("confirmation", f"{BTN}/confirm_btn.png",
         "accept a confirmation dialog", click=f"{BTN}/confirm_btn.png"),
  # Deliberately unbounded. Several screens carry a Next button at different
  # heights - Scenario Select ~907, Trainee Select ~879, result screens ~963 -
  # and pressing Next is the right move on all of them. Restricting this rule
  # to the result screens' band stranded the bot on Scenario Select, which has
  # no template of its own yet.
  Screen("result", f"{BTN}/next_btn.png",
         "press Next", click=f"{BTN}/next_btn.png"),
  Screen("dialog", f"{BTN}/close_btn.png",
         "close a dialog", click=f"{BTN}/close_btn.png"),
)

# Template that tells the borrow slot is still empty. Checked by the
# "formation" handler; scores 1.000 empty vs 0.581 filled.
FRIENDS_SLOT_EMPTY = f"{AUTO}/friends_slot_empty.png"

# The Normal Mode row on the Choose Career Mode popup. Cropped to the title
# text on plain white, clear of the border and the selection brackets, so it
# reads the same whether or not the row is the one currently selected.
NORMAL_MODE_LABEL = f"{AUTO}/normal_mode_label.png"

# The Complete Career screen's Skills button, which differs from the career
# lobby's (upstream skills_btn.png scores 0.558 here).
SKILLS_BUTTON = f"{AUTO}/skills_btn_career_complete.png"

START_CAREER_BUTTON = f"{AUTO}/start_career_btn.png"
START_BUTTON = f"{AUTO}/start_btn.png"

# Agenda flow: Final Confirmation -> Edit -> Agenda -> My Agendas ->
# Load List -> back to Agenda, now filled -> Close -> Start.
EDIT_AGENDA_BUTTON = f"{AUTO}/edit_agenda_btn.png"
MY_AGENDAS_BUTTON = f"{AUTO}/my_agendas_btn.png"
# Repeats once per saved agenda; locate() returns the topmost, i.e. the first.
LOAD_LIST_BUTTON = f"{AUTO}/load_list_btn.png"

TP_EVENT_BUTTON = f"{AUTO}/tp_event.png"
NEXT_BUTTON = f"{AUTO}/next_btn_below_ribbon.png"

MATCH_THRESHOLD = 0.85

# --- geometry, in ADB frame coordinates (800x1080) --------------------
# main.py shifts GAME_WINDOW_BBOX by -155 for ADB, giving (0,0,800,1080),
# so these line up with utils/constants.py once that shift is applied.

# Text column of the Borrow Card rows, left of the "Following" badge.
BORROW_LIST_LTRB = (225, 175, 530, 935)
# Where to tap a chosen row, and the swipe that scrolls the list one page.
BORROW_ROW_X = 400
BORROW_SCROLL_FROM = (400, 820)
BORROW_SCROLL_TO = (400, 400)

# Reloads the borrow list with a different set of friends. Not a page control -
# it is the only way to reach candidates the current list never contained.
BORROW_RELOAD_BUTTON = f"{AUTO}/borrow_refresh_btn.png"

# Badge marking a borrow row whose card is already in the deck. Drawn over the
# thumbnail, left of the OCR'd text column, so it has to be matched as an image.
DUPLICATE_BADGE = f"{AUTO}/duplicate_support_badge.png"

# Skill point total on the Learn screen, right of the "Skill Points" bar.
# Verified reading 3258 off a real frame.
SKILL_POINTS_LTRB = (560, 342, 680, 375)

# current TP 
TP_LTRB = (325,31,359,47)
TP_TIME_LTRB = (270,37,305,48)

# easyocr allowlist for the Borrow Card rows. The repo default omits brackets,
# which measurably degrades card titles ([Teio-Oo-Oolll] 0.80 -> 1.00).
BORROW_ALLOWLIST = (
  "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-!.,'#? []()"
)
