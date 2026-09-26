# Vera Challenge — Build Plan (v1, for review)

> **Status:** v2. Adjusted for **free tier only** and a **submission deadline of 27 Sep 2026, 15:00 IST** (about 12 working hours). Where §0.5 conflicts with anything below it, §0.5 wins.
> **Base:** the research report ("Plan 2"), with your review notes applied, the useful parts of Plan 1 kept, and 16 more corrections that came from auditing the actual starter pack (§2).
> **Rule of this document:** where two sources disagree, the **starter pack wins**. That means `challenge-brief.md`, `challenge-testing-brief.md`, `examples/*.md`, `judge_simulator.py` and the dataset. Each decision cites where it comes from.

---

## 0. The problem in one paragraph

Build **one always-on HTTP service** (5 endpoints). It receives four layers of context (category, merchant, customer, trigger). On every `tick` it decides **whether** to message, **whom**, and **what**, then writes one grounded WhatsApp message. It also handles the merchant's replies over up to 5 turns. An LLM judge scores every message 0–10 on five dimensions:
- specificity
- category fit
- merchant fit
- decision quality / trigger relevance
- engagement compulsion

On top of that come the Phase 3 adaptation bonus (up to +5 per dimension), the Phase 4 replay bonus for the top 10 (up to +30), and operational penalties (up to −20). The hard part isn't the server. It's **making the right decision on thin or contradictory data without inventing anything**.

---

## 0.5 v2 overrides: free tier + 12-hour build

### What free tier forces (checked on 26 Sep 2026)
| Constraint | Consequence |
|---|---|
| Hugging Face Spaces: new free Docker Spaces now require PRO | Not usable |
| Cerebras: card required, credits expire after 30 days | Not usable |
| Render free: sleeps after 15 min idle (about 1 min to wake), may restart at any time, disk wiped on restart, 750 h/month | Usable **only** with an external keep-alive ping every minute. State lives in memory only |
| Gemini API free, no card: 2.5 Flash about 10 RPM / 500 RPD; Flash-Lite-class models 15–30 RPM / about 1,500 RPD (June 2026 figures; confirm in AI Studio) | Primary LLM, but limited to roughly 10–30 calls a minute |
| Groq free, no card: gpt-oss-120b/20b, 30 RPM, 1,000 RPD, **8K TPM** (Llama was removed from the free tier on 16 Aug 2026) | Failover only; 8K TPM is about 5 messages a minute |
| My workspace reaches GitHub but **not the LLM APIs or Render** | I build and test all the rule-based logic here; you run the LLM tests locally and deploy |

### Architecture change: templates write the messages, the LLM only polishes
- **Templates are now the main path, not the fallback.** Every trigger kind gets hand-written templates in English and Hinglish, in each category's voice, filled from the verified fact sheet. On their own they must produce a valid, specific, high-scoring message.
- **The LLM rewrites the template draft for fluency**, using only the same facts. Gemini is tried first, Groq if Gemini fails.
  - It runs only when the shared **rate limiter** has budget (a token bucket per provider covering RPM, TPM and RPD) and the tick deadline still allows.
  - Its output goes through the same validator. On any failure, the template draft is sent instead.
- **Replies are mostly rules.** The LLM writes only answers to open questions.
- **The cache keeps output identical for identical input**, whichever path produced it.
- **Prompts stay short** (about 700 input tokens) to fit Groq's 8K TPM.
- **Use two Gemini keys from two Google Cloud projects:** one for **dev** (testing on your laptop) and one for **prod** (Render). That way testing never uses up the judged bot's daily quota. Daily quotas reset at 12:30 IST.

### Hosting change
- **Render free web service**, deployed from your GitHub repo using the `render.yaml` in the repo.
- **cron-job.org** (free, 1-minute interval) pings `/v1/healthz`. This prevents sleep, cold starts, and the memory loss a sleep would cause.
- **No SQLite**, because the disk is wiped on restart. State lives in memory.
- **Remaining risk:** Render can restart a free instance at any time. We accept this because the only fix costs money.

### v2.1 hardening (after the external review)
| Item | Decision |
|---|---|
| **Cache vs LLM polish** | **Rejected: "upgrade a cached template to the LLM version later".** Identical input must keep giving identical output; the challenge rejects "unstable responses". Instead: (1) compose in the background as soon as a trigger is pushed, so the first thing served is usually the LLM version; (2) the first output served for an input is cached and served forever; (3) `generation_source` (llm / template) is stored for logs and metrics only. |
| **`$PORT` binding** | Already in the plan: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, never a hard-coded port. |
| **Memory caps (512 MB)** | Accepted. Per conversation: at most 10 turns plus sent-body hashes (5-turn cap anyway). Per merchant: at most 20 auto-reply fingerprints. Cache limited to 5,000 entries (LRU). Ended conversations older than 24 simulated hours are pruned. |
| **Boot marker** | Accepted. Log `=== BOOT boot_id=<uuid> — state is empty ===` at startup, so a platform restart shows up in the Render logs. `/v1/healthz` keeps the exact documented schema. |
| **Rate limits / 429s** | Accepted with a correction: **Gemini is primary**, Groq is failover (the review had them reversed). Details: a token bucket per provider (RPM, TPM, RPD, set about 20% below the published limits); concurrency 3 for Gemini and 2 for Groq; a 429 or timeout puts that provider on a 60 s cooldown. Failover happens only if the other provider has budget and at least 3 s remain before the deadline; otherwise the template is sent. The buckets prevent most 429s before they happen. |
| **Groq model** | gpt-oss is a reasoning model, so `reasoning_effort=low` and a small `max_tokens`, because reasoning tokens count against the 8K TPM. |
| **README tone** | Accepted in substance, **rejected in wording**. No "0% hallucination" or "100% uptime" claims, which are false on a free host and exactly the kind of unfounded claim the judges penalize. Describe what is actually true: every number is checked against the context, and the template path guarantees a valid reply within the deadline even with zero LLM quota. |
| **Secrets** | Keys only in the local `.env` (gitignored) and Render env vars; never in chat, code, commits or logs. |

