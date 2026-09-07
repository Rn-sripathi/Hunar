"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Ban,
  Clock,
  ExternalLink,
  Link2,
  Link2Off,
  PhoneOff,
  ShieldCheck,
  ShieldOff,
} from "lucide-react";
import { toast } from "sonner";

import { ScorePill } from "@/components/score-pill";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api, ApiError, queryKeys } from "@/lib/api";
import type { ProspectOut } from "@/lib/types";
import { cn } from "@/lib/utils";

const PHONE_LABEL: Record<string, string> = {
  REVEALED: "Number available",
  PRESENT_MASKED: "Mobile on file, not returned on this plan",
  ABSENT: "No mobile on file",
  UNKNOWN: "Unknown",
};

/**
 * Why this person cannot be called, shown rather than hidden.
 *
 * Quietly dropping people the app will not phone would make the consent
 * gate invisible, and an invisible gate teaches the operator nothing. It
 * would also make the list look like a provider failure rather than a
 * deliberate refusal.
 */
function CallableCell({ prospect }: { prospect: ProspectOut }) {
  if (prospect.do_not_contact) {
    return (
      <Badge
        variant="secondary"
        className="gap-1 border-transparent bg-red-50 font-normal text-red-700 dark:bg-red-950/40 dark:text-red-300"
      >
        <Ban className="size-3" />
        Do not contact
      </Badge>
    );
  }

  if (prospect.callable) {
    return (
      <Badge
        variant="secondary"
        className="gap-1 border-transparent bg-emerald-50 font-normal text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300"
      >
        <ShieldCheck className="size-3" />
        Consented
      </Badge>
    );
  }

  // Consented, but not this minute. Worth its own state: the consent is
  // real and durable, and only the clock is in the way. Showing this as
  // a flat "not callable" would make recording a consent look like it
  // did nothing whenever the calling window happens to be shut.
  if (prospect.consented) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge
            variant="secondary"
            className="gap-1 border-transparent bg-amber-50 font-normal text-amber-800 dark:bg-amber-950/40 dark:text-amber-300"
          >
            <Clock className="size-3" />
            Consented, queued
          </Badge>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs leading-relaxed">
          {prospect.not_callable_reason ??
            "Consented, but outside the calling window."}
        </TooltipContent>
      </Tooltip>
    );
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          variant="secondary"
          className="text-muted-foreground gap-1 border-transparent font-normal"
        >
          <ShieldOff className="size-3" />
          Not callable
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs leading-relaxed">
        {prospect.not_callable_reason ??
          "Not on the consent allowlist for this deployment."}
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * Attach a sourced person to a number that is already consented.
 *
 * Worth being exact about what this is. It does not grant permission:
 * the dialable numbers come from the environment and nothing in the
 * browser can add to them. It records which sourced person is reachable
 * on one of them, which is how consent genuinely arrives, through a
 * reply or a referral. Being findable never implies it.
 *
 * Without this step nobody sourced is ever callable, which is both the
 * correct default and the honest one.
 */
