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
  CallingPolicyOut,
  CampaignCreate,
  CampaignDetail,
  CampaignSummary,
  CandidateCreate,
  CandidateImportReport,
  CandidateOut,
  ExtractionResult,
  JobCreate,
  JobDetail,
  JobDraft,
  JobSummary,
  JobUpdate,
  LaunchOutreachReport,
  LaunchReport,
  LaunchRequest,
  MetaResponse,
  ProspectOut,
  ProspectPage,
  ResultsResponse,
  SearchFilters,
  SearchResponse,
  SearchSummary,
} from "@/lib/types";

/**
 * Where the demo token is kept between page loads.
 *
 * The API is on a different site from this one, so its cookie is a
 * third-party cookie: mobile Safari discards it and Chrome is phasing
 * them out. Keeping the token here and sending it as a header works
 * regardless of cookie policy, which is the difference between the
 * password working on a phone and appearing to do nothing.
 */
const TOKEN_KEY = "hunar_demo_token";

function readToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    // Private browsing can throw rather than return null.
    return null;
  }
}

function storeToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // Without storage the cookie is the only route; nothing else to do.
  }
}

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
      // The deployment's password cookie lives on the API's origin, which
      // is a different site from this one, so it is only sent when
      // credentials are included explicitly.
      credentials: "include",
      ...init,
      headers: {
        Accept: "application/json",
        ...(init.body instanceof FormData
          ? {}
          : { "Content-Type": "application/json" }),
        ...(readToken() ? { "X-Demo-Token": readToken() as string } : {}),
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
  /**
   * Exchange the shared demo password for a session cookie.
   *
   * The deployed API holds real candidates' names and phone numbers, so
   * it is not open to anyone who finds the URL. This is a shared
   * password, not authentication: there are no accounts and no record of
   * who did what.
   */
  unlock: async (password: string) => {
    const result = await request<{ status: string; token?: string }>(
      "/api/unlock",
      { method: "POST", ...json({ password }) },
      true,
    );
    // Kept so later requests carry it as a header. Relying on the cookie
    // alone left the password doing nothing on any browser that refuses
    // third-party cookies, which includes every iPhone.
    if (result.token) storeToken(result.token);
    return result;
  },

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
     * Read a pasted job description and fill in the whole form.
     *
     * Saves nothing. The response carries `source`, saying whether a
     * model read the description or whether it was filled in by keyword
     * matching, so the UI can tell the recruiter how much to trust it.
     */
    extract: (jdText: string) =>
      request<JobDraft>("/hiring/jobs/extract", {
        method: "POST",
        ...json({ jd_text: jdText }),
      }),

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

  people: {
    /**
     * What this deployment is allowed to do, in its own words.
     *
     * Fetched by the search screen so the calling policy is visible in
     * the product rather than only in a README. A recruiter looking at
     * people the app will not call deserves the reason on the same page.
     */
    policy: () => request<CallingPolicyOut>("/people/policy"),

    /**
     * Read a job description into search filters.
     *
     * Costs nothing and saves nothing, which is the whole point of
     * separating it from the search: the filters can be corrected before
     * a provider credit is spent on them.
     */
    extractFilters: (jdText: string) =>
      request<ExtractionResult>("/people/extract-filters", {
        method: "POST",
        ...json({ jd_text: jdText }),
      }),

    /**
     * Everyone sourced so far.
     *
     * The search response is not the only place prospects live. They are
     * persisted, and this is the durable view: reloading a page should
     * not discard people who cost provider credits to find.
     */
    prospects: (
      params: {
        limit?: number;
        offset?: number;
        consented_only?: boolean;
        search_id?: string | null;
      } = {},
    ) => {
      const query = new URLSearchParams();
      if (params.limit != null) query.set("limit", String(params.limit));
      if (params.offset != null) query.set("offset", String(params.offset));
      if (params.consented_only) query.set("consented_only", "true");
      if (params.search_id) query.set("search_id", params.search_id);
      const suffix = query.toString();
      return request<ProspectPage>(
        `/people/prospects${suffix ? `?${suffix}` : ""}`,
      );
    },

    /** Past searches, so a result set that cost credits can be found again. */
    searches: (limit = 20) =>
      request<SearchSummary[]>(`/people/searches?limit=${limit}`),

    /**
     * Record that a sourced person is reachable on a consented number.
     *
     * This does not grant permission. The set of dialable numbers comes
     * from the environment and the product cannot add to it. This only
     * says which sourced person is reachable on one of them, which is
     * how consent actually arrives: through a reply or a referral, never
     * from the fact that someone was findable.
     */
    linkConsent: (prospectId: string, allowlistId: string) =>
      request<ProspectOut>(`/people/prospects/${prospectId}/consent`, {
        method: "POST",
        ...json({ allowlist_id: allowlistId }),
      }),

    unlinkConsent: (prospectId: string) =>
      request<ProspectOut>(`/people/prospects/${prospectId}/consent`, {
        method: "DELETE",
      }),

    /** Run the search. Pass `filters` to search exactly what is shown. */
    search: (payload: {
      jd_text: string;
      filters?: SearchFilters | null;
      limit?: number;
    }) =>
      request<SearchResponse>("/people/search", {
        method: "POST",
        ...json({ limit: 25, filters: null, ...payload }),
      }),
  },

  campaigns: {
    list: () => request<CampaignSummary[]>("/campaigns"),
    get: (campaignId: string) =>
      request<CampaignDetail>(`/campaigns/${campaignId}`),
    create: (payload: CampaignCreate) =>
      request<CampaignDetail>("/campaigns", {
        method: "POST",
        ...json(payload),
      }),

    /**
     * Place the calls.
     *
     * Every target is re-checked against the consent gate server-side
     * before it is dialled, so a campaign assembled before the calling
     * window closed will defer rather than ring someone at midnight.
     */
    launch: (campaignId: string) =>
      request<LaunchOutreachReport>(`/campaigns/${campaignId}/launch`, {
        method: "POST",
      }),
    remove: (campaignId: string) =>
      request<void>(`/campaigns/${campaignId}`, { method: "DELETE" }),
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

  policy: ["people", "policy"] as const,
  searches: ["people", "searches"] as const,
  prospects: (params: {
    offset: number;
    consentedOnly: boolean;
    searchId: string | null;
  }) => ["people", "prospects", params] as const,
  campaigns: ["campaigns"] as const,
  campaign: (campaignId: string) => ["campaigns", campaignId] as const,
};