### Scope cuts to fit 12 hours
**Kept:** everything in §6–§9 (the decision layer, the playbook for all 26 trigger kinds, the validator, the reply state machine, the API contract).

**Cut or reduced:**
- Model bake-off: Gemini is fixed as primary, Groq as failover.
- SQLite persistence.
- The full 60-minute harness emulator, replaced by a short context-injection drill.
- The festival-date table.
- The 24-hour soak test, replaced by a morning smoke test.
- Local LLM-judge runs, limited to about 2 because of the free quota.

### Who does what
**Me (here):**
- all code, tests, templates and the README
- committing phase by phase to your GitHub repo

**You:**
- creating accounts and keys (keys never need to pass through me)
- running the `eval/` scripts locally with your dev keys and pasting the results back
- deploying on Render, setting environment variables and the keep-alive ping
- **reading every module** (magicpin verifies you did the work before any offer)
- submitting the final form

### Timeline (IST)
| Time | Me | You |
|---|---|---|
| 16:45–17:15 | Phase 1: API server + contract tests | Repo, keys, Render and cron-job.org accounts |
| 17:15–19:45 | Phase 2: data audit, eligibility, claim checks, fact sheets, templates for all 26 kinds | Review the audit matrix; read `app/decide/` |
| 19:45–20:45 | Phase 4: reply engine | Read `app/converse/` |
| 20:45–21:45 | LLM polish layer, rate limiter, repair step, cache | First local run with dev keys; paste the output |
| 21:45–22:15 | Break / fixes | Break |
| 22:15–23:30 | Eval loop 1: fix copy and logic from your results | Run canonical-30 + replay scripts |
| 23:30–00:30 | `render.yaml`, README draft | Deploy to Render, set env vars + keep-alive, run `judge_simulator.py` against the public URL |
| 00:30–02:30 | Eval loops 2–3; injection, latency and determinism drills | Run the drills; paste results |
| 02:30–03:00 | Freeze **v1.0.0**; finalize metadata + README | Redeploy the final commit |
| 03:00–09:00 | — | Sleep (the ping keeps the bot awake) |
| 09:00–11:00 | Fixes only if the smoke test fails | Morning smoke test on the public URL → **submit by 11:00** |
| 11:00–15:00 | — | Buffer. **Don't redeploy after submitting.** |

---

## 1. Verdict on the three inputs

### 1a. Your review notes on Plan 2: 5 accepted, 1 accepted with a correction

| # | Your point | Verdict | Evidence / refinement |
|---|---|---|---|
| 1 | Don't blindly ban URLs | **Accept the rule, correct the premise** | The −3/URL penalty *is* in the starter pack (`api-call-examples.md` F.4), while the website and brief allow useful URLs. Your rule, "a URL only if it comes from the supplied context", is the right one. The audit found **zero URLs anywhere in the dataset**, so in practice the rule produces no URLs. The validator implements your rule literally, so an injected URL in future context would pass. |
| 2 | CTA isn't always binary | **Accept** | Brief §5: binary for action triggers, none allowed for pure information. Case Study 2 uses slot choice for booking. CTA type is decided per trigger kind (§6.3). |
| 3 | Owner name isn't a universal hard rule | **Accept with refinement** | The judge's scorer explicitly checks "uses their name/owner name correctly", and cross-case pattern #3 says a generic "Hi" loses 1 point. So: the **opening merchant-facing message** must use the owner name ("Dr. {first}" for dentists). Later turns don't repeat it. Customer-facing messages use the **customer's** name plus the business name. |
| 4 | Version-conflict inconsistency | **Accept, resolved in §9** | The testing brief prose says a re-post is a "no-op". Example 1.5 *and* the reference skeleton return **409 `stale_version` for the same version**. We return 409 and leave state unchanged, which satisfies both readings. |
| 5 | Five required endpoints, not six | **Accept** | Five required. `/v1/teardown` is optional; we implement it but never count it. |
| 6 | Don't build a spaceship | **Accept** | At runtime: **one** writer call per message, **at most one** repair call if time allows, a second provider **only on error**, then a deterministic template. Best-of-N and LLM-judging at runtime are dropped. The local judge is a **development tool only**. |

### 1b. Plan 1: keep the simplicity, drop these specific ideas

