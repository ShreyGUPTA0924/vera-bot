# Vera message engine — magicpin AI Challenge (solo entry, Shrey Gupta)

A small FastAPI service that decides **whether**, **whom** and **what** Vera should message, then handles the conversation that follows. Built to run reliably on free-tier infrastructure.

## Approach: rules decide, templates write, an LLM only polishes
1. **Versioned context store** (`app/store.py`): the 5 endpoints follow the testing brief exactly (200 / 409 `stale_version` / 400).
2. **Decision layer** (`app/decide.py`). Before anything is written, each trigger is checked for:
   - known IDs
   - a suppression key that hasn't been sent yet
   - category × kind compatibility (e.g. no chronic-refill reminder from a dentist)
   - customer consent (scope or `reminder_opt_in`)
   - an opted-out merchant
3. **Claim verification** (`app/playbook.py`). A trigger's label is treated as a claim and checked against the numbers. A "perf_dip" whose data shows +8% is **not** called a dip; the bot pivots to the strongest real signal (expired plan, unverified profile, renewal, benchmark gap) and says so in the rationale.
4. **Playbook for all 26 trigger kinds.** Each one has anchor facts, one compulsion lever, one CTA, and a ready "next action" artifact.
   - Thin merchants get derived facts: click-rate against the category benchmark, 7-day deltas, days to renewal.
   - Research and compliance items always cite their source.
   - Social proof comes only from real aggregates.
5. **Validator** (`app/validator.py`). Every number in an outgoing message must exist in the context or be computed from it. It also checks taboo words, internal jargon, URLs not in the context, the CTA shape, and verbatim repeats.
6. **LLM polish** (`app/llm.py`): Gemini first, Groq if Gemini fails, both on free tiers.
   - It rewrites the validated draft for fluency and must keep every number from the draft.
   - Its output goes through the same validator; if anything fails, the draft is sent unchanged.
   - Per-provider rate limiters (RPM / TPM / RPD) and cooldowns keep us within free quotas.
   - Composition starts in the background as soon as a trigger is pushed.
7. **Reply engine** (`app/converse.py`): rule-first and deterministic.
   - Auto-replies are detected by phrase (English / Hindi / Hinglish) plus a per-merchant fingerprint across conversations: flag once, then wait, then end.
   - "Let's do it" delivers the drafted artifact immediately, with no more qualifying questions.
   - Opt-outs end the conversation; hostility gets a one-line apology and then an exit; off-topic asks are politely declined and redirected.
   - Replies mirror the merchant's language each turn.
8. **Determinism.** Templates are pure functions. The first output served for an input is cached and served again for that input.

## Tradeoffs
- **Templates before LLM.** Free-tier limits (about 10–30 requests a minute) can't support 20 compositions per tick. The template path always produces a valid, grounded message within the deadline, even with zero LLM quota left. The cost is somewhat plainer wording when the LLM isn't available.
- **Narrow restraint.** The bot skips only hard violations. Everything else is sent, because a message that isn't sent can't help the merchant.
- **State lives in memory,** because the free host has no persistent disk. A platform restart empties it; startup logs a `=== BOOT ===` marker so this is visible.

## What additional context would help most
Per-trigger event details for generated triggers (festival name and date, competitor details, appointment time), post-level performance, review counts, and open booking slots. Most placeholder triggers force the bot to rely on aggregate facts.

## Run
```bash
pip install -r requirements.txt
cp .env.example .env            # add your keys (never commit .env)
uvicorn app.main:app --port 8080
python -m pytest -q             # contract tests
python eval/llm_check.py        # which models your keys can use
python eval/canonical30.py --judge
python eval/drills.py
```
Deploy: Render free web service from `render.yaml`, plus a 1-minute keep-alive ping to `/v1/healthz`.
