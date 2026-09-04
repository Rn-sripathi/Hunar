"use client";

import { AlertTriangle } from "lucide-react";

import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import { scoreBand } from "@/lib/types";

const BAND_TEXT = {
  strong: "text-emerald-700 dark:text-emerald-400",
  fair: "text-amber-700 dark:text-amber-400",
  weak: "text-muted-foreground",
  none: "text-muted-foreground",
} as const;

const BAND_BAR = {
  strong: "[&>div]:bg-emerald-500",
  fair: "[&>div]:bg-amber-500",
  weak: "[&>div]:bg-muted-foreground/40",
  none: "",
} as const;

/**
 * A candidate's score, or the reason there isn't one.
 *
 * Disqualification is shown as a distinct state rather than a score of
 * zero, because the two mean different things: zero says "answered
 * badly", disqualified says "failed a hard requirement". Conflating them
 * would hide why someone dropped off the list.
 */
export function ScorePill({
  score,
  disqualified,
  reason,
  className,
}: {
  score?: number | null;
  disqualified?: boolean;
  reason?: string | null;
  className?: string;
}) {
  if (disqualified) {
    return (
      <span
        title={reason ?? "Failed a required condition"}
        className={cn(
          "inline-flex items-center gap-1.5 text-xs font-medium text-red-700 dark:text-red-400",
          className,
        )}
      >
        <AlertTriangle className="size-3.5" />
        Disqualified
      </span>
    );
  }

  if (score === null || score === undefined) {
    return (
      <span className={cn("text-muted-foreground text-xs", className)}>
        Not scored
      </span>
    );
  }

  const band = scoreBand(score);

  return (
    <div className={cn("flex w-full min-w-24 items-center gap-2", className)}>
      <Progress value={score} className={cn("h-1.5 flex-1", BAND_BAR[band])} />
      <span
        className={cn(
          "w-8 text-right text-xs font-semibold tabular-nums",
          BAND_TEXT[band],
        )}
      >
        {Math.round(score)}
      </span>
    </div>
  );
}
