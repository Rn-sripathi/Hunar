"use client";

import { Loader2, MessageSquareQuote, ScrollText, Table2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { AgentPreview as AgentPreviewData } from "@/lib/types";

/**
 * Shows exactly what the voice agent will be told, before any call.
 *
 * This is the screen that turns the product from a black box into
 * something a recruiter can correct. They wrote a job description and
 * some questions; this is the script and the extraction schema those
 * produced. If the agent is going to say something wrong, it is visible
 * here rather than discovered from a confused candidate on the phone.
 */

const TYPE_TONE: Record<string, string> = {
  boolean: "bg-blue-50 text-blue-700 dark:bg-blue-950/50 dark:text-blue-300",
  number:
    "bg-violet-50 text-violet-700 dark:bg-violet-950/50 dark:text-violet-300",
  string: "bg-muted text-muted-foreground",
};

function Script({ label, body }: { label: string; body: string }) {
  return (
    <div>
      <p className="text-muted-foreground mb-1.5 text-xs font-medium tracking-wide uppercase">
        {label}
      </p>
      <p className="bg-muted/50 rounded-md border p-3 text-[13px] leading-relaxed whitespace-pre-wrap">
        {body}
      </p>
    </div>
  );
}

export function AgentPreviewPanel({
  preview,
  isLoading,
  isStale,
}: {
  preview?: AgentPreviewData | null;
  isLoading?: boolean;
  isStale?: boolean;
}) {
  if (isLoading && !preview) {
    return (
      <div className="space-y-3 p-5">
        <Skeleton className="h-4 w-1/3" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-4 w-1/4" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (!preview) {
    return (
      <div className="text-muted-foreground flex h-full min-h-64 flex-col items-center justify-center gap-2 p-8 text-center">
        <ScrollText className="size-5" />
        <p className="text-sm text-balance">
          Add a question and the generated call script will appear here.
        </p>
      </div>
    );
  }

  const schema = Object.entries(preview.result_schema ?? {});

  return (
    <div className="relative">
      {isStale && (
        <div className="text-muted-foreground absolute top-4 right-4 z-10 flex items-center gap-1.5 text-xs">
          <Loader2 className="size-3 animate-spin" />
          updating
        </div>
      )}

      <Tabs defaultValue="script" className="gap-0">
        <div className="border-b px-4 pt-4">
          <TabsList>
            <TabsTrigger value="script">
              <MessageSquareQuote className="size-3.5" />
              Call script
            </TabsTrigger>
            <TabsTrigger value="schema">
              <Table2 className="size-3.5" />
              Answers captured
            </TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="script" className="space-y-4 p-4">
          <Script label="Opening line" body={preview.introduction} />
          <Script label="Goal" body={preview.objective} />

          <div>
            <p className="text-muted-foreground mb-1.5 text-xs font-medium tracking-wide uppercase">
              Standing instructions
            </p>
            <pre className="bg-muted/50 max-h-[26rem] overflow-auto rounded-md border p-3 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap">
              {preview.agent_prompt}
            </pre>
          </div>

          {preview.variables.length > 0 && (
            <div>
              <p className="text-muted-foreground mb-1.5 text-xs font-medium tracking-wide uppercase">
                Filled in per candidate
              </p>
              <div className="flex flex-wrap gap-1.5">
                {preview.variables.map((variable) => (
                  <code
                    key={variable}
                    className="bg-muted rounded px-1.5 py-0.5 font-mono text-[11px]"
                  >
                    {variable}
                  </code>
                ))}
              </div>
            </div>
          )}
        </TabsContent>

        <TabsContent value="schema" className="space-y-4 p-4">
          <p className="text-muted-foreground text-[13px] leading-relaxed">
            Each row below becomes a column in the results table. The voice
            provider returns every value as text, so answers are converted to
            the type shown here and the original wording is kept alongside.
          </p>

          <div className="divide-y rounded-md border">
            {schema.map(([key, type]) => (
              <div
                key={key}
                className="flex items-center justify-between gap-3 px-3 py-2"
              >
                <code className="font-mono text-[12px]">{key}</code>
                <Badge
                  variant="secondary"
                  className={`shrink-0 border-transparent ${TYPE_TONE[type] ?? TYPE_TONE.string}`}
                >
                  {type}
                </Badge>
              </div>
            ))}
          </div>

          <div>
            <p className="text-muted-foreground mb-1.5 text-xs font-medium tracking-wide uppercase">
              Extraction instructions
            </p>
            <pre className="bg-muted/50 max-h-72 overflow-auto rounded-md border p-3 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap">
              {preview.result_prompt}
            </pre>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
