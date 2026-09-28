"""Render the anonymous UNV assignment fields from the public UI contract.

The contract is grounded in captured UNV modules98648/63493/69920/32245/44530
on2026-09-13. Renderer context contains public translations and display config;
onsite duty stations and eligibility are separate public endpoint responses.
No ERP, private applicant, internal scoring or financial fields are rendered.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import math
import re
from typing import Any


UI_COMPONENT_URL = 'https://app.unv.org/static/js/114.02d2a94b.chunk.js'
UI_COMPONENT_SHA256 = 'f163da032e0178c0027cd86712f17988e8f46e807bc02ae6b15ebf53bb341c9f'
UI_DATE_ALGORITHM = 'moment.utc(sourcingEndDate).add(1,"day").tz().format("L LT")'


def label(value: Any) -> str | None:
    return value.get('label') if isinstance(value, dict) else str(value) if value is not None else None


def code(value: Any) -> str | None:
    return (value.get('value') or {}).get('code') if isinstance(value, dict) else None


def legacy_description(item: dict[str, Any]) -> str | None:
    return '\n\n'.join(str(item[k]) for k in (
        'organizationMission', 'context', 'taskDescription', 'requiredSkillExperience',
        'competency', 'additionalEligibilityCriteria', 'accessibilityComment', 'livingConditions'
    ) if item.get(k)) or None


def public_deadline(value: Any) -> tuple[datetime | None, dict[str, Any]]:
    proof = {'original_sourcing_end_date': value, 'algorithm': UI_DATE_ALGORITHM,
             'public_code_url': UI_COMPONENT_URL, 'public_code_sha256': UI_COMPONENT_SHA256,
             'module': '63493.QA',
             'viewer_timezone_is_presentation_only': True}
    try:
        if not isinstance(value, str) or not re.fullmatch(
                r'\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?', value):
            raise ValueError('Missing or unsupported ISO source date')
        instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if instant.year < 1900:
            raise ValueError('Source sentinel date')
        # The source calls moment.utc explicitly: a naive API clock is UTC.
        instant = instant.replace(tzinfo=UTC) if instant.tzinfo is None else instant.astimezone(UTC)
        instant += timedelta(days=1)
        return instant, {**proof, 'kind': 'known_instant', 'utc': instant.isoformat()}
    except (TypeError, ValueError, OverflowError) as exc:
        return None, {**proof, 'kind': 'unknown', 'reason': str(exc)}


def explicit_deadline_conflicts(item: dict[str, Any], instant: datetime | None) -> list[dict[str, str]]:
    """Retain conflicting public prose; never rewrite its application deadline."""
    if instant is None:
        return []
    result = []
    for field in ('additionalEligibilityCriteria', 'taskDescription', 'context'):
        value = item.get(field)
        if not isinstance(value, str):
            continue
        for match in re.finditer(r'\bApplication deadline\s*:\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})', value, re.I):
            try:
                calendar = datetime.strptime(match[1], '%d %B %Y').date()
            except ValueError:
                continue
            if calendar != instant.date():
                result.append({'source_field': field, 'verbatim_public_phrase': match[0],
                               'prose_calendar_date': calendar.isoformat(),
                               'advertisement_end_utc_date': instant.date().isoformat(),
                               'status': 'different_public_application_dates_require_review; both preserved'})
    return result


def select_translations(payload: dict[str, Any]) -> dict[str, str]:
    """Keep only text consumed by anonymous public assignment components."""
    result: dict[str, str] = {}
    prefixes = ('doa_detail.labels.', 'doa_detail.headers.', 'doa_detail.helpers.',
                'doa.funding_labels.', 'helpers.', 'opportunities.helpers.', 'candidates.helpers.')
    exact = {'volunteer_profile.languages.level', 'eligibility.noExperience',
             *(f'doa_detail.{k}' for k in ('volunteerism_description', 'inclusivity_statment',
                                          'vaccination_notice', 'Unicef_first_para',
                                          'Unicef_second_para', 'un_fee_notice'))}

    def walk(value: Any, path: str = '') -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, f'{path}.{key}' if path else str(key))
        elif isinstance(value, str) and (path in exact or path.startswith(prefixes)):
            result[path] = value

    walk(payload)
    return result


def display_config(payload: dict[str, Any]) -> dict[str, Any]:
    import json

    options = [r for r in payload.get('value', {}).get('configurations', {}).get('doa', [])
               if r.get('concept') == 'doa_details']
    if len(options) != 1:
        raise ValueError('UNV guest assignment display configuration is missing or ambiguous')
    config = json.loads(options[0]['value'])['displayConfig']
    if any(not isinstance(config.get(k), list) or any(type(v) is not int for v in config[k])
           for k in ('sections', 'fields', 'headers')):
        raise ValueError('Unsupported UNV anonymous display configuration schema')
    return config


def _number(value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Public numeric field is missing or invalid')
    return value


def _numeric_text(value: int | float) -> str:
    return str(int(value)) if int(value) == value else str(value)


def _date_label(value: Any) -> str:
    if not value:
        return '-'
    instant = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if instant.tzinfo is not None:
        instant = instant.astimezone(UTC)
    return instant.strftime('%d/%m/%Y')


def render_public(item: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """Return readable public text and an explicit completeness/scope result."""
    context = item.get('_unv_public_render_context')
    errors: list[str] = []
    emitted: list[dict[str, Any]] = []
    if not isinstance(context, dict):
        return legacy_description(item), {'complete': False, 'missing': ['public_render_context'], 'fields': [],
            'normalized_description_sha256': hashlib.sha256(' '.join(str(legacy_description(item) or '').split()).encode()).hexdigest()}
    try:
        translations = context['translations']
        config = context['display_config']
        if (not isinstance(translations, dict) or not isinstance(config, dict)
                or any(not isinstance(config.get(k), list) for k in ('sections', 'fields', 'headers'))):
            raise ValueError('Invalid public translation/display context')
        onsite = item.get('isOnsite')
        if type(onsite) is not bool:
            raise ValueError('Assignment onsite/online modality is not explicit')
        category_enabled = context.get('category_configuration_enabled')
        if type(category_enabled) is not bool:
            raise ValueError('Public category section configuration has not been observed')
        category_sections = context.get('category_sections')
        if category_enabled and (not isinstance(category_sections, list)
                                 or any(type(x) is not int for x in category_sections)):
            raise ValueError('Enabled public category section filter is missing')

        def text(key: str) -> str:
            value = translations.get(key)
            if not isinstance(value, str):
                raise ValueError('Missing public translation: ' + key)
            return value

        def title(key: str) -> str:
            return text('doa_detail.labels.' + key)

        def enabled(section: int) -> bool:
            return section in config['sections'] and (not category_enabled or section in category_sections)

        def emit(section: int, key: int | None, name: str | None, value: Any, path: str) -> None:
            # Public module69920 filters fields only in its splitInColumns
            # branch (General, Eligibility, Requirements). Details does not.
            if not enabled(section) or (section in {1000, 3000, 4000} and key and key not in config['fields']):
                return
            if value is None or value == '':
                value = '-'
            emitted.append({'section': section, 'field_key': key, 'label': name,
                            'value': str(value), 'public_input': path})

        # Header fields are outside the category section filters. The public
        # date is viewer-local presentation of a known UTC instant; publish UTC
        # explicitly so text does not depend on the crawler's machine timezone.
        def header(name: str | None, value: Any, path: str) -> None:
            emitted.append({'section': 0, 'field_key': None, 'label': name,
                            'value': str(value), 'public_input': path})

        if not onsite:
            # This public banner defines the assignment's contractual and
            # remuneration status, independently of category field filters.
            header(None, text('doa_detail.headers.online_warning'),
                   'isOnsite=false; doa_detail.headers.online_warning')

        if item.get('numberOfAssigments'):
            header(title('vacants'), item['numberOfAssigments'], 'numberOfAssigments; public header')
        if 1 in config['headers'] and label(item.get('status')) and code(item.get('status')):
            header(title('status'), label(item['status']), 'status; public header displayConfig.headers')
        funding = 'fully_funded' if item.get('isFullyFunding') else 'single_source' if item.get('isSingleSource') else 'regular_sourcing'
        header(None, text('doa.funding_labels.' + funding), 'isFullyFunding/isSingleSource; public header')
        if code(item.get('status')) == 'DOA_SOURCING':
            deadline, _ = public_deadline(item.get('sourcingEndDate'))
            if deadline is None:
                raise ValueError('Public advertisement end date is invalid or missing')
            header(text('doa.funding_labels.date_of_end_sourcing'), deadline.strftime('%d/%m/%Y %H:%M UTC'),
                   'sourcingEndDate; source UI UTC plus one day, explicit UTC presentation')
            if not item.get('isSourcingActive'):
                header(None, text('doa.funding_labels.badge_stop_sourcing'), 'isSourcingActive; public header')

        category = item.get('volunteersCategoryDetails') or {}
        host = item.get('hostEntity') or {}
        for key, name, value, path in [
            (1001, 'full_title', item.get('name'), 'name'),
            (1002, 'host_entity', host.get('name'), 'hostEntity.name'),
            (1003, 'assignment_country', label(item.get('country')), 'country.label'),
            (1004, 'modality', text('opportunities.helpers.onsite' if onsite else 'opportunities.helpers.online'), 'isOnsite'),
        ]:
            emit(1000, key, title(name), value, path)
        if onsite:
            duties = item.get('_unv_duty_station_response')
            if not isinstance(duties, list) or any(not isinstance(d, dict) for d in duties):
                raise ValueError('Onsite public duty-station response is missing or invalid')
            names = []
            for duty in duties:
                if type(duty.get('isCustomDutyStation')) is not bool:
                    raise ValueError('Invalid duty-station custom-name flag')
                name = duty.get('customDutyStation') if duty['isCustomDutyStation'] else label(duty.get('dutyStation'))
                if not isinstance(name, str) or not name:
                    raise ValueError('Missing public duty-station label')
                names.append(name)
            names_with_counts = [f'{n} ({names.count(n)})' if names.count(n) > 1 else n for n in dict.fromkeys(names)]
            emit(1000, 1011, title('duty_stations'), ', '.join(names_with_counts) or '-', '_unv_duty_station_response')
            for key, name, source_key in [(1030, 'type', 'categoryType'), (1050, 'work_location', 'workLocation'),
                                           (1010, 'volunteer_category', 'categoryName')]:
                emit(1000, key, title(name), label(category.get(source_key)), 'volunteersCategoryDetails.' + source_key)
        emit(1000, 1005, title('start_date'), _date_label(item.get('startDate')), 'startDate; UI UTC calendar date')
        if onsite:
            emit(1000, 1040, title('work_arrangement'), label(category.get('workArrangement')), 'volunteersCategoryDetails.workArrangement')
        duration = item.get('duration')
        duration_parts = []
        if duration and item.get('isDurationFilled'):
            number = _number(duration)
            if number < 0:
                raise ValueError('Negative assignment duration')
            # JS Math.round for positive days; source uses30.44 days/month or7/week.
            duration_parts.append(f'{math.floor(number / (30.44 if onsite else 7) + .5)} {text("helpers.months" if onsite else "helpers.weeks")}')
            if onsite and item.get('possibilityOfExtension'):
                duration_parts.append(title('possibility_of_extension'))
            duration_name = 'duration'
        else:
            duration_parts.append(_date_label(item.get('expectedEndDate')))
            duration_name = 'end_date'
        if onsite:
            short = str(code(category.get('assignmentDuration')) or '').lower() in {'shortterm', 'short-term', 'short_term'}
            duration_parts.append(title('short_term_benefits' if short else 'long_term_benefits'))
        emit(1000, 1006, title(duration_name), '\n'.join(duration_parts), 'duration/isDurationFilled/expectedEndDate/assignmentDuration')
        emit(1000, 1009, title('number_of_assignments'), item.get('numberOfAssigments'), 'numberOfAssigments')
        emit(1000, 1007, title('sdg_type'), label(item.get('sdgType')), 'sdgType.label')
        if onsite:
            for key, name, raw_key in [(1013, 'people_with_disabilities', 'peopleWithDisabilities'),
                                       (1014, 'reasonable_accommodation', 'reasonableAccommodation')]:
                value = item.get(raw_key)
                emit(1000, key, title(name), title(name + ('_yes' if value else '_no')) if type(value) is bool else None, raw_key)
            accessibility = item.get('accessibilityList')
            if not isinstance(accessibility, list):
                raise ValueError('Invalid public accessibility list')
            details = [title('disabilityLabels.' + k) + '\n' + title('disabilityDescriptions.' + k) for k in accessibility]
            emit(1000, 1015, title('accessibility_checklist'), '\n'.join(details), 'accessibilityList; exact public label/description mappings')
            reference_code = code(item.get('refereesType'))
            reference_label = 'no' if not item.get('isReferenceChecksRequired') else 'yes' if reference_code == 'PROACAD' else 'pro' if reference_code == 'PRO' else 'acad'
            emit(1000, 1020, title('reference_checks'), title('referees_type.' + reference_label), 'isReferenceChecksRequired/refereesType')
            emit(1000, 1016, title('accessibility_comment'), item.get('accessibilityComment'), 'accessibilityComment')
        else:
            emit(1000, 1017, title('hours_week'), label(item.get('hoursWeek')), 'hoursWeek.label')

        for key, name, raw_key in [(2001, 'mission_and_objectives', 'organizationMission'),
                                   (2002, 'context', 'context')]:
            emit(2000, key, title(name), item.get(raw_key), raw_key)
        if not onsite:
            emit(2000, 2004, title('task_type'), label(item.get('taskType')), 'taskType.label')
        emit(2000, 2003, title('task_description'), item.get('taskDescription'), 'taskDescription')

        if onsite:
            criteria = item.get('_unv_eligibility_criteria')
            if not isinstance(criteria, list) or any(not isinstance(x, dict) for x in criteria):
                raise ValueError('Onsite public category eligibility is missing or invalid')
            def criterion(token: str) -> dict[str, Any]:
                values = [x for x in criteria if token.casefold() in str(code(x) or '').casefold()]
                if len(values) != 1:
                    raise ValueError('Missing or ambiguous public eligibility ' + token)
                return values[0]
            age = criterion('AgeRequired').get('props') or {}
            low, high = _number(age.get('minValue') or 0), _number(age.get('maxValue') or 100)
            age_text = (text('eligibility.noExperience') if low == 0 else f'{_numeric_text(low)}+') if high >= 100 else f'{_numeric_text(low)} - {_numeric_text(high)}'
            emit(3000, 3001, title('eligibility_criteria_age'), age_text, '_unv_eligibility_criteria.AgeRequired.props')
            emit(3000, 3002, title('eligibility_criteria_nationality'), criterion('Nationality').get('label'), '_unv_eligibility_criteria.Nationality.label')
            experience = criterion('ExperienceRequired').get('props') or {}
            low, high = _number(experience.get('minValue') or 0), _number(experience.get('maxValue') or 15)
            if low == 0:
                experience_text = text('doa_detail.helpers.eligibility_criteria_required_experience_community')
            else:
                years_low, years_high = low / 12, high / 12
                unit = text('doa_detail.helpers.month' if low == 1 else 'candidates.helpers.months') if years_low % 1 else text('candidates.helpers.years')
                first = _numeric_text(low if years_low % 1 else years_low)
                experience_text = f'{first} {unit}' if years_high == 100 or years_low == years_high else f'{first} - {_numeric_text(high if years_high % 1 else years_high)} {unit}'
            emit(3000, 4001, title('eligibility_criteria_required_experience'), experience_text, '_unv_eligibility_criteria.ExperienceRequired.props; public month/year conversion')
            if item.get('isFullyFunding'):
                emit(3000, 3003, title('additional_eligibility_criteria'), item.get('additionalEligibilityCriteria'), 'additionalEligibilityCriteria')
                if isinstance(item.get('batch'), dict):
                    emit(3000, 3004, title('donor_priorities'), item['batch'].get('donorPriorities'), 'batch.donorPriorities only')

        relevant = item.get('requiredExperience')
        if onsite:
            if relevant == 0:
                relevant_text = text('doa_detail.helpers.no_experience')
            elif _number(relevant) > 0 and item.get('unitOfExperience') in (1, 2):
                unit = ('year' if relevant == 1 else 'years') if item['unitOfExperience'] == 1 else ('month' if relevant == 1 else 'months')
                relevant_text = f'{_numeric_text(relevant)} {text("doa_detail.helpers." + unit)}'
            else:
                relevant_text = '-'
            emit(4000, 4001, title('relevant_experience'), relevant_text, 'requiredExperience/unitOfExperience')
            emit(4000, 4007, title('RequiredSkillExperience'), item.get('requiredSkillExperience'), 'requiredSkillExperience')
        else:
            emit(4000, 4001, title('required_experience'), item.get('requiredSkillExperience'), 'requiredSkillExperience')
        languages = item.get('languages')
        if not isinstance(languages, list) or any(not isinstance(x, dict) or type(x.get('isRequired')) is not bool for x in languages):
            raise ValueError('Public language/level/required list is missing or invalid')
        language_text = [f'{label(x.get("language"))}, {text("volunteer_profile.languages.level")}: {label(x.get("level"))}, {text("doa_detail.helpers.required" if x["isRequired"] else "doa_detail.helpers.desirable")}' for x in languages]
        if any(not label(x.get('language')) or not label(x.get('level')) for x in languages):
            raise ValueError('Missing public language or proficiency label')
        emit(4000, 4002, title('languages'), '\n'.join(language_text), 'languages[].language/level/isRequired')
        if onsite:
            expertise = item.get('expertiseAreas')
            if not isinstance(expertise, list):
                raise ValueError('Invalid public expertise list')
            emit(4000, 4003, title('expertise_area'), ', '.join(label(x) or '' for x in expertise), 'expertiseAreas[].label')
            education = label(item.get('requiredEducation'))
            specialization = item.get('specializationArea')
            if code(item.get('requiredEducation')) != 'SEC_EDU' and specialization:
                education = f'{education} {text("doa_detail.helpers.education_connector")} {specialization}'
            emit(4000, 4004, title('required_education'), education, 'requiredEducation/specializationArea; SEC_EDU excludes specialization')
            driving = item.get('drivingLicense')
            if driving:
                driving = f'{driving} {text("doa_detail.helpers.required" if item.get("requiredDrivingLicense") else "doa_detail.helpers.desirable")}'
            emit(4000, 4005, title('driving_licence'), driving, 'drivingLicense/requiredDrivingLicense')
            emit(4000, 4006, title('competencies_and_values'), item.get('competency'), 'competency')
        emit(5000, None, None, text('doa_detail.volunteerism_description'), 'public translation: volunteerism_description')
        if onsite:
            emit(5000, None, title('living_conditions'), item.get('livingConditions'), 'livingConditions')
        emit(5000, None, title('inclusivity_statment'), text('doa_detail.inclusivity_statment'), 'public translation: inclusivity_statment')
        host_code = code(host.get('institution'))
        if onsite and host_code not in {'HCR', 'CF'}:
            emit(5000, None, title('vaccination_notice'), text('doa_detail.vaccination_notice'), 'public translation; onsite and host not HCR/CF')
        if host_code == 'CF':
            emit(5000, None, title('reasonable_accommodation'), text('doa_detail.Unicef_first_para'), 'public translation; host CF')
            emit(5000, None, title('vaccination_notice'), text('doa_detail.Unicef_second_para'), 'public translation; host CF')
        emit(5000, None, title('scam_warning'), text('doa_detail.un_fee_notice'), 'public translation: un_fee_notice')
        body = []
        previous_section = None
        headings = {1000: 'general', 2000: 'details', 3000: 'elegibility_criteria', 4000: 'requirements', 5000: 'other'}
        for field in emitted:
            if field['section'] != previous_section:
                if field['section'] != 0:
                    body.append(text('doa_detail.headers.' + headings[field['section']]))
                previous_section = field['section']
            body.append((field['label'] + '\n' if field['label'] else '') + field['value'])
        result = '\n\n'.join(body)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        errors.append(str(exc))
        result = legacy_description(item)
    return result, {'complete': not errors, 'missing': errors, 'fields': emitted,
                    'contract_url': UI_COMPONENT_URL, 'contract_sha256': UI_COMPONENT_SHA256,
                    'contract_modules': ['98648', '63493', '69920', '32245', '44530'],
                    'scope': 'Anonymous public assignment header, sections and conditional fields; advertisement end rendered in explicit UTC equivalent to viewer-local public instant',
                    'normalized_description_sha256': hashlib.sha256(' '.join(str(result or '').split()).encode()).hexdigest()}
