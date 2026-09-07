"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ChevronDown,
  Code2,
  Loader2,
  Lock,
  PhoneCall,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  TriangleAlert,
  Users,
  Wand2,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import {
  CampaignDetailsDialog,
  type CampaignDetails,
} from "@/components/campaign-details-dialog";
import { FilterEditor } from "@/components/filter-editor";
import { ProspectTable } from "@/components/prospect-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError, queryKeys } from "@/lib/api";
import type { SearchFilters, SearchResponse } from "@/lib/types";

const EMPTY_FILTERS: SearchFilters = {
  titles: [],
  excluded_titles: [],
  seniorities: [],
  skills_required: [],
  skills_nice: [],
  cities: [],
  country: "india",
  industries: [],
  company_size_bands: [],
  exclude_companies: [],
  min_years: null,
  max_years: null,
  require_phone: true,
  hiring_title: "",
  company_name: "",
  role_pitch: "",
  comp_range_text: "",
  work_mode: "",
};

/**
 * What this deployment may and may not do, stated in the product.
 *
 * This is not decoration. Sourced numbers come from a data broker, and
 * the honest position is that finding someone and being allowed to phone
 * them are different permissions. Saying so on the screen where people
 * are sourced is more useful than saying it in a README.
 */
function PolicyCard() {
  // Refetched on a timer because two things here go stale on their own.
  // The calling window closes at 19:00 whether or not anyone reloads, and
  // an operator who edits the environment's allowlist should see it
  // without restarting the browser. A minute is frequent enough that the
  // banner is never meaningfully wrong and cheap enough to ignore: this
  // endpoint reads settings and one small table, and spends no provider
  // credits.
  //
  // Search results deliberately do *not* poll. Refreshing them means
  // re-running the provider query, and a background timer that quietly
  // spends a recruiter's credits would be indefensible.
  const { data: policy } = useQuery({
    queryKey: queryKeys.policy,
    queryFn: api.people.policy,
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  });

  if (!policy) return null;

  return (
    <Card className="gap-0 border-blue-200 bg-blue-50/60 p-4 dark:border-blue-900 dark:bg-blue-950/30">
      <div className="flex items-start gap-3">
        <ShieldCheck className="mt-0.5 size-4 shrink-0 text-blue-700 dark:text-blue-300" />
        <div className="min-w-0 flex-1 text-[13px] leading-relaxed text-blue-950 dark:text-blue-100">
          <p className="font-medium">Sourcing is broad. Calling is narrow.</p>
          <p className="mt-1 text-blue-900/85 dark:text-blue-200/85">
            Anyone can be found here. Only numbers on the operator&rsquo;s
            consent allowlist can be phoned, and that list is supplied through
            the environment so this app cannot extend its own permission to call
            people.{" "}
            {!policy.provider_reveals_phone && (
              <>
                The <span className="font-medium">{policy.provider}</span>{" "}
                provider does not return dialable numbers on this plan, which is
                the same conclusion arrived at from the other direction.
              </>
            )}
          </p>

          <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-blue-900/75 dark:text-blue-200/75">
            <span>
              Calling window {policy.calling_hours_start}&ndash;
              {policy.calling_hours_end} {policy.calling_timezone}
              {policy.within_calling_hours ? " (open now)" : " (closed now)"}
            </span>
            <span>
              {policy.allowlist.length} number
              {policy.allowlist.length === 1 ? "" : "s"} consented
            </span>
          </div>
        </div>
      </div>
    </Card>
  );
}

