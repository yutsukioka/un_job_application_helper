# Copilot Instructions — un_job_application_helper

## Repository vs Workspace layout (CRITICAL)

This workspace has an unusual structure that you MUST understand before performing git operations:

```
un_job_application_helper/          ← VS Code workspace root (NOT a git repo)
├── .agents/                        ← ACTUAL GIT REPO ROOT
│   ├── .git/
│   ├── .github/copilot-instructions.md  (this file)
│   ├── .gitignore
│   ├── skills/                     ← skill definitions (SKILL.md + agents/openai.yaml)
│   └── ...
├── AGENTS.md                       ← workspace-level doc (outside git repo)
├── inputs/                         ← personal data — NEVER commit
├── output/                         ← generated docs — NEVER commit
├── tmp/                            ← scratch files — NEVER commit
└── bak/                            ← backup copies — NEVER commit
```

### Key rules

1. **Git root is `.agents/`, not the workspace root.**
   - All `git` commands must be run from `.agents/` (or use `-C .agents/`).
   - The remote is `https://github.com/yutsukioka/un_job_application_helper.git`.
   - Branches: `master` (default), `develop` (active development).

2. **The workspace parent directory is NOT a git repo.**
   - It has a `.gitignore` for local safety, but no `.git` tracking.
   - Do NOT run `git init`, `git add`, or `git commit` from the workspace root.
   - Do NOT try to add `.agents` as a submodule of an outer repo.

3. **Privacy: directories outside `.agents/` contain personal data.**
   - `inputs/`, `output/`, `tmp/`, `bak/` are all excluded in `.agents/.gitignore`.
   - Before any commit, verify no PII (names, emails, phone numbers, addresses) is staged.
   - SKILL.md files should use only generic placeholders, never real personal data.

4. **Branching convention:**
   - `master` — stable, reviewed.
   - `develop` — active work; push new skill additions and edits here first.
   - Feature branches from `develop` for large changes.

## Workflow for syncing local files to repo

```bash
cd <workspace>/.agents
git checkout develop
git add -A
git status                    # review staged files
git commit -m "descriptive message"
git push origin develop
```

## What NOT to do

- Do NOT create a git repo at the workspace root.
- Do NOT stage or commit files from `inputs/`, `output/`, `tmp/`, or `bak/`.
- Do NOT use `git push --force` without explicit user confirmation.
- Do NOT commit the CCOG full database (`skills/apex-ccog-resolver/resource/ccog_reference_full.md`).
