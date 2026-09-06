"use client";

import { useQuery } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { MapPin, PhoneCall, Search, ShieldOff, Sparkles } from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError, queryKeys } from "@/lib/api";
import type { CampaignSummary } from "@/lib/types";
import { useLivePoll } from "@/lib/use-live-poll";
import { cn } from "@/lib/utils";

const STATUS_TONE: Record<string, string> = {
  DRAFT: "bg-muted text-muted-foreground",
  CALLING: "bg-blue-50 text-blue-700 dark:bg-blue-950/50 dark:text-blue-300",
  DONE: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300",
};

const STATUS_LABEL: Record<string, string> = {
  DRAFT: "Not yet called",
  CALLING: "Calling",
  DONE: "Complete",
};

function CampaignCard({ campaign }: { campaign: CampaignSummary }) {
  const total = campaign.target_count ?? 0;
  const done = campaign.completed_count ?? 0;
  const progress = total > 0 ? Math.round((done / total) * 100) : 0;

  return (
    <Link
      href={`/sourcing/campaigns/${campaign.id}`}
      className="group focus-visible:outline-none"
    >
      <Card className="hover:border-foreground/20 h-full gap-0 p-5 transition-colors group-focus-visible:ring-2">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="truncate text-[15px] font-semibold tracking-tight">
              {campaign.job_title}
            </h3>
            <p className="text-muted-foreground mt-0.5 truncate text-sm">
              {campaign.company_name}
            </p>
          </div>
          <Badge
            variant="secondary"
            className={cn(
              "shrink-0 border-transparent",
              STATUS_TONE[campaign.status] ?? "",
            )}
          >
            {STATUS_LABEL[campaign.status] ?? campaign.status}
          </Badge>
        </div>

        <div className="text-muted-foreground mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
          {campaign.job_city && (
            <span className="inline-flex items-center gap-1">
              <MapPin className="size-3" />
              {campaign.job_city}
            </span>
          )}
          <span className="inline-flex items-center gap-1">
            <PhoneCall className="size-3" />
            {total} {total === 1 ? "person" : "people"}
          </span>
          {(campaign.blocked_count ?? 0) > 0 && (
            <span className="inline-flex items-center gap-1 text-red-700 dark:text-red-400">
              <ShieldOff className="size-3" />
              {campaign.blocked_count} blocked
            </span>
          )}
        </div>

        <div className="mt-4">
          <div className="text-muted-foreground mb-1.5 flex items-center justify-between text-xs">
            <span>
              {done} of {total} reached
            </span>
            {(campaign.interested_count ?? 0) > 0 && (
              <span className="text-emerald-700 dark:text-emerald-400">
                {campaign.interested_count} interested
              </span>
            )}
          </div>
          <Progress value={progress} className="h-1.5" />
        </div>

        <div className="text-muted-foreground mt-4 border-t pt-3 text-xs">
          {campaign.launched_at
            ? `Called ${formatDistanceToNow(new Date(campaign.launched_at), { addSuffix: true })}`
            : `Built ${formatDistanceToNow(new Date(campaign.created_at), { addSuffix: true })}`}
        </div>
      </Card>
    </Link>
  );
}

export default function CampaignsPage() {
  const poll = useLivePoll<CampaignSummary[]>((data) =>
    (data ?? []).some((campaign) => campaign.status === "CALLING"),
  );

  const {
    data: campaigns,
    isPending,
    error,
  } = useQuery({
    queryKey: queryKeys.campaigns,
    queryFn: api.campaigns.list,
    refetchInterval: poll.refetchInterval,
    refetchIntervalInBackground: true,
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Outreach campaigns
          </h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Cold calls to people who were sourced rather than applied. Every
            number is consented before it is dialled.
          </p>
        </div>
        <Button asChild>
          <Link href="/sourcing">
            <Search className="size-4" />
            Find people
          </Link>
        </Button>
      </div>

      {isPending && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((index) => (
            <Card key={index} className="gap-0 p-5">
              <Skeleton className="h-5 w-2/3" />
              <Skeleton className="mt-2 h-4 w-1/3" />
              <Skeleton className="mt-5 h-3 w-full" />
            </Card>
          ))}
        </div>
      )}

      {error && (
        <EmptyState
          icon={PhoneCall}
          title="Could not load campaigns"
          description={
            error instanceof ApiError
              ? error.message
              : "Something went wrong reaching the API."
          }
        />
      )}

      {campaigns && campaigns.length === 0 && (
        <EmptyState
          icon={Sparkles}
          title="No campaigns yet"
          description="Start from a job description. People are sourced first, then the ones on the consent allowlist can be called."
          action={
            <Button asChild>
              <Link href="/sourcing">
                <Search className="size-4" />
                Find people
              </Link>
            </Button>
          }
        />
      )}

      {campaigns && campaigns.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {campaigns.map((campaign) => (
            <CampaignCard key={campaign.id} campaign={campaign} />
          ))}
        </div>
      )}
    </div>
  );
}
