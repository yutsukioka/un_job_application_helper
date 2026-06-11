# Search Taxonomy Enrichment

The job aggregator keeps three classification layers separate:

- `occupational_*`: CCOG occupational/function taxonomy. This answers what kind of work the role performs, such as `1.A.01` financial management specialists.
- `mandate_*`: UN Careers Job Network and Job Family taxonomy. This answers the substantive mandate or area of expertise, such as `MAGNET / Finance`.
- `capability_tags`: granular skills and capabilities from the Skills Catalog seed taxonomy, source-native categories, and extracted text evidence.

Do not collapse these layers into one domain field. A UNICEF finance job can have an occupational finance classification, a Management and Administration / Finance mandate, and budgeting/accounting capability tags without being classified as child protection.

## CCOG

Runtime classification still preserves existing `ccog_*` fields. The enrichment layer derives:

- `occupational_family_code` and `occupational_family_label`
- `occupational_medium_code` and `occupational_medium_label`
- `occupational_small_code` and `occupational_small_label`
- `occupational_confidence`
- `occupational_evidence`

Small CCOG codes are stored for audit/debugging but the default search UI should expose only family and medium codes.

The full CCOG Markdown artifact under `agents/apex/skills/apex-ccog-resolver/resource/ccog_reference_full.md` was repaired for OCR/code-formatting errors only. The runtime merges that full reference with the curated `jobagg/classification/rules/ccog_reference.yaml` subset.

## Mandate Area

Canonical mandate fields use UN Careers Job Network and Job Family:

- `mandate_network_code`
- `mandate_network_label`
- `mandate_family_code`
- `mandate_family_label`
- `mandate_source`
- `mandate_confidence`
- `mandate_evidence`

For Inspira/UN Careers records, native Job Network and Job Family are authoritative. For other sources, rules in `config/taxonomies/mandate_crosswalk.yaml` infer only clear matches from title, department, native category, and description.

## Capability Tags

Capability tags live in `config/taxonomies/capability_tags.yaml`. Tags are multi-label and evidence-backed:

- `capability_tags`
- `capability_tag_scores`
- `capability_tag_evidence`

The initial file is seeded from the attached Skills Catalog request. Expand it by adding a new tag slug and optional keywords.

## Contract And Seniority

The older `contract_category` and grade fields remain. The search layer adds:

- `contract_group`: `staff`, `consultant_contractor`, `volunteer`, `internship`, `fellowship_ypp_pathway`, `roster_pipeline`, `other`, or `unknown`
- `seniority_group`: `internship_trainee`, `volunteer`, `entry_junior`, `mid`, `senior`, `director_executive`, `ungraded_nonstaff_or_pathway`, or `unknown`

Rules are in `config/taxonomies/contract_groups.yaml` and `config/taxonomies/seniority_groups.yaml`.

## Search Examples

```bash
jobagg search --mandate-network-code MAGNET --mandate-family-code MAGNET.finance
jobagg search --occupational-family-code 1.A --occupational-medium-code 1.A.01
jobagg search --capability-tag budgeting --contract-group staff --seniority-group mid
```

Use `--explain` or `search-debug` to inspect why a job matched or missed a filter.

## Backfill

Existing jobs receive these fields by re-running classification:

```bash
jobagg classify --all --reclassify-all
```

The exported job files include the new fields through the existing export pipeline.
