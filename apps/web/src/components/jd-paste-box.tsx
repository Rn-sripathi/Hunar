"use client";

import { useMutation } from "@tanstack/react-query";
import { ClipboardPaste, Loader2, TriangleAlert, Wand2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import type { JobDraft } from "@/lib/types";

/**
 * Paste a job description, get the whole form filled in.
 *
 * The recruiter already has the description. Retyping the title, the
 * city and five screening questions out of text that already contains
 * them is work the text can do itself.
 *
 * Two things are deliberate. It always says how the fields were
 * produced, because a form filled in by keyword matching deserves more
 * scrutiny than one a model actually read. And it never saves anything:
 * every field lands in the form below, editable, and nothing is created
 * until the recruiter presses Create role.
 */
export function JdPasteBox({
  onExtracted,
}: {
  onExtracted: (draft: JobDraft, jdText: string) => void;
}) {
  const [text, setText] = useState("");
  const [lastSource, setLastSource] = useState<JobDraft["source"] | null>(null);
  const [lastNote, setLastNote] = useState<string | null>(null);

  const extract = useMutation({
    mutationFn: () => api.jobs.extract(text),
    onSuccess: (draft) => {
      onExtracted(draft, text);
      setLastSource(draft.source);
      setLastNote(draft.note ?? null);

      const count = draft.questions?.length ?? 0;
      if (draft.source === "model") {
        toast.success(`Filled in the form with ${count} screening questions`, {
          description: "Check the details and edit anything that is not right.",
        });
      } else {
        toast.warning("Filled in from keywords", {
          description:
            "No language model was available, so please check every field carefully.",
          duration: 7000,
        });
      }
    },
    onError: (error) =>
      toast.error("Could not read that description", {
        description:
          error instanceof ApiError ? error.message : "Please try again.",
      }),
  });

  const canSubmit = text.trim().length >= 40 && !extract.isPending;

  return (
    <Card className="gap-0 border-dashed p-5">
      <div className="flex items-start gap-3">
        <span className="bg-primary/10 text-primary mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md">
          <ClipboardPaste className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-[15px] font-semibold">
            Start from a job description
          </h2>
          <p className="text-muted-foreground mt-0.5 text-sm">
            Paste the description and everything below is filled in for you,
            including the screening questions. You can change any of it
            afterwards.
          </p>

          <Textarea
            value={text}
            onChange={(event) => setText(event.target.value)}
            rows={6}
            className="mt-3"
            placeholder={
              "Paste the full job description here.\n\n" +
              "For example: Hiring delivery executives in Bengaluru for same-day parcel " +
              "delivery. Own two-wheeler and valid licence required. Shifts are 9 hours " +
              "with one weekly off. 18,000 to 24,000 per month including incentives."
            }
            onPaste={() => {
              // Reset the previous outcome so a stale "filled from keywords"
              // warning does not sit above a fresh description.
              setLastSource(null);
              setLastNote(null);
            }}
          />

          <div className="mt-3 flex flex-wrap items-center gap-3">
            <Button
              type="button"
              disabled={!canSubmit}
              onClick={() => extract.mutate()}
            >
              {extract.isPending ? (
                <>
                  <Loader2 className="size-4 animate-spin" />
                  Reading the description…
                </>
              ) : (
                <>
                  <Wand2 className="size-4" />
                  Fill in the form
                </>
              )}
            </Button>

            {text.trim().length > 0 && text.trim().length < 40 && (
              <span className="text-muted-foreground text-xs">
                A little more text and this will work better.
              </span>
            )}

            {lastSource === "model" && !extract.isPending && (
              <span className="text-xs text-emerald-700 dark:text-emerald-400">
                Filled in below. Review before creating.
              </span>
            )}
          </div>

          {lastNote && (
            <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-[13px] text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" />
              <p className="leading-relaxed">{lastNote}</p>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}
