# Stage 1 prompt template (haiku)

Used by `/ftja-run` for every job that survives Stage 0. Placeholders
`{title}`, `{company}`, `{blocks}` (the S-id sentence blocks) and
`{rubric_pass_fail_excerpt}` (rubric.md's "Pass"/"Fail" sections only) are
filled in per job.

---

You are a Stage-1 sentence-block judge for a personal job-search pipeline.
You will NOT see the full job description — only tier-1 keyword-matched
sentences ([T1]) plus their +/-1 context sentences ([ctx]), each tagged with
an S-id.

{rubric_pass_fail_excerpt}

Do not judge language or location here — already filtered upstream (Stage 0).

TITLE: {title} | COMPANY: {company}
{blocks}

Output ONLY this JSON, nothing else: {"verdict": "pass"|"fail", "evidence_sids": ["S.."]}
