# FTJA Native Onboarding Checklist Implementation Plan

> **For Terra in a fresh session:** Implement this plan task-by-task in `/Users/taegyujeong/work/FTJA-release`. Do not replace it with another planning pass. Use the repository’s actual files and current official Claude Code and Codex documentation; do not rely on remembered tool names or behavior.

**Goal:** Make FTJA create a complete native agent task checklist immediately after cloning, then advance it only as each mandatory onboarding operation and confirmation is actually verified.

**Architecture:** Keep `onboarding-state.json` and observed command/browser results as the source of truth. The native client checklist is a visible mirror and execution contract: Claude Code uses its supported task-list tools; Codex uses `update_plan`. Clone happens before the repository-local skill can run, so the setup skill must verify the clone, create the complete checklist, and mark only the verified clone item complete before continuing.

**Tech Stack:** Repository-local Claude Code and Agent Skills (`SKILL.md`), Python `unittest`, existing file-backed FTJA onboarding state machine.

---

## Required outcome

The first FTJA setup skill turn after clone must:

1. Resolve and verify the exact cloned FTJA repository.
2. Create the entire native checklist before workspace attachment or other setup work.
3. Include the already-completed clone operation in the checklist.
4. Mark clone complete only after verifying the repository path, `.git`, and expected remote.
5. Keep exactly one later task `in_progress` at a time.
6. Mark a task complete only after its required evidence is observed.
7. Keep chat messages minimal because the native checklist carries progress.
8. Never enter Claude Code Plan Mode or Codex Plan Mode for this workflow.
9. Never present a fictional “session detected” claim. The current product uses a bounded file/action bridge, not automatic client-session discovery.
10. Keep the Claude Code and Codex skill copies behaviorally identical.

## Mandatory user-visible checklist

Use these English task labels and preserve this order unless inspection proves that an operation is not part of the real mandatory flow:

### Workspace setup

1. `Clone the FTJA workspace`
2. `Connect this session to the workspace`
3. `Prepare the local environment`
4. `Initialize the onboarding state`
5. `Start the local FTJA server`
6. `Verify the local server`
7. `Open FTJA in the browser`
8. `Connect this setup session to FTJA`

### Profile

9. `Add your background files`
10. `Confirm your background files`
11. `Build your profile summary`
12. `Review and confirm your profile summary`

### Search settings

13. `Prepare suggested roles and location`
14. `Review and confirm your search settings`
15. `Prepare the code-based filter`
16. `Review and confirm the code-based filter`

### Judgment rubric

17. `Define what makes a role a clear yes`
18. `Define your dealbreakers`
19. `Define your preferences`
20. `Review and confirm the judgment rubric`

### First run

21. `Open the full pipeline`
22. `Run your first job search`
23. `Review your first results`

Do not collapse this back into only `Profile → Rubric → Run → Learn`. Those remain useful web-stage groupings, but they do not expose every mandatory agent operation.

## Evidence contract

At minimum, encode these completion rules in the setup skill:

| Checklist task | Required evidence before completion |
|---|---|
| Clone the FTJA workspace | Exact repository path resolved; `.git` exists; expected FTJA remote is verified |
| Connect this session to the workspace | Client workspace/directory operation explicitly confirms access; shell `cd` is insufficient |
| Prepare the local environment | Required interpreter/environment and dependencies are present or successfully created; verify with the repository’s actual commands |
| Initialize the onboarding state | `onboarding-state.json` is initialized or successfully resumed from the connected repository |
| Start the local FTJA server | Server process is actually started, or an existing matching server is identified |
| Verify the local server | `http://127.0.0.1:8765/` returns the expected FTJA response |
| Open FTJA in the browser | Navigation succeeds and expected FTJA page text is observed |
| Connect this setup session to FTJA | The current setup session starts the bounded action wait/bridge; do not call this “session detection” |
| Add your background files | Current state contains at least one selected source; do not treat first selection as final confirmation |
| Confirm your background files | `confirm_profile_sources` is received and the durable revision advances |
| Build your profile summary | Summary is derived from the full confirmed source list and written to the expected local state/file |
| Review and confirm your profile summary | `confirm_profile_summary` is received and the durable revision advances |
| Prepare suggested roles and location | Draft titles/location/settings are written without promoting them to confirmed configuration |
| Review and confirm your search settings | `confirm_profile` is received and the durable revision advances |
| Prepare the code-based filter | Stage 0 draft is present with keywords, languages, and exclusions |
| Review and confirm the code-based filter | `confirm_stage0` is received and the durable revision advances |
| Three rubric-answer tasks | The matching `answer_rubric_question` action is received in strict order for `clear_yes`, `dealbreakers`, and `preferences` |
| Review and confirm the judgment rubric | `confirm_rubric` is received; `criteria.json`, `rubric.md`, and `profile/summary.md` are written successfully |
| Open the full pipeline | Pipeline view is actually reachable and expected content is observed |
| Run your first job search | The real `/ftja-run` flow completes; do not complete this item merely because the command was suggested |
| Review your first results | A result surface/run artifact is actually available for review; if no jobs pass, verify and explain the valid empty result rather than fabricating results |

