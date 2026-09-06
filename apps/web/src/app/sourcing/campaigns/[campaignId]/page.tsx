"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Ban,
  Check,
  ChevronDown,
  Clock,
  FileText,
  Loader2,
  Minus,
  PhoneCall,
  PlayCircle,
  RefreshCw,
  ShieldOff,
  X,
} from "lucide-react";
import Link from "next/link";
import { use, useState } from "react";
import { toast } from "sonner";

import { ConsentDialog } from "@/components/consent-dialog";
import { EmptyState } from "@/components/empty-state";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
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
import { api, ApiError, queryKeys } from "@/lib/api";
import type { CampaignDetail, FieldSpec, TargetOut } from "@/lib/types";
import { useLivePoll } from "@/lib/use-live-poll";
import { cn } from "@/lib/utils";

const TARGET_TONE: Record<string, string> = {
  PENDING: "bg-muted text-muted-foreground",
  DEFERRED:
    "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
  CALLING: "bg-blue-50 text-blue-700 dark:bg-blue-950/50 dark:text-blue-300",
  DONE: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300",
  BLOCKED: "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300",
  FAILED: "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300",
};

const TARGET_LABEL: Record<string, string> = {
  PENDING: "Ready",
  DEFERRED: "Deferred",
  CALLING: "Calling",
  DONE: "Done",
  BLOCKED: "Blocked",
  FAILED: "Failed",
};

/**
 * One extracted answer.
 *
 * The coerced value is what you see; the literal words the person said
 * are one hover away. Keeping both visible is what makes a coercion bug
 * a misread column rather than a lost answer, and it is the difference
 * between a dashboard you can audit and one you have to believe.
 */
function AnswerCell({
  column,
  target,
}: {
  column: FieldSpec;
  target: TargetOut;
}) {
  const value = target.values?.[column.key];
  const raw = target.raw_values?.[column.key];

  let rendered: React.ReactNode;
  if (value === null || value === undefined || value === "") {
    rendered = <span className="text-muted-foreground text-xs">—</span>;
  } else if (typeof value === "boolean") {
    rendered = value ? (
      <Check className="size-4 text-emerald-600 dark:text-emerald-400" />
    ) : (
      <X className="text-muted-foreground size-4" />
    );
  } else {
    rendered = <span className="text-sm">{String(value)}</span>;
  }

  if (raw === null || raw === undefined || String(raw) === String(value)) {
    return rendered;
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="cursor-help border-b border-dotted">{rendered}</span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        <span className="text-xs">They said: &ldquo;{String(raw)}&rdquo;</span>
      </TooltipContent>
    </Tooltip>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div>
      <div className="text-xl font-semibold tabular-nums">{value}</div>
      <div className="text-muted-foreground text-xs">{label}</div>
    </div>
  );
}