Keep: FastAPI + state store + templates + simulator loop, and its build order. **Drop** these, because each one loses points or breaks rules:
- Invented stats ("30 people nearby searching", "80% office workers"). This is fabrication, which costs −2 and caps the case at 5.
- "10% off" style offers. That's anti-pattern #1; use service + price from the catalog.
- Fake urgency ("only today", "limited spots") when it isn't backed by data.
- "No trigger → send a gentle nudge". Every message must have a trigger, and restraint is rewarded.
- `send_as: "[Merchant] Team"`. The value is an enum, `vera` or `merchant_on_behalf`.
- Building suppression keys from message content. Use `trigger.suppression_key`.
- "On a no, offer an alternative". An explicit no or stop means `end`.
- The default reply "Happy to help!". Generic replies score low.
- The claim that triggers arrive in the tick payload. Wrong: tick carries **IDs only**, and trigger bodies arrive through `/v1/context` with `scope: "trigger"`.

### 1c. Plan 2 (research): the right backbone, but these parts were wrong or missing (all from auditing the pack)

| # | Plan 2 said / assumed | What the pack actually shows | Change |
|---|---|---|---|
| A | Tune against `judge_simulator.py` on the 30 pairs | The simulator loads **seed files only** (10 merchants, 25 triggers), warms up only 5 merchants, and never touches `test_pairs.json` | Build our **own 30-pair runner** on the expanded dataset (§12) |
| B | "Pre-warm compositions at warmup" | Warmup pushes **0 triggers** | **Compose as soon as a trigger is pushed** (background task on `/v1/context scope=trigger`), so the tick is a cache hit |
| C | Skip customer reminders when consent scope is only `promotional_offers` | Generated customers carry `preferences.reminder_opt_in: true` | Consent engine uses scope **or** `reminder_opt_in`. T03/T04 become sendable instead of wrongly skipped |
| D | Placeholder triggers → build from `metric_or_topic` | Some trigger claims **contradict** the data: T25 `perf_dip` has views +8% / calls +2%; trg_039 `perf_spike` has −24% / −14% | **Verify every trigger claim** against data before writing (§6.2). Never claim a dip that isn't there |
| E | Blanket-skip the 13 mismatched triggers | Some are adaptable (`appointment_tomorrow` for a restaurant = a table booking; `research_digest` for salons = trend/tech items) | A **category × kind compatibility matrix** with values native / adapt / incompatible (§6.4). Skip only incompatible ones |
| F | Handle expiry by comparing to `now` | Every `expires_at` is ≤ 2026-06-30, but the local simulator sends the **real clock** (Sep 2026), so we'd send nothing locally | **`available_triggers` is authoritative.** The judge defines them as "active right now". Expiry only affects ranking |
| G | Social proof: "3 dentists in your locality did Y" | The data has **no peer counts**, only aggregates in `peer_stats` and `trend_signals` | Social proof comes only from benchmarks and trend deltas |
| H | — | The scorer penalizes **"exposing internal jargon" −1** | A jargon filter: no snake_case, and no words like "signal", "trigger", "payload", "CTR" (write "click rate") |
| I | — | The local scorer **doesn't see** peer_stats, digest, subscription or the customer relationship, so true facts can look fabricated | Put a light source tag in the body ("Delhi clinics avg 3.0%", "JIDA Oct 2026 p.14"). Don't over-fit to the local judge |
| J | — | Case-study text is checked for similarity | **Never put case-study bodies into prompts as few-shot examples.** Describe the *shape* only |
| K | Auto-reply: flag, wait, end | Example 2.5 says `wait` on first detection; Replay 4.1 says send once, then wait, then end | Use the **4.1 policy**, because that scenario is explicitly scored |
| L | Hostile → end | Replay 4.3 sends abuse **then** a GST question | After `end`, every later reply still returns a valid `end` (never an error). Details in §8 |
| M | — | The judge re-lists the same active trigger **every tick** | A sent-suppression ledger; never re-send the same `suppression_key` |
| N | — | Brief: "every message must have a trigger" | The bot **never initiates** without a trigger in `available_triggers` |
| O | Read participant repos for pitfalls | magicpin verifies you did the work yourself | **Don't read other participants' code.** Only the starter pack and general docs |
| P | Brief §7: `bot.py` + `submission.jsonl` | Website and portal accept **only the bot URL**, and the site says solo only | Submit the URL. Generate `submission.jsonl` anyway as a self-eval artifact and README evidence |

---

## 2. Source-of-truth decisions (contradictions resolved)

| Topic | Conflict | Decision |
|---|---|---|
| URLs | Website/brief allow them; API F.4 gives −3 per URL | Only if verbatim in the context. The dataset has none, so effectively none |
| Same-version context push | "no-op" vs 409 | 409 `stale_version`, state unchanged |
| First auto-reply | wait (2.5) vs send once (4.1) | Send once (4.1), then wait, then end |
| Timeouts | 30 s official vs 15 s simulator | Design to **10 s per tick** and **8 s per reply** |
| Rubric dimension name | "Trigger relevance" (brief) vs "Decision quality" (site/simulator) | Treat them as one dimension: *why this signal, why now* |
| Team size | Brief says solo or pairs; site says solo only | Solo |
| Deliverable | `bot.py` + jsonl (brief §7) vs URL (site) | URL, plus an optional 1-page README |
| Trigger expiry | `expires_at` vs `available_triggers` | `available_triggers` wins |
| Language | Merchant `languages` vs customer `language_pref` | Merchant-facing follows the merchant; customer-facing follows the customer |