function ConsentLink({
  prospect,
  onUpdated,
}: {
  prospect: ProspectOut;
  onUpdated: (updated: ProspectOut) => void;
}) {
  const { data: policy } = useQuery({
    queryKey: queryKeys.policy,
    queryFn: api.people.policy,
    staleTime: 60_000,
  });

  const link = useMutation({
    mutationFn: (allowlistId: string) =>
      api.people.linkConsent(prospect.id, allowlistId),
    onSuccess: (updated) => {
      onUpdated(updated);
      toast.success(
        updated.callable
          ? `${prospect.full_name} can now be called`
          : `${prospect.full_name} is consented, and queued for the next calling window`,
      );
    },
    onError: (error) =>
      toast.error("Could not link that number", {
        description:
          error instanceof ApiError ? error.message : "Please try again.",
      }),
  });

  const unlink = useMutation({
    mutationFn: () => api.people.unlinkConsent(prospect.id),
    onSuccess: (updated) => {
      onUpdated(updated);
      toast.success(`${prospect.full_name} is no longer callable`);
    },
  });

  const entries = policy?.allowlist ?? [];
  const busy = link.isPending || unlink.isPending;

  if (prospect.do_not_contact) return null;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          disabled={busy}
          aria-label={`Consent options for ${prospect.full_name}`}
          className="text-muted-foreground hover:text-foreground mt-1 inline-flex items-center gap-1 text-[11px] underline underline-offset-2 disabled:opacity-50"
        >
          {prospect.consented ? (
            <>
              <Link2Off className="size-3" />
              Unlink
            </>
          ) : (
            <>
              <Link2 className="size-3" />
              Link consented number
            </>
          )}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-64">
        <DropdownMenuLabel className="text-xs font-normal">
          Numbers this deployment may call. Set in the environment, not here.
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {entries.length === 0 && (
          <div className="text-muted-foreground px-2 py-3 text-xs leading-relaxed">
            None configured. Add one to DEMO_ALLOWLIST in the environment and
            restart.
          </div>
        )}
        {entries.map((entry) => (
          <DropdownMenuItem
            key={entry.id}
            onSelect={() => link.mutate(entry.id)}
            className="text-xs"
          >
            <span className="font-mono">{entry.e164_masked}</span>
            <span className="text-muted-foreground ml-auto">{entry.label}</span>
          </DropdownMenuItem>
        ))}
        {prospect.consented && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onSelect={() => unlink.mutate()}
              className="text-xs"
            >
              <Link2Off className="size-3" />
              Make uncallable
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** The score, with the reasoning behind it one hover away. */
function FitCell({ prospect }: { prospect: ProspectOut }) {
  const reasons = prospect.fit_reasons ?? [];

  if (reasons.length === 0) {
    return <ScorePill score={prospect.fit_score} />;
  }

  return (
    <HoverCard openDelay={120}>
      <HoverCardTrigger asChild>
        <div className="cursor-help">
          <ScorePill score={prospect.fit_score} />
        </div>
      </HoverCardTrigger>
      <HoverCardContent className="w-80" align="end">
        <p className="text-xs font-medium">How this score was reached</p>
        <ul className="mt-2 space-y-1.5">
          {reasons.map((reason, index) => {
            const label = String(reason.label ?? "");
            const detail = String(reason.detail ?? "");
            const points = Number(reason.points ?? 0);
            return (
              <li
                key={`${label}-${index}`}
                className="flex items-baseline justify-between gap-3 text-xs"
              >
                <span className="min-w-0">
                  <span className="font-medium">{label}</span>
                  {detail && (
                    <span className="text-muted-foreground"> — {detail}</span>
                  )}
                </span>
                <span
                  className={cn(
                    "shrink-0 tabular-nums",
                    points > 0
                      ? "text-emerald-700 dark:text-emerald-400"
                      : "text-muted-foreground",
                  )}
                >
                  +{points}
                </span>
              </li>
            );
          })}
        </ul>
        <p className="text-muted-foreground mt-2.5 border-t pt-2 text-[11px] leading-relaxed">
          This ranks who to approach first. It is not a judgement about
          anyone&rsquo;s ability and must never gate a hiring decision.
        </p>
      </HoverCardContent>
    </HoverCard>
  );
}

export function ProspectTable({
  prospects,
  selected,
  onSelectedChange,
  onProspectUpdated,
}: {
  prospects: ProspectOut[];
  selected: Set<string>;
  onSelectedChange: (next: Set<string>) => void;
  onProspectUpdated: (updated: ProspectOut) => void;
}) {
  // Selection follows consent rather than the clock. A campaign built
  // outside calling hours defers its targets and rings them when the
  // window opens, so blocking selection here would disable a workflow
  // the server already supports.
  const selectable = prospects.filter((p) => p.consented);
  const allSelected =
    selectable.length > 0 && selectable.every((p) => selected.has(p.id));

  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onSelectedChange(next);
  };

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-10">
              <Checkbox
                checked={allSelected}
                disabled={selectable.length === 0}
                aria-label="Select everyone who can be called"
                onCheckedChange={(checked) =>
                  onSelectedChange(
                    checked ? new Set(selectable.map((p) => p.id)) : new Set(),
                  )
                }
              />
            </TableHead>
            <TableHead>Person</TableHead>
            <TableHead>Now</TableHead>
            <TableHead>Location</TableHead>
            <TableHead className="w-40 min-w-36">Skills</TableHead>
            <TableHead className="w-32">Phone</TableHead>
            <TableHead className="w-36">Reachable</TableHead>
            <TableHead className="bg-background sticky right-0 w-36 text-right shadow-[-8px_0_8px_-8px_rgba(0,0,0,0.12)]">
              Fit
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {prospects.map((prospect) => (
            <TableRow
              key={prospect.id}
              data-state={selected.has(prospect.id) ? "selected" : undefined}
              className={cn(!prospect.consented && "opacity-70")}
            >
              <TableCell>
                <Checkbox
                  checked={selected.has(prospect.id)}
                  disabled={!prospect.consented}
                  aria-label={`Select ${prospect.full_name}`}
                  onCheckedChange={() => toggle(prospect.id)}
                />
              </TableCell>

              <TableCell>
                <div className="flex items-center gap-1.5">
                  <span className="font-medium">{prospect.full_name}</span>
                  {prospect.linkedin_url && (
                    <a
                      href={prospect.linkedin_url}
                      target="_blank"
                      rel="noreferrer noopener"
                      aria-label={`Open ${prospect.full_name}'s profile`}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      <ExternalLink className="size-3" />
                    </a>
                  )}
                </div>
                {prospect.headline && (
                  <p className="text-muted-foreground mt-0.5 max-w-72 truncate text-xs">
                    {prospect.headline}
                  </p>
                )}
              </TableCell>

              <TableCell className="text-sm">
                <div>{prospect.job_title ?? "—"}</div>
                <div className="text-muted-foreground text-xs">
                  {prospect.company_name ?? "—"}
                  {prospect.years_experience != null &&
                    ` · ${prospect.years_experience}y`}
                </div>
              </TableCell>

              <TableCell className="text-sm">
                {prospect.location_city ?? "—"}
              </TableCell>

              <TableCell>
                <div className="flex flex-wrap gap-1">
                  {(prospect.skills ?? []).slice(0, 3).map((skill) => (
                    <Badge
                      key={skill}
                      variant="secondary"
                      className="px-1.5 py-0 text-[10px] font-normal"
                    >
                      {skill}
                    </Badge>
                  ))}
                  {(prospect.skills?.length ?? 0) > 3 && (
                    <span className="text-muted-foreground text-[10px]">
                      +{(prospect.skills?.length ?? 0) - 3}
                    </span>
                  )}
                </div>
              </TableCell>

              <TableCell>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
                      <PhoneOff className="size-3" />
                      {prospect.phone_status === "PRESENT_MASKED"
                        ? "On file"
                        : prospect.phone_status === "REVEALED"
                          ? "Available"
                          : "None"}
                    </span>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs">
                    {PHONE_LABEL[prospect.phone_status] ?? "Unknown"}
                  </TooltipContent>
                </Tooltip>
              </TableCell>

              <TableCell>
                <CallableCell prospect={prospect} />
                <ConsentLink
                  prospect={prospect}
                  onUpdated={onProspectUpdated}
                />
              </TableCell>

              {/* Pinned: a wide skills column otherwise pushes the score,
                  which is the thing people actually scan, past the right
                  edge of the viewport. */}
              <TableCell className="bg-background sticky right-0 text-right shadow-[-8px_0_8px_-8px_rgba(0,0,0,0.12)]">
                <FitCell prospect={prospect} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
