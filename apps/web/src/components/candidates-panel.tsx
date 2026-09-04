"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PhoneOutgoing, Trash2, Upload, UserPlus, Users } from "lucide-react";
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

/**
 * Loading candidates and starting the calls.
 *
 * The launch button opens a confirmation that states the exact number of
 * real phone calls about to be placed. This is the one irreversible
 * action in the product: once a call goes out, a person's phone rings.
 */
export function CandidatesPanel({ jobId }: { jobId: string }) {
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

  const remove = useMutation({
    mutationFn: (candidateId: string) =>
      api.candidates.remove(jobId, candidateId),
    onSuccess: refreshAll,
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
              <UserPlus className="size-4" />
              Add candidate
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
              <Upload className="size-4" />
              {importCsv.isPending ? "Importing…" : "Import CSV"}
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
                  {rows.map((candidate) => (
                    <TableRow key={candidate.id}>
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
                          aria-label={`Remove ${candidate.name}`}
                          onClick={() => remove.mutate(candidate.id)}
                        >
                          <Trash2 className="size-3.5" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
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
