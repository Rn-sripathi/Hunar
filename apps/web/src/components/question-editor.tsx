"use client";

import { ChevronDown, GripVertical, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import type { UseFieldArrayReturn, UseFormReturn } from "react-hook-form";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { JobFormValues } from "@/lib/job-form";
import { ANSWER_TYPES, RULE_HINT, emptyQuestion } from "@/lib/job-form";
import { cn } from "@/lib/utils";

/**
 * The screening question builder.
 *
 * Deliberately progressive: the two fields a recruiter always needs are
 * on the surface, and weighting, knockouts and matching rules are folded
 * away behind "Scoring". Most roles never open it, and the ones that do
 * are making a considered decision about how candidates get ranked.
 */
export function QuestionEditor({
  form,
  fields,
}: {
  form: UseFormReturn<JobFormValues>;
  fields: UseFieldArrayReturn<JobFormValues, "questions", "id">;
}) {
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const errors = form.formState.errors.questions;

  return (
    <div className="space-y-3">
      {fields.fields.map((field, index) => {
        const answerType = form.watch(`questions.${index}.answer_type`);
        const isKnockout = form.watch(`questions.${index}.is_knockout`);
        const label = form.watch(`questions.${index}.label`);
        const open = expanded[index] ?? false;
        const rowErrors = errors?.[index];

        return (
          <Card key={field.id} className="gap-0 overflow-hidden p-0">
            <div className="flex items-start gap-2 p-3">
              <span
                className="text-muted-foreground mt-2.5 cursor-grab"
                aria-hidden
              >
                <GripVertical className="size-4" />
              </span>

              <div className="min-w-0 flex-1 space-y-2.5">
                <div className="grid gap-2.5 sm:grid-cols-[1fr_11rem]">
                  <div className="space-y-1">
                    <Label
                      htmlFor={`q-${index}-label`}
                      className="text-muted-foreground text-xs"
                    >
                      Column heading
                    </Label>
                    <Input
                      id={`q-${index}-label`}
                      placeholder="Years of experience"
                      {...form.register(`questions.${index}.label`)}
                      aria-invalid={Boolean(rowErrors?.label)}
                    />
                    {rowErrors?.label && (
                      <p className="text-destructive text-xs">
                        {rowErrors.label.message}
                      </p>
                    )}
                  </div>

                  <div className="space-y-1">
                    <Label className="text-muted-foreground text-xs">
                      Answer type
                    </Label>
                    <Select
                      value={answerType}
                      onValueChange={(value) =>
                        form.setValue(
                          `questions.${index}.answer_type`,
                          value as JobFormValues["questions"][number]["answer_type"],
                          { shouldDirty: true },
                        )
                      }
                    >
                      <SelectTrigger className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {ANSWER_TYPES.map((type) => (
                          <SelectItem key={type.value} value={type.value}>
                            {type.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                <div className="space-y-1">
                  <Label
                    htmlFor={`q-${index}-text`}
                    className="text-muted-foreground text-xs"
                  >
                    What the agent asks, word for word
                  </Label>
                  <Input
                    id={`q-${index}-text`}
                    placeholder="How many years of delivery experience do you have?"
                    {...form.register(`questions.${index}.text`)}
                    aria-invalid={Boolean(rowErrors?.text)}
                  />
                  {rowErrors?.text && (
                    <p className="text-destructive text-xs">
                      {rowErrors.text.message}
                    </p>
                  )}
                </div>

                {answerType === "ENUM" && (
                  <div className="space-y-1">
                    <Label
                      htmlFor={`q-${index}-options`}
                      className="text-muted-foreground text-xs"
                    >
                      Allowed answers, comma separated
                    </Label>
                    <Input
                      id={`q-${index}-options`}
                      placeholder="Day shift, Night shift, Either"
                      {...form.register(`questions.${index}.enum_options_raw`)}
                    />
                  </div>
                )}

                <button
                  type="button"
                  onClick={() =>
                    setExpanded((state) => ({ ...state, [index]: !open }))
                  }
                  className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs font-medium"
                >
                  <ChevronDown
                    className={cn(
                      "size-3.5 transition-transform",
                      open && "rotate-180",
                    )}
                  />
                  Scoring
                  {isKnockout && (
                    <Badge
                      variant="secondary"
                      className="ml-1 border-transparent bg-red-50 text-red-700 dark:bg-red-950/50 dark:text-red-300"
                    >
                      Required
                    </Badge>
                  )}
                </button>

                {open && (
                  <div className="bg-muted/40 space-y-3 rounded-md border p-3">
                    <div className="grid gap-3 sm:grid-cols-[8rem_1fr]">
                      <div className="space-y-1">
                        <Label
                          htmlFor={`q-${index}-weight`}
                          className="text-muted-foreground text-xs"
                        >
                          Weight
                        </Label>
                        <Input
                          id={`q-${index}-weight`}
                          type="number"
                          min={0}
                          max={10}
                          step={0.5}
                          {...form.register(`questions.${index}.weight`, {
                            valueAsNumber: true,
                          })}
                        />
                      </div>

                      <div className="space-y-1">
                        <Label
                          htmlFor={`q-${index}-rule`}
                          className="text-muted-foreground text-xs"
                        >
                          Counts as a good answer when
                        </Label>
                        <Input
                          id={`q-${index}-rule`}
                          placeholder={RULE_HINT[answerType]}
                          {...form.register(`questions.${index}.rule_raw`)}
                        />
                      </div>
                    </div>

                    <label className="flex cursor-pointer items-start gap-2.5">
                      <Checkbox
                        checked={isKnockout}
                        onCheckedChange={(checked) =>
                          form.setValue(
                            `questions.${index}.is_knockout`,
                            checked === true,
                            {
                              shouldDirty: true,
                            },
                          )
                        }
                        className="mt-0.5"
                      />
                      <span className="text-xs leading-relaxed">
                        <span className="font-medium">Hard requirement</span>
                        <span className="text-muted-foreground block">
                          A candidate who clearly fails this is set aside with
                          the reason shown. Leaving it unanswered never
                          disqualifies anyone.
                        </span>
                      </span>
                    </label>
                  </div>
                )}
              </div>

              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="text-muted-foreground hover:text-destructive mt-1 size-8 shrink-0"
                aria-label={`Remove question ${label || index + 1}`}
                disabled={fields.fields.length === 1}
                onClick={() => fields.remove(index)}
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
          </Card>
        );
      })}

      <Button
        type="button"
        variant="outline"
        className="w-full"
        disabled={fields.fields.length >= 15}
        onClick={() => fields.append(emptyQuestion())}
      >
        <Plus className="size-4" />
        Add question
      </Button>

      {fields.fields.length >= 15 && (
        <p className="text-muted-foreground text-center text-xs">
          Fifteen questions is the maximum. Long calls lose people.
        </p>
      )}
    </div>
  );
}
