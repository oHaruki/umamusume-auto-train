import { useState } from "react";
import { X } from "lucide-react";
import { Input } from "../../ui/input";
import { Button } from "../../ui/button";
import EventDialog from "../../event/_c/EventDialog";

export type SupportCardEntry = {
  id: string;
  name: string;
  image_url: string;
  rarity: string;
  type: string;
};

// The picker lists cards as "Matikanetannhauser (SSR) (WIT)", but the game's
// borrow list shows "[Card Title] Matikanetannhauser". Keeping the rarity and
// type suffix would drag the match score down to roughly 0.62 and miss.
const toCharacterName = (pickerName: string) => pickerName.split("(")[0].trim();

type Props = {
  value: string[];
  onChange: (next: string[]) => void;
  supportCards: SupportCardEntry[];
  // Distinguishes the shared editor from each client's own, both in the empty
  // state and in the placeholder.
  emptyText: string;
  compact?: boolean;
};

/**
 * The borrow-target editor: card picker, free text, and a reorderable list.
 *
 * Pulled out of AutopilotSection so the shared list and each client's own list
 * are the same control rather than two that drift apart - the order is the
 * priority, so the up/down arrows have to work everywhere it appears.
 */
export default function CardTargets({
  value,
  onChange,
  supportCards,
  emptyText,
  compact = false,
}: Props) {
  const [draft, setDraft] = useState("");

  const add = (name: string) => {
    const trimmed = name.trim();
    if (!trimmed || value.includes(trimmed)) return;
    onChange([...value, trimmed]);
  };

  const addDraft = () => {
    add(draft);
    setDraft("");
  };

  const move = (index: number, delta: number) => {
    const next = [...value];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  };

  return (
    <>
      <div className="flex gap-2 mb-2">
        <EventDialog
          button={compact ? "Pick" : "Select Support Card"}
          data={supportCards}
          setSelected={(v) => {
            if (typeof v === "string") add(toCharacterName(v));
          }}
        />
        <Input
          placeholder="Or type a name, e.g. Kitasan Black"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addDraft();
            }
          }}
        />
        <Button type="button" onClick={addDraft} disabled={!draft.trim()}>
          Add
        </Button>
      </div>

      <div className="flex flex-col gap-2">
        {value.length === 0 && (
          <p className="text-sm text-muted-foreground italic">{emptyText}</p>
        )}
        {value.map((target, index) => (
          <div
            key={target}
            className={`border-2 border-border rounded-lg flex items-center gap-3 ${
              compact ? "px-2 py-1 text-sm" : "px-3 py-2"
            }`}
          >
            <span className="text-sm text-muted-foreground w-6">{index + 1}.</span>
            <span className="flex-1">{target}</span>
            <button
              type="button"
              className="px-2 text-muted-foreground hover:text-foreground disabled:opacity-30 cursor-pointer"
              onClick={() => move(index, -1)}
              disabled={index === 0}
              aria-label="Move up"
            >
              &uarr;
            </button>
            <button
              type="button"
              className="px-2 text-muted-foreground hover:text-foreground disabled:opacity-30 cursor-pointer"
              onClick={() => move(index, 1)}
              disabled={index === value.length - 1}
              aria-label="Move down"
            >
              &darr;
            </button>
            <button
              type="button"
              className="px-2 text-muted-foreground hover:text-destructive cursor-pointer"
              onClick={() => onChange(value.filter((t) => t !== target))}
              aria-label={`Remove ${target}`}
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        ))}
      </div>
    </>
  );
}
