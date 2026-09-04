# Hunar.ai Assignment — Combined Build Plan

## Context

Three-part hiring assignment from Hunar.ai, due **2026-09-07 16:10 IST**. `c:\dev\Hunar.ai` is empty, so this is greenfield. The Hunar API key expires in three days and is currently untested.

Both build tasks ship as **one monorepo, one FastAPI service, one Next.js app** with route groups:

- **App 1, AI Hiring Assistant** — recruiter defines a role and screening questions, loads applicants, AI voice agents screen them by phone, extracted answers land in a dashboard with ranked shortlisting.
- **App 2, People Search & Reachout** — paste a job description, source matching people from a people-search API, cold-call them with a voice agent, answers land in a dashboard.
- **Item 3** — a written answer on attendance tracking without smartphones. Lands in `docs/`.

App 2 is feasible only because it reuses app 1's voice infrastructure almost entirely. It writes zero new voice code.

**First action after approval:** copy this plan to `docs/build-plan.md` so it is versioned with the code and visible to the grader.

## Findings that overturn the naive design

Verified against the live OpenAPI spec at `https://api.voice.hunar.ai/docs/external/openapi.json`.

| # | Finding | Consequence |
|---|---|---|
| F1 | `result_schema` is a flat map of field name to **type-hint string**, lives on the **agent** not the call, and returned `result` values come back as **strings** regardless of declared type | One agent per job role is forced. A string-coercion normaliser is mandatory, not optional. All extraction nuance goes in `result_prompt`, not the schema |
| F2 | `call_status_updated` fires **only on terminal status** | Webhooks cannot drive live status. Polling `GET /calls/` is mandatory infrastructure, not a fallback |
| F3 | The `call_result_done` payload contains only `event_type` and `result`, with **no call id** | Correlation must live in the callback URL path as an opaque token. `callback_config` is per-call, so this is free |
| F4 | The HMAC secret **is the API key**. `message = f"{timestamp.strip()}.".encode() + raw_body`, sha256, base64. `X-Hunar-Signature` may be **comma-separated**. Retries at 1, 2, 4, 8 minutes | Verifier loops over a set of trusted keys against a list of provided signatures. The 8-minute retry **exceeds a 300s skew window**, so make the window configurable |
| F5 | `custom_data` is strings only, template syntax is **single-brace** `{var}`. `GET /calls/` filters are only `campaign_id`, `agent_id[]`, `status[]`, `page`, `page_size` (max 200) | Pasted job descriptions **must have braces stripped** or substitution corrupts. No `request_id` filter means a timed-out call POST cannot be cheaply confirmed, so never auto-retry a call creation |

Also: agent create has **no** `conclusion` or `silence_response` field. Callback URLs must be HTTPS. Guardrails need at least three distinct days and a three-hour window. `retry_interval_hours` accepts only 0, 3, 6, 9, 12, 24, and a partial `retry_config` is a 400.

Local environment: Node 24.12.0, uv 0.11.21, Docker 29.6.2, git 2.52. System Python is 3.14.1, so **pin 3.12 via uv** rather than lose hours to wheel builds for asyncpg on Windows.

## The people-search blocker, and the answer

**No provider in the brief will return a mobile phone number on a free tier.**

| Provider | Self-serve key | Person search free | Mobile phone free |
|---|---|---|---|
| People Data Labs | Yes, 100 credits/mo | **Yes** | No, contact fields return `true`/`false` since v29.0 |
| Apollo.io | **No**, free plan excludes API entirely | n/a | No |
| Proxycurl | **Shut down 4 July 2025** after the LinkedIn suit | n/a | n/a |
| Coresignal | Yes, 7-day trial | Yes | **No, the dataset has no personal phones by design** |

So the chain of search to phone to call cannot be closed on free credits. **Do not pretend otherwise. Turn it into the compliance story**, which is where a well-designed product lands anyway.

