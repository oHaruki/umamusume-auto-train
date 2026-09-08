# Autopilot

Runs **Independent Training** back to back, unattended: borrows a support card, starts the
run, buys skills when it ends, returns Home, and starts the next one.

The game trains the uma by itself. This only does the parts it won't.

---

## Two things that fail silently

Get these wrong and the bot starts, does nothing, and never errors. Check them first if
anything seems broken.

**1. The emulator must be exactly 800 x 1080.** Every button it looks for was cut from a
frame at that size, and matching is pixel-exact. At any other resolution nothing matches.

**2. It reads the screen over ADB, not the window.** With ADB debugging off it cannot see
the game at all.

---

## 1. Install MuMu Player

Get it from [mumuplayer.com](https://www.mumuplayer.com/), install Umamusume inside it, and
sign in. Make sure you can start a career by hand first.

## 2. Set the display

**Device Settings → Display**, set Resolution settings to **Custom**:

| | |
| --- | --- |
| Resolution settings | `Custom` |
| Width | `800` |
| Height | `1080` |
| DPI | `240` |

Restart the emulator afterwards.

## 3. Turn on ADB

**Device Settings → Developer options**:

| | |
| --- | --- |
| ADB debug | `Enable local connection` |
| Enable root | `Off` |

Root is not needed.

## 4. Get the bot

It lives on the `autopilot` branch. Cloning without `-b autopilot` gets you the version
without any of this.

```
git clone -b autopilot https://github.com/oHaruki/umamusume-auto-train.git
cd umamusume-auto-train
pip install -r requirements.txt
```

Python 3.10 to 3.13.

## 5. Point it at the emulator

```
py -3.12 main.py
```

Open the address it prints, go to **Set-Up**:

| | |
| --- | --- |
| Use ADB | on |
| Device ID | `127.0.0.1:5555` |

That is MuMu's default. Other emulators use a different port.

## 6. Choose your card and skills

On the **Autopilot** tab:

- **Support cards to borrow** — pick from the card list or type a character name. Add
  several in priority order; the friend list changes every run, so backups mean fewer
  reloads.
- **Auto Buy Skills** — turn on, then choose skills in the Skill List below.
- **Spend Leftover Points** — buys anything else affordable once your list runs out.

Press **Save Changes**.

## 7. Set the game up by hand, once

The bot repeats a run you have already configured. It does not make these choices for you:

- **The trainee** — whoever is selected on Trainee Select is who it trains, every run.
- **The parents** — inheritance must be picked in advance. The bot passes that screen
  through untouched.
- **The scenario** — Scenario Select is a carousel and it presses Next on whatever is
  showing. Leave the right one on screen.
- **The agenda, in the first slot** — if you use *Load Saved Agenda* it always loads the
  top entry under My Agendas.
- **The support deck** — only the borrowed friend slot is filled automatically. Your own
  five come from the saved formation.

One choice it *does* make: while an event is running, Next on Scenario Select opens *Choose
Career Mode*. The bot always picks **Normal Mode** — it will not take a Trainer Aptitude
Test for you.

## 8. Run it

With `main.py` running, press **F2** to start and stop the autopilot. **F1** still runs the
normal training bot; only one of the two runs at a time.

`py -3.12 autopilot_run.py` runs it standalone if you prefer a separate window.

---

## Running two clients at once

Find out what you actually have first — guessing a port is the usual reason a second
client starts and immediately dies with *device not found*:

```
py -3.12 autopilot/tools/devices.py --scan
```

It groups the serials that turn out to be the same emulator (one instance often answers
to both a `127.0.0.1:PORT` and an `emulator-NNNN` form, and listing both would start two
bots fighting over one screen), checks each is 800 x 1080 with the game open, and prints
the line to paste. Ports differ by product: MuMu's first two instances are usually
`127.0.0.1:7555` and `127.0.0.1:16416`, BlueStacks uses 5555 and 5565.

Then list them under **Clients** on the Autopilot tab, or pass them on the command line:

```
py -3.12 autopilot_run.py --device 127.0.0.1:5555 --device 127.0.0.1:7555
```

F2 and F10 start and stop the whole set. Each client gets a process of its own, its own
`logs/<port>/` directory, and a `[port]` tag on every console line so two clients sharing
one window stay readable.

They are separate processes rather than threads because the bot keeps its ADB connection,
its screenshot cache and its OCR reader in module-level state. Sharing that between two
clients would mean frames captured from one emulator being read as the other's.

Every client needs:

- **its own emulator instance**, each at 800 x 1080 with ADB on and its own port. In MuMu
  that is Multi-Instance Manager; other emulators have their own equivalent.
- **roughly a gigabyte of memory and a core**, on top of what the emulator itself wants.
  Two clients on a machine that could only just manage one will make both slower than one
  alone.

### A different card per client

Tick **Own cards** on a client and it gets its own borrow list, in its own priority order,
instead of the shared one below. Everything else - skills, agenda, TP - still comes from
the shared settings, so two accounts chasing different support cards need one tick and one
list each.

In `config.json` that is a client written as an object rather than a bare id:

```json
"devices": [
  "127.0.0.1:5555",
  { "id": "127.0.0.1:7555", "borrow_card_targets": ["Kitasan Black"] }
]
```

Any other setting from the `autopilot` block can go in there too, by hand -
`"use_agenda": false`, say - and that client alone will use it. Each client logs which
settings it is overriding when it starts, so what is actually in force is in
`logs/<port>/log.txt` rather than guesswork.

---

## Buying TP

Off by default, because it spends carats. Turn on **Auto Recover TP** and the bot checks
Home's TP counter before every run: under the threshold it presses the **+** beside the TP
bar, uses **Carats** on the Recover TP list, raises the amount by one, confirms, closes the
receipt and carries on into the run. One press is **10 carats for 30 TP**, and TP above the
cap is banked rather than lost.

It needs four templates cut from your own screen first, because this dialog has none
shipped with the bot:

```
py -3.12 autopilot/tools/cut_template.py --recover-tp
```

That walks through them one at a time, telling you which screen to be on and what to drag a
box around. Until all four exist the feature stays off and says so once in the log.

Check it before letting it loose:

```
py -3.12 autopilot/tools/whereami.py --tp
```

On Home that prints the TP it read; on each of the Recover TP dialogs it prints which one
it thinks is up and what it would press. It never clicks, so you can walk the whole flow by
hand while it watches.

Two things stop it running away: it only ever presses **Use** on the row whose thumbnail
matches Carats, never the topmost row, so a reordered list cannot make it spend a
Handmade Chocolate; and after **Max Refills In A Row** top-ups without TP coming back up it
stops spending until TP recovers on its own.

---

## Settings

| Setting | What it does |
| --- | --- |
| Clients | ADB ids to drive at once, one process each. Empty uses the single Device ID |
| Own cards | Give one client its own borrow list instead of the shared one |
| Support cards to borrow | Priority order; the topmost available one wins. Shared unless a client overrides it |
| Stop If No Card Matches | Stop rather than borrow a card you didn't ask for |
| Scroll Attempts | How far down the borrow list to look |
| List Reloads | Reload the list for a different set of friends when none match |
| Match Strictness | How closely a row must match, 0-1. 0.8 is a good default |
| Load Saved Agenda | Load the first entry under My Agendas before each run |
| Spend Leftover Points | After your skill list, buy anything else affordable |
| Max Skill Visits | Cap on reopening the skill screen per career |
| Idle Poll | How often to check while training runs |
| Wait When Out Of TP | Below the 15 TP a run costs, wait on Home rather than starting |
| Auto Recover TP | Spend carats on TP when Home shows too little for another run |
| Refill Below (TP) | The level that triggers a refill. 30 keeps a run always affordable |
| Refills Per Top-Up | Presses of + per visit. Each is 10 carats for 30 TP |
| Max Refills In A Row | Safety cap; stops spending if TP never comes back up |

## Pace

A run costs **15 TP**, and TP refills at **1 per 10 minutes**, while training takes about
**50 minutes**. So the steady rate is roughly **one run every 2.5 hours**, and it will idle
in between. That is TP, not the bot.

---

## Troubleshooting

**It starts, then does nothing.** Almost always the resolution. Check what the bot sees:

```
py -3.12 autopilot/tools/capture.py --session check
```

The first line reports the frame size. Anything but 800 x 1080 means nothing can match.

**It can't reach the device.** ADB debug is off, the emulator isn't running, or the Device
ID is wrong.

**It stops at the borrow list.** None of your cards were there and *Stop If No Card
Matches* is on. Add more cards, raise *List Reloads*, or turn that setting off.

**It sits on "Nothing actionable on screen".** Normal during training and loading screens.
If it never moves on it has hit a popup with no rule for it — note which screen.

**It didn't buy skills.** *Auto Buy Skills* is off, or nothing on your list was available.
The log says which.

**It waits on Home saying TP is low when it isn't.** The counter is read by OCR from a
fixed crop. `whereami.py --tp` prints what it read; if that is wrong, `HOME_TP_TEXT_LTRB`
in `autopilot/screens.py` needs adjusting for your frame.

**TP recovery does nothing.** Either the four templates are missing - the log says which -
or *Auto Recover TP* is off. `whereami.py --tp` on each dialog says what it would press.

**One client of two is stuck.** They are independent processes, so check
`logs/<port>/log.txt` for that client rather than the shared console.

**A client dies at once with "device not found".** Its ADB id is wrong — that port has
nothing on it. `autopilot/tools/devices.py --scan` lists what is really there. The others
keep running regardless, and the console says which one stopped.

**Anything else.** Run with `--debug` and include the log. `autopilot/tools/whereami.py`
reports what the bot thinks it is looking at without ever clicking, which separates a
seeing problem from a doing one.

---

Built on [umamusume-auto-train](https://github.com/samsulpanjul/umamusume-auto-train).
Global client, English text.
