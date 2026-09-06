"use client";

import { Megaphone } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { SearchFilters } from "@/lib/types";

export interface CampaignDetails {
  job_title: string;
  company_name: string;
  job_city: string | null;
  work_mode: string | null;
  role_pitch: string;
  comp_range_text: string | null;
  recruiter_name: string;
}

/**
 * Confirm what the agent will actually say, before it says it.
 *
 * These four fields are read out loud to a stranger, so none of them is
 * safe to infer. The role title in particular must not be taken from the
 * search filters: those hold the titles people hold *today*, usually a
 * rung below the opening, and announcing the wrong one is both confusing
 * and faintly insulting.
 *
 * Prefilled from the job description so it is a confirmation rather than
 * a form, but every field is editable because the description is not
 * always right and the phone call is not reversible.
 */
export function CampaignDetailsDialog({
  open,
  onOpenChange,
  filters,
  count,
  pending,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  filters: SearchFilters | null;
  count: number;
  pending?: boolean;
  onConfirm: (details: CampaignDetails) => void;
}) {
  // Seeded once, at mount. The parent mounts this only while it is open,
  // so "once" is the moment the recruiter asked for it and the values are
  // never stale. An effect that pushed props into state would fight the
  // recruiter's own edits on every re-render.
  const [title, setTitle] = useState(
    () => filters?.hiring_title || filters?.titles?.[0] || "",
  );
  const [company, setCompany] = useState(() => filters?.company_name ?? "");
  const [pitch, setPitch] = useState(() => filters?.role_pitch ?? "");
  const [recruiter, setRecruiter] = useState("our recruiter");

  const city = filters?.cities?.[0] ?? null;
  const ready =
    title.trim().length >= 2 &&
    company.trim().length >= 1 &&
    pitch.trim().length >= 3;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Megaphone className="size-4" />
            What should the agent say?
          </DialogTitle>
          <DialogDescription className="leading-relaxed">
            These words are read aloud to {count}{" "}
            {count === 1 ? "person who has" : "people who have"} never heard of
            this company. Nothing here is guessed for you.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="campaign-title" className="text-[13px]">
                Role being hired
              </Label>
              <Input
                id="campaign-title"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="Senior Backend Engineer"
                className="mt-1.5"
              />
            </div>
            <div>
              <Label htmlFor="campaign-company" className="text-[13px]">
                Company
              </Label>
              <Input
                id="campaign-company"
                value={company}
                onChange={(event) => setCompany(event.target.value)}
                placeholder="Razorpay"
                className="mt-1.5"
              />
            </div>
          </div>

          <div>
            <Label htmlFor="campaign-pitch" className="text-[13px]">
              Why it might interest them
            </Label>
            <p className="text-muted-foreground mt-0.5 text-xs">
              One sentence. Concrete beats enthusiastic, and short beats
              complete.
            </p>
            <Textarea
              id="campaign-pitch"
              value={pitch}
              onChange={(event) => setPitch(event.target.value.slice(0, 240))}
              rows={3}
              className="mt-1.5"
              placeholder="a Series B fintech in Bengaluru hiring a senior backend engineer to own their payments ledger"
            />
            <p className="text-muted-foreground mt-1 text-right text-[11px] tabular-nums">
              {pitch.length}/240
            </p>
          </div>

          <div>
            <Label htmlFor="campaign-recruiter" className="text-[13px]">
              Who will call them back
            </Label>
            <Input
              id="campaign-recruiter"
              value={recruiter}
              onChange={(event) => setRecruiter(event.target.value)}
              className="mt-1.5"
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={!ready || pending}
            onClick={() =>
              onConfirm({
                job_title: title.trim(),
                company_name: company.trim(),
                job_city: city,
                work_mode: filters?.work_mode || null,
                role_pitch: pitch.trim(),
                comp_range_text: filters?.comp_range_text || null,
                recruiter_name: recruiter.trim() || "our recruiter",
              })
            }
          >
            {pending ? "Building…" : "Build campaign"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
