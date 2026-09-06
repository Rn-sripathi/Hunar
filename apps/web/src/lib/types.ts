/**
 * Ergonomic aliases over the generated OpenAPI types.
 *
 * `api.gen.ts` is generated from the backend's own schema by
 * `npm run gen:api`, so it is the single source of truth and must not be
 * edited. This file only gives those types readable names. The practical
 * effect is that renaming a field in a Pydantic model becomes a
 * TypeScript compile error here rather than a value that silently
 * arrives as `undefined` at runtime.
 */

import type { components } from "@/lib/api.gen";

type Schemas = components["schemas"];

export type JobSummary = Schemas["JobSummary"];
export type JobDetail = Schemas["JobDetail"];
export type JobCreate = Schemas["JobCreate"];
export type JobUpdate = Schemas["JobUpdate"];
export type QuestionInput = Schemas["QuestionInput"];
export type QuestionOut = Schemas["QuestionOut"];
export type FieldSpec = Schemas["FieldSpec"];
export type AgentPreview = Schemas["AgentPreview"];
export type JobDraft = Schemas["JobDraft"];
export type DraftQuestion = Schemas["DraftQuestion"];

export type CandidateOut = Schemas["CandidateOut"];
export type CandidateCreate = Schemas["CandidateCreate"];
export type CandidateImportReport = Schemas["CandidateImportReport"];

export type CallAttemptOut = Schemas["CallAttemptOut"];
export type LaunchRequest = Schemas["LaunchRequest"];
export type LaunchReport = Schemas["LaunchReport"];
export type ResultRow = Schemas["ResultRow"];
export type ResultsResponse = Schemas["ResultsResponse"];

export type MetaResponse = Schemas["MetaResponse"];
export type ReadinessResponse = Schemas["ReadinessResponse"];

export type AnswerType = Schemas["AnswerType"];
export type JobStatus = Schemas["JobStatus"];
export type CandidateDecision = Schemas["CandidateDecision"];

/**
 * Call statuses, as the voice API reports them.
 *
 * The four terminal ones matter operationally: polling stops when every
 * call has reached one, because nothing will change afterwards.
 */
export const CALL_STATUSES = [
  "NOT_STARTED",
  "SCHEDULED",
  "INITIATED",
  "RINGING",
  "IN_PROGRESS",
  "COMPLETED",
  "NOT_CONNECTED",
  "FAILED",
  "CANCELLED",
] as const;

export type CallStatus = (typeof CALL_STATUSES)[number];

export const TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  "COMPLETED",
  "NOT_CONNECTED",
  "FAILED",
  "CANCELLED",
]);

export function isTerminal(status: string): boolean {
  return TERMINAL_STATUSES.has(status);
}

/** A row is still moving if a call exists for it and has not settled. */
export function isActive(status: string): boolean {
  return status !== "NOT_STARTED" && !isTerminal(status);
}

/** Score bands, used for colour and for the plain-English label beside it. */
export function scoreBand(
  score: number | null | undefined,
): "strong" | "fair" | "weak" | "none" {
  if (score === null || score === undefined) return "none";
  if (score >= 75) return "strong";
  if (score >= 45) return "fair";
  return "weak";
}

/** One entry from a call's score breakdown, as rendered in the UI. */
export interface ScoreContribution {
  field_key: string;
  label: string;
  value: unknown;
  weight: number;
  passed: boolean | null;
  is_knockout: boolean;
  explanation: string;
}

export interface ScoreBreakdown {
  score: number | null;
  coverage: number;
  disqualified: boolean;
  reason: string | null;
  contributions?: ScoreContribution[];
  knockouts?: string[];
  note?: string;
  questions_answered?: number;
  questions_scorable?: number;
}