export default function CampaignPage({
  params,
}: {
  params: Promise<{ campaignId: string }>;
}) {
  const { campaignId } = use(params);
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);

  // Keep the table moving while any call is running or while a finished
  // call is still waiting on its answers. Hunar extracts after the
  // hangup, so stopping at "completed" would leave the columns empty.
  const poll = useLivePoll<CampaignDetail>(
    (data) => data?.in_progress ?? false,
  );

  const {
    data: campaign,
    isPending,
    error,
  } = useQuery({
    queryKey: queryKeys.campaign(campaignId),
    queryFn: () => api.campaigns.get(campaignId),
    refetchInterval: poll.refetchInterval,
    refetchIntervalInBackground: true,
  });

  const launch = useMutation({
    mutationFn: () => api.campaigns.launch(campaignId),
    onSuccess: (report) => {
      setConfirming(false);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.campaign(campaignId),
      });
      void queryClient.invalidateQueries({ queryKey: queryKeys.campaigns });

      if (report.launched > 0) {
        toast.success(
          `Calling ${report.launched} ${report.launched === 1 ? "person" : "people"}`,
        );
      }
      if (report.deferred > 0) {
        toast.warning(`${report.deferred} deferred`, {
          description: "Outside the calling window. They will wait.",
        });
      }
      for (const blocked of report.blocked ?? []) {
        toast.error(`Blocked: ${blocked.prospect}`, {
          description: blocked.reason,
          duration: 8000,
        });
      }
      const blockedCount = (report.blocked ?? []).length;
      if (
        report.launched === 0 &&
        blockedCount === 0 &&
        report.deferred === 0
      ) {
        toast.info("Nothing to call", {
          description: "Everyone here has already been reached.",
        });
      }
    },
    onError: (error) =>
      toast.error("Could not place the calls", {
        description:
          error instanceof ApiError ? error.message : "Please try again.",
      }),
  });

  if (isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (error || !campaign) {
    return (
      <EmptyState
        icon={PhoneCall}
        title="Could not load this campaign"
        description={
          error instanceof ApiError
            ? error.message
            : "Something went wrong reaching the API."
        }
      />
    );
  }

  const columns = (campaign.columns ?? []) as unknown as FieldSpec[];
  const targets = campaign.targets ?? [];
  const ready = targets.filter(
    (t) => t.status === "PENDING" || t.status === "DEFERRED",
  ).length;

  return (
    <div className="space-y-6">
      <div>
        <Button variant="ghost" size="sm" asChild className="-ml-2">
          <Link href="/sourcing/campaigns">
            <ArrowLeft className="size-4" />
            Campaigns
          </Link>
        </Button>
      </div>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight">
            {campaign.job_title}
          </h1>
          <p className="text-muted-foreground mt-1 text-sm">
            {campaign.company_name}
            {campaign.job_city && ` · ${campaign.job_city}`}
            {campaign.work_mode && ` · ${campaign.work_mode}`}
          </p>
        </div>

        <div className="flex items-center gap-2">
          {campaign.in_progress && (
            <span className="text-muted-foreground inline-flex items-center gap-1.5 text-xs">
              <RefreshCw className="size-3 animate-spin" />
              Live
            </span>
          )}
          <Button
            onClick={() => setConfirming(true)}
            disabled={ready === 0 || launch.isPending}
          >
            {launch.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <PlayCircle className="size-4" />
            )}
            {ready === 0 ? "Nothing to call" : `Call ${ready}`}
          </Button>
        </div>
      </div>

      {poll.gaveUp && (
        <Card className="flex flex-row items-center justify-between gap-3 p-4">
          <p className="text-sm">
            Stopped watching after ten minutes. Something here is stuck rather
            than slow.
          </p>
          <Button variant="outline" size="sm" onClick={poll.resume}>
            Keep watching
          </Button>
        </Card>
      )}

      <Card className="grid grid-cols-2 gap-4 p-5 sm:grid-cols-5">
        <Stat label="People" value={campaign.target_count} />
        <Stat label="Reached" value={campaign.completed_count} />
        <Stat label="Interested" value={campaign.interested_count} />
        <Stat label="Blocked" value={campaign.blocked_count} />
        <Stat label="Status" value={campaign.status.toLowerCase()} />
      </Card>

      {/* The script, readable before it is spoken to a stranger. */}
      <Card className="gap-0 p-5">
        <Collapsible>
          <CollapsibleTrigger className="flex w-full items-center justify-between gap-3 text-left">
            <div className="flex items-center gap-2">
              <FileText className="text-muted-foreground size-4" />
              <div>
                <p className="text-[15px] font-semibold">
                  What the agent will say
                </p>
                <p className="text-muted-foreground text-sm">
                  Read the exact words before a stranger hears them.
                </p>
              </div>
            </div>
            <ChevronDown className="text-muted-foreground size-4 shrink-0" />
          </CollapsibleTrigger>
          <CollapsibleContent>
            <Separator className="my-4" />
            <div className="space-y-4">
              <div>
                <p className="text-xs font-medium">Opening</p>
                <p className="bg-muted mt-1.5 rounded-md p-3 text-[13px] leading-relaxed italic">
                  &ldquo;{campaign.script_preview?.introduction}&rdquo;
                </p>
              </div>
              <div>
                <p className="text-xs font-medium">Full instructions</p>
                <pre className="bg-muted mt-1.5 max-h-80 overflow-auto rounded-md p-3 text-[11px] leading-relaxed whitespace-pre-wrap">
                  {campaign.script_preview?.agent_prompt}
                </pre>
              </div>
            </div>
          </CollapsibleContent>
        </Collapsible>
      </Card>

      <Card className="gap-0 p-5">
        <h2 className="text-[15px] font-semibold">Responses</h2>
        <p className="text-muted-foreground mt-0.5 text-sm">
          Columns come from what the agent was asked to extract, so this table
          builds itself from the campaign rather than being hardcoded.
        </p>

        <div className="mt-4 overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="min-w-44">Person</TableHead>
                <TableHead className="w-32">Status</TableHead>
                {columns.map((column) => (
                  <TableHead key={column.key} className="whitespace-nowrap">
                    {column.label}
                  </TableHead>
                ))}
                <TableHead className="w-20">Call</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {targets.map((target) => (
                <TableRow key={target.id}>
                  <TableCell>
                    <div className="font-medium">{target.prospect_name}</div>
                    <div className="text-muted-foreground font-mono text-xs">
                      {target.mobile_masked}
                    </div>
                  </TableCell>

                  <TableCell>
                    <div className="flex flex-col items-start gap-1">
                      <Badge
                        variant="secondary"
                        className={cn(
                          "gap-1 border-transparent font-normal",
                          TARGET_TONE[target.status],
                        )}
                      >
                        {target.status === "BLOCKED" && (
                          <ShieldOff className="size-3" />
                        )}
                        {target.status === "DEFERRED" && (
                          <Clock className="size-3" />
                        )}
                        {TARGET_LABEL[target.status] ?? target.status}
                      </Badge>
                      {target.call_status !== "NOT_STARTED" && (
                        <StatusBadge status={target.call_status} />
                      )}
                    </div>
                    {target.block_reason && (
                      <p className="text-muted-foreground mt-1 max-w-56 text-[11px] leading-relaxed">
                        {target.block_reason}
                      </p>
                    )}
                  </TableCell>

                  {columns.map((column) => (
                    <TableCell key={column.key}>
                      <AnswerCell column={column} target={target} />
                    </TableCell>
                  ))}

                  <TableCell>
                    {target.recording_url ? (
                      <a
                        href={target.recording_url}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="text-xs underline underline-offset-2"
                      >
                        Listen
                      </a>
                    ) : (
                      <Minus className="text-muted-foreground size-3" />
                    )}
                  </TableCell>
                </TableRow>
              ))}

              {targets.length === 0 && (
                <TableRow>
                  <TableCell
                    colSpan={columns.length + 3}
                    className="text-muted-foreground py-10 text-center text-sm"
                  >
                    <Ban className="mx-auto mb-2 size-5" />
                    Nobody in this campaign could be added. Every prospect
                    selected was refused by the consent gate, which means no
                    target row could be created at all.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </Card>

      <ConsentDialog
        open={confirming}
        onOpenChange={setConfirming}
        campaign={campaign}
        pending={launch.isPending}
        onConfirm={() => launch.mutate()}
      />
    </div>
  );
}