---

## 3. How points are actually earned (scoring model)

**Phase 2** (every proactive message, 5 × 10):
- **Specificity**: at least 2 verifiable numbers, dates, prices or sources from the context.
- **Category fit**: voice, register, vocabulary, no taboo words.
- **Merchant fit**: owner name, their own metrics, offers and history, their language.
- **Decision quality**: the single best signal, and "why now" stated explicitly.
- **Engagement**: one lever and one low-friction CTA as the last sentence.

**Phase 3:** up to +5 per dimension if later messages *use* newly injected facts (new digest items, shifted metrics, surprise customers). Stale composition loses; invented context loses most.

**Phase 4** (top 10 only, +30): auto-reply hell, intent transition, hostile then off-topic. Five turns each, scored on flow.

**Operational penalties** (up to −20):

| Failure | Penalty |
|---|---|
| Timeout | −1 |
| Malformed action or empty body | −2 |
| Verbatim repeat in a conversation | −2 |
| URL in body | −3 |
| Healthz offline | −10 (3 misses = disqualified) |
| Fabrication | −2, and the case is capped at 5 per dimension |
| Research claim without a source | capped at 7 |
| Rationale that doesn't match the message | penalized |

**What the judge sees in the local scorer:**
- category slug, voice tone, taboos
- merchant name, owner name, locality, languages, views/calls/ctr, signals, active offers
- trigger kind, payload and urgency
- customer identity
- our body, cta and send_as

The official judge also gets the full brief, the dataset and our **rationale**.

---

## 4. Data reality (audit of the expanded dataset)

- **Merchants:** 10 rich seeds and 40 thin generated ones. The thin ones have no offers, no signals, no history and no review themes. They do have: owner name, city/locality, languages, subscription, views/calls/ctr/directions, 7-day deltas, `total_unique_ytd`.
- **Triggers:** 100 total, of which **75 are placeholders** with payload `{"placeholder": true, "metric_or_topic": kind}`. 13 are category-mismatched. Several contradict the merchant's own numbers.
- **Customers:** 200 total. 185 generated ones have consent scope `["promotional_offers"]` plus `reminder_opt_in` (mostly true). Some states conflict with the trigger (T14: trigger says `customer_lapsed_soft`, customer state is `churned`), so **customer state wins for tone**.
- **The 30 canonical pairs:**
  - 12 use placeholder triggers.
  - 9 are customer-facing.
  - T08 is a pharmacy-style `chronic_refill_due` sent to a **dentist's** customer, which is incompatible.
  - T25 is a `perf_dip` with no dip in the data.
  - T27 is a `perf_spike` of only +2% / +5%.
- **Categories:** each has voice (tone, register, code_mix, vocab_allowed, vocab_taboo, salutations), offer_catalog (service + price), peer_stats, 5 digest items with sources, seasonal_beats and trend_signals. **This is the richest source of real facts for thin merchants.**
- **Languages:** every merchant has `en` + `hi`, some also mr/kn/ta/te. Category `code_mix` is `hindi_english_natural`, except gyms (`english_primary_some_hindi`).

---

## 5. Architecture

```
 /v1/context ──► VALIDATE ──► VERSIONED STORE (in memory)
                                   │  (trigger push → background compose → cache)
 /v1/tick ────► for each available trigger:
                  ELIGIBILITY (known ids, suppression, compatibility, consent, opt-out)
                  CLAIM CHECK (does the data support the trigger?)
                  FACT SHEET (atoms with value + display + source field, all maths in Python)
                  PLAYBOOK (lever, CTA type, template_name, next_action)
               ► RANK ► SELECT (≤1 per merchant×audience, ≤20 total, fatigue rule)
               ► TEMPLATE DRAFT ─► VALIDATOR ─► LLM POLISH (only if rate limiter + deadline allow)
                                                   ─► VALIDATOR ─┬ pass ─► action
                                                                 └ fail ─► send the template draft
 /v1/reply ───► CLASSIFIER (rules first) ► CONVERSATION FSM ► send / wait / end
                   └ LLM only for open questions, with the same fact sheet + validator
 CACHE: sha256(prompt_version, model, canonical facts, now_date) → output   (determinism)
```

**Stack:** Python 3.11, FastAPI, uvicorn (single worker), httpx async, pydantic v2, pytest. State in memory (§0.5). No framework beyond that.

**LLM budget per composition:** the template draft always exists first. At most one polish call, from Gemini or from Groq if Gemini fails, only when the rate limiter allows. Every path ends in a validated message, so **a tick can never time out and an action can never be malformed**, even with zero LLM quota left.

---

## 6. Decision layer (where most of the score comes from)

### 6.1 Eligibility, in order (the first failure excludes the trigger and logs a reason)
1. The trigger, merchant, and customer (if customer-scoped) are all known. If the customer is still unknown, **defer to the next tick** rather than drop. This covers the "surprise customer + recall 2 min later" race.
2. `suppression_key` hasn't already been sent.
3. The merchant isn't opted out or ended because of hostility (30-day suppression).
4. Category × kind compatibility isn't *incompatible* (§6.4).
5. Customer consent: reminder purposes need `reminder_opt_in` or a matching scope; promotional or winback purposes need `promotional_offers` or `winback_offers`.
6. The claim check passes, or a pivot signal exists (§6.2).

