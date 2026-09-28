# Local lookup and validation

Run these commands from the repository root using Python 3.10+ (standard library
only). Supply another catalog path when using a newer user-provided snapshot.
Read the exact returned descriptions before deciding a skill matches evidence.
Search terms are AND-matched across labels and descriptions; repeat with synonyms
rather than treating one query as exhaustive. The query is processed locally.

```bash
python3 agents/apex/skills/apex-unops-application-fit/scripts/search_catalog.py --catalog private/inputs/taxonomies/unops/UNOPS_Skills_2026-09-27.csv --query 'Monitoring, Evaluation, and Learning' --limit 5
python3 agents/apex/skills/apex-unops-application-fit/scripts/validate_unops_fit.py --catalog private/inputs/taxonomies/unops/UNOPS_Skills_2026-09-27.csv --selection "private/output/<vacancy-key>/<run-id>/unops_selection.json" --report "private/output/<vacancy-key>/<run-id>/structural_validation.json"
python3 -m unittest discover -s agents/apex/skills/apex-unops-application-fit/tests -v
```

Replace `<vacancy-key>` and `<run-id>` in the second command with actual paths.
The report path must be new; the validator will not overwrite a previous report.
A successful result proves structural checks only, not applicant facts, Position
Area validity, live portal constraints, or eligibility. Imported private examples
may be used to inspect the package format, but not as accepted application data.
