Use `apex-build-context-pack` to prepare a new application process;

1. Update ## JOB_DESCRIPTION_TEXT and ## JOB_QUALIFICATION_QUESTIONS section of `private/inputs/application_context.md` with the attached job description. The attached job description contains each section name (e.g., ## XXXXX) to match `private/inputs/application_context.md`. If you can not match section name, please ask user to clarify it. Only update sections in the attached document. Update also ## JOB_REQUIREMENT_TEXT section of `private/inputs/application_context.md` based on "Education" and "Work Experience" in the ## JOB_DESCRIPTION_TEXT section of the attached document.
2. Clear the content of ## TERM_EXTRACTOR and ## CCOG_CLASSIFICATION section in `private/inputs/application_context.md`
3. Update ## LIMITS section of `private/inputs/application_context.md` as below;
TARGET_SYSTEM: UNICEF
CHAR_LIMIT: 4000
TARGET_LOW: 3900
TARGET_HIGH: 4000
WORD_TARGET: 800

# Example per ATS system

TARGET_SYSTEM: UNICEF
CHAR_LIMIT: 4000
TARGET_LOW: 3900
TARGET_HIGH: 4000
WORD_TARGET: 800

TARGET_SYSTEM: INSPIRA
CHAR_LIMIT: 1000
TARGET_LOW: 980
TARGET_HIGH: 1000
WORD_TARGET: 200

TARGET_SYSTEM: IOM
CHAR_LIMIT:
TARGET_LOW:
TARGET_HIGH:
WORD_TARGET:
