# Tracking attendance for 1,000 people across 100 locations, without apps

**The problem.** A thousand people work across a hundred sites, roughly ten
per site. None of them has a smartphone, so there is no app, no GPS, no
QR code, no selfie check-in. Large language models are available and
cheap. What do you build?

The short answer: **the missed call is the primitive, and the language
model is the exception handler.** Almost everything below follows from
refusing to put the model in the hot path.

---

## 1. What these people actually have

"No smartphone" is not "no phone". In Indian frontline work the near
universal device is a feature phone, and it can do four things:

| Capability | Cost to the worker | Carries identity? |
|---|---|---|
| **Missed call** | **Free** | Yes — the caller ID |
| SMS | ~₹0.10 | Yes |
| USSD (`*123#`) | Free on most plans | Yes |
| Voice call | Paid, unless we call them | Yes |

The missed call is the interesting one, and it is worth being precise
about why. It is free for the worker, it works on every handset ever
sold, it needs no literacy, no data connection, no menu, no training
beyond "ring this number and hang up", and the caller ID is an
identifier the worker cannot easily forge and did not have to remember.

India already runs on this. Missed-call campaigns are how banks deliver
balances and how political parties count supporters. Building attendance
on it is not a clever hack; it is using the channel these users already
understand.

So the base design is one phone number per site, and the worker gives it
a missed call at the start and end of their shift. We capture three
things for free: **who** (caller ID), **where** (which number was
dialled), **when** (the timestamp).

One number per site for a hundred sites is a trivial cost. The check-ins
themselves cost nothing at all, because the call is never answered.

---

## 2. Where a language model actually earns its place

The tempting design is to have a voice agent ring all thousand people
every morning. That is worse on every axis: it costs real money per call,
it annoys people at 6 a.m., it is trivially gamed by saying "yes" to a
robot, and it puts a probabilistic system in the path of something that
should be deterministic.

**A missed call is already a perfect, unambiguous signal. Do not send an
LLM to interpret it.**

The model belongs where there is genuine ambiguity, which on a normal day
is about five to ten percent of the roster:

**Nobody checked in.** Thirty minutes past shift start, the system rings
the worker. Not to nag — to find out what happened. "Are you on your way,
or is something wrong?" The answer is the valuable part, and it is the
part conventional attendance systems throw away. Most of them record a
bit: present or absent. What a site manager actually needs is *"bus broke
down at Silk Board, forty minutes out"* versus *"child is ill, not coming"*
versus *"I have been here since six, my phone is dead"*. Those three
absences require three completely different responses, and only the third
one is not an absence at all.

This is exactly the extraction problem the rest of this repository
solves: unstructured speech in, structured fields out.

```
"bus kharab ho gaya, Silk Board pe, chalis minute lagega"
  → { status: DELAYED, eta_minutes: 40, reason: TRANSPORT,
      raw: "bus kharab ho gaya, Silk Board pe, chalis minute lagega" }
```

**Language and literacy.** A voice agent that speaks Hindi, Tamil,
Kannada and the mixture people actually use is more accessible than any
SMS menu, and far more accessible than a USSD tree. Reading is a real
constraint in this workforce; speaking is not.

**Reconciliation with supervisors.** At a hundred sites, the supervisor's
headcount and the check-in log will disagree daily. Resolving that
normally needs a literate person at a computer. Instead the agent calls
the supervisor, reads out the three names in dispute, and accepts a
spoken correction. A back-office task becomes a two-minute phone call.

**Everything else stays dumb on purpose.** Detecting that one handset
checked in for six different workers is a `GROUP BY`, not a language
model. Use the cheap deterministic thing wherever a cheap deterministic
thing works.

---

## 3. Proving presence, not just identity

A missed call proves who is calling. It does not prove where they are.
Someone can ring the Whitefield number from their bed. This is the real
problem, and it deserves an honest answer rather than a clever one.

**Perfect verification without hardware is impossible.** The goal is not
to make cheating impossible; it is to make it effortful enough not to be
worth it, and to surface the residue to a human. Three layers, in
increasing cost:

1. **A rotating site code.** A short code, changed daily, written on the
   board at muster or announced by the supervisor. The worker sends it by
   SMS instead of a missed call, or reads it to the agent. Knowing it
   implies having been at the site this morning. It is not
   unforgeable — someone can text it to a friend — but it converts casual
   absence into deliberate collusion, which is a much higher bar.