**Primary provider: People Data Labs.** Only one that self-serves a key in minutes and offers genuine person search, including filtering on phone presence with `{"exists": {"field": "mobile_phone"}}`. Real names, titles, companies, locations, LinkedIn URLs, and a boolean for whether a mobile exists. Not the digits. Fall back to the PDL Sandbox API, which charges zero credits, then to committed fixtures.

**Sourcing is broad, calling is narrow.** Prospects are sourced, scored and ranked for real. Outbound dialling is gated on a consent allowlist the developer controls, enforced by a `NOT NULL` foreign key so no code path can dial a broker-sourced number. Three independent reasons make this correct, not merely cautious: India's TCCCPR 2018 as amended February 2025 requires DLT sender registration and this demo has none; PDL's Acceptable Data Use Policy forbids using their data for employment eligibility decisions; and PDL settled a $6.36M Colorado class action in 2025 over exactly this kind of phone data. The grader works at the company selling this product, so demonstrating this awareness scores points.

## Architecture

```
Hunar.ai/
├─ README.md                       # links, screenshots, architecture, decisions
├─ .gitignore                      # written BEFORE git init
├─ .env.example
├─ pyproject.toml                  # uv workspace, requires-python >=3.12,<3.13
├─ docker-compose.yml              # postgres only; apps run natively
├─ .pre-commit-config.yaml         # gitleaks + ruff
├─ docs/
│  ├─ build-plan.md                # this plan
│  ├─ architecture.md
│  ├─ hunar-api-notes.md           # F1..F5, evidence of real investigation
│  └─ attendance-without-apps.md   # ITEM 3
├─ packages/hunar-sdk/             # shared, app-agnostic
│  └─ src/hunar_sdk/
│     ├─ protocol.py               # HunarClient Protocol
│     ├─ client.py                 # LiveHunarClient
│     ├─ fake.py                   # demo mode, posts genuinely signed webhooks
│     ├─ models.py enums.py errors.py
│     ├─ webhooks.py               # signature verification, pure function
│     ├─ sanitize.py               # brace stripping, F5
│     └─ fixtures/                 # captured real payloads + MP3s
└─ apps/
   ├─ api/src/app/
   │  ├─ core/                     # errors, logging, pagination, security
   │  ├─ hiring/                   # APP 1
   │  ├─ people/                   # APP 2
   │  └─ webhooks/                 # SHARED receiver, no app awareness
   └─ web/src/
      ├─ app/(hiring)/  app/(people)/
      ├─ components/shared/        # DataTable, StatusBadge, EmptyState,
      │                            # DemoModeBanner, AudioPlayer
      └─ lib/                      # api-client, types.gen.ts, hooks
```

One Next.js app with route groups means one Vercel project, one design system, zero cross-app plumbing. `hiring` and `people` never import from each other, only from `core` and `hunar-sdk`.

## Tech stack

Everything the employer mandated is marked. The rest are our choices, each with a one-line reason.

**Backend**

| Piece | Choice | Why |
|---|---|---|
| Language | **Python 3.12** *(mandated: Python preferred)* | Pinned via uv. System Python is 3.14 and asyncpg wheels on 3.14 Windows are a risk we cannot debug on this clock |
| Framework | FastAPI | Async, native Pydantic, and it publishes an OpenAPI schema we generate frontend types from |
| Package manager | uv | One binary for venv, resolve, lock and run. Resolves in about a second where poetry takes thirty |
| ORM | SQLAlchemy 2.0 async | Typed, and the only mature async ORM with real JSONB support |
| Migrations | Alembic | Standard for SQLAlchemy, runs as a Render pre-deploy step |
| Validation | Pydantic v2, pydantic-settings | Every environment variable typed and validated at startup, failing fast on a missing key |
| HTTP client | httpx | Async, and `respx` mocks it cleanly in tests |
| Retries | tenacity | Retry reads only, never call creation |
| Scheduling | APScheduler | In-process reconciler loop, which is why the backend pins to one instance |
| Logging | structlog | JSON logs with correlation IDs and a redactor for keys and phone numbers |
| Quality | ruff, mypy strict, pytest, pytest-asyncio, respx | Lint, format, typecheck, test |

