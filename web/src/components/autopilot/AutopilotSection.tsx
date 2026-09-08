import { useState } from "react";
import { Bot, X } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import type { Config, UpdateConfigType } from "@/types";
import type { Autopilot, DeviceEntry } from "@/types/autopilot.type";
import { Input } from "../ui/input";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import Tooltips from "@/components/_c/Tooltips";
import CardTargets, { type SupportCardEntry } from "./_c/CardTargets";

type Props = {
  config: Config;
  updateConfig: UpdateConfigType;
};

type EventsPayload = {
  supportCardArraySchema?: { supportCards?: SupportCardEntry[] };
};

// A client is stored as a bare id until it needs settings of its own, at which
// point it becomes an object carrying them. Reading through these two helpers
// means the rest of the component never has to care which form it is in.
const deviceId = (entry: DeviceEntry) =>
  typeof entry === "string" ? entry : entry.id;

const ownCards = (entry: DeviceEntry) =>
  typeof entry === "string" ? undefined : entry.borrow_card_targets;

// Used when config.json predates the autopilot block.
const FALLBACK: Autopilot = {
  devices: [],
  borrow_card_targets: [],
  borrow_required: true,
  borrow_max_scrolls: 8,
  borrow_max_reloads: 3,
  borrow_match_threshold: 0.8,
  use_agenda: false,
  buy_leftover_skills: false,
  max_skill_visits: 5,
  wait_when_out_of_tp: true,
  idle_poll_seconds: 20,
  auto_recover_tp: false,
  tp_min: 30,
  tp_recover_uses: 1,
  tp_recover_max_attempts: 3,
};

