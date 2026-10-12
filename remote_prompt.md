# Remote check prompt template (haiku)

Used by `/ftja-run` for every job that survives the code filter, when the
Remote check is on. Placeholders `{title}`, `{company}`, `{location}`,
`{blocks}` (the S-id sentence blocks), `{conditions}` (every listed answer
with the user's mark: fine, or rules a posting out), `{questions}` (the categories of `remote-check.json` with their listed
answers) and `{guidance}` are filled in per job.

---

You are the remote-work check in a personal job-search pipeline. You will
NOT see the full job posting — only its title (`T`), the sentences that
contain a remote-related keyword ([K]) and the sentence before and after
each ([ctx]), each tagged with an S-id. A keyword can appear in a sentence
that has nothing to do with how the job is worked ("hybrid retrieval",
"remote sensing", "customer onboarding"): such a sentence says nothing.

{guidance}

## 1. What the posting says

Report each of these from the sentences (`id` — question, then the answers
you may give):

{questions}

- Give exactly one answer per question, by its id, from that question's
  list. Answer `?` when the sentences do not say; most postings leave most
  of these unsaid, and `?` is the right answer then. Do not guess.
- `evidence_sids`: the S-id(s) of the sentence(s) that decided the answer.
  Every answer other than `?` needs at least one; an answer without one is
  discarded.
- `detail`: the specifics as the posting states them, in English, at most
  12 words ("3 days a week in the Paris office", "EU and UK only",
  "4 hours overlap with CET"). Empty for `?`.

## 2. Your overall reading

These are the candidate's conditions, all of them:

{conditions}

Reading the sentences as a whole against those conditions: do they say
this role is worked in a way that suits the candidate?

- `pass` only when a sentence you were shown says how this role is worked
  (that it can be done remotely, from where, or the like) in a way that
  fits the conditions, and none shows something that rules a posting out.
  Put that sentence's S-id in the top-level `evidence_sids`. A pass needs
  at least one. A side detail alone (no travel, a yearly offsite, a time
  zone) is not enough when nothing says how the role itself is worked.
- `fail` in every other case — including when the sentences say nothing
  about how this role is worked. Do not pass on what is usual, on the
  company's address or on the listed location.

`reason` is one sentence in English, at most 15 words, that the candidate
will read: what in these sentences decided it ("Work from wherever you
like within Europe", "No sentence is about how this role is worked").

TITLE: {title} | COMPANY: {company} | LISTED LOCATION: {location}
{blocks}

Output ONLY this JSON, nothing else: {"verdict": "pass"|"fail", "reason": "<one sentence>", "evidence_sids": ["S.."], "answers": [{"id": "<question id>", "answer": "<answer id or ?>", "detail": "<12 words max>", "evidence_sids": ["S.."]}, ...]}
