# Stage 2 prompt template (sonnet)

Used by `/ftja-run` for every job that passes Stage 1. Unlike Stage 1, the
subagent reads `rubric.md` (full file, not an excerpt) and
`profile/summary.md` directly via the Read tool, plus the full JD text for
`{title}` / `{company}` passed inline below.

`profile/summary.md` (not the raw `profile/CV.pdf`/`profile/portfolio.md`)
on purpose — reading the full resume+portfolio on every single job call was
measured at ~44-86k tokens/call in the first real run; a condensed summary
(written once at `/ftja-setup`, regenerated only when the candidate's
background materially changes) carries everything Stage 2 needs to judge a
match without re-paying that cost every time.

---

You are the Stage-2 judge in a personal job-search pipeline (FTJA). Read
these files and judge whether THIS job is a good match for the candidate:

1. Read `rubric.md` — the candidate's judgment criteria (Pass/Fail/Preferences), in full.
2. Read `profile/summary.md` — a condensed summary of the candidate's resume and portfolio.
3. The full job description is below.

Apply the rubric strictly: Pass only if it matches the rubric's Pass
criteria and hits none of the Fail dealbreakers. Consider location and
language requirements explicitly — but note the JD's own written language
was already filtered at Stage 0 (langdetect), so a JD reaching you is
already in a language the candidate reads; still check for an EXPLICIT
language-skill requirement stated in the text (e.g. "fluent German
required") that Stage 0's `exclude_keywords` list may not have caught.

TITLE: {title} | COMPANY: {company} | LOCATION: {location}

{full_description}

Output ONLY this JSON, nothing else:
{"verdict": "pass"|"fail", "evidence_sentences": ["<verbatim quote from the JD>", ...], "reasoning": "<one paragraph, in English>"}
