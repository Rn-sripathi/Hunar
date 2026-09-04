"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Download,
  Minus,
  PhoneOff,
  Play,
  RefreshCw,
  ThumbsDown,
  ThumbsUp,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/empty-state";
import { ScorePill } from "@/components/score-pill";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, queryKeys } from "@/lib/api";
import type {
  FieldSpec,
  ResultRow,
  ScoreBreakdown,
  ScoreContribution,
} from "@/lib/types";
import { cn } from "@/lib/utils";

type Filter = "all" | "interested" | "shortlisted" | "setaside";

/**
 * Renders a coerced answer according to its declared type.
 *
 * A null is drawn as an explicit dash rather than left blank, because an
 * empty cell reads as a rendering failure while a dash reads as "not
 * answered", which is what it means.
 */
function AnswerCell({ value, field }: { value: unknown; field: FieldSpec }) {
  if (value === null || value === undefined || value === "") {
    return (
      <span
        className="text-muted-foreground/60"
        title="Not answered on the call"
      >
        <Minus className="size-3.5" />
      </span>
    );
  }

  if (field.answer_type === "BOOLEAN") {
    return value === true ? (
      <span className="inline-flex items-center gap-1 text-emerald-700 dark:text-emerald-400">
        <Check className="size-3.5" /> Yes
      </span>
    ) : (
      <span className="text-muted-foreground inline-flex items-center gap-1">
        <X className="size-3.5" /> No
      </span>
    );
  }

  if (field.answer_type === "NUMBER") {
    return <span className="tabular-nums">{String(value)}</span>;
  }

  const text = String(value);
  return (
    <span className="block max-w-[18rem] truncate" title={text}>
      {text}
    </span>
  );
}

function ContributionRow({
  contribution,
}: {
  contribution: ScoreContribution;
}) {
  const tone =
    contribution.passed === true
      ? "text-emerald-700 dark:text-emerald-400"
      : contribution.passed === false
        ? "text-red-700 dark:text-red-400"
        : "text-muted-foreground";

  return (
    <div className="border-b py-2.5 last:border-0">
      <div className="flex items-start justify-between gap-3">
        <span className="text-sm font-medium">{contribution.label}</span>
        <div className="flex shrink-0 items-center gap-2">
          {contribution.is_knockout && (
            <Badge
              variant="secondary"
              className="border-transparent text-[10px]"
            >
              Required
            </Badge>
          )}
          <span className="text-muted-foreground text-xs tabular-nums">
            weight {contribution.weight}
          </span>
        </div>
      </div>
      <p className={cn("mt-0.5 text-xs", tone)}>{contribution.explanation}</p>
    </div>
  );
}