**Database:** Neon serverless Postgres over asyncpg. JSONB carries the per-job extraction schemas and raw provider payloads. Generous free tier, connection string only, nothing to run locally.

**Frontend**

| Piece | Choice | Why |
|---|---|---|
| Framework | **Next.js, App Router** *(mandated)* | Deploys to Vercel with zero config |
| Language | **TypeScript strict** *(mandated: not plain JavaScript)* | |
| Components | **shadcn/ui** *(mandated)* | Owned source rather than a dependency, so it can be adapted |
| Styling | Tailwind CSS | shadcn is built on it |
| Data fetching | TanStack Query | `refetchInterval` gives us call-status polling in a few lines, and it stops itself when calls go terminal |
| Tables | TanStack Table | Columns are built at runtime from each job's schema, which a static table cannot do |
| Forms | react-hook-form with zod | The screening-question builder is a dynamic array, which is where this pairing earns its keep |
| Reordering | dnd-kit | Drag to reorder screening questions |
| Icons, toasts | lucide-react, sonner | shadcn's defaults |
| Type sync | openapi-typescript | Generates TypeScript types from FastAPI's OpenAPI schema, so the two sides cannot silently drift |

**External services:** Hunar.ai Voice API for the agents and calls, People Data Labs for people search, and Claude for turning a job description into structured search filters and screening questions.

**Infrastructure:** Vercel for the frontend, Render Starter for the backend, Neon for the database, ngrok with a reserved domain for local webhooks, Docker Compose running only Postgres locally, GitHub Actions for CI, and pre-commit with gitleaks for secret scanning.

## Data model

**Shared:** `call_attempt` serves both apps. Rather than a polymorphic subject, use two nullable foreign keys with a check constraint so Postgres enforces exactly one:

```sql
ADD CONSTRAINT call_attempt_one_subject CHECK (
  (candidate_id IS NOT NULL) <> (outreach_target_id IS NOT NULL)
);
```

Key columns: `request_id` unique, `callback_token` unique, `hunar_call_id`, `status`, `lifecycle_status`, `recording_url`, `raw_result` jsonb, `normalized_result` jsonb, `submit_state` for the POST-retry hazard.

**App 1:** `job` (with `result_schema` jsonb as sent to Hunar, plus a richer `field_spec` jsonb the UI renders from), `screening_question`, `candidate`.

**App 2:** `people_search` (job description text, resolved filters, literal provider query for reproducibility, credits charged), `prospect` (with `phone_status` enum rather than a phone string), `prospect_search_hit`, `outreach_campaign`, `outreach_target` (with `allowlist_id` NOT NULL), `contact_allowlist`, `dnc_number` storing a **SHA-256 hash** so honouring suppression does not require retaining the number.

`webhook_event` with a unique `dedupe_key` for idempotency.

JSONB is right for provider payloads, resolved filters and extracted results, because those shapes genuinely vary. It is wrong for anything the UI sorts or badges, so `phone_status`, `seniority`, `status` and `fit_score` stay real indexed columns.

## The load-bearing implementation details

**Correlation token, solving F3.** At call creation generate `secrets.token_urlsafe(24)`, persist it, and build `{PUBLIC_API_BASE_URL}/webhooks/hunar/{token}/{status|recording|result|summary}`. Correlation no longer depends on payload contents, and the same scheme serves both apps unchanged.

**Webhook handler.** Read raw bytes **before** any JSON parsing, since the HMAC covers them. Keep the webhook path free of body-rewriting middleware and assert that in a test. Verify, persist raw with `ON CONFLICT DO NOTHING` on the dedupe key, acknowledge fast, process in a background task. Apply changes monotonically: never move a call backwards out of terminal, never null a `recording_url` already held. The four event types arrive unordered, so treat every one as an upsert.

**Reconciler, mandatory per F2.** APScheduler every 10 seconds over jobs with non-terminal calls, paging `GET /calls/?agent_id=...&page_size=200`. This delivers live ringing and in-progress transitions the webhooks never send, recovers missed webhooks, and resolves calls whose POST timed out. Back off to 30 seconds when quiet, stop when everything is terminal.