### 6.2 Claim verification (the core of decision quality)
The trigger kind is a **claim**. Check it against the data before writing:
- **`perf_dip`:** find the metric with delta ≤ −10%, or use the payload metric. If none exists, don't claim a dip. Pivot to the strongest *real* signal (e.g., T25: expired 39 days ago → renewal/winback framing) and state the pivot in the rationale.
- **`perf_spike`:** requires delta ≥ +15%. Below that, frame it as "steady +5% calls", not a spike.
- **`milestone_reached` (placeholder):** state a real total (e.g., `total_unique_ytd`) **without** asserting a threshold you can't see.
- **`competitor_opened` (placeholder):** never name a competitor or give a distance. Use the merchant's position against category benchmarks.
- **`review_theme_emerged`:** needs non-empty `review_themes`. Otherwise use an ask-the-merchant fallback.
- **`festival_upcoming` (placeholder):** use the payload if present; otherwise the category `seasonal_beats` entry matching `now`'s month. Open decision: a small static calendar (§17).

### 6.3 Trigger playbook (26 kinds: anchor facts → lever → CTA)

| Kind | Audience | Anchor facts (fallback if thin) | Lever | CTA |
|---|---|---|---|---|
| research_digest | M | digest item by id (title, source, n, segment) + a matching cohort from customer_aggregate (fallback: newest category digest item, always with its source) | curiosity + reciprocity | open_ended |
| regulation_change | M | digest item + days to `deadline_iso` | loss aversion | binary |
| cde_opportunity | M | digest item, credits, fee | reciprocity | binary |
| supply_alert | M | molecule, batches, manufacturer, `chronic_rx_count` | urgency | binary |
| category_seasonal | M | payload trends + shelf action (fallback: seasonal_beats) | loss aversion | binary |
| festival_upcoming | M | payload (fallback: seasonal beat + catalog offer) | timeliness | binary |
| ipl_match_today | M | match, venue, time, weeknight flag + IPL digest item + active offer, with contrarian advice when the data supports it | loss aversion | binary |
| competitor_opened | M | payload if present; otherwise rating/ctr against peer_stats | loss aversion | binary |
| perf_dip / seasonal_perf_dip | M | verified delta + peer gap (+ season note, reframed as normal) | loss aversion / reassurance | binary |
| perf_spike | M | verified delta ≥ 15% + suggestion to convert the momentum | momentum | binary |
| milestone_reached | M | payload or real totals | social proof | binary |
| dormant_with_vera | M | days since last history entry (if any) + a specific question | ask the merchant | open_ended |
| curious_ask_due | M | a specific guess from the catalog or seasonal beat ("cleaning or whitening?") | ask the merchant | open_ended |
| renewal_due / winback_eligible | M | days_remaining or days_since_expiry, plus results since then | loss aversion | binary |
| gbp_unverified | M | verification path, estimated uplift % | effort externalization | binary |
| active_planning_intent | M | merchant's last message + a **delivered draft** built from real catalog prices | effort externalization | binary (confirm draft) |
| review_theme_emerged | M | theme, occurrences, quote | reciprocity | binary |
| recall_due | C | last visit → months, services, price, preferred slots | continuity | slot choice / binary |
| appointment_tomorrow | C | payload time/service if present; otherwise confirm/reschedule **without inventing a time** | utility | 1 / 2 |
| chronic_refill_due | C | molecules, run-out date, delivery/senior offers | utility | binary (CONFIRM) |
| trial_followup | C | trial service/date + next-step offer | reciprocity | binary |
| customer_lapsed_soft / hard | C | days since last visit (from `now`), previous focus, catalog offer, no-shame tone | reassurance | binary |
| wedding_package_followup | C | wedding date, days to go, trial date, next window, catalog price | timeline urgency | binary |

**Levers are limited to what the data can back.** Social proof comes only from `peer_stats` and `trend_signals`. Effort claims come only from a fixed whitelist (2 / 5 / 10 min).

### 6.4 Category × kind compatibility
- **Incompatible → skip, with a logged rationale:**
  - `chronic_refill_due` for non-pharmacies (e.g., T08)
  - `recall_due` for pharmacies
  - `trial_followup` for pharmacies and restaurants
- **Adapt:**
  - `recall_due` for gyms → "back to routine"
  - `appointment_tomorrow` for restaurants → table reservation
  - `research_digest` for salons/restaurants → the category's trend/tech digest item
- **Everything else is native.** The matrix lives in one config file.

### 6.5 Ranking and selection per tick
- **Score** = 3×urgency + evidence strength (number of verified facts, max 4) + freshness (fact first seen in the latest context version, +2) + audience fit − fatigue.
- **Limits:**
  - at most one merchant-facing and one customer-facing new conversation per merchant per tick
  - at most 20 actions in total
  - stable tie-break: trigger_id