## Native tool contract

Before editing, verify the current official docs and current client behavior.

- Claude Code official references:
  - `https://code.claude.com/docs/en/tools`
  - `https://code.claude.com/docs/en/interactive-mode`
- Codex official references/source:
  - `https://developers.openai.com/codex/cli/reference.md`
  - `https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/handlers/plan_spec.rs`
  - `https://github.com/openai/codex/blob/main/codex-rs/collaboration-mode-templates/templates/plan.md`

Required behavior after verification:

- Claude Code: use the currently supported native task creation/update tools. Do not assume availability on every model. If the tools are unavailable, follow the exact officially documented activation/relaunch path and report the limitation; do not fake a native checklist with prose while claiming success.
- Codex: use `update_plan` with `pending`, `in_progress`, and `completed` states, with at most one item in progress.
- Do not use `EnterPlanMode`, `/plan`, or permission/collaboration Plan Mode. Codex’s Plan Mode and `update_plan` checklist are explicitly separate, and `update_plan` is not usable in Plan Mode.
- Tool availability is not the same as guaranteed tool invocation. Skill instructions can require native task usage, but do not claim hard runtime enforcement unless a tested hook or equivalent enforcement mechanism is implemented.
- Hooks are not part of this card by default. If true runtime enforcement is necessary, document it as a separate follow-up rather than silently adding cross-client hook infrastructure.

## Chat-copy contract

The checklist carries detailed progress. Normal successful chat updates should therefore be one or two short sentences containing only:

1. What was just verified, if useful.
2. The single next user action, or that no action is needed yet.

Examples:

```text
FTJA is ready. Add your background files in the browser.
```

```text
Your files are confirmed. I’m building your profile summary.
```

```text
Your profile summary is ready. Review and confirm it in FTJA.
```

Do not dump the entire checklist into ordinary prose after creating the native list. Do not repeat fields already visible in the web card. Technical details belong only in failure/recovery messages.

---

### Task 1: Inspect and lock the real setup sequence

**Objective:** Reconcile the proposed 23 tasks with the repository’s actual mandatory setup, state transitions, first-run handoff, and supported clients before editing instructions.

**Files:**
- Read: `.claude/skills/ftja-setup/SKILL.md`
- Read: `.agents/skills/ftja-setup/SKILL.md`
- Read: `ftja/onboarding.py`
- Read: `ftja/server.py`
- Read: `ftja/app.html`
- Read: `.claude/skills/ftja-run/SKILL.md`
- Read: `.agents/skills/ftja-run/SKILL.md`
- Read: `landing.html`
- Read: `setup.html`
- Read: `README.md`

**Step 1: Verify repository identity**

Run:

```bash
pwd
git status --short
git branch --show-current
git remote -v
```

Expected: exact target repository, clean or understood working tree, expected FTJA remote. Do not reset unrelated changes.

**Step 2: Verify client-native tool behavior from official sources**

Read the official URLs listed above and record only facts needed for this implementation. Do not rely on remembered tool names, flags, model availability, or Plan Mode behavior.

**Step 3: Trace every checklist item to real evidence**

For each proposed label, identify the existing command, client result, state transition, action type, file, HTTP response, or first-run artifact that proves completion. If a proposed operation does not exist, do not invent it. Rename it to match the real mechanism or report the mismatch before implementation.

**Step 4: Confirm scope**

This card should primarily change the two setup skill copies and focused contract tests. Do not redesign `ftja/app.html` or the state machine unless inspection proves that the checklist cannot truthfully mirror the existing flow.

**Step 5: Commit only if the repository workflow expects commits**

Do not commit inspection-only work.

---

### Task 2: Add failing tests for the native checklist contract

**Objective:** Make the complete checklist, client-specific native tools, evidence rules, and copy parity regression-testable before modifying the skills.

**Files:**
- Create: `tests/test_setup_skill_contract.py`
- Test: `tests/test_setup_skill_contract.py`

**Step 1: Write failing tests**

Add focused tests that read both setup skill files and assert:

- `.claude/skills/ftja-setup/SKILL.md` and `.agents/skills/ftja-setup/SKILL.md` remain identical, unless verified client-specific syntax requires narrowly documented differences.
- All 23 English labels appear in the intended order.
- Claude native task tools and Codex `update_plan` are explicitly required.
- Plan Mode is explicitly prohibited for this workflow.
- The skill requires exactly one in-progress checklist item.
- Clone is verified and then marked complete after checklist creation.
- Shell `cd` is explicitly insufficient proof of workspace connection.
- Server start and server verification are separate tasks.
- Browser open requires navigation/page-text evidence.
- “Session detection” is not claimed; the bounded bridge is described truthfully.
- Every web confirmation maps to the existing action names.
- A command invocation alone cannot complete a task.
- Successful chat copy is constrained to a minimal next-action update.

