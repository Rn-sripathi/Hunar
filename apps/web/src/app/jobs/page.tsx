"use client";

import { useQuery } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { Briefcase, MapPin, Plus, Users } from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError, queryKeys } from "@/lib/api";
import type { JobSummary } from "@/lib/types";
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

function JobCard({ job }: { job: JobSummary }) {
  const called = job.call_count ?? 0;
  const completed = job.completed_count ?? 0;
  const candidates = job.candidate_count ?? 0;
  const progress =
    candidates > 0 ? Math.round((completed / candidates) * 100) : 0;

  return (
    <Link href={`/jobs/${job.id}`} className="group focus-visible:outline-none">
      <Card className="hover:border-foreground/20 h-full gap-0 p-5 transition-colors focus-within:ring-2 group-focus-visible:ring-2">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="truncate text-[15px] font-semibold tracking-tight">
              {job.title}
            </h3>
            <p className="text-muted-foreground mt-0.5 truncate text-sm">
              {job.company_name}
            </p>
          </div>
          <Badge
            variant="secondary"
            className={cn(
              "shrink-0 border-transparent",
              STATUS_TONE[job.status] ?? "",
            )}
          >
            {STATUS_LABEL[job.status] ?? job.status}
          </Badge>
        </div>

        <div className="text-muted-foreground mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
          {job.location && (
            <span className="inline-flex items-center gap-1">
              <MapPin className="size-3" />
              {job.location}
            </span>
          )}
          <span className="inline-flex items-center gap-1">
            <Users className="size-3" />
            {candidates} {candidates === 1 ? "candidate" : "candidates"}
          </span>
          <span>{job.language.toLowerCase()}</span>
        </div>

        <div className="mt-4">
          <div className="text-muted-foreground mb-1.5 flex items-center justify-between text-xs">
            <span>
              {completed} of {candidates} screened
            </span>
            {called > completed && (
              <span className="text-blue-600 dark:text-blue-400">
                {called - completed} in flight
              </span>
            )}
          </div>
          <Progress value={progress} className="h-1.5" />
        </div>

        <div className="text-muted-foreground mt-4 flex items-center justify-between border-t pt-3 text-xs">
          <span>
            {(job.shortlisted_count ?? 0) > 0
              ? `${job.shortlisted_count} shortlisted`
              : "No shortlist yet"}
          </span>
          <span>
            {formatDistanceToNow(new Date(job.created_at), { addSuffix: true })}
          </span>
        </div>
      </Card>
    </Link>
  );
}

function LoadingGrid() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {[0, 1, 2].map((index) => (
        <Card key={index} className="gap-0 p-5">
          <Skeleton className="h-5 w-2/3" />
          <Skeleton className="mt-2 h-4 w-1/3" />
          <Skeleton className="mt-5 h-3 w-full" />
          <Skeleton className="mt-4 h-3 w-1/2" />
        </Card>
      ))}
    </div>
  );
}

export default function JobsPage() {
  const {
    data: jobs,
    isPending,
    error,
  } = useQuery({
    queryKey: queryKeys.jobs,
    queryFn: api.jobs.list,
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Roles</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Define a role, load candidates, and let the voice agent screen them
            by phone.
          </p>
        </div>
        <Button asChild>
          <Link href="/jobs/new">
            <Plus className="size-4" />
            New role
          </Link>
        </Button>
      </div>

      {isPending && <LoadingGrid />}

      {error && (
        <EmptyState
          icon={Briefcase}
          title="Could not load roles"
          description={
            error instanceof ApiError
              ? error.message
              : "Something went wrong reaching the API."
          }
        />
      )}

      {jobs && jobs.length === 0 && (
        <EmptyState
          icon={Briefcase}
          title="No roles yet"
          description="A role holds the job description and the questions the voice agent will ask. Create one to start screening candidates."
          action={
            <Button asChild>
              <Link href="/jobs/new">
                <Plus className="size-4" />
                Create your first role
              </Link>
            </Button>
          }
        />
      )}

      {jobs && jobs.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {jobs.map((job) => (
            <JobCard key={job.id} job={job} />
          ))}
        </div>
      )}
    </div>
  );
}