- **Fatigue:** if the merchant has an open conversation active within the last 15 simulated minutes, triggers with urgency ≤ 3 are **deferred** to a later tick, not dropped.
- **Restraint is narrow.** Only hard violations are skipped: unknown IDs, incompatible kind, no consent, opted-out merchant, already-sent key, or a contradicted claim with no pivot. Everything else is sent, because unsent pairs can't score.

---

## 7. Fact sheet → writer → validator → fallback

### 7.1 Fact sheet (built in Python, the only thing the LLM sees)
Each fact is an atom: `{id, label, value, display, source_field}`. For example: `ctr_gap = {value: 0.009, display: "2.1% vs Delhi avg 3.0%", source: performance.ctr + peer_stats.avg_ctr}`.
- **All arithmetic is precomputed:** deltas, gaps, days until/since (relative to the judge's `now`), savings, counts.
- The LLM also receives:
  - voice block: tone, register, code_mix, allowed vocabulary, taboos, salutation
  - language decision
  - lever
  - CTA type
  - `next_action`
  - send_as rules
  - forbidden list
  - 2–3 *shape* rules (never case-study text)

### 7.2 Writer output (JSON schema)
`{body, cta, template_params[], used_fact_ids[], next_action, rationale}`. The rationale names the trigger, the pivot (if any), the lever and the fact source fields, so it can be cross-checked.

### 7.3 Validator

**Hard rules (a failure goes to repair, then to the template):**
- Body is non-empty.
- Every numeric token (numbers, ₹, %, dates, times) normalizes to a fact value or a whitelist item (CTA option digits 1–3; effort minutes 2/5/10).
- `used_fact_ids` is a subset of the fact sheet.
- No URL unless it appears verbatim in the context.
- No word from the category taboo list or the global banned list ("guaranteed", "miracle", "best in city", "100%").
- No fake-urgency phrase unless it is backed by a fact.
- No internal jargon (snake_case, "trigger", "signal", "payload", "context", "placeholder", "suppression", "peer median").
- CTA shape matches the CTA type: binary ends with one yes/no ask; `none` has no question; only one ask in the whole message.
- Body isn't identical to any earlier body in the conversation, and doesn't re-introduce Vera after turn 1.
- `send_as` matches the audience. Customer-facing messages mention neither Vera nor magicpin internals.
- Commit-mode replies pass the action-keyword rule (§8).

**Soft rules (used to rank a repair):**
- Owner name or "Dr." in the opening merchant message.
- At least 2 verified facts (curious-ask excepted).
- 180–450 characters (booking messages up to ~520).
- At most one emoji, category-appropriate (none for pharmacies; clinical ones for dentists).
- CTA is the last sentence.
- Language matches.

### 7.4 Fallback templates
Every kind × {English, Hinglish} has a deterministic template filled from the same fact sheet and run through the same validator. The fallback rate is tracked as a metric; the target is under 5% of compositions.

---

## 8. Reply engine (rules first; the LLM writes only open answers)

**Classifier priority (first match wins):**
1. The conversation has already ended → `end`.
2. Opt-out ("stop", "band karo", "mat bhejo", "not interested", "unsubscribe") → `end`, and suppress the merchant for 30 days.
3. Hostile or abusive → `send` a one-line apology containing "sorry" + "won't message again" (cta `none`), and set the state to CLOSING. The next inbound: if it's off-topic, send one polite decline, then `end`; otherwise `end`.
4. Auto-reply. The detector combines three checks:
   - a phrase lexicon in English, Hindi (Roman and Devanagari) and Hinglish
   - a **per-merchant fingerprint** that spans conversations, because the simulator uses a new conversation_id each turn
   - generic thanks-and-deferral with no reference to Vera's message
   
   The policy counts per merchant: 1st → a single send asking for the owner, 2nd → `wait` 3600 s, 3rd+ → `end`.
5. Commit ("haan", "ok karo", "chalo", "let's do it", "go ahead", "done", "what's next", "join", 👍) → **action mode**. Deliver the stored `next_action` artifact immediately: the draft post, message or offer text, built from facts. End with "Reply CONFIRM to make it live". Must include one of done/sending/draft/confirm/next and must avoid "would you / do you / can you tell / what if / how about".
6. Soft decline or later ("baad mein", "busy", "next week") → `wait`, with duration from the message or 86400 s by default.
7. Off-topic (GST, loans, ITR…) → one-line polite decline, plus one line redirecting to the pending action. A second off-topic message → `end`.
8. Question → the LLM answers **only from the fact sheet**, or says honestly that it doesn't have that data, then re-offers the pending action.
9. Anything else → the LLM writes a contextual reply under the same validator.

**Conversation state:**
- stored per conversation: state, turn count, unanswered-nudge count, sent-body hashes, `next_action`, language (mirrored per turn using a Devanagari ratio plus Hinglish stopwords)
- stored per merchant: fingerprint set
- End after 5 turns or 3 unanswered nudges.

**Unknown conversation_id** (the simulator does this): create state from the merchant's best current signal so a commit still produces a real action. Never return an error.

**Customer replies:** slot "1"/"2" → confirm; stop → end; question → answer from merchant facts.

---

## 9. API contract (exact behaviour)

- **`POST /v1/context`**
  - Key: `(scope, context_id)`.
  - Higher version → replace atomically, return **200** `{accepted:true, ack_id, stored_at}`.
  - Same or lower version → **409** `{accepted:false, reason:"stale_version", current_version}`, state unchanged.
  - Bad scope or schema → **400** `{accepted:false, reason:"invalid_scope"|..., details}`.
  - Store in memory, then schedule a background compose if the scope is `trigger`.
  - A digest version bump records which items are new, for the freshness score.
- **`POST /v1/tick`**
  - Hard deadline of 10 s. Awaits in-flight background composes; anything unfinished uses the template.
  - Returns `{actions:[...]}`, each with every required field: `conversation_id` (readable, e.g. `conv_m001_research_2026W17`), `merchant_id`, `customer_id`, `send_as`, `trigger_id`, `template_name`, `template_params`, `body`, `cta`, `suppression_key`, `rationale`.
  - An empty list is valid.
- **`POST /v1/reply`**
  - 8 s deadline.
  - Always returns one of the three action shapes; `send` always has a non-empty body.
- **`GET /v1/healthz`**
  - Served from memory in under 50 ms and never touches the LLM.
  - `contexts_loaded` = count of distinct IDs per scope.
- **`GET /v1/metadata`**
  - Real details: your name, solo, the model, a one-line approach, email, version, submitted_at.
- **`POST /v1/teardown`** (optional)
  - Wipes memory and cache.
- **Concurrency:**
  - single uvicorn worker (one source of state)
  - a per-conversation asyncio lock
  - LLM calls behind a semaphore (8–10)
  - per-call timeout 6 s
- **Security:**
  - API keys in environment variables
  - payloads go only to the LLM provider
  - no third-party logging of payloads

---

## 10. Determinism
- The cache key is `sha256(prompt_version, model_id, canonical JSON of the fact sheet + voice + lever + CTA type, now truncated to date if any fact depends on it)`. On a hit, return the stored output byte for byte.
- Temperature 0, provider seed where supported, max_tokens ≈ 350, strict JSON.
- The decision layer is pure. Sorting always uses stable tie-breakers.
- The cache lives in memory next to the contexts. Templates are pure functions, so even after a restart, template output for the same input is identical.
- Test: run the full 100-trigger sweep twice; the diff must be empty.

---

## 11. Adaptive injection (Phase 3 bonus)
- A version bump invalidates only the affected cache keys, because keys are built from facts.
- New digest items get priority as anchors (freshness +2) in research, regulation and fallback compositions.
- Performance updates recompute the fact sheet. Claim checks re-run on deferred triggers.
- A surprise customer followed by `recall_due` two minutes later: stored → consent check → `merchant_on_behalf` recall using the customer's name, last visit and preferred slot. If the trigger arrives first, defer it by one tick.

---

## 12. Evaluation harness (dev-time only)

| Tool | What it does | Pass bar |
|---|---|---|
| Contract tests (pytest + httpx) | 200/409/400 semantics, counts, schemas, empty tick, unknown IDs, oversized payload | 100% green |
| **Canonical-30 runner** | Pushes the expanded dataset, ticks each test pair's trigger, writes `submission.jsonl`, runs validator + local judge | Every pair valid; avg ≥ 40/50; no dimension avg < 7 |
| 100-trigger sweep | Robustness across all kinds | 0 crashes, 0 invalid, fallback < 5% |
| Replay scripts | The simulator's 3 checks + Hinglish auto-reply + "haan kar do" + abuse→GST + unknown conversation_id | All pass |
| Harness emulator | 60 simulated minutes, 12 ticks, full injection schedule, scripted reply personas | No repeats, no timeouts, new facts used |
| Latency drill | 20 actions in one tick with cold cache | p100 < 10 s |
| Determinism check | Two full runs | Identical |
| `judge_simulator.py` | Official smoke test, `TEST_SCENARIO="all"` and `"full_evaluation"` | Runs clean (restart the bot between runs because of the 409 rule) |
| Manual read | 20 random outputs per iteration, read by you | You'd reply to them yourself |

The local judge reuses the simulator's `LLMScorer` prompt, run with a **different model family** from the writer to avoid self-preference.

---

## 13. Deployment and operations
- **Render free web service** built from the GitHub repo (`render.yaml`: Python 3.11, `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, one worker). Render provides HTTPS.
- **Environment variables on Render:** `GEMINI_API_KEY` (prod project), `GEMINI_MODEL`, `GROQ_API_KEY`, `GROQ_MODEL`, `LLM_ENABLED=1`.
- **Keep-alive:** cron-job.org hitting `/v1/healthz` every minute. Optionally add UptimeRobot every 5 minutes as a second monitor that also alerts you.
- **Never use a laptop tunnel.** The bot must stay live until results arrive, which could be days.
- **750 free hours a month covers one always-on service.** Don't run a second Render service on the same account.
- **Freeze the code after submitting.** A redeploy restarts the instance and wipes the loaded contexts.
- **Cost: ₹0.**

---

## 14. Build phases (each ends with an exit test)

| Phase | Scope | Exit criteria |
|---|---|---|
| 0. Data audit script | Tag triggers (rich / placeholder / mismatched / contradicted), merchants (rich / thin), consent; produce the coverage matrix for the 30 pairs | Matrix reviewed by you |
| 1. Contract server | 5 endpoints + teardown, in-memory versioned store, boot marker, healthz counts, metadata; the tick returns templates only | Contract tests + simulator `all` pass |
| 2. Decision layer | Eligibility, compatibility, consent, claim check, fact sheets, playbook for all 26 kinds, ranking, suppression | 100-trigger sweep with templates: 0 invalid |
| 3. Writer + validator | Prompt per voice, JSON schema, validator, repair, cache, background compose | Canonical-30 avg ≥ 38 with the LLM; fallback < 5% |
| 4. Reply engine | Classifier, FSM, fingerprints, action artifacts, language mirroring | All replay scripts pass |
| 5. Eval and tune | Harness emulator, latency/determinism drills, model bake-off, copy tuning | Canonical-30 avg ≥ 40, every dimension ≥ 7, drills green |
| 6. Deploy + README | Render free + keep-alive ping, README (approach, tradeoffs, what context would help) | Remote simulator run green, morning smoke test green → **submit** |

**Timeline:** see §0.5. About 12 working hours, submission by 11:00 IST on 27 Sep. In that version the phase-5 bar becomes "every pair valid, fallback path tested, average ≥ 38 where the LLM judge was run."

---

## 15. Differentiators (all within the rules)
1. **Claim verification with honest pivots.** The bot refuses to call +8% a "dip" and says what the data actually shows. This is the strongest decision-quality signal.
2. **Derived anchors for thin merchants.** Examples: click-rate gap against the city benchmark, 7-day deltas, days to renewal, customers this year. Every number is computed and sourced.
3. **Contrarian advice only when the data supports it.** Example: Saturday IPL + weekday-only BOGO → push delivery instead.
4. **"Ask the merchant" with a specific guess** for curious-ask and dormant triggers. This is the lever the brief says production Vera barely uses.
5. **Action mode delivers the artifact.** On "yes", reply with the actual draft post, offer or message, not another question.
6. **Per-merchant auto-reply fingerprint across conversations.** The second conversation recognizes the canned text on first sight.
7. **Consent-aware customer messaging** (scope or `reminder_opt_in`). Customer state overrides the trigger label for tone.
8. **Language mirroring per turn.** Roman Hinglish per the category `code_mix` setting; gyms lean English.
9. **Freshness-first on injected context** for the Phase 3 bonus.
10. **Auditable rationale:** trigger → pivot → lever → exact source fields.

---

## 16. Things we will not do
- Invent numbers, peer counts, competitor names, dates, slots, times or research.
- Use generic % discounts when a service + price exists in the catalog.
- Include more than one CTA, bury the CTA, or open with a preamble.
- Re-introduce ourselves after turn 1.
- Use promotional hype in clinical categories.
- Send without a trigger, re-send a suppression key, or message after a stop.
- Put case-study text in prompts or copy participants' code.
- Make more than two LLM calls per message at runtime, or let any path skip the validator.
- Deploy on a host that sleeps or restarts.

---

## 17. Decisions (resolved in v2)
1. **LLM:** free tier only. Gemini (model set by env var; pick the fastest Flash/Flash-Lite that AI Studio offers free) as primary, Groq `openai/gpt-oss-20b` or `-120b` as failover. Templates are the main path (§0.5).
2. **Hosting:** Render free + a 1-minute keep-alive from cron-job.org.
3. **Restraint:** narrow (§6.5).
4. **Festival triggers with no festival named:** category seasonal beats only.
5. **Script:** Roman Hinglish.
6. **Hostile:** a short apology ("sorry, won't message again"), then end.
7. **Code:** your GitHub repo, committed phase by phase.
8. **Metadata:** `team_name` "Shrey Gupta", `team_members` ["Shrey Gupta"], `contact_email` shreygupta0924@gmail.com. These must match what you enter in the submission form.

---

## 18. Repository layout
```
vera-bot/
  app/main.py            # FastAPI endpoints, deadlines
  app/store.py           # in-memory versioned store {(scope,id): {version,payload}}, conversations, LRU cache
  app/decide/            # eligibility, compatibility.yaml, consent, claims, facts, playbook, rank
  app/write/             # prompts/, llm client (primary+failover), validator, templates/
  app/converse/          # classifier, lexicons (en/hi/hinglish), fsm, fingerprints
  eval/                  # contract tests, canonical30 runner, sweep, replay, harness emulator, local judge
  data/                  # starter pack (read-only)
  README.md  render.yaml  requirements.txt  .env.example
```

## 19. Risk register

| Risk | Mitigation |
|---|---|
| Free LLM quota runs out mid-test | Rate limiter per provider → Groq → template draft (always valid); separate prod key; reset at 12:30 IST |
| Tick exceeds 15/30 s | Background compose on trigger push, 10 s deadline, template fallback |
| Local-judge over-fitting | Different model family, manual reads, official-scorer prompt |
| Render free sleeps or restarts and loses state | 1-min keep-alive ping; no redeploys after submitting; accepted residual risk (only paid plans fix it) |
| Hidden spec variance in the real harness | Tolerant request parsing (unknown fields ignored, missing optional fields defaulted), never an error for valid-looking input |
| Originality check before the offer | Keep a decision log; be able to explain every module |