function ScoreSheet({
  row,
  onClose,
}: {
  row: ResultRow | null;
  onClose: () => void;
}) {
  const breakdown = row?.score_breakdown as ScoreBreakdown | null | undefined;
  const rawValues = row?.raw_values ?? {};

  return (
    <Sheet open={Boolean(row)} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full gap-0 overflow-y-auto sm:max-w-lg">
        {row && (
          <>
            <SheetHeader>
              <SheetTitle>{row.candidate_name}</SheetTitle>
              <SheetDescription>
                Why this candidate scored what they did, question by question.
              </SheetDescription>
            </SheetHeader>

            <div className="space-y-5 px-4 pb-6">
              <div className="flex items-center gap-4">
                <StatusBadge status={row.status} />
                <ScorePill
                  score={row.score}
                  disqualified={row.disqualified}
                  reason={row.disqualified_reason}
                  className="max-w-40"
                />
              </div>

              {row.disqualified && row.disqualified_reason && (
                <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
                  <p className="font-medium">Set aside</p>
                  <p className="mt-0.5 text-[13px]">
                    {row.disqualified_reason}
                  </p>
                </div>
              )}

              {breakdown?.contributions &&
                breakdown.contributions.length > 0 && (
                  <div>
                    <h4 className="mb-1 text-sm font-semibold">Scoring</h4>
                    {typeof breakdown.coverage === "number" && (
                      <p className="text-muted-foreground mb-2 text-xs">
                        Answered {Math.round(breakdown.coverage * 100)}% of the
                        questions. Only answered questions count towards the
                        score, so an interrupted call lowers coverage rather
                        than the score.
                      </p>
                    )}
                    <div className="rounded-md border px-3">
                      {breakdown.contributions.map((contribution) => (
                        <ContributionRow
                          key={contribution.field_key}
                          contribution={contribution}
                        />
                      ))}
                    </div>
                  </div>
                )}

              {Object.keys(rawValues).length > 0 && (
                <div>
                  <h4 className="mb-1 text-sm font-semibold">
                    What the candidate said
                  </h4>
                  <p className="text-muted-foreground mb-2 text-xs">
                    The provider returns every answer as text. These are the
                    original words, kept alongside the interpreted values above.
                  </p>
                  <div className="divide-y rounded-md border">
                    {Object.entries(rawValues).map(([key, value]) => (
                      <div key={key} className="px-3 py-2">
                        <code className="text-muted-foreground font-mono text-[11px]">
                          {key}
                        </code>
                        <p className="mt-0.5 text-[13px]">
                          {String(value) || "—"}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {row.recording_url && (
                <div>
                  <h4 className="mb-2 text-sm font-semibold">Recording</h4>
                  <audio controls src={row.recording_url} className="w-full">
                    Your browser cannot play this recording.
                  </audio>
                </div>
              )}
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

export function ResultsPanel({ jobId }: { jobId: string }) {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<Filter>("all");
  const [selected, setSelected] = useState<ResultRow | null>(null);

  const results = useQuery({
    queryKey: queryKeys.results(jobId),
    queryFn: () => api.calls.results(jobId),
    // Poll only while something is still moving, then stop. The voice API
    // pushes a webhook only once a call has finished, so in-flight
    // progress exists solely because of this.
    refetchInterval: (query) => (query.state.data?.in_progress ? 3000 : false),
    refetchIntervalInBackground: false,
  });

  const decide = useMutation({
    mutationFn: ({
      candidateId,
      decision,
    }: {
      candidateId: string;
      decision: string;
    }) => api.candidates.decide(jobId, candidateId, decision),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({
        queryKey: queryKeys.results(jobId),
      });
      void queryClient.invalidateQueries({ queryKey: queryKeys.job(jobId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs });
      toast.success(
        variables.decision === "SHORTLISTED"
          ? "Shortlisted"
          : "Marked as not proceeding",
      );
    },
  });

  const refresh = useMutation({
    mutationFn: () => api.calls.refresh(jobId),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: queryKeys.results(jobId),
      });
      toast.success("Refreshed");
    },
  });

  const columns = results.data?.columns ?? [];

  // Memoised because `?? []` would otherwise produce a new array on every
  // render, invalidating the sort below each time and re-sorting rows
  // that have not changed.
  const allRows = useMemo(() => results.data?.rows ?? [], [results.data]);

  const rows = useMemo(() => {
    const filtered = allRows.filter((row) => {
      if (filter === "interested") return row.values?.interested === true;
      if (filter === "shortlisted") return row.decision === "SHORTLISTED";
      if (filter === "setaside")
        return row.disqualified || row.decision === "REJECTED";
      return true;
    });

    // Highest score first, with unscored rows last. A recruiter opening
    // this screen wants the shortlist at the top, not the alphabet.
    return [...filtered].sort((a, b) => {
      if (a.disqualified !== b.disqualified) return a.disqualified ? 1 : -1;
      const left = a.score ?? -1;
      const right = b.score ?? -1;
      return right - left;
    });
  }, [allRows, filter]);

  const exportCsv = () => {
    const headers = [
      "Candidate",
      "Status",
      "Score",
      "Decision",
      ...columns.map((c) => c.label),
    ];
    const lines = rows.map((row) => [
      row.candidate_name,
      row.status,
      row.disqualified ? "disqualified" : (row.score ?? ""),
      row.decision,
      ...columns.map((column) => {
        const value = row.values?.[column.key];
        return value === null || value === undefined ? "" : String(value);
      }),
    ]);

    const escape = (cell: unknown) => `"${String(cell).replaceAll('"', '""')}"`;
    const csv = [headers, ...lines]
      .map((line) => line.map(escape).join(","))
      .join("\n");

    const url = URL.createObjectURL(
      new Blob([csv], { type: "text/csv;charset=utf-8" }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `screening-results.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  if (results.isPending) {
    return (
      <Card className="gap-0 p-5">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="mt-4 h-64 w-full" />
      </Card>
    );
  }

  if (allRows.length === 0) {
    return (
      <EmptyState
        icon={PhoneOff}
        title="No screening results yet"
        description="Add candidates and start screening. Answers appear here as each call finishes, and the table updates itself while calls are running."
      />
    );
  }

  const completed = results.data?.completed ?? 0;
  const total = results.data?.total ?? 0;
  const shortlisted = allRows.filter(
    (row) => row.decision === "SHORTLISTED",
  ).length;

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <Card className="gap-0 p-4">
          <p className="text-muted-foreground text-xs">Screened</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">
            {completed}
            <span className="text-muted-foreground text-base font-normal">
              {" "}
              / {total}
            </span>
          </p>
          <Progress
            value={total ? (completed / total) * 100 : 0}
            className="mt-2 h-1.5"
          />
        </Card>
        <Card className="gap-0 p-4">
          <p className="text-muted-foreground text-xs">Interested</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">
            {allRows.filter((row) => row.values?.interested === true).length}
          </p>
        </Card>
        <Card className="gap-0 p-4">
          <p className="text-muted-foreground text-xs">Shortlisted</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">
            {shortlisted}
          </p>
        </Card>
      </div>

      <Card className="gap-0 overflow-hidden p-0">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b p-3">
          <Tabs
            value={filter}
            onValueChange={(value) => setFilter(value as Filter)}
          >
            <TabsList>
              <TabsTrigger value="all">All</TabsTrigger>
              <TabsTrigger value="interested">Interested</TabsTrigger>
              <TabsTrigger value="shortlisted">Shortlist</TabsTrigger>
              <TabsTrigger value="setaside">Set aside</TabsTrigger>
            </TabsList>
          </Tabs>

          <div className="flex items-center gap-2">
            {results.data?.in_progress && (
              <span className="text-muted-foreground flex items-center gap-1.5 text-xs">
                <span className="relative flex size-1.5">
                  <span className="absolute inline-flex size-full animate-ping rounded-full bg-blue-500 opacity-70" />
                  <span className="relative inline-flex size-1.5 rounded-full bg-blue-500" />
                </span>
                updating live
              </span>
            )}
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={refresh.isPending}
              onClick={() => refresh.mutate()}
            >
              <RefreshCw
                className={cn("size-3.5", refresh.isPending && "animate-spin")}
              />
              Refresh
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={exportCsv}
            >
              <Download className="size-3.5" />
              Export
            </Button>
          </div>
        </div>

        {rows.length === 0 ? (
          <p className="text-muted-foreground p-8 text-center text-sm">
            No candidates match this filter.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader className="bg-muted/50">
                <TableRow>
                  <TableHead className="bg-muted/50 sticky left-0 min-w-44">
                    Candidate
                  </TableHead>
                  <TableHead className="min-w-28">Status</TableHead>
                  <TableHead className="min-w-32">Score</TableHead>
                  {columns.map((column) => (
                    <TableHead
                      key={column.key}
                      className="min-w-32 whitespace-nowrap"
                    >
                      {column.label}
                      {column.is_knockout && (
                        <span
                          className="ml-1 text-red-600 dark:text-red-400"
                          title="Required"
                        >
                          *
                        </span>
                      )}
                    </TableHead>
                  ))}
                  <TableHead className="min-w-32 text-right">
                    Decision
                  </TableHead>
                </TableRow>
              </TableHeader>

              <TableBody>
                {rows.map((row) => (
                  <TableRow
                    key={row.candidate_id}
                    className={cn(
                      "cursor-pointer",
                      row.disqualified && "opacity-70",
                      row.decision === "SHORTLISTED" &&
                        "bg-emerald-50/50 dark:bg-emerald-950/20",
                    )}
                    onClick={() => setSelected(row)}
                  >
                    <TableCell className="bg-background sticky left-0">
                      <div className="font-medium">{row.candidate_name}</div>
                      <div className="text-muted-foreground font-mono text-[11px]">
                        {row.mobile_masked}
                      </div>
                    </TableCell>

                    <TableCell>
                      <StatusBadge status={row.status} />
                    </TableCell>

                    <TableCell>
                      <ScorePill
                        score={row.score}
                        disqualified={row.disqualified}
                        reason={row.disqualified_reason}
                      />
                    </TableCell>

                    {columns.map((column) => (
                      <TableCell
                        key={column.key}
                        className="text-sm"
                        title={
                          row.raw_values?.[column.key]
                            ? `Said: ${String(row.raw_values[column.key])}`
                            : undefined
                        }
                      >
                        <AnswerCell
                          value={row.values?.[column.key]}
                          field={column}
                        />
                      </TableCell>
                    ))}

                    <TableCell
                      className="text-right"
                      onClick={(event) => event.stopPropagation()}
                    >
                      {row.status === "COMPLETED" ? (
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            type="button"
                            variant={
                              row.decision === "SHORTLISTED"
                                ? "default"
                                : "ghost"
                            }
                            size="icon"
                            className="size-8"
                            aria-label={`Shortlist ${row.candidate_name}`}
                            onClick={() =>
                              decide.mutate({
                                candidateId: row.candidate_id,
                                decision: "SHORTLISTED",
                              })
                            }
                          >
                            <ThumbsUp className="size-3.5" />
                          </Button>
                          <Button
                            type="button"
                            variant={
                              row.decision === "REJECTED"
                                ? "secondary"
                                : "ghost"
                            }
                            size="icon"
                            className="size-8"
                            aria-label={`Reject ${row.candidate_name}`}
                            onClick={() =>
                              decide.mutate({
                                candidateId: row.candidate_id,
                                decision: "REJECTED",
                              })
                            }
                          >
                            <ThumbsDown className="size-3.5" />
                          </Button>
                          {row.recording_url && (
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              className="size-8"
                              aria-label={`Play the call with ${row.candidate_name}`}
                              onClick={() => setSelected(row)}
                            >
                              <Play className="size-3.5" />
                            </Button>
                          )}
                        </div>
                      ) : (
                        <span className="text-muted-foreground text-xs">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </Card>

      <ScoreSheet row={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
