"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import {
  ChevronLeft,
  ChevronRight,
  History,
  Loader2,
  Search,
  ShieldCheck,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import {
  CampaignDetailsDialog,
  type CampaignDetails,
} from "@/components/campaign-details-dialog";
import { EmptyState } from "@/components/empty-state";
import { ProspectTable } from "@/components/prospect-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { api, ApiError, queryKeys } from "@/lib/api";
import type { ProspectOut } from "@/lib/types";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 25;

/**
 * Everyone sourced so far, and the searches that found them.
 *
 * This page exists because the search screen used to be the only place
 * prospects were ever visible. They were persisted correctly, but
 * reloading the page discarded a list that had cost real provider
 * credits to build, which is close to the worst possible way to lose
 * data: silently, and with a bill attached.
 */
export default function ProspectsPage() {
  const router = useRouter();
  const queryClient = useQueryClient();

  const [page, setPage] = useState(0);
  const [consentedOnly, setConsentedOnly] = useState(false);
  const [searchId, setSearchId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [detailing, setDetailing] = useState(false);

  const { data: searches } = useQuery({
    queryKey: queryKeys.searches,
    queryFn: () => api.people.searches(),
    staleTime: 30_000,
  });

  const {
    data: pageData,
    isPending,
    error,
    isFetching,
  } = useQuery({
    queryKey: queryKeys.prospects({
      offset: page * PAGE_SIZE,
      consentedOnly,
      searchId,
    }),
    queryFn: () =>
      api.people.prospects({
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        consented_only: consentedOnly,
        search_id: searchId,
      }),
    // The consent state depends on the calling window, which moves on its
    // own. Refetching on a slow timer keeps the "reachable" column
    // truthful without ever re-running a paid provider query, because
    // this endpoint only reads what we already stored.
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  });

  const createCampaign = useMutation({
    mutationFn: (details: CampaignDetails) =>
      api.campaigns.create({
        search_id: searchId,
        prospect_ids: [...selected],
        ...details,
      }),
    onSuccess: (campaign) => {
      setDetailing(false);
      toast.success("Campaign ready to review");
      router.push(`/sourcing/campaigns/${campaign.id}`);
    },
    onError: (error) =>
      toast.error("Could not build the campaign", {
        description:
          error instanceof ApiError ? error.message : "Please try again.",
      }),
  });

  const prospects = pageData?.prospects ?? [];
  const total = pageData?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const activeSearch = searches?.find((entry) => entry.id === searchId);

  const patchRow = (updated: ProspectOut) => {
    queryClient.setQueryData(
      queryKeys.prospects({
        offset: page * PAGE_SIZE,
        consentedOnly,
        searchId,
      }),
      (current: typeof pageData) =>
        current
          ? {
              ...current,
              prospects: (current.prospects ?? []).map((person) =>
                person.id === updated.id ? updated : person,
              ),
            }
          : current,
    );
    void queryClient.invalidateQueries({ queryKey: queryKeys.policy });
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Prospects</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Everyone sourced so far. These cost credits to find, so they are
            kept rather than discarded when you leave the search screen.
          </p>
        </div>
        <Button variant="outline" asChild>
          <Link href="/sourcing">
            <Search className="size-4" />
            New search
          </Link>
        </Button>
      </div>

      {/* Past searches: each one cost money, so each one is findable. */}
      {searches && searches.length > 0 && (
        <Card className="gap-0 p-4">
          <div className="flex items-center gap-2">
            <History className="text-muted-foreground size-4" />
            <p className="text-[13px] font-medium">Past searches</p>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => {
                setSearchId(null);
                setPage(0);
              }}
              className={cn(
                "rounded-md border px-2.5 py-1 text-xs font-medium transition-colors",
                searchId === null
                  ? "bg-primary text-primary-foreground border-transparent"
                  : "text-muted-foreground hover:bg-muted",
              )}
            >
              Everyone ({total})
            </button>
            {searches.map((entry) => (
              <button
                key={entry.id}
                type="button"
                onClick={() => {
                  setSearchId(entry.id);
                  setPage(0);
                  setSelected(new Set());
                }}
                title={`${entry.provider} · ${entry.credits_charged} credits · ${entry.extraction_method}`}
                className={cn(
                  "max-w-72 truncate rounded-md border px-2.5 py-1 text-xs font-medium transition-colors",
                  searchId === entry.id
                    ? "bg-primary text-primary-foreground border-transparent"
                    : "text-muted-foreground hover:bg-muted",
                )}
              >
                {entry.label} · {entry.result_count}
              </button>
            ))}
          </div>
        </Card>
      )}

      <Card className="gap-0 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-[15px] font-semibold">
              {activeSearch ? activeSearch.label : "All prospects"}
            </h2>
            <p className="text-muted-foreground mt-0.5 text-sm">
              {activeSearch ? (
                <>
                  Searched{" "}
                  {formatDistanceToNow(new Date(activeSearch.created_at), {
                    addSuffix: true,
                  })}{" "}
                  via {activeSearch.provider}, {activeSearch.credits_charged}{" "}
                  {activeSearch.credits_charged === 1 ? "credit" : "credits"}.
                </>
              ) : (
                <>Ranked by fit. Hover a score to see how it was reached.</>
              )}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            {isFetching && (
              <Loader2 className="text-muted-foreground size-4 animate-spin" />
            )}
            <label className="flex cursor-pointer items-center gap-2 text-xs">
              <Switch
                checked={consentedOnly}
                onCheckedChange={(checked) => {
                  setConsentedOnly(checked);
                  setPage(0);
                }}
              />
              <span className="inline-flex items-center gap-1">
                <ShieldCheck className="size-3" />
                Reachable only
              </span>
            </label>
            <Badge variant="secondary" className="font-normal">
              {total} {total === 1 ? "person" : "people"}
            </Badge>
          </div>
        </div>

        <Separator className="my-5" />

        {isPending && (
          <div className="space-y-2">
            {[0, 1, 2, 3, 4].map((index) => (
              <Skeleton key={index} className="h-12 w-full" />
            ))}
          </div>
        )}

        {error && (
          <EmptyState
            icon={Users}
            title="Could not load prospects"
            description={
              error instanceof ApiError
                ? error.message
                : "Something went wrong reaching the API."
            }
          />
        )}

        {!isPending && !error && prospects.length === 0 && (
          <EmptyState
            icon={Users}
            title={
              consentedOnly ? "Nobody is reachable yet" : "No prospects yet"
            }
            description={
              consentedOnly
                ? "Link a consented number to somebody to make them reachable. Sourced numbers are never dialled on their own."
                : "Run a search to source people. Everyone you find is kept here afterwards."
            }
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

        {prospects.length > 0 && (
          <>
            <ProspectTable
              prospects={prospects}
              selected={selected}
              onSelectedChange={setSelected}
              onProspectUpdated={patchRow}
            />

            <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t pt-4">
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page === 0}
                  onClick={() => setPage((current) => current - 1)}
                >
                  <ChevronLeft className="size-4" />
                  Previous
                </Button>
                <span className="text-muted-foreground text-xs tabular-nums">
                  Page {page + 1} of {pages}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page + 1 >= pages}
                  onClick={() => setPage((current) => current + 1)}
                >
                  Next
                  <ChevronRight className="size-4" />
                </Button>
              </div>

              <div className="flex items-center gap-3">
                <p className="text-muted-foreground text-sm">
                  {selected.size === 0
                    ? "Select who you want to reach."
                    : `${selected.size} selected.`}
                </p>
                <Button
                  disabled={selected.size === 0 || createCampaign.isPending}
                  onClick={() => setDetailing(true)}
                >
                  {createCampaign.isPending ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Users className="size-4" />
                  )}
                  Build outreach campaign
                </Button>
              </div>
            </div>
          </>
        )}
      </Card>

      {detailing && (
        <CampaignDetailsDialog
          open
          onOpenChange={setDetailing}
          filters={null}
          count={selected.size}
          pending={createCampaign.isPending}
          onConfirm={(details) => createCampaign.mutate(details)}
        />
      )}
    </div>
  );
}
