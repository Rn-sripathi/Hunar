/**
 * Typed client for the backend API.
 *
 * The browser talks only to this backend, never to Hunar directly. That
 * is deliberate and not merely tidy: the Hunar key doubles as the HMAC
 * secret for inbound webhooks, so exposing it to a browser would let
 * anyone forge webhooks that mutate call records.
 *
 * Every failure arrives in one envelope carrying a stable `code`, which
 * is what lets the UI react differently to an expired key, an exhausted
 * quota and a rejected phone number without matching on prose.
 */

import type {
  CallAttemptOut,
  CandidateCreate,
  CandidateImportReport,
  CandidateOut,
  JobCreate,
  JobDetail,
  JobSummary,
  JobUpdate,
  LaunchReport,
  LaunchRequest,
  MetaResponse,
  ResultsResponse,
} from "@/lib/types";

const BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1"
).replace(/\/$/, "");

/** The root, without the versioned API prefix, for /meta and /healthz. */
const ROOT_URL = BASE_URL.replace(/\/api\/v\d+$/, "");

interface ErrorEnvelope {
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, unknown>;
  };
  request_id?: string | null;
}

/**
 * A failed API call, carrying the backend's stable error code.
 *
 * `code` is what UI branches on. The message is for humans and may be
 * reworded at any time.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;
  readonly requestId: string | null;

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, unknown> = {},
    requestId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }

  /**
   * Whether this failure means the voice API is unusable rather than the
   * request being wrong. Both cases are expected here, because the
   * assignment key is time limited.
   */
  get isVoiceUnavailable(): boolean {
    return (
      this.code === "hunar_auth_error" || this.code === "hunar_quota_error"
    );
  }

  /** Per-field messages from a validation failure, for form display. */
  get fieldErrors(): Record<string, string[]> {
    const errors = this.details.field_errors;
    return typeof errors === "object" && errors !== null
      ? (errors as Record<string, string[]>)
      : {};
  }
}

async function parseError(response: Response): Promise<ApiError> {
  let envelope: ErrorEnvelope = {};
  try {
    envelope = (await response.json()) as ErrorEnvelope;
  } catch {
    // A proxy timeout or a crash can return HTML, which is not a bug in
    // the caller and should still produce a usable message.
  }

  const error = envelope.error ?? {};
  return new ApiError(
    response.status,
    error.code ?? `http_${response.status}`,
    error.message ?? response.statusText ?? "The request failed.",
    error.details ?? {},
    envelope.request_id ?? null,
  );
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  root = false,
): Promise<T> {
  const url = `${root ? ROOT_URL : BASE_URL}${path}`;

  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init.body instanceof FormData
          ? {}
          : { "Content-Type": "application/json" }),
        ...init.headers,
      },
    });
  } catch (cause) {
    // A network failure is distinct from a rejected request, and the
    // remedy the user needs is different: check the backend is running,
    // rather than fix the input.
    throw new ApiError(
      0,
      "network_error",
      "Could not reach the API. Check that the backend is running.",
      { cause: String(cause) },
    );
  }

  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body) });

export const api = {
  /** Runtime facts, including whether the data shown is simulated. */
  meta: () => request<MetaResponse>("/meta", { method: "GET" }, true),

  jobs: {
    list: () => request<JobSummary[]>("/hiring/jobs"),
    get: (jobId: string) => request<JobDetail>(`/hiring/jobs/${jobId}`),
    create: (payload: JobCreate) =>
      request<JobDetail>("/hiring/jobs", { method: "POST", ...json(payload) }),
    update: (jobId: string, payload: JobUpdate) =>
      request<JobDetail>(`/hiring/jobs/${jobId}`, {
        method: "PATCH",
        ...json(payload),
      }),
    archive: (jobId: string) =>
      request<void>(`/hiring/jobs/${jobId}`, { method: "DELETE" }),

    /**
     * Render the agent script for a draft role without saving it.
     *
     * Powers the live preview in the create form, which is what lets a
     * recruiter correct a bad prompt before a candidate hears it.
     */
    preview: (payload: {
      title: string;
      company_name: string;
      location?: string | null;
      description_raw: string;
      language: string;
      questions: JobCreate["questions"];
    }) =>
      request<JobDetail["preview"]>("/hiring/jobs/preview", {
        method: "POST",
        ...json(payload),
      }),
  },

  candidates: {
    list: (jobId: string) =>
      request<CandidateOut[]>(`/hiring/jobs/${jobId}/candidates`),
    add: (jobId: string, payload: CandidateCreate) =>
      request<CandidateOut>(`/hiring/jobs/${jobId}/candidates`, {
        method: "POST",
        ...json(payload),
      }),
    remove: (jobId: string, candidateId: string) =>
      request<void>(`/hiring/jobs/${jobId}/candidates/${candidateId}`, {
        method: "DELETE",
      }),
    decide: (
      jobId: string,
      candidateId: string,
      decision: string,
      note?: string,
    ) =>
      request<CandidateOut>(
        `/hiring/jobs/${jobId}/candidates/${candidateId}/decision`,
        {
          method: "PATCH",
          ...json({ decision, note: note ?? null }),
        },
      ),
    import: (jobId: string, file: File) => {
      const form = new FormData();
      form.append("file", file);
      return request<CandidateImportReport>(
        `/hiring/jobs/${jobId}/candidates/import`,
        {
          method: "POST",
          body: form,
        },
      );
    },
  },

  calls: {
    /**
     * Start screening.
     *
     * Retries default to off. Automatic redialling is a decision about
     * how often to ring a real person, so it is opted into explicitly
     * rather than inherited from a default.
     */
    launch: (jobId: string, payload: Partial<LaunchRequest> = {}) =>
      request<LaunchReport>(`/hiring/jobs/${jobId}/calls/launch`, {
        method: "POST",
        ...json({
          candidate_ids: null,
          max_retry_count: 0,
          retry_interval_hours: 0,
          ...payload,
        } satisfies LaunchRequest),
      }),
    list: (jobId: string) =>
      request<CallAttemptOut[]>(`/hiring/jobs/${jobId}/calls`),
    refresh: (jobId: string) =>
      request<CallAttemptOut[]>(`/hiring/jobs/${jobId}/calls/refresh`, {
        method: "POST",
      }),
    results: (jobId: string) =>
      request<ResultsResponse>(`/hiring/jobs/${jobId}/results`),
  },
};

/** Query keys, centralised so invalidation cannot go out of step. */
export const queryKeys = {
  meta: ["meta"] as const,
  jobs: ["jobs"] as const,
  job: (jobId: string) => ["jobs", jobId] as const,
  candidates: (jobId: string) => ["jobs", jobId, "candidates"] as const,
  calls: (jobId: string) => ["jobs", jobId, "calls"] as const,
  results: (jobId: string) => ["jobs", jobId, "results"] as const,
};
