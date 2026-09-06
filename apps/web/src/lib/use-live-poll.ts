"use client";

import { useCallback, useRef, useState } from "react";

/**
 * Bounded polling for anything that updates itself while calls run.
 *
 * Polling exists because the voice provider only pushes a webhook once a
 * call has already finished. Ringing, in-progress and the extracted
 * answers all have to be asked for.
 *
 * It is bounded because an unbounded poll is a slow leak: a browser tab
 * left open overnight on a role whose call is wedged would keep hitting
 * the API and, through it, the provider, forever. After the cap the loop
 * stops and says so, with a button to start it again. Giving up loudly
 * beats either polling forever or pretending the data is current.
 *
 * The clock starts when work begins and resets when it ends, so a role
 * that is called repeatedly gets a fresh budget each time rather than
 * exhausting one shared allowance.
 */

/** Fast enough to feel live, slow enough not to hammer the API. */
export const POLL_INTERVAL_MS = 3_000;

/**
 * How long to keep polling one stretch of activity.
 *
 * Ten minutes comfortably covers a screening call plus the transcription
 * that follows. Anything still unresolved after that is stuck, not slow.
 */
export const MAX_POLL_DURATION_MS = 10 * 60 * 1_000;

export interface LivePoll<TData> {
  /** Pass straight to TanStack Query's `refetchInterval`. */
  refetchInterval: (query: { state: { data?: TData } }) => number | false;
  /** True when the cap was reached while work was still outstanding. */
  gaveUp: boolean;
  /** Clear the cap and resume, for the "keep watching" button. */
  resume: () => void;
}

export function useLivePoll<TData>(
  isActive: (data: TData | undefined) => boolean,
  options: { intervalMs?: number; maxDurationMs?: number } = {},
): LivePoll<TData> {
  const intervalMs = options.intervalMs ?? POLL_INTERVAL_MS;
  const maxDurationMs = options.maxDurationMs ?? MAX_POLL_DURATION_MS;

  const startedAt = useRef<number | null>(null);
  const [gaveUp, setGaveUp] = useState(false);

  const refetchInterval = useCallback(
    (query: { state: { data?: TData } }) => {
      if (!isActive(query.state.data)) {
        // Work finished. Release the budget so the next round starts fresh.
        startedAt.current = null;
        if (gaveUp) setGaveUp(false);
        return false as const;
      }

      if (gaveUp) return false as const;

      startedAt.current ??= Date.now();
      if (Date.now() - startedAt.current > maxDurationMs) {
        setGaveUp(true);
        return false as const;
      }
      return intervalMs;
    },
    [isActive, intervalMs, maxDurationMs, gaveUp],
  );

  const resume = useCallback(() => {
    startedAt.current = Date.now();
    setGaveUp(false);
  }, []);

  return { refetchInterval, gaveUp, resume };
}
