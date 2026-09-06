"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, MapPin, Mic, Users } from "lucide-react";
import Link from "next/link";
import { use, useState } from "react";

import { AgentPreviewPanel } from "@/components/agent-preview";
import { CandidatesPanel } from "@/components/candidates-panel";
import { EmptyState } from "@/components/empty-state";
import { ResultsPanel } from "@/components/results-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, ApiError, queryKeys } from "@/lib/api";
import type { JobDetail } from "@/lib/types";
import { useLivePoll } from "@/lib/use-live-poll";
import { cn } from "@/lib/utils";

const STATUS_TONE: Record<string, string> = {
  DRAFT: "bg-muted text-muted-foreground",
  READY: "bg-blue-50 text-blue-700 dark:bg-blue-950/50 dark:text-blue-300",
  CALLING: "bg-blue-50 text-blue-700 dark:bg-blue-950/50 dark:text-blue-300",
  DONE: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300",
};

const STATUS_LABEL: Record<string, string> = {
  DRAFT: "Draft",
  READY: "Ready to call",
  CALLING: "Calling",
  DONE: "Screening complete",
};

export default function JobDetailPage({
  params,
}: {
  params: Promise<{ jobId: string }>;
}) {
  const { jobId } = use(params);

  // The header counts have to move too. A role stays CALLING until every
  // call has settled *and* delivered its answers, so that status is the
  // honest signal for "something is still happening here".
  const poll = useLivePoll<JobDetail>((data) => data?.status === "CALLING");

  const job = useQuery({
    queryKey: queryKeys.job(jobId),
    queryFn: () => api.jobs.get(jobId),
    refetchInterval: poll.refetchInterval,
    refetchIntervalInBackground: true,
  });

  // Land on Results once calling has started. Launching happens on the
  // Candidates tab, and leaving the user there watching a static list
  // while the answers arrive one tab over is the whole problem.
  const [tab, setTab] = useState<string | null>(null);
  const started = job.data ? job.data.call_count > 0 : false;
  const activeTab = tab ?? (started ? "results" : "candidates");

  if (job.isPending) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-96" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (job.error || !job.data) {
    return (
      <EmptyState
        icon={Users}
        title="Role not found"
        description={
          job.error instanceof ApiError
            ? job.error.message
            : "This role may have been archived."
        }
        action={
          <Button asChild variant="outline">
            <Link href="/jobs">Back to roles</Link>
          </Button>
        }
      />
    );
  }

  const detail = job.data;

  return (
    <div className="space-y-6">
      <div>
        <Button asChild variant="ghost" size="sm" className="-ml-2 mb-1">
          <Link href="/jobs">
            <ArrowLeft className="size-4" />
            Roles
          </Link>
        </Button>

        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-2.5">
              <h1 className="text-2xl font-semibold tracking-tight">
                {detail.title}
              </h1>
              <Badge
                variant="secondary"
                className={cn(
                  "border-transparent",
                  STATUS_TONE[detail.status] ?? "",
                )}
              >
                {STATUS_LABEL[detail.status] ?? detail.status}
              </Badge>
            </div>

            <div className="text-muted-foreground mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
              <span>{detail.company_name}</span>
              {detail.location && (
                <span className="inline-flex items-center gap-1">
                  <MapPin className="size-3.5" />
                  {detail.location}
                </span>
              )}
              <span className="inline-flex items-center gap-1">
                <Mic className="size-3.5" />
                {detail.voice_persona.charAt(0) +
                  detail.voice_persona.slice(1).toLowerCase()}
                {", "}
                {detail.language.toLowerCase()}
              </span>
            </div>
          </div>

          <div className="flex gap-6 text-sm">
            <div>
              <p className="text-muted-foreground text-xs">Candidates</p>
              <p className="text-lg font-semibold tabular-nums">
                {detail.candidate_count}
              </p>
            </div>
            <div>
              <p className="text-muted-foreground text-xs">Screened</p>
              <p className="text-lg font-semibold tabular-nums">
                {detail.completed_count}
              </p>
            </div>
            <div>
              <p className="text-muted-foreground text-xs">Shortlisted</p>
              <p className="text-lg font-semibold tabular-nums">
                {detail.shortlisted_count}
              </p>
            </div>
          </div>
        </div>
      </div>

      <Tabs value={activeTab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="candidates">Candidates</TabsTrigger>
          <TabsTrigger value="results">Results</TabsTrigger>
          <TabsTrigger value="script">Call script</TabsTrigger>
        </TabsList>

        <TabsContent value="candidates" className="mt-5">
          <CandidatesPanel jobId={jobId} onLaunched={() => setTab("results")} />
        </TabsContent>

        <TabsContent value="results" className="mt-5">
          <ResultsPanel jobId={jobId} />
        </TabsContent>

        <TabsContent value="script" className="mt-5">
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
            <Card className="gap-0 p-5">
              <h3 className="text-[15px] font-semibold">Questions</h3>
              <p className="text-muted-foreground mt-0.5 text-sm">
                Asked in this order. Once screening has started these are fixed,
                because answers already collected only mean anything against the
                questions that produced them.
              </p>

              <ol className="mt-4 space-y-3">
                {(detail.questions ?? []).map((question, index) => (
                  <li key={question.id} className="flex gap-3">
                    <span className="bg-muted text-muted-foreground mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-medium tabular-nums">
                      {index + 1}
                    </span>
                    <div className="min-w-0">
                      <p className="text-sm">{question.text}</p>
                      <div className="text-muted-foreground mt-1 flex flex-wrap items-center gap-2 text-xs">
                        <span className="font-medium">{question.label}</span>
                        <span>·</span>
                        <span className="lowercase">
                          {question.answer_type}
                        </span>
                        {question.weight > 0 && (
                          <>
                            <span>·</span>
                            <span>weight {question.weight}</span>
                          </>
                        )}
                        {question.is_knockout && (
                          <Badge
                            variant="secondary"
                            className="border-transparent bg-red-50 text-[10px] text-red-700 dark:bg-red-950/50 dark:text-red-300"
                          >
                            Required
                          </Badge>
                        )}
                      </div>
                    </div>
                  </li>
                ))}
              </ol>

              {detail.description_raw && (
                <div className="mt-5 border-t pt-4">
                  <h4 className="text-sm font-semibold">Job description</h4>
                  <p className="text-muted-foreground mt-1.5 text-[13px] leading-relaxed whitespace-pre-wrap">
                    {detail.description_raw}
                  </p>
                </div>
              )}
            </Card>

            <Card className="gap-0 overflow-hidden p-0 lg:sticky lg:top-20 lg:self-start">
              <AgentPreviewPanel preview={detail.preview} />
            </Card>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
