"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Loader2,
  PhoneOutgoing,
  Trash2,
  Upload,
  UserPlus,
  Users,
} from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/empty-state";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api, ApiError, queryKeys } from "@/lib/api";
import type { CandidateOut } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Loading candidates and starting the calls.
 *
 * The launch button opens a confirmation that states the exact number of
 * real phone calls about to be placed. This is the one irreversible
 * action in the product: once a call goes out, a person's phone rings.
 */
export function CandidatesPanel({
  jobId,
  onLaunched,
}: {
  jobId: string;
  /** Called once calls are away, so the view can move to the results. */
  onLaunched?: () => void;
}) {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);

  const [name, setName] = useState("");
  const [mobile, setMobile] = useState("");
  const [confirmLaunch, setConfirmLaunch] = useState(false);

  const candidates = useQuery({
    queryKey: queryKeys.candidates(jobId),
    queryFn: () => api.candidates.list(jobId),
  });

  const refreshAll = () => {
    void queryClient.invalidateQueries({
      queryKey: queryKeys.candidates(jobId),
    });
    void queryClient.invalidateQueries({ queryKey: queryKeys.results(jobId) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.job(jobId) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.jobs });
  };

  const add = useMutation({
    mutationFn: () =>
      api.candidates.add(jobId, { name, mobile_number: mobile, extra: {} }),
    onSuccess: () => {
      setName("");
      setMobile("");
      refreshAll();
      toast.success("Candidate added");
    },
    onError: (error) =>
      toast.error("Could not add the candidate", {
        description: error instanceof ApiError ? error.message : undefined,
      }),
  });

  const importCsv = useMutation({
    mutationFn: (file: File) => api.candidates.import(jobId, file),
    onSuccess: (report) => {
      refreshAll();
      const rejected = report.rejected ?? [];
      const parts = [`${report.imported} imported`];
      if (report.skipped_duplicates)
        parts.push(`${report.skipped_duplicates} already present`);
      if (rejected.length) parts.push(`${rejected.length} could not be read`);

      // Rejected rows are surfaced rather than swallowed: someone who
      // uploaded two hundred rows needs to know which ones were lost.
      if (rejected.length) {
        toast.warning(parts.join(", "), {
          description: rejected
            .slice(0, 3)
            .map((row) => `Row ${row.row}: ${row.reason}`)
            .join(" · "),
          duration: 8000,
        });
      } else {
        toast.success(parts.join(", "));
      }
    },
    onError: (error) =>
      toast.error("Import failed", {
        description: error instanceof ApiError ? error.message : undefined,
      }),
  });

  // Tracked as a set rather than from the mutation's own pending flag,
  // because a mutation reports on its latest call only. Deleting three
  // rows quickly would otherwise spin one of them while the other two sat
  // there looking untouched.
  const [removing, setRemoving] = useState<Set<string>>(new Set());

  /**
   * Deleting is optimistic because the round trip is slow enough to feel
   * broken: the database is a long way from the user, so a delete plus
   * its refetches can take ten seconds. The row goes immediately and is
   * put back if the server refuses, which is the honest version of fast:
   * the optimism is visible and reversible, not a lie about what happened.
   */
  const remove = useMutation({
    mutationFn: (candidateId: string) =>
      api.candidates.remove(jobId, candidateId),

    onMutate: async (candidateId) => {
      setRemoving((current) => new Set(current).add(candidateId));

      // Stop an in-flight refetch from landing after our edit and
      // resurrecting the row.
      await queryClient.cancelQueries({
        queryKey: queryKeys.candidates(jobId),
      });
      const previous = queryClient.getQueryData<CandidateOut[]>(
        queryKeys.candidates(jobId),
      );
      queryClient.setQueryData<CandidateOut[]>(
        queryKeys.candidates(jobId),
        (rows) => (rows ?? []).filter((row) => row.id !== candidateId),
      );
      return { previous };
    },

    onError: (error, _candidateId, context) => {
      // Put the row back. A candidate that quietly survives a delete the
      // user believes worked is how someone gets phoned by mistake.
      if (context?.previous) {
        queryClient.setQueryData(queryKeys.candidates(jobId), context.previous);
      }
      toast.error("Could not remove that candidate", {
        description: error instanceof ApiError ? error.message : undefined,
      });
    },

    onSettled: (_data, _error, candidateId) => {
      setRemoving((current) => {
        const next = new Set(current);
        next.delete(candidateId);
        return next;
      });
      // Only the counts need re-reading. The candidate list is already
      // correct locally, and refetching it here was a third of the wait.
      void queryClient.invalidateQueries({ queryKey: queryKeys.job(jobId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs });
    },
  });

  const launch = useMutation({
    mutationFn: () => api.calls.launch(jobId, {}),
    onSuccess: (report) => {
      refreshAll();
      void queryClient.invalidateQueries({ queryKey: queryKeys.calls(jobId) });

      if (report.launched === 0 && report.skipped > 0) {
        toast.info("Everyone has already been called", {
          description:
            "Candidates with a completed or in-flight call are not dialled again.",
        });
        return;
      }
      // Move to the results before the first call even connects. Watching
      // a static candidate list while the answers land on another tab is
      // what makes the app feel like it is not updating.
      onLaunched?.();

      const blocked = report.blocked ?? [];
      toast.success(
        `Calling ${report.launched} ${report.launched === 1 ? "candidate" : "candidates"}`,
        {
          description: blocked.length
            ? `${blocked.length} could not be dialled: ${blocked[0].reason}`
            : "Answers appear in the results tab as each call finishes.",
        },
      );
    },
    onError: (error) =>
      toast.error("Could not start calling", {
        description: error instanceof ApiError ? error.message : undefined,
      }),
  });

  const rows = candidates.data ?? [];
  const uncalled = rows.length;

  return (
    <div className="space-y-5">
      <div className="grid gap-4 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
        <Card className="gap-0 p-5">
          <h3 className="text-[15px] font-semibold">Add candidates</h3>
          <p className="text-muted-foreground mt-0.5 text-sm">
            One at a time, or import a spreadsheet.
          </p>

          <div className="mt-4 space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="candidate-name">Name</Label>
              <Input
                id="candidate-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Asha Rao"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="candidate-mobile">Mobile number</Label>
              <Input
                id="candidate-mobile"
                value={mobile}
                onChange={(event) => setMobile(event.target.value)}
                placeholder="+91 98765 43210"
                inputMode="tel"
              />
              <p className="text-muted-foreground text-xs">
                Indian numbers work without the country code.
              </p>
            </div>

            <Button
              type="button"
              className="w-full"
              disabled={!name.trim() || !mobile.trim() || add.isPending}
              onClick={() => add.mutate()}
            >
              {add.isPending ? (
                <>
                  <Loader2 className="size-4 animate-spin" />
                  Adding…
                </>
              ) : (
                <>
                  <UserPlus className="size-4" />
                  Add candidate
                </>
              )}
            </Button>

            <div className="relative py-1">
              <div className="absolute inset-0 flex items-center">
                <span className="w-full border-t" />
              </div>
              <div className="relative flex justify-center">
                <span className="bg-card text-muted-foreground px-2 text-xs">
                  or
                </span>
              </div>
            </div>

            <input
              ref={fileInput}
              type="file"
              accept=".csv,text/csv"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) importCsv.mutate(file);
                event.target.value = "";
              }}
            />
            <Button
              type="button"
              variant="outline"
              className="w-full"
              disabled={importCsv.isPending}
              onClick={() => fileInput.current?.click()}
            >
              {importCsv.isPending ? (
                <>
                  <Loader2 className="size-4 animate-spin" />
                  Importing…
                </>
              ) : (
                <>
                  <Upload className="size-4" />
                  Import CSV
                </>
              )}
            </Button>
            <p className="text-muted-foreground text-xs">
              Needs a name column and a phone column. Other columns are kept and
              can be referenced by the agent.
            </p>
          </div>
        </Card>

        <Card className="gap-0 overflow-hidden p-0">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
            <div>
              <h3 className="text-[15px] font-semibold">
                Candidates{rows.length > 0 && ` (${rows.length})`}
              </h3>
              <p className="text-muted-foreground mt-0.5 text-sm">
                Numbers are masked. Only the last four digits are shown.
              </p>
            </div>
            <Button
              type="button"
              disabled={uncalled === 0 || launch.isPending}
              onClick={() => setConfirmLaunch(true)}
            >
              <PhoneOutgoing className="size-4" />
              {launch.isPending ? "Starting…" : "Start screening"}
            </Button>
          </div>

          {rows.length === 0 ? (
            <EmptyState
              icon={Users}
              title="No candidates yet"
              description="Add someone on the left, or import a spreadsheet exported from your applicant tracker."
              className="m-4 border-0"
            />
          ) : (
            <div className="max-h-[28rem] overflow-auto">
              <Table>
                <TableHeader className="bg-muted/50 sticky top-0">
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Mobile</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead className="w-10" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((candidate) => {
                    const isRemoving = removing.has(candidate.id);
                    return (
                      <TableRow
                        key={candidate.id}
                        className={cn(
                          "transition-opacity",
                          isRemoving && "pointer-events-none opacity-50",
                        )}
                      >
                        <TableCell className="font-medium">
                          {candidate.name}
                        </TableCell>
                        <TableCell className="text-muted-foreground font-mono text-xs">
                          {candidate.mobile_masked || "•••••"}
                        </TableCell>
                        <TableCell className="text-muted-foreground text-xs capitalize">
                          {candidate.source.toLowerCase()}
                        </TableCell>
                        <TableCell>
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="text-muted-foreground hover:text-destructive size-8"
                            aria-label={
                              isRemoving
                                ? `Removing ${candidate.name}`
                                : `Remove ${candidate.name}`
                            }
                            disabled={isRemoving}
                            onClick={() => remove.mutate(candidate.id)}
                          >
                            {isRemoving ? (
                              <Loader2 className="size-3.5 animate-spin" />
                            ) : (
                              <Trash2 className="size-3.5" />
                            )}
                          </Button>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </Card>
      </div>

      <AlertDialog open={confirmLaunch} onOpenChange={setConfirmLaunch}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Place real phone calls?</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-sm">
                <p>
                  The voice agent will call up to{" "}
                  <span className="text-foreground font-semibold">
                    {uncalled}
                  </span>{" "}
                  {uncalled === 1 ? "candidate" : "candidates"} on this role and
                  conduct the screening interview.
                </p>
                <p>
                  Anyone who already has a completed or in-flight call is
                  skipped, so nobody is phoned twice about the same job.
                </p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => launch.mutate()}>
              Call {uncalled} {uncalled === 1 ? "candidate" : "candidates"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