**Never auto-retry `POST /calls/`.** A duplicate is a second real phone call to a real person. Mark `submit_state=UNKNOWN` and let the reconciler match on `request_id` from the list response.

**Result normaliser.** Hunar returns strings for everything. Coerce per `field_spec`, mapping yes/haan/true to booleans, extracting bare numerals from "3 years", and treating empty or "NA" as null. **Always keep `raw_result` verbatim** alongside the coerced values and reveal it on hover. That transparency is exactly what a grader looks for.

**Consent gate for app 2.** `assert_callable(target)` checks allowlist membership, do-not-contact status, the DNC hash table, and calling hours in Asia/Kolkata. Called at campaign launch **and again immediately before each call**, because a campaign launched at 18:55 can drain past 19:00.

**Demo mode, three ways.** `HUNAR_MODE=live|mock|auto` and `PEOPLE_PROVIDER=pdl|pdl_sandbox|fixture` as **independent** axes, so a Hunar expiry does not force fake people data. Deploy with `auto`: it starts live and latches to mock on the first 401 or 402, surfacing the reason in a banner. The fake client posts **genuinely HMAC-signed webhooks to our own endpoint**, so the production verification path is exercised identically rather than bypassed.

## Screens

**App 1** under `(hiring)`: job list, a create-job wizard whose final step **previews the generated `agent_prompt` and `result_schema`**, candidates with CSV import and column mapping, a live call monitor, and a results dashboard whose columns derive at runtime from `field_spec`, plus shortlisting with transparent scores.

That prompt preview is the single most persuasive screen for a grader, because it makes the translation from job description to voice agent visible.

**App 2** under `(people)`: a search screen where the job description is pasted and the resolved filters are **shown and editable before a credit is spent**, a prospect table with contactability states and fit scores, a campaign launch screen carrying the blocking consent dialog and a script preview, live monitoring, and the answers dashboard.

The consent dialog states the exact count, lists the numbers to be dialled, shows the calling window against the current time, and requires a ticked confirmation. Blocked prospects are shown **with their reason rather than hidden**, because the point is to demonstrate the gate firing.

Live updates are polling via TanStack Query `refetchInterval` returning false once every call is terminal, with `refetchIntervalInBackground: false`. Server-sent events and websockets both add failure modes on Render for no benefit at this volume.

## Build sequence

Roughly 30 focused hours from the evening of 4 September to 16:10 on 7 September.

**Day 0, evening, 4 hours.** The first hour is a throwaway spike against the live key, and it is the highest-priority hour in the whole plan. Call `GET /numbers/` to learn whether the org even has a validated outbound number, create an agent with a three-field schema, place one call to your own mobile, and watch `GET /calls/{id}/`. This answers whether `result_schema` values are type names or descriptions, whether results return as strings, whether `{var}` substitution works, and what the `language` enum format actually is. Everything downstream depends on it. Then: repo skeleton, `.gitignore` before `git init`, gitleaks, uv workspace on 3.12, the SDK with webhook verification unit tests, the FastAPI skeleton, and **deploy the empty API to Render immediately** so TLS and environment problems surface now rather than on day three.

**Day 1, 10 hours.** Prompt builder with tests on slugification and brace sanitising, agent service, jobs and questions, candidate CSV import with E.164 normalisation, call service with correlation tokens, webhook router and processor, result normaliser, reconciler. Then the first real end-to-end call to your own phone through the deployed URL. Finally, **capture fixtures while the key still lives**, including two or three real MP3 recordings, because `recording_url` links will likely die with the key.

**Day 2, 10 hours.** App 1 frontend end to end: layout, shared components, the job wizard with prompt preview, candidates, live monitoring, results dashboard, shortlisting. Fake client, auto-degrade, demo seed. Deploy the frontend.

