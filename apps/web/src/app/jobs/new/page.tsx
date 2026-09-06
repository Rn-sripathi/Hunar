"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Sparkles } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { toast } from "sonner";

import { AgentPreviewPanel } from "@/components/agent-preview";
import { JdPasteBox } from "@/components/jd-paste-box";
import { QuestionEditor } from "@/components/question-editor";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError, queryKeys } from "@/lib/api";
import {
  LANGUAGES,
  VOICES,
  defaultJobValues,
  fromDraft,
  jobFormSchema,
  sampleJobValues,
  toJobCreate,
  type JobFormValues,
} from "@/lib/job-form";

/** Debounce, so the preview does not refetch on every keystroke. */
function useDebounced<T>(value: T, delay = 500): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

export default function NewJobPage() {
  const router = useRouter();
  const queryClient = useQueryClient();

  const form = useForm<JobFormValues>({
    resolver: zodResolver(jobFormSchema),
    defaultValues: defaultJobValues(),
    mode: "onBlur",
  });

  const questions = useFieldArray({ control: form.control, name: "questions" });

  // `watch()` returns a fresh function each render, so React Compiler
  // cannot memoize this component and says so. That is a performance
  // note rather than a correctness one, and the alternative, `useWatch`,
  // returns a deeply partial type that would need widening at every use.
  // eslint-disable-next-line react-hooks/incompatible-library
  const watched = form.watch();
  const debounced = useDebounced(watched);

  // Only ask for a preview once there is a question with real text.
  // Previewing a blank form would show a script nobody wrote.
  const previewable = useMemo(
    () =>
      debounced.questions.some((question) => question.text.trim().length >= 3),
    [debounced],
  );

  const payload = useMemo(() => toJobCreate(debounced), [debounced]);

  const preview = useQuery({
    queryKey: ["preview", payload],
    queryFn: () =>
      api.jobs.preview({
        title: payload.title || "the role",
        company_name: payload.company_name || "the company",
        location: payload.location,
        description_raw: payload.description_raw ?? "",
        language: payload.language ?? "ENGLISH",
        questions: payload.questions,
      }),
    enabled: previewable,
    staleTime: 30_000,
  });

  const create = useMutation({
    mutationFn: (values: JobFormValues) => api.jobs.create(toJobCreate(values)),
    onSuccess: (job) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs });
      toast.success("Role created", {
        description: "Add candidates, then launch the screening calls.",
      });
      router.push(`/jobs/${job.id}`);
    },
    onError: (error) => {
      toast.error("Could not create the role", {
        description:
          error instanceof ApiError ? error.message : "Please try again.",
      });
    },
  });

  const isPreviewStale = preview.isFetching && preview.data !== undefined;

  return (
    <form
      onSubmit={form.handleSubmit((values) => create.mutate(values))}
      className="space-y-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Button asChild variant="ghost" size="sm" className="-ml-2 mb-1">
            <Link href="/jobs">
              <ArrowLeft className="size-4" />
              Roles
            </Link>
          </Button>
          <h1 className="text-2xl font-semibold tracking-tight">New role</h1>
          <p className="text-muted-foreground mt-1 max-w-xl text-sm">
            The job description and questions below become the script the voice
            agent reads on the phone. You can see exactly what it will say
            before anyone is called.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => form.reset(sampleJobValues())}
          >
            <Sparkles className="size-4" />
            Use a sample role
          </Button>
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? "Creating…" : "Create role"}
          </Button>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
        <div className="space-y-6">
          <JdPasteBox
            onExtracted={(draft, jdText) => {
              // Replaces the whole form rather than merging. A partial
              // merge would leave fields from a previous description
              // sitting beside the new one, which is worse than either.
              form.reset(fromDraft(draft, jdText));
            }}
          />

          <Card className="gap-0 p-5">
            <h2 className="text-[15px] font-semibold">The role</h2>
            <p className="text-muted-foreground mt-0.5 text-sm">
              Used to introduce the job on the call.
            </p>

            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="title">Job title</Label>
                <Input
                  id="title"
                  placeholder="Delivery Executive"
                  {...form.register("title")}
                  aria-invalid={Boolean(form.formState.errors.title)}
                />
                {form.formState.errors.title && (
                  <p className="text-destructive text-xs">
                    {form.formState.errors.title.message}
                  </p>
                )}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="company">Company</Label>
                <Input
                  id="company"
                  placeholder="Acme Logistics"
                  {...form.register("company_name")}
                  aria-invalid={Boolean(form.formState.errors.company_name)}
                />
                {form.formState.errors.company_name && (
                  <p className="text-destructive text-xs">
                    {form.formState.errors.company_name.message}
                  </p>
                )}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="location">Location</Label>
                <Input
                  id="location"
                  placeholder="Bengaluru"
                  {...form.register("location")}
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label>Language</Label>
                  <Select
                    value={watched.language}
                    onValueChange={(value) =>
                      form.setValue("language", value, { shouldDirty: true })
                    }
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {LANGUAGES.map((language) => (
                        <SelectItem key={language} value={language}>
                          {language.charAt(0) + language.slice(1).toLowerCase()}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-1.5">
                  <Label>Voice</Label>
                  <Select
                    value={watched.voice_persona}
                    onValueChange={(value) =>
                      form.setValue("voice_persona", value, {
                        shouldDirty: true,
                      })
                    }
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {VOICES.map((voice) => (
                        <SelectItem key={voice} value={voice}>
                          {voice.charAt(0) + voice.slice(1).toLowerCase()}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </div>

            <div className="mt-4 space-y-1.5">
              <Label htmlFor="description">Job description</Label>
              <Textarea
                id="description"
                rows={7}
                placeholder="Paste the job description. The agent uses it to answer questions about the role, and never reads it aloud."
                {...form.register("description_raw")}
              />
              <p className="text-muted-foreground text-xs">
                Pasted formatting is safe. Braces are stripped before the text
                reaches the agent, so a snippet or a salary range cannot corrupt
                what it says.
              </p>
            </div>
          </Card>

          <Card className="gap-0 p-5">
            <div className="mb-4 flex items-start justify-between gap-4">
              <div>
                <h2 className="text-[15px] font-semibold">
                  Screening questions
                </h2>
                <p className="text-muted-foreground mt-0.5 text-sm">
                  Asked one at a time, in this order. Each becomes a column in
                  the results.
                </p>
              </div>
              <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
                {questions.fields.length} / 15
              </span>
            </div>

            <QuestionEditor form={form} fields={questions} />

            {form.formState.errors.questions?.root && (
              <p className="text-destructive mt-2 text-xs">
                {form.formState.errors.questions.root.message}
              </p>
            )}
          </Card>
        </div>

        <div className="lg:sticky lg:top-20 lg:self-start">
          <Card className="gap-0 overflow-hidden p-0">
            <AgentPreviewPanel
              preview={preview.data}
              isLoading={preview.isPending && previewable}
              isStale={isPreviewStale}
            />
          </Card>
        </div>
      </div>
    </form>
  );
}
