# ApexStrategist Agent Runtime

This directory contains the application-document generation agent system.

## Layout

- `skills/` - canonical `SKILL.md` contracts and runtime adapters.
- `prompts/` - single-agent, v1 multi-agent, and v2 ensemble prompts.
- `prompts/apex-single.prompt.md` - default low-cost runtime prompt.
- `topology/server_manifest.yaml` - optional v2 ensemble topology.
- `scripts/launch_v2_servers.sh` - optional local `agent_sync` server launcher.
- `.github/agents/` - role/persona files used by Copilot-style agent runtimes.
- `runtime_profiles.yaml` - explicit `single`, `ensemble_v2`, and future
  `auto_budget` runtime profiles.

## Runtime Defaults

Use `single` unless the user explicitly selects a multi-agent run or provides a
budget/runtime policy that enables `ensemble_v2`.

Personal inputs and generated documents live outside this source tree:

- `private/inputs/application_context.md`
- `private/output/generated_documents/`
- `private/tmp/agent_sync/`

`agent_sync` itself is a local runtime dependency and is ignored at
`agents/apex/agent_sync/`.

## Live vacancy shortlist

Use [apex-match-live-vacancies](skills/apex-match-live-vacancies/SKILL.md) to
rescan the live database against candidate evidence and refresh a selected
shortlist. It distinguishes eligibility from functional fit, reassesses older
omissions, and preserves existing workbook statuses and notes.

```text
$apex-match-live-vacancies Review the full live database and update my existing
job shortlist with supported additions and clearly labelled conditional roles.
```

## Phase 8 Option 9: Skills and proficiency

Use [`apex-generate-skills-proficiency`](skills/apex-generate-skills-proficiency/SKILL.md)
to prepare World Bank-style **Skill / Certification / Language** entries with
**High / Medium / Low** proficiency from the current vacancy and applicant job
history. It keeps source evidence and unresolved items separate from the entry
table. Proficiency is an advisory assessment, independent of Option 6 relevance
scores and experience totals; the skill does not submit entries to a portal.

The strategy report now prepares **Phase 1.4 — Skill / Certification / Language
Evidence Map** through the existing
[`apex-candidate-evidence-bank`](skills/apex-candidate-evidence-bank/SKILL.md).
It covers relevant items across the full vacancy, connecting requirements to
source evidence, demonstrated capability, and missing information. Skills record
personal contribution, independence, complexity, and repeated use; credentials
record exact identity and known status; languages record activity-specific
evidence. Supplied portal labels are preserved, while final label choices and
proficiency remain Option 9's responsibility.

[`apex-user-feedback-revision`](skills/apex-user-feedback-revision/SKILL.md)
surfaces these item-specific gaps in Phase 7.5. Supported entries can proceed
while unresolved items stay in review. Option 9 uses a current map when
available and still supports direct generation from the JD and job history
without requiring a strategy report.

After preparing `private/inputs/application_context.md`, select **Option 9**
from the Phase 8 menu or invoke the skill directly, for example:

```text
Use apex-generate-skills-proficiency to generate Phase 8 Option 9 from my
application context. Use the available portal labels if supplied.
```

If the skill is missing from the skill picker after updating the repository,
restart Codex to refresh skill discovery. Its canonical contract remains
available at the link above. Option 9 runs in single-agent mode; the v2
ensemble scope remains Options 1-4 and 7.

## UNESCO profile and Employment History Form

These named skills work directly from applicant evidence; a strategy report or
Phase 8 menu selection is optional:

| Skill | Result |
| --- | --- |
| [`apex-generate-unesco-employment-history`](skills/apex-generate-unesco-employment-history/SKILL.md) | An EHF content draft or native Word form, with the companion expertise map and role-to-narrative evidence links. |
| [`apex-select-domain-of-expertise`](skills/apex-select-domain-of-expertise/SKILL.md) | A broad role-evidence map, ranked experience and exact Area / Sub area / Years choices from the verified inventory, with companion EHF content. |
| [`apex-curate-publications`](skills/apex-curate-publications/SKILL.md) | A bibliography review or requested portal entries, with authorship, publication status and exact titles preserved. |

