"use client";

import { Plus, X } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { SearchFilters } from "@/lib/types";
import { cn } from "@/lib/utils";

const SENIORITIES = [
  "ic",
  "senior",
  "lead",
  "manager",
  "director",
  "vp",
  "cxo",
] as const;

type ChipKey = Extract<
  keyof SearchFilters,
  | "titles"
  | "excluded_titles"
  | "skills_required"
  | "skills_nice"
  | "cities"
  | "industries"
>;

/**
 * A list of values you can add to and remove from.
 *
 * Chips rather than a comma-separated text box because the recruiter is
 * correcting a machine's guess, and removing one wrong title should be a
 * single click rather than careful cursor work inside a sentence.
 */
function ChipList({
  label,
  hint,
  values,
  onChange,
  placeholder,
  tone = "default",
}: {
  label: string;
  hint?: string;
  values: string[];
  onChange: (next: string[]) => void;
  placeholder: string;
  tone?: "default" | "negative";
}) {
  const [draft, setDraft] = useState("");

  const add = () => {
    const value = draft.trim();
    if (!value) return;
    if (
      values.some((existing) => existing.toLowerCase() === value.toLowerCase())
    )
      return setDraft("");
    onChange([...values, value]);
    setDraft("");
  };

  return (
    <div>
      <Label className="text-[13px]">{label}</Label>
      {hint && (
        <p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">
          {hint}
        </p>
      )}

      <div className="mt-2 flex flex-wrap gap-1.5">
        {values.map((value) => (
          <Badge
            key={value}
            variant="secondary"
            className={cn(
              "gap-1 border-transparent py-1 pr-1 pl-2 font-normal",
              tone === "negative" &&
                "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300",
            )}
          >
            {value}
            <button
              type="button"
              aria-label={`Remove ${value}`}
              onClick={() => onChange(values.filter((item) => item !== value))}
              className="hover:bg-foreground/10 rounded-sm p-0.5"
            >
              <X className="size-3" />
            </button>
          </Badge>
        ))}
        {values.length === 0 && (
          <span className="text-muted-foreground text-xs italic">
            Nothing yet
          </span>
        )}
      </div>

      <div className="mt-2 flex gap-1.5">
        <Input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              add();
            }
          }}
          placeholder={placeholder}
          className="h-8 text-sm"
        />
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="size-8 shrink-0"
          onClick={add}
          aria-label={`Add to ${label}`}
        >
          <Plus className="size-3.5" />
        </Button>
      </div>
    </div>
  );
}

/**
 * The filters a job description was read into, open for correction.
 *
 * This component exists because the translation from prose to filters is
 * a guess however it is made, and a guess applied silently is the thing
 * that makes a search feel broken. Showing the filters turns "the
 * provider returned rubbish" into "it searched for the wrong title",
 * which is a problem the recruiter can actually fix.
 *
 * It sits between extraction and search on purpose: extraction is free,
 * the search costs a credit, and this is the gap where a mistake is still
 * cheap.
 */
export function FilterEditor({
  filters,
  onChange,
  disabled,
}: {
  filters: SearchFilters;
  onChange: (next: SearchFilters) => void;
  disabled?: boolean;
}) {
  const set = <K extends keyof SearchFilters>(
    key: K,
    value: SearchFilters[K],
  ) => onChange({ ...filters, [key]: value });

  const chip = (key: ChipKey) => (next: string[]) =>
    set(key, next as SearchFilters[ChipKey]);

  return (
    <fieldset
      disabled={disabled}
      className="grid gap-5 sm:grid-cols-2 disabled:opacity-60"
    >
      <ChipList
        label="Current job titles"
        hint="What these people are called today, which is often not the title you are hiring for."
        values={filters.titles ?? []}
        onChange={chip("titles")}
        placeholder="Backend Engineer"
      />
      <ChipList
        label="Titles to exclude"
        hint="Roles that look like a match on paper but are not."
        values={filters.excluded_titles ?? []}
        onChange={chip("excluded_titles")}
        placeholder="QA Engineer"
        tone="negative"
      />
      <ChipList
        label="Must have"
        hint="Every one of these is required. Fewer is usually better."
        values={filters.skills_required ?? []}
        onChange={chip("skills_required")}
        placeholder="python"
      />
      <ChipList
        label="Nice to have"
        values={filters.skills_nice ?? []}
        onChange={chip("skills_nice")}
        placeholder="kafka"
      />
      <ChipList
        label="Cities"
        values={filters.cities ?? []}
        onChange={chip("cities")}
        placeholder="Bengaluru"
      />
      <ChipList
        label="Industries"
        hint="Only if the person should come from one."
        values={filters.industries ?? []}
        onChange={chip("industries")}
        placeholder="fintech"
      />

      <div>
        <Label className="text-[13px]">Seniority</Label>
        <p className="text-muted-foreground mt-0.5 text-xs">
          One or two adjacent levels works better than a wide net.
        </p>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {SENIORITIES.map((level) => {
            const active = (filters.seniorities ?? []).includes(level);
            return (
              <button
                key={level}
                type="button"
                aria-pressed={active}
                onClick={() =>
                  set(
                    "seniorities",
                    active
                      ? (filters.seniorities ?? []).filter((s) => s !== level)
                      : [...(filters.seniorities ?? []), level],
                  )
                }
                className={cn(
                  "rounded-md border px-2.5 py-1 text-xs font-medium capitalize transition-colors",
                  active
                    ? "bg-primary text-primary-foreground border-transparent"
                    : "text-muted-foreground hover:bg-muted",
                )}
              >
                {level}
              </button>
            );
          })}
        </div>
      </div>

      <div>
        <Label className="text-[13px]">Years of experience</Label>
        <p className="text-muted-foreground mt-0.5 text-xs">
          Leave blank rather than inventing a floor. A minimum nobody stated
          will quietly exclude good people.
        </p>
        <div className="mt-2 flex items-center gap-2">
          <Input
            type="number"
            min={0}
            max={50}
            value={filters.min_years ?? ""}
            onChange={(event) =>
              set(
                "min_years",
                event.target.value === "" ? null : Number(event.target.value),
              )
            }
            placeholder="Min"
            className="h-8 w-24 text-sm"
          />
          <span className="text-muted-foreground text-xs">to</span>
          <Input
            type="number"
            min={0}
            max={50}
            value={filters.max_years ?? ""}
            onChange={(event) =>
              set(
                "max_years",
                event.target.value === "" ? null : Number(event.target.value),
              )
            }
            placeholder="Max"
            className="h-8 w-24 text-sm"
          />
        </div>
      </div>

      <div className="sm:col-span-2">
        <Label className="text-[13px]" htmlFor="role-pitch">
          What the agent will say about the role
        </Label>
        <p className="text-muted-foreground mt-0.5 text-xs">
          One sentence, spoken aloud to a stranger who has never heard of you.
          Concrete beats enthusiastic.
        </p>
        <Input
          id="role-pitch"
          value={filters.role_pitch ?? ""}
          onChange={(event) => set("role_pitch", event.target.value)}
          maxLength={240}
          className="mt-2 text-sm"
          placeholder="a Series B fintech in Bengaluru hiring a senior backend engineer to own their payments ledger"
        />
      </div>
    </fieldset>
  );
}