export default function SourcingPage() {
  const router = useRouter();

  const [jdText, setJdText] = useState("");
  const [filters, setFilters] = useState<SearchFilters | null>(null);
  const [method, setMethod] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [limit, setLimit] = useState(25);
  const [results, setResults] = useState<SearchResponse | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [detailing, setDetailing] = useState(false);

  const extract = useMutation({
    mutationFn: () => api.people.extractFilters(jdText),
    onSuccess: (result) => {
      setFilters(result.filters);
      setMethod(result.method);
      setNote(result.note ?? null);
      setResults(null);
      setSelected(new Set());
      if (result.method === "llm") {
        toast.success("Read the description into filters", {
          description: "Check them below, then search.",
        });
      } else {
        toast.warning("Filled the filters in from keywords", {
          description: "No model was available, so review these carefully.",
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

  const search = useMutation({
    mutationFn: () =>
      api.people.search({ jd_text: jdText.trim(), filters, limit }),
    onSuccess: (response) => {
      const found = response.prospects ?? [];
      setResults(response);
      setSelected(new Set());
      setNote(response.note ?? null);
      toast.success(
        `Found ${found.length} ${found.length === 1 ? "person" : "people"}`,
        {
          description:
            (response.callable_count ?? 0) > 0
              ? `${response.callable_count} can be called from this deployment.`
              : "None are on the consent allowlist yet. Link a consented number to reach someone.",
        },
      );
    },
    onError: (error) =>
      toast.error("The search failed", {
        description:
          error instanceof ApiError ? error.message : "Please try again.",
      }),
  });

  const createCampaign = useMutation({
    mutationFn: (details: CampaignDetails) =>
      api.campaigns.create({
        search_id: results?.search_id ?? null,
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

  const canExtract = jdText.trim().length >= 40 && !extract.isPending;
  const busy = search.isPending || createCampaign.isPending;

  // Mirrors the server's own rule, so the button explains itself instead
  // of the request coming back as a validation error. Country is excluded
  // on purpose: every record has one, so filtering only by it is a search
  // for everybody.
  const filtersAreEmpty =
    filters !== null &&
    !filters.titles?.length &&
    !filters.skills_required?.length &&
    !filters.skills_nice?.length &&
    !filters.cities?.length &&
    !filters.industries?.length &&
    !filters.seniorities?.length &&
    filters.min_years === null &&
    filters.max_years === null;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Find people</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Paste a job description, source people who never applied, and reach
            the ones you are allowed to call.
          </p>
        </div>
        <Button variant="outline" asChild>
          <Link href="/sourcing/campaigns">
            <PhoneCall className="size-4" />
            Campaigns
          </Link>
        </Button>
      </div>

      <PolicyCard />

      {/* ── step one: the description ─────────────────────── */}
      <Card className="gap-0 p-5">
        <div className="flex items-start gap-3">
          <span className="bg-primary/10 text-primary mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md">
            <Search className="size-4" />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="text-[15px] font-semibold">
              Describe who you are looking for
            </h2>
            <p className="text-muted-foreground mt-0.5 text-sm">
              The description is read into search filters you can correct. It
              costs nothing until you press search.
            </p>

            <Textarea
              value={jdText}
              onChange={(event) => setJdText(event.target.value)}
              rows={6}
              className="mt-3"
              placeholder={
                "Paste the job description here.\n\n" +
                "For example: We are hiring a Senior Backend Engineer in Bengaluru. " +
                "5+ years with Python and PostgreSQL, ideally some Kafka. Hybrid, three " +
                "days in the office. 35 to 50 LPA."
              }
            />

            <div className="mt-3 flex flex-wrap items-center gap-3">
              <Button
                type="button"
                disabled={!canExtract}
                onClick={() => extract.mutate()}
              >
                {extract.isPending ? (
                  <>
                    <Loader2 className="size-4 animate-spin" />
                    Reading…
                  </>
                ) : (
                  <>
                    <Wand2 className="size-4" />
                    Read into filters
                  </>
                )}
              </Button>

              {/* Always offered, never gated on having typed a description.
                  Requiring text in the box before this appeared made
                  manual entry a fallback from the thing it replaces, and
                  a recruiter who already knows who they want should not
                  have to write a job advert to say so. */}
              {!filters && (
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    setFilters(EMPTY_FILTERS);
                    setMethod("manual");
                    setNote(null);
                  }}
                >
                  <SlidersHorizontal className="size-4" />
                  Skip this, I&rsquo;ll set the filters
                </Button>
              )}

              {method === "llm" && (
                <span className="text-xs text-emerald-700 dark:text-emerald-400">
                  A model read the description.
                </span>
              )}
              {method === "heuristic" && (
                <Badge variant="secondary" className="gap-1 font-normal">
                  <TriangleAlert className="size-3" />
                  Keyword matched
                </Badge>
              )}
            </div>

            {note && (
              <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-[13px] text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
                <TriangleAlert className="mt-0.5 size-4 shrink-0" />
                <p className="leading-relaxed">{note}</p>
              </div>
            )}
          </div>
        </div>
      </Card>

      {/* ── step two: correct the interpretation ──────────── */}
      {filters && (
        <Card className="gap-0 p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-[15px] font-semibold">
                {method === "manual"
                  ? "Search filters"
                  : "Check what will be searched"}
              </h2>
              <p className="text-muted-foreground mt-0.5 text-sm">
                {method === "manual"
                  ? "Add at least a title, a city, a skill or a seniority. The more specific, the fewer people someone has to read through."
                  : "Turning prose into filters is a guess however it is made. This is where a wrong guess is still free to fix."}
              </p>
            </div>
            <div className="flex items-end gap-2">
              <div>
                <Label htmlFor="limit" className="text-xs">
                  Results
                </Label>
                <Input
                  id="limit"
                  type="number"
                  min={1}
                  max={100}
                  value={limit}
                  onChange={(event) =>
                    setLimit(
                      Math.min(
                        100,
                        Math.max(1, Number(event.target.value) || 1),
                      ),
                    )
                  }
                  className="mt-1 h-9 w-20 text-sm"
                />
              </div>
              <Button
                onClick={() => search.mutate()}
                disabled={busy || filtersAreEmpty}
                title={
                  filtersAreEmpty
                    ? "Add a title, city, skill or seniority first"
                    : undefined
                }
              >
                {search.isPending ? (
                  <>
                    <Loader2 className="size-4 animate-spin" />
                    Searching…
                  </>
                ) : (
                  <>
                    <Search className="size-4" />
                    Search
                  </>
                )}
              </Button>
            </div>
          </div>

          <Separator className="my-5" />

          <FilterEditor
            filters={filters}
            onChange={setFilters}
            disabled={busy}
          />
        </Card>
      )}

      {/* ── step three: the people ────────────────────────── */}
      {results && (
        <Card className="gap-0 p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-[15px] font-semibold">
                {(results.prospects ?? []).length}{" "}
                {(results.prospects ?? []).length === 1 ? "person" : "people"}
              </h2>
              <p className="text-muted-foreground mt-0.5 text-sm">
                Ranked by fit for outreach priority, never as a judgement about
                anyone. Hover a score to see how it was reached.
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="secondary" className="font-normal">
                {results.provider_degraded_to ?? results.provider}
              </Badge>
              {results.provider_degraded_to && (
                <Badge
                  variant="secondary"
                  className="gap-1 border-transparent bg-amber-50 font-normal text-amber-800 dark:bg-amber-950/40 dark:text-amber-300"
                >
                  <TriangleAlert className="size-3" />
                  Sample data
                </Badge>
              )}
              <Badge variant="secondary" className="gap-1 font-normal">
                <Lock className="size-3" />
                {results.callable_count ?? 0} reachable
              </Badge>
              {(results.credits_charged ?? 0) > 0 && (
                <Badge variant="secondary" className="font-normal">
                  {results.credits_charged} credit
                  {results.credits_charged === 1 ? "" : "s"}
                </Badge>
              )}
            </div>
          </div>

          <Collapsible className="mt-4">
            <CollapsibleTrigger className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1.5 text-xs">
              <Code2 className="size-3.5" />
              Show the exact query that was sent
              <ChevronDown className="size-3.5" />
            </CollapsibleTrigger>
            <CollapsibleContent>
              <pre className="bg-muted mt-2 max-h-64 overflow-auto rounded-md p-3 text-[11px] leading-relaxed">
                {JSON.stringify(results.provider_query, null, 2)}
              </pre>
            </CollapsibleContent>
          </Collapsible>

          <Separator className="my-5" />

          <ProspectTable
            prospects={results.prospects ?? []}
            selected={selected}
            onSelectedChange={setSelected}
            onProspectUpdated={(updated) =>
              setResults((current) =>
                current
                  ? {
                      ...current,
                      prospects: (current.prospects ?? []).map((p) =>
                        p.id === updated.id ? updated : p,
                      ),
                      callable_count: (current.prospects ?? []).filter((p) =>
                        p.id === updated.id ? updated.consented : p.consented,
                      ).length,
                    }
                  : current,
              )
            }
          />

          <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t pt-4">
            <p className="text-muted-foreground text-sm">
              {selected.size === 0
                ? "Select the people you want to reach."
                : `${selected.size} selected.`}{" "}
              The consent gate runs again when you launch, so a selection made
              now cannot bypass it later.
            </p>
            <Button
              disabled={selected.size === 0 || busy}
              onClick={() => setDetailing(true)}
            >
              {createCampaign.isPending ? (
                <>
                  <Loader2 className="size-4 animate-spin" />
                  Building…
                </>
              ) : (
                <>
                  <Users className="size-4" />
                  Build outreach campaign
                </>
              )}
            </Button>
          </div>
        </Card>
      )}

      {detailing && (
        <CampaignDetailsDialog
          open
          onOpenChange={setDetailing}
          filters={filters}
          count={selected.size}
          pending={createCampaign.isPending}
          onConfirm={(details) => createCampaign.mutate(details)}
        />
      )}
    </div>
  );
}