Invoking either the expertise or EHF skill for authoring activates both once.
They share an application-scoped evidence ledger and a crosswalk linking each
supported role/domain to exact EHF responsibilities or achievements. Role
relevance, practice duration and vacancy priority remain distinct. Review-only
requests and explicit output restrictions retain their scope; the pair does
not automatically rewrite CVs, cover letters or publications. See the
[shared contract](skills/apex-select-domain-of-expertise/references/paired-evidence-contract.md).

Example requests:

```text
$apex-generate-unesco-employment-history Prepare a Word EHF from my supplied employment history.
$apex-select-domain-of-expertise Recommend UNESCO expertise entries from my CV and this vacancy.
$apex-curate-publications Review my publication list and prepare supported UNESCO publication entries.
```

Install the canonical folders and their two shared dependencies as reversible
user-scope links, then check installation:

```bash
python3 agents/apex/scripts/install_unesco_skills.py
python3 agents/apex/scripts/install_unesco_skills.py --check
```

The installer uses `~/.agents/skills/`, leaves existing conflicting installations
untouched, and links to this checkout so skill updates are immediately available.
Keep the checkout at its installed location. `--scope project` instead installs
under the repository's ignored `.agents/skills/` directory. If the skill picker
has stale discovery state, restart Codex. The skills prepare local artifacts;
uploading or submitting an application remains a separate action.

## Contracts

For repository-level structure, see `docs/repo-structure.md`.
For runtime config shape, see `contracts/agents/runtime_config.schema.json`.

## UNOPS application fit

Use [apex-unops-application-fit](skills/apex-unops-application-fit/SKILL.md)
for **Option 10** or invoke it directly:

```text
$apex-unops-application-fit Prepare the UNOPS fit plan and role/skills companion from my vacancy and raw job history.
```

Use `TARGET_SYSTEM: OTHER` with `TARGET_ORGANIZATION: UNOPS`. Selected UNOPS
Phase 8 documents reuse its preparation plan; Option 10 alone does not request
CV/letter generation. Options 1–9 keep their meanings. The default skill budget
is 20, not a verified portal limit. Unknown Position Area options or limits stay
unresolved. Skill durations require dated evidence of use, not just job tenure.

Canonical skill: `agents/apex/skills/apex-unops-application-fit/`.
Expose it with a reversible `~/.agents/skills/apex-unops-application-fit` symlink
to that folder; do not overwrite a different existing skill. Restart Codex to
refresh discovery. This remains a single-agent option.

The supplied dated dictionary and manifest are installed in ignored
`private/inputs/taxonomies/unops/`. Imported examples are in
`private/inputs/unops/reference-seed-2026-09-27/`; they are not approved evidence.
These private resources are not distributed by a public repository clone.
See the skill's [local commands](skills/apex-unops-application-fit/references/local-commands.md)
for catalog lookup and structural validation. Installation does not run an
application generation task or change an online profile.

## UNOPS ATS input and revision

Use [apex-unops-ats-input](skills/apex-unops-ats-input/SKILL.md) to inspect the
current Careers Marketplace form or transfer selected approved content:

```text
$apex-unops-ats-input Use Codex Browser to revise my selected UNOPS draft with the approved duties, Position Areas, skills, qualification answers and final attachments. Verify saves and leave submission to me.
```

An inspection-only request does not authorize entry. Position Areas and skill
selection use the existing Option 10 evidence/fit plan; the ATS skill rechecks
live labels and identifies shared-profile scope. Narrative caps, upload limits,
role counts and skill controls are taken from the current form. Unsupported
browser uploads remain pending for a manual user handoff without desktop automation.

The canonical folder is `agents/apex/skills/apex-unops-ats-input/`. Expose it with
`~/.agents/skills/apex-unops-ats-input` pointing to that folder, preserving any
conflicting installation. Restart Codex if the new name is absent from the picker;
the canonical SKILL.md can also be invoked directly in the current task.
See the [ATS consistency review](../../docs/ats-input-skill-consistency.md) for
shared behavior and retained organization-specific differences.
