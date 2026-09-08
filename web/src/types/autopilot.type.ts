import { z } from "zod";

// A client is either a bare ADB id, or that id plus the settings it wants for
// itself. The bare form is what a config written before per-client settings
// existed contains, and what a client with nothing of its own stays as.
export const DeviceEntrySchema = z.union([
  z.string(),
  z.object({
    id: z.string(),
    borrow_card_targets: z.array(z.string()).optional(),
  }),
]);

export type DeviceEntry = z.infer<typeof DeviceEntrySchema>;

export const AutopilotSchema = z.object({
  // The emulators to drive at once, one client each. Empty falls back to the
  // single Device ID on the Set-Up tab.
  devices: z.array(DeviceEntrySchema).default([]),
  // Support cards to borrow, best first. Written as they appear in game;
  // matching normalises punctuation, so "[Q!=0] Agnes Tachyon" and the real
  // "[Q<>0] Agnes Tachyon" both resolve to the same card.
  borrow_card_targets: z.array(z.string()).default([]),
  // Stop rather than borrow something unintended when no target is found.
  borrow_required: z.boolean().default(true),
  borrow_max_scrolls: z.number().default(8),
  // Reloads the list with a different set of friends when no target is in it.
  borrow_max_reloads: z.number().default(3),
  borrow_match_threshold: z.number().default(0.8),
  // Load a saved race agenda before each run, always the first one listed.
  use_agenda: z.boolean().default(false),
  // Spend leftover points on any affordable skill once the list is exhausted.
  buy_leftover_skills: z.boolean().default(false),
  // Cap on Skills visits per career, so a career cannot loop forever between
  // Complete Career and the Learn screen.
  max_skill_visits: z.number().default(5),
  wait_when_out_of_tp: z.boolean().default(true),
  idle_poll_seconds: z.number().default(20),
  // Spend carats on TP from Home when a run cannot be afforded. Off by
  // default: it costs real currency.
  auto_recover_tp: z.boolean().default(false),
  tp_min: z.number().default(30),
  tp_recover_uses: z.number().default(1),
  tp_recover_max_attempts: z.number().default(3),
});

export type Autopilot = z.infer<typeof AutopilotSchema>;
