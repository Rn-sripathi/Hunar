"use client";

import { cn } from "@/lib/utils";

/**
 * One visual language for call state, used everywhere.
 *
 * Colour alone never carries the meaning: every badge shows a word, and
 * the in-flight states add motion. That matters because roughly one in
 * twelve men has some colour vision deficiency, and a recruiter scanning
 * fifty rows for failures should not be relying on hue to find them.
 */

type Tone = "neutral" | "progress" | "success" | "warning" | "danger";

interface StatusMeta {
  label: string;
  tone: Tone;
  /** Plain-English explanation, shown on hover and to screen readers. */
  hint: string;
  pulse?: boolean;
}

const STATUS: Record<string, StatusMeta> = {
  NOT_STARTED: {
    label: "Not called",
    tone: "neutral",
    hint: "This candidate has not been called yet.",
  },
  SCHEDULED: {
    label: "Scheduled",
    tone: "neutral",
    hint: "Queued to be called inside the permitted calling hours.",
  },
  INITIATED: {
    label: "Connecting",
    tone: "progress",
    hint: "Placing the call.",
    pulse: true,
  },
  RINGING: {
    label: "Ringing",
    tone: "progress",
    hint: "The phone is ringing.",
    pulse: true,
  },
  IN_PROGRESS: {
    label: "In progress",
    tone: "progress",
    hint: "The interview is happening now.",
    pulse: true,
  },
  COMPLETED: {
    label: "Completed",
    tone: "success",
    hint: "The candidate answered and the interview finished.",
  },
  NOT_CONNECTED: {
    label: "No answer",
    tone: "warning",
    hint: "Nobody picked up. This candidate can be called again.",
  },
  FAILED: {
    label: "Failed",
    tone: "danger",
    hint: "The call could not be placed. See the error for the reason.",
  },
  CANCELLED: {
    label: "Cancelled",
    tone: "neutral",
    hint: "The call was cancelled.",
  },
};

const TONES: Record<Tone, string> = {
  neutral: "bg-muted text-muted-foreground border-transparent",
  progress:
    "bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-950/50 dark:text-blue-300 dark:border-blue-900",
  success:
    "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/50 dark:text-emerald-300 dark:border-emerald-900",
  warning:
    "bg-amber-50 text-amber-800 border-amber-200 dark:bg-amber-950/50 dark:text-amber-300 dark:border-amber-900",
  danger:
    "bg-red-50 text-red-700 border-red-200 dark:bg-red-950/50 dark:text-red-300 dark:border-red-900",
};

const DOTS: Record<Tone, string> = {
  neutral: "bg-muted-foreground/50",
  progress: "bg-blue-500",
  success: "bg-emerald-500",
  warning: "bg-amber-500",
  danger: "bg-red-500",
};

export function StatusBadge({
  status,
  className,
}: {
  status: string;
  className?: string;
}) {
  const meta = STATUS[status] ?? {
    label: status.replaceAll("_", " ").toLowerCase(),
    tone: "neutral" as const,
    hint: "Unrecognised status reported by the voice provider.",
  };

  return (
    <span
      title={meta.hint}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        TONES[meta.tone],
        className,
      )}
    >
      <span className="relative flex size-1.5">
        {meta.pulse && (
          <span
            className={cn(
              "absolute inline-flex size-full animate-ping rounded-full opacity-70",
              DOTS[meta.tone],
            )}
            // Decorative only; the label already carries the meaning.
            aria-hidden
          />
        )}
        <span
          className={cn(
            "relative inline-flex size-1.5 rounded-full",
            DOTS[meta.tone],
          )}
        />
      </span>
      {meta.label}
      <span className="sr-only"> — {meta.hint}</span>
    </span>
  );
}

export function statusLabel(status: string): string {
  return STATUS[status]?.label ?? status;
}