**Day 3 morning, 6 hours.** Record the demo video first, while the key is valid. Then app 2 at its cut line: fixture provider first, then the PDL adapter, heuristic extraction with the LLM pass if time allows, the search and prospect screens reusing app 1's table, the consent gate, and one real allowlisted call feeding the shared answers dashboard. Then item 3, the README, `docs/hunar-api-notes.md`, and a full-history gitleaks scan.

**Cut lines, in the order things get dropped.** App 1's core is untouchable: job to agent to calls to live status to results to shortlist, deployed, with working demo mode. App 2's minimum is a job description in, a ranked prospect list out, and one consent-gated call whose answers render in the shared table. That still demonstrates every capability the brief names. Drop in this order if you slip: app 2's campaign abstraction, then the LLM extraction in favour of keyword rules, then CSV export, then the settings screen, then app 1's bulk endpoint in favour of a single-call loop.

**Never cut:** the README ethics section for app 2, and demo mode. The first is the highest score-per-minute artefact in the project. The second is what the grader will actually open after the key expires.

## Deployment

Backend to **Render on the Starter instance, not Free**. A free instance sleeps, which drops webhooks and freezes the reconciler that the live-status UI depends on. Roughly seven dollars for the assignment window is correct spending. Root `apps/api`, pre-deploy `alembic upgrade head`, and pin a single instance because APScheduler runs in-process. Note that limitation honestly in the README; the real answer is a separate worker with a Redis lock, which is out of scope.

Database is Neon Postgres, connection string only, with the direct rather than pooled endpoint for migrations.

Frontend to Vercel, root `apps/web`, with `NEXT_PUBLIC_API_BASE_URL` pointing at Render. Deploy the backend first, because `PUBLIC_API_BASE_URL` is the root of every callback URL and Hunar requires HTTPS. Locally, use an ngrok reserved domain so restarts do not invalidate it.

## Security

`.gitignore` is written before `git init`, so no key can ever enter history. Pre-commit runs gitleaks. The Hunar key reaches only the FastAPI process: no `NEXT_PUBLIC_` variable carries it, no Next route handler proxies it, and CI fails if the variable name appears anywhere under `apps/web/`. Structlog redacts the key, `X-API-Key`, `X-Hunar-Signature`, and phone fields. Mobile numbers are PII, so normalise to E.164, mask in list views, and never log them whole. A shared demo password gate on an HttpOnly cookie keeps the public URL from being open to the world, exempting only the webhook and health paths. CORS is locked to the Vercel origin. If a key is ever committed, rotate first and rewrite history second, never the reverse.

## Verification

1. The day-zero spike returns agents and validated numbers without error.
2. Create a job through the UI, then confirm the agent exists via `GET /agents/{id}/` and that the previewed prompt matches what was sent.
3. Add your own mobile as the only candidate, launch, and confirm the phone rings, the agent conducts the interview, and answers appear within a minute of hanging up.
4. Kill the tunnel mid-call to prove the reconciler recovers state without the webhook.
5. Unit-test webhook verification against a captured real signature, including the comma-separated multi-signature case and a stale timestamp.
6. Confirm the consent gate blocks a non-allowlisted prospect at the service layer and at the database constraint.
7. Set a deliberately bogus key and confirm the deployed link still demonstrates the full flow from seeded data.
8. Full-history secret scan with `gitleaks detect --log-opts="--all"` before submission.

## Open unknowns to settle in the day-zero spike

Blocking: whether the org has a validated `from_phone_number` at all, since there is no workaround on our side and it would need Hunar support. Then the `language` enum format, whether `result_schema` values are type names or free text, and whether `{var}` substitution works as documented.

Non-blocking but worth confirming: whether Hunar re-signs on retry, since an 8-minute retry against a 300-second skew window would otherwise be silently dropped; whether `POST /calls/` deduplicates on `request_id`, assumed not; any per-organisation cap on agent count, which is why an `HUNAR_AGENT_STRATEGY` escape hatch exists; and PDL's Indian phone-presence density and whether its `skills` field exists in the person schema.

Budget real calls tightly. Perhaps ten to fifteen across all three days, to your own numbers only, because a 402 mid-demo is fatal and quota is unknown.
