---
name: ftja-tune
description: Update rubric.md/criteria.json from a conversational statement of a new preference or dealbreaker. Invoke when, in the course of ANY conversation about jobs/career in this project, the user states a new preference, dealbreaker, or pivot ("I'm not interested in traditional PM roles", "drop the onsite-required ones") — proactively offer to add it, don't silently apply it.
---

# FTJA tune

This is the explicit-confirmation half of "self-improving rubric" (see
SPEC.md's Future Vision section for why the implicit/automatic half is
deliberately out of scope). Never edit rubric.md or criteria.json without
the user confirming first.

## When to trigger

The user says something, in passing or directly, that reads as a job-search
preference, dealbreaker, or a scope pivot (e.g. moving from one kind of role
to another). This can happen mid-conversation about something else that
touches on career/roles — you don't need an explicit `/ftja-tune` invocation
if the routing rule in this project's CLAUDE.md covers it.

## What to do

1. Read the current `rubric.md` and `criteria.json`.
2. Restate the new signal back to the user in one line and ask: "should I
   add this to the judgment criteria?" Do not just add it.
3. **Check for conflict** against the existing rubric/criteria. If the new
   statement contradicts something already there (e.g. rubric says "remote
   preferred" and the user now says "drop the onsite-required ones" — that's
   not a conflict, that's a tightening; but if it contradicts something like an
   existing dealbreaker being loosened, or two preferences that can't both
   hold), surface the conflict explicitly and ask which one wins. Don't
   silently pick.
4. On confirmation, edit the file (`rubric.md` for narrative criteria,
   `criteria.json` for structured filters — a "no on-site" statement likely
   touches both: an `exclude_keywords` entry AND a rubric.md dealbreaker
   line, ask if unsure which).
5. Commit:
   ```
   git add rubric.md criteria.json
   git commit -m "tune: <one-line description of the change>"
   ```
   Nothing to regenerate — the review server's Pipeline tab reads
   `criteria.json`/`rubric.md` live on every request.
6. Confirm the change back to the user in one line.

## What NOT to do

- Don't infer preferences from behavior (which digest jobs they clicked,
  applied to, etc.) — there's no tracking of that in this version. Only act
  on things the user actually said.
- Don't batch up multiple unrelated changes into one commit — one
  conversational update, one commit, so rollback (`git checkout <sha> --
  rubric.md`) stays precise.