2. **Random voice audits.** A small random sample each day, perhaps two
   percent, gets a short call asking them to put the supervisor on, or to
   confirm something about the day's work. Unpredictability does most of
   the work here; the cost is negligible because the sample is tiny.

3. **Anomaly detection, no model required.** One handset checking in for
   several workers. Check-ins clustered within the same few seconds.
   A worker whose arrival precedes shift start by exactly the same
   interval every single day. These are queries, they are nearly free,
   and they point a human at the right site.

Where a site happens to have a landline, caller ID from a fixed line is
strong location evidence and should be preferred. Cell-tower location
from the telco would be stronger still, and I would not use it: it needs
a carrier partnership, it costs real money, and it tracks people well
beyond the workplace. That is a large privacy cost for a marginal
improvement over a rotating code.

---

## 4. The asymmetry that should shape the whole design

Attendance determines pay. That makes the two failure modes profoundly
unequal:

- A **false present** costs the employer a few hundred rupees.
- A **false absent** costs a worker a day's wages, and their trust in the
  system, permanently.

So the system must never automatically mark anyone absent. Unresolved
means *flagged for a human*, never *not paid*. Every automated decision
needs the raw evidence attached to it — the recording, the timestamp, the
literal words — so that a worker who disputes it can be shown what
happened rather than told what the system decided.

For the same reason the worker needs a way in. A missed call to a
different number, and the agent reads back their own attendance for the
week. Someone whose pay depends on a record should not have to ask
permission to see it.

The provenance of each record should be stored alongside it, because not
all attendance is equally well evidenced:

| How it was recorded | Confidence |
|---|---|
| Missed call plus valid site code | High |
| Missed call only | Medium |
| Supervisor attested on the worker's behalf | Low, and auditable |
| Agent call, worker confirmed | Medium |

A record that carries its own provenance can be argued with. A bare
boolean cannot.

---

## 5. What it costs

Monthly, for a thousand workers across a hundred sites:

| Item | Estimate |
|---|---|
| 100 site numbers (DIDs) | ~₹10,000 |
| Missed calls, ~44,000/month | **₹0** — never answered |
| Agent calls at ~7% of check-ins, ~1.5 min each | ~₹5,000 |
| LLM inference on ~3,000 short calls | ~₹3,000 |
| **Total** | **~₹18,000/month, or ₹18 per worker** |

Against biometric terminals at ₹5,000–15,000 per site, that is ₹5–15
lakh of capital expenditure before anyone has clocked in once, plus
maintenance, power and connectivity at a hundred sites, plus the
fingerprint-reader failure rate on hands that do manual work all day.
The voice approach is roughly two orders of magnitude cheaper and works
during a power cut.

---

## 6. What I would build first

**Week one, and it needs no AI at all:** one number per site, missed-call
capture, a table of check-ins, and an SMS to each supervisor at shift
start plus thirty minutes listing who is missing. This alone solves most
of the problem, and shipping it first proves the channel works before any
model is involved.

**Week two:** the outbound agent for missing check-ins, with structured
extraction of the reason. This is where the system starts producing
something a spreadsheet never could.

**Week three:** rotating site codes, the anomaly queries, the
worker-facing "read me my attendance" line, and supervisor reconciliation
by voice.

Sequencing matters here beyond ordinary iteration. If the missed-call
layer is not solid, an LLM layered on top will mask its failures with
plausible-sounding calls, and nobody will notice for a month.

---

## 7. What this does not solve

Being honest about the edges:

- **No phone at all.** A real minority. They need supervisor attestation,
  recorded as such, with the lower confidence that implies.
- **Shared handsets.** Common in households, and more common for women
  workers. One number cannot distinguish two people. Site codes help;
  a genuinely shared phone needs a second factor or attestation.
- **Determined collusion.** A supervisor who wants to mark a whole site
  present can. No attendance system without hardware survives a
  supervisor who is in on it; what this design can do is make the pattern
  visible in the data.
- **Network dead zones.** Some sites have no coverage. Those need the
  supervisor to batch-report from wherever signal exists, and the record
  should say so.
- **Number churn.** People change SIMs. The enrolment flow needs to be as
  easy as the daily flow, or the roster rots.

None of these is a reason not to build it. A system that covers ninety
percent of a thousand people for ₹18 a head, and is honest about the
other ten percent, is worth far more than one that claims to cover
everybody and quietly gets some of them docked a day's pay.