Prefer parsing ordered labels from a dedicated Markdown section over twenty-three unrelated `assertIn` calls if that keeps the test readable.

**Step 2: Run the focused test and verify RED**

Run:

```bash
python -m unittest tests.test_setup_skill_contract -v
```

Expected: FAIL because the current setup skills do not contain the complete native checklist contract.

**Step 3: Commit the failing test**

```bash
git add tests/test_setup_skill_contract.py
git commit -m "test: define native onboarding checklist contract"
```

Do not alter existing tests to make the new behavior pass.

---

### Task 3: Implement the checklist contract in both setup skills

**Objective:** Make the setup agent create and maintain the complete native checklist after clone and before the remaining onboarding work.

**Files:**
- Modify: `.claude/skills/ftja-setup/SKILL.md`
- Modify: `.agents/skills/ftja-setup/SKILL.md`
- Test: `tests/test_setup_skill_contract.py`

**Step 1: Add a mandatory checklist section near the top**

Place it before the existing first-turn procedure so the agent cannot begin workspace attachment, server startup, or source collection before creating the list.

The section must:

- distinguish Claude Code native task tools from Codex `update_plan`;
- explicitly prohibit Plan Mode;
- include the exact ordered checklist;
- explain that clone precedes skill activation;
- require clone verification before completing item 1;
- require one and only one current item;
- require evidence before completion;
- require the checklist to mirror durable state rather than replace it;
- preserve every already-completed item instead of removing or hiding it;
- stop honestly when a required native tool is unavailable.

**Step 2: Integrate, do not duplicate, the existing mandatory first-turn order**

Update the existing prose so each operation explicitly updates the corresponding native task. Avoid creating a second conflicting sequence elsewhere in the file.

**Step 3: Integrate all web confirmation transitions**

Map the existing action bridge to checklist advancement for:

```text
confirm_profile_sources
confirm_profile_summary
confirm_profile
confirm_stage0
answer_rubric_question: clear_yes
answer_rubric_question: dealbreakers
answer_rubric_question: preferences
confirm_rubric
```

The agent must update the native checklist only after `wait-for-action` returns `status: confirmed` and the full durable state has been read.

**Step 4: Integrate the first-run handoff**

Do not mark `Run your first job search` or `Review your first results` complete during setup merely because `/ftja-run` is mentioned. Align these tasks with the actual run skill and observable result artifacts. If a single native checklist cannot reliably survive the setup-to-run boundary on a supported client, state that limitation precisely and choose the least misleading supported behavior; do not invent persistence.

**Step 5: Tighten routine chat output**

Replace verbose default narration requirements with the minimal chat-copy contract. Preserve detailed recovery information for failures and timeouts.

**Step 6: Run the focused test and verify GREEN**

Run:

```bash
python -m unittest tests.test_setup_skill_contract -v
```

Expected: PASS.

**Step 7: Review the diff for accidental behavior changes**

Run:

```bash
git diff -- .claude/skills/ftja-setup/SKILL.md .agents/skills/ftja-setup/SKILL.md tests/test_setup_skill_contract.py
```

Expected: only the native checklist/copy contract and its tests change.

**Step 8: Commit**

```bash
git add .claude/skills/ftja-setup/SKILL.md .agents/skills/ftja-setup/SKILL.md tests/test_setup_skill_contract.py
git commit -m "feat: expose complete onboarding checklist"
```

---

### Task 4: Verify the full repository test suite

**Objective:** Prove that the instruction changes did not weaken the existing onboarding state machine, source bridge, review flow, or web app.

**Files:**
- Verify only; do not change tests to make failures disappear.

**Step 1: Run onboarding-focused tests**

Run:

```bash
python -m unittest tests.test_setup_skill_contract tests.test_onboarding_state tests.test_onboarding_review_flow tests.test_profile_sources -v
```

Expected: all tests pass.

**Step 2: Run the complete Python suite**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: all tests pass.

**Step 3: Run any repository-documented build or syntax checks**

Inspect `README.md`, workflow files, and available command help first. Run only commands actually supported by this repository. Record exact output summary.

**Step 4: Commit any legitimate test-only follow-up**

If no follow-up is required, do not create an empty commit. Never modify tests merely to match an incorrect implementation.

---

### Task 5: Perform a fresh-session behavioral validation

**Objective:** Verify the behavior in a clean agent session rather than declaring success from static skill text alone.

