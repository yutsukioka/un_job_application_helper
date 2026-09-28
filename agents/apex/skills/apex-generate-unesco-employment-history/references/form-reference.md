# Verified standard EHF and portal distinction

## Source and scope

The bundled asset was downloaded on **13 September 2026** from the official
[UNESCO Employment History Form](https://www.unesco.org/sites/default/files/medias/fichiers/2025/02/UNESCO%20Employment%20History%20Form.docx)
linked in the candidate-profile header. Its SHA-256 is
`364ed4385a5542fde650a7e7bab5b7e03fae9792ad668e69f3d0b1e21194a30c`.
The URL's `2025/02` path is provenance, not proof of a document revision date.
[Template manifest](template-manifest.json) records the full package inventory.

This is a standard English EHF snapshot, not a promise that every UNESCO
vacancy uses it. Consultancy and office-specific vacancies can require other
forms and combinations of documents. The actual vacancy and applicable form
instructions control. For example, a UNESCO Lima consultancy linked a
different form and Spanish CV section; do not add that appendix to the standard
EHF without applicable instructions.

The standard form requires clear, complete answers, unchanged field labels,
every employment in reverse order starting with the present/recent post,
and separate job blocks with unused blocks removed. These are document
instructions, not inferred selection preferences. Neither the form nor its
Word schema provides a maximum number of jobs.

## Actual native slots

The asset has a name table plus five sample work-experience tables. Native
table coordinates below are zero-based within `word/document.xml` direct
body tables. Table 0 is names; tables 1–5 are repeatable jobs.

| Label | Native slot | Evidence / handling |
|---|---|---|
| Last Name | Names row 1, cell 0 | Legacy Word text field; maximum 20 |
| First Name | Names row 1, cell 1 | Legacy Word text field; maximum 20 |
| From (DD/MM/YYYY) | Job row 2, cell 0 | Legacy text field; no explicit numeric maximum |
| To (DD/MM/YYYY) | Job row 2, cell 1 | Same; do not invent days or an end date |
| Job title | Job row 2, cell 2 | Same |
| Name of employer | Job row 3, cell 0 | Same; preserve employer/client distinction |
| Location (City and Country) | Job row 3, cell 1 | Same |
| Current Annual Salary (approx.) | Job row 4, cell 0 | Same; preserve this exact label and salary basis |
| Number of direct reports | Job row 4, cell 1 | Same; direct reports are not project stakeholders |
| UN Grade/Level (if applicable) | Job row 5, cell 0 | Same; conditional on applicability |
| Main responsibilities: | Label row 6; answer row 7, cell 0 | Ordinary paragraphs; no numeric cap found |
| Main achievements: | Job row 8, cell 0 after label paragraph | Ordinary paragraphs; no numeric cap found |

The two names contain `w:ffData/w:textInput/w:maxLength` with value 20.
The other 40 legacy fields have `w:textInput` without an explicit maximum.
There are no content controls or document-protection setting. The form has no
per-field machine-readable required flags; complete-answer instructions and
the grade's applicability qualification are the relevant evidence. Absence
of a native maximum is not a universal portal upload or file-size rule.

Keep native fields, merged cells, grid widths, row geometry, Unicode and
paragraph/run styling. The header contains the UNESCO logo and title; the
footer contains a `PAGE` field. Two continuous A4 sections have different
vertical margins. The helper retains both and does not apply a generic design.

## Separate online Employment History section

The 496-entry user-supplied HAR reviewed on 13 September 2026 contains an
independent profile template. Source: zero-based entry 410,
`rcmV12CandidateProfileControllerProxy.getCandidateProfileVO`, full-HAR SHA-256
`64fc478e26e6130cf3101618fd7d11223c054356914f57db1621485d74673616`.
Only allowlisted configuration, not candidate values, informs this reference.

Its section is `outsideWorkExperience`, titled Employment History, with
`required=true`. The following mapping is contextual; this EHF skill does
not generate the online-profile output unless a separate applicable task
routes it appropriately.

| Online label | ID | Control | Required flag |
|---|---|---|---|
| Employer | employer | Text | true |
| Country | CountryExp | Picklist Country2 | true |
| City | CityExp | Text | true |
| Current Job | CurrentJob | Picklist YesNo | true |
| Start Date | startDate | Date | true |
| End Date | endDate | Date | false |
| Job Title | JobTitle | Text | true |
| Grade (for UN staff) | Grade | Picklist graderetirement | false |
| Supervisor's Name | SupervisorName | Text | true |
| Supervisor's Title | SupervisorTitle | Text | true |
| Supervisor's Phone | SupervisorPhone | Text | false |
| Supervisor's Email | SupervisorEmail | Text | false |
| May we contact the supervisor? | contactSupervisor | Picklist contactSupervisor | true |
| If not, please specify | IfNoSpecify | Text | false |
| Reason for leaving | reasonleaving | Text | false |

The online text inputs have `inputLength=4000`, passed to client `maxLength`
in captured static code (entry 354) and rendered as an HTML `maxlength`
attribute (entry 44, duplicated at 188). Do not transfer that maximum to the
native EHF narratives. Picklist `4000` and date `999` metadata are not narrative
budgets. `maxEntries=0` acts as no positive configured client row cap in the
captured code, not a guarantee of unlimited server records. Conditional
business validation, selection choices and Unicode counting semantics were
not exhaustively tested. There are no online responsibility, achievement,
salary or direct-report controls in that captured template.

## Document upload

The same captured profile header distinguishes the candidate profile from
the EHF and asks candidates to prepare/update both before applying. Additional
CV or motivation-letter documents are requested only when the vacancy or a
direct recruitment communication explicitly calls for them. Its backend
`resume` attachment is displayed as the EHF/My Documents area; that internal
name does not turn the required EHF into an ordinary CV.

The active captured upload instruction allows DOCX, PDF, Image and Text and
excludes MSG, PPT and XLS. It also warns that the document cannot be deleted
or replaced after submission. No numeric resolved upload size was observed.
Do not borrow the FAQ's selection-stage file-size limit for every EHF upload,
or assume a one-file dialog means a universal one-attachment profile limit.
Recheck the applicable instructions when preparing an actual upload package.
This skill performs no upload.

Broader official guidance:
[UNESCO candidate FAQ](https://careers.unesco.org/content/FAQs_candidates/?locale=en_GB)
and [application process](https://www.unesco.org/en/careers/application-process).
The native DOCX above is the authority for this bundled helper's form structure.