export default function AutopilotSection({ config, updateConfig }: Props) {
  const autopilot = { ...FALLBACK, ...(config.autopilot ?? {}) };
  const [deviceDraft, setDeviceDraft] = useState("");

  // Same key and URL as the Events tab, so the 1.8MB payload is fetched once.
  const { data: events } = useQuery<EventsPayload>({
    queryKey: ["events"],
    queryFn: async () => {
      const res = await fetch("/data/events.json");
      if (!res.ok) throw new Error("Failed to fetch events");
      return res.json();
    },
    staleTime: 10 * 60 * 1000,
  });
  const supportCards = events?.supportCardArraySchema?.supportCards ?? [];

  const set = (patch: Partial<Autopilot>) =>
    updateConfig("autopilot", { ...autopilot, ...patch });

  const devices: DeviceEntry[] = autopilot.devices ?? [];
  const ids = devices.map(deviceId);

  const setDevice = (index: number, entry: DeviceEntry) => {
    const next = [...devices];
    next[index] = entry;
    set({ devices: next });
  };

  // Giving a client its own list promotes it from a bare id to an object;
  // clearing the list demotes it back, so a config only carries the shape it
  // actually needs.
  const setDeviceCards = (index: number, cards: string[] | undefined) => {
    const id = deviceId(devices[index]);
    setDevice(index, cards === undefined ? id : { id, borrow_card_targets: cards });
  };

  // Several at once, however they were pasted: commas, spaces or one per line.
  // Adding two emulators one at a time is the common case and there is no
  // reason to make it two round trips.
  const addDevice = () => {
    const added = deviceDraft
      .split(/[\s,;]+/)
      .map((d) => d.trim())
      .filter((d) => d && !ids.includes(d));
    if (!added.length) return;
    set({ devices: [...devices, ...new Set(added)] });
    setDeviceDraft("");
  };

  const removeDevice = (index: number) =>
    set({ devices: devices.filter((_d, i) => i !== index) });

  return (
    <div className="section-card">
      <h2 className="text-3xl font-semibold mb-4 flex items-center gap-3">
        <Bot className="text-primary" />
        Autopilot
      </h2>

      <p className="text-sm text-muted-foreground mb-4">
        Runs Independent Training back to back: borrows a support card, starts the run,
        buys skills when it ends, then starts the next one. Press{" "}
        <kbd className="px-1 py-0.5 rounded bg-muted font-mono">F2</kbd> to start and stop
        it, or run{" "}
        <code className="px-1 py-0.5 rounded bg-muted">py -3.12 autopilot_run.py</code>{" "}
        separately. What it buys at the end of a run is set in the two Skill sections
        below.
      </p>

      <p className="text-lg font-medium mb-1">Clients</p>
      <p className="text-sm text-muted-foreground mb-3">
        ADB ids of the emulators to drive, one autopilot each, all at once. Add them
        together separated by commas or spaces, or one at a time. Leave empty to drive the
        single Device ID from the Set-Up tab. Every client gets its own window into{" "}
        <code className="px-1 py-0.5 rounded bg-muted">logs/&lt;port&gt;/</code>. They share
        the settings on this page, except that each can borrow its own card.
      </p>

      <div className="flex gap-2 mb-2">
        <Input
          placeholder="127.0.0.1:7555, 127.0.0.1:16416"
          value={deviceDraft}
          onChange={(e) => setDeviceDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addDevice();
            }
          }}
        />
        <Button type="button" onClick={addDevice} disabled={!deviceDraft.trim()}>
          Add
        </Button>
      </div>

      <p className="text-xs text-muted-foreground mb-3">
        Each one needs its own emulator instance at 800 x 1080 with ADB on, and each runs
        a full copy of the bot &mdash; roughly a gigabyte of memory and a core apiece.
        Every instance has its own port: MuMu&rsquo;s first two are usually{" "}
        <code className="px-1 py-0.5 rounded bg-muted">127.0.0.1:7555</code> and{" "}
        <code className="px-1 py-0.5 rounded bg-muted">127.0.0.1:16416</code>, while
        BlueStacks uses 5555 and 5565. Run{" "}
        <code className="px-1 py-0.5 rounded bg-muted">py -3.12 autopilot/tools/devices.py</code>{" "}
        to list what is actually reachable.
      </p>

      <div className="flex flex-col gap-2 mb-6">
        {devices.length === 0 && (
          <p className="text-sm text-muted-foreground italic">
            No clients listed. The Device ID on the Set-Up tab is used on its own.
          </p>
        )}
        {devices.map((entry, index) => {
          const id = deviceId(entry);
          const cards = ownCards(entry);
          return (
            <div key={id} className="px-3 py-2 border-2 border-border rounded-lg">
              <div className="flex items-center gap-3">
                <span className="text-sm text-muted-foreground w-6">{index + 1}.</span>
                <span className="flex-1 font-mono text-sm">{id}</span>
                <label className="flex items-center gap-2 text-sm cursor-pointer">
                  <Checkbox
                    id={`own-cards-${id}`}
                    checked={cards !== undefined}
                    onCheckedChange={() =>
                      setDeviceCards(index, cards === undefined ? [] : undefined)
                    }
                  />
                  Own cards
                </label>
                <button
                  type="button"
                  className="px-2 text-muted-foreground hover:text-destructive cursor-pointer"
                  onClick={() => removeDevice(index)}
                  aria-label={`Remove ${id}`}
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              {cards !== undefined && (
                <div className="mt-3 pl-9">
                  <p className="text-xs text-muted-foreground mb-2">
                    Borrowed on this client instead of the shared list below. Everything
                    else still comes from the settings on this page.
                  </p>
                  <CardTargets
                    value={cards}
                    onChange={(next) => setDeviceCards(index, next)}
                    supportCards={supportCards}
                    emptyText="Nothing set, so this client stops at the borrow list rather than picking something unintended."
                    compact
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>

      <p className="text-lg font-medium mb-1">Support cards to borrow</p>
      <p className="text-sm text-muted-foreground mb-3">
        Used by every client that does not have its own list above. Pick from the card
        list, or type a name. Matching reads the borrow list as text and ignores
        punctuation, so
        <span className="whitespace-nowrap"> &ldquo;[Q&ne;0] Agnes Tachyon&rdquo;</span> and
        &ldquo;Agnes Tachyon&rdquo; both work. Topmost entry wins when several are available.
      </p>

      <CardTargets
        value={autopilot.borrow_card_targets}
        onChange={(next) => set({ borrow_card_targets: next })}
        supportCards={supportCards}
        emptyText="No cards yet. With none set the bot stops at the borrow list rather than picking something unintended."
      />

      <p className="text-xs text-muted-foreground mt-3 mb-6">
        The card list carries character names only, not card titles, and it lags behind new
        releases &mdash; SSR Agnes Tachyon is missing from it, for instance. Type the name
        by hand for anything absent, or to tell two cards of the same character apart.
      </p>

      <div className="grid lg:grid-cols-3 grid-cols-1 gap-2">
        <label className="uma-label col-span-3">
          <Checkbox
            id="borrow-required"
            checked={autopilot.borrow_required}
            onCheckedChange={() => set({ borrow_required: !autopilot.borrow_required })}
          />
          Stop If No Card Matches
          <Tooltips>
            On means the bot stops rather than borrowing a card you did not ask for. Off
            means it closes the list and runs without a borrowed card.
          </Tooltips>
        </label>

        <label className="uma-label">
          <span>Scroll Attempts</span>
          <Tooltips>How far down the borrow list to look before giving up.</Tooltips>
          <Input
            className="w-18"
            type="number"
            min={1}
            value={autopilot.borrow_max_scrolls}
            onChange={(e) => set({ borrow_max_scrolls: e.target.valueAsNumber })}
          />
        </label>

        <label className="uma-label">
          <span>List Reloads</span>
          <Tooltips>
            When none of your cards are in the list, press its reload button to pull a
            different set of friends and look again. 0 disables it.
          </Tooltips>
          <Input
            className="w-18"
            type="number"
            min={0}
            value={autopilot.borrow_max_reloads}
            onChange={(e) => set({ borrow_max_reloads: e.target.valueAsNumber })}
          />
        </label>

        <label className="uma-label">
          <span>Match Strictness</span>
          <Tooltips>
            How closely a row must match, 0 to 1. Lower tolerates worse text recognition
            but risks borrowing the wrong card. 0.8 is a good default.
          </Tooltips>
          <Input
            className="w-20"
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={autopilot.borrow_match_threshold}
            onChange={(e) => set({ borrow_match_threshold: e.target.valueAsNumber })}
          />
        </label>

        <label className="uma-label col-span-3">
          <Checkbox
            id="use-agenda"
            checked={autopilot.use_agenda}
            onCheckedChange={() => set({ use_agenda: !autopilot.use_agenda })}
          />
          Load Saved Agenda
          <Tooltips>
            Before starting each run, opens Edit next to Agenda, goes to My Agendas and
            loads the first saved list. Off means whatever agenda is already set is used.
          </Tooltips>
        </label>

        <label className="uma-label col-span-3">
          <Checkbox
            id="buy-leftover-skills"
            checked={autopilot.buy_leftover_skills}
            onCheckedChange={() =>
              set({ buy_leftover_skills: !autopilot.buy_leftover_skills })
            }
          />
          Spend Leftover Points
          <Tooltips>
            After buying everything on your skill list, reopen the skill screen and buy
            any affordable skill until the points run out. The game greys out what you
            cannot afford, so it stops on its own.
          </Tooltips>
        </label>

        <label className="uma-label">
          <span>Max Skill Visits</span>
          <Tooltips>
            How many times per career the bot may open the skill screen. Caps the loop
            between Complete Career and Learn.
          </Tooltips>
          <Input
            className="w-18"
            type="number"
            min={0}
            value={autopilot.max_skill_visits}
            onChange={(e) => set({ max_skill_visits: e.target.valueAsNumber })}
          />
        </label>

        <label className="uma-label">
          <span>Idle Poll (seconds)</span>
          <Tooltips>
            How often to check the screen while training is running. Training takes about
            50 minutes, so there is no point checking often.
          </Tooltips>
          <Input
            className="w-20"
            type="number"
            min={1}
            value={autopilot.idle_poll_seconds}
            onChange={(e) => set({ idle_poll_seconds: e.target.valueAsNumber })}
          />
        </label>

        <label className="uma-label col-span-3">
          <Checkbox
            id="auto-recover-tp"
            checked={autopilot.auto_recover_tp}
            onCheckedChange={() => set({ auto_recover_tp: !autopilot.auto_recover_tp })}
          />
          Auto Recover TP
          <Tooltips>
            When Home shows less TP than the threshold below, press + beside the TP bar
            and spend carats on a refill before starting the next run. This costs real
            carats every time &mdash; 10 for 30 TP. Off by default.
          </Tooltips>
        </label>

        <label className="uma-label">
          <span>Refill Below (TP)</span>
          <Tooltips>
            Refill when Home shows less TP than this. A run costs 15 and one refill gives
            30, so 30 keeps a run always affordable.
          </Tooltips>
          <Input
            className="w-20"
            type="number"
            min={1}
            value={autopilot.tp_min}
            onChange={(e) => set({ tp_min: e.target.valueAsNumber })}
          />
        </label>

        <label className="uma-label">
          <span>Refills Per Top-Up</span>
          <Tooltips>
            How many times to press + in the recovery dialog. Each press is 10 carats for
            30 TP, and TP over the cap is kept rather than lost.
          </Tooltips>
          <Input
            className="w-20"
            type="number"
            min={1}
            value={autopilot.tp_recover_uses}
            onChange={(e) => set({ tp_recover_uses: e.target.valueAsNumber })}
          />
        </label>

        <label className="uma-label">
          <span>Max Refills In A Row</span>
          <Tooltips>
            Safety cap. If TP has not come back up after this many refills something is
            wrong with the dialog, so the bot stops spending rather than buying in a loop.
            Resets as soon as TP reads healthy.
          </Tooltips>
          <Input
            className="w-20"
            type="number"
            min={1}
            value={autopilot.tp_recover_max_attempts}
            onChange={(e) => set({ tp_recover_max_attempts: e.target.valueAsNumber })}
          />
        </label>

        <label className="uma-label col-span-3">
          <Checkbox
            id="wait-out-of-tp"
            checked={autopilot.wait_when_out_of_tp}
            onCheckedChange={() =>
              set({ wait_when_out_of_tp: !autopilot.wait_when_out_of_tp })
            }
          />
          Wait When Out Of TP
          <Tooltips>
            A run costs 15 TP and TP regenerates at 1 per 10 minutes, so sustained pace is
            about one run every 2.5 hours. On means wait for TP rather than stopping.
          </Tooltips>
        </label>
      </div>
    </div>
  );
}