**Files/Artifacts:**
- Use a fresh temporary clone or clean E2E folder; do not reset or mutate unrelated user state.
- Preserve any logs or transcript evidence outside tracked product data unless the repository has a documented location.

**Step 1: Start a genuinely new supported client session**

Use a fresh session in the cloned FTJA workspace. Confirm which client and model are under test. If testing Claude Code, verify that its native task tools are actually available in that session before claiming checklist support.

**Step 2: Trigger FTJA setup through the real public/bootstrap handoff**

Clone first, then allow the repository-local setup skill to activate. Verify that the full checklist is created before workspace connection and that `Clone the FTJA workspace` is present and completed only after repository verification.

**Step 3: Observe early infrastructure transitions**

Verify, in order:

- clone complete;
- workspace connection in progress, then complete only after explicit access confirmation;
- environment preparation;
- onboarding state initialization;
- server start;
- HTTP verification;
- browser open and page-text verification;
- bounded setup bridge started.

Do not infer completion from prose. Capture the actual native checklist statuses and corresponding evidence.

**Step 4: Exercise at least one real web confirmation boundary**

Add one or more disposable background sources in the web UI, use the explicit confirmation control, and verify that:

- the action file/state revision changes;
- the waiting setup session receives `status: confirmed`;
- only then does the native checklist advance;
- the chat response remains concise.

Do not use private user documents for this validation.

**Step 5: Validate a later-state fixture without mutating live state**

Use a clean temporary project/state directory to exercise later checklist transitions through profile summary, search settings, Stage 0, rubric questions, and rubric confirmation. Confirm strict order and exactly one current item.

**Step 6: Validate first-run ownership honestly**

Check whether the same native checklist persists across the real setup-to-`/ftja-run` interaction in each supported client. If it does not, do not claim end-to-end checklist continuity. Report the precise boundary and request review rather than fabricating a pass.

**Step 7: Final verification report**

Report:

- exact files changed;
- exact commands run;
- key passing output lines;
- client/model tested;
- which checklist transitions were observed in the native UI;
- any unverified client, model, hook, or persistence behavior.

If every named acceptance criterion was not observed, request review instead of declaring completion.

---

## Files likely to change

Expected:

- `.claude/skills/ftja-setup/SKILL.md`
- `.agents/skills/ftja-setup/SKILL.md`
- `tests/test_setup_skill_contract.py` (new)

Only if real inspection proves necessary:

- `.claude/skills/ftja-run/SKILL.md`
- `.agents/skills/ftja-run/SKILL.md`

Explicitly out of scope by default:

- `ftja/app.html`
- `ftja/onboarding.py`
- `ftja/server.py`
- landing-page design changes
- new cross-client hook infrastructure
- changing existing tests to hide failures

## Acceptance criteria

- [ ] Checklist is created after clone and before the remaining setup actions.
- [ ] Clone remains visible and is verified before being marked complete.
- [ ] All mandatory operations and confirmation gates are listed, not only four product stages.
- [ ] Claude Code uses verified native task tools when available.
- [ ] Codex uses `update_plan` outside Plan Mode.
- [ ] Exactly one task is in progress.
- [ ] No task completes from an issued command alone.
- [ ] Workspace, HTTP, browser, bridge, and action confirmations use observable evidence.
- [ ] No fictional automatic session detection is claimed.
- [ ] Normal chat copy is concise and asks for one next action.
- [ ] Both setup skill copies remain synchronized.
- [ ] Focused and full test suites pass.
- [ ] At least one fresh-session native checklist flow is exercised with real tool output.
- [ ] Untested clients/models or setup-to-run checklist persistence are explicitly reported.

## Risks and tradeoffs

1. **Claude task-tool availability varies by version/model.** Verify the current official docs and the actual session. Do not silently downgrade to prose while calling it native.
2. **A long native checklist may not display every item simultaneously.** This is acceptable if all tasks exist and the client offers a way to inspect the full list; verify actual UI behavior rather than assuming it.
3. **Setup and first run may cross a task-list persistence boundary.** Test this. If continuity is not supported, report the boundary and keep state truthful.
4. **Skill instructions are not hard runtime enforcement.** This card establishes a strong, tested workflow contract. True enforcement via hooks is a separate decision and must not be overstated.
5. **Duplicated skill copies can drift.** Add a parity test and update both in the same commit.
6. **Overly verbose chat can negate the benefit.** Keep detailed status in the native checklist and rich decisions in the web UI.

## Terra completion standard

Do not finish with “the skill was updated.” Completion requires:

- the modified paths;
- the exact test and validation commands;
- key output lines proving success;
- real native-checklist evidence from a fresh session;
- a list of anything not verified.

If fresh-session native checklist behavior cannot be executed, stop at a review request and state exactly what a human must run or approve to continue.
