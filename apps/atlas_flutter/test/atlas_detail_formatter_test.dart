import 'package:atlas/atlas.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('AtlasATSDetailFormatter', () {
    test('drops chrome-dominant summary', () {
      final formatted = AtlasATSDetailFormatter.format(
        sections: const [
          AtlasDetailSection(
            title: 'Summary',
            body:
                'Vacancies | UNICEF Careers Skip to main content Global Links Toggle navigation Candidate login Main navigation Current vacancies Explore our current job opportunities Filter results Search using keywords',
          ),
        ],
      );

      expect(formatted.sections, isEmpty);
      expect(formatted.hiddenBoilerplate, isTrue);
    });

    test('trims trailing chrome from long section', () {
      final useful = List.filled(
        10,
        'Real responsibilities content here.',
      ).join(' ');
      final formatted = AtlasATSDetailFormatter.format(
        sections: [
          AtlasDetailSection(
            title: 'Responsibilities',
            body:
                '$useful Back to search results Apply now Whatsapp Facebook LinkedIn Send me jobs like these',
          ),
        ],
      );

      expect(formatted.sections, hasLength(1));
      expect(formatted.hiddenBoilerplate, isTrue);
      final text = _paragraphText(formatted.sections.single);
      expect(text, contains('Real responsibilities content'));
      expect(text, isNot(contains('Back to search results')));
    });

    test('extracts facts from key-value run and keeps prose tail', () {
      final formatted = AtlasATSDetailFormatter.format(
        sections: const [
          AtlasDetailSection(
            title: 'Contract type',
            body:
                'Temporary Appointment Duty Station: San Jose Level: G-5 Location: Costa Rica Categories: Administration UNICEF trabaja en mas de 190 paises y territorios para salvar la vida de ninos y ninas, defender sus derechos y ayudarles a desarrollar su maximo potencial desde la primera infancia.',
          ),
        ],
      );

      final facts = _factBlocks(formatted.sections.single).expand((x) => x);
      expect(
        facts,
        contains(
          const AtlasDetailFact('Contract type', 'Temporary Appointment'),
        ),
      );
      expect(
        facts,
        contains(const AtlasDetailFact('Duty Station', 'San Jose')),
      );
      expect(facts, contains(const AtlasDetailFact('Level', 'G-5')));
      expect(facts, contains(const AtlasDetailFact('Location', 'Costa Rica')));
      expect(_paragraphText(formatted.sections.single), contains('UNICEF'));
    });

    test('heals orphan fragments into previous canonical section', () {
      final formatted = AtlasATSDetailFormatter.format(
        sections: const [
          AtlasDetailSection(
            title: 'Competencies',
            body:
                'Professionalism: Shows pride in work and achievements. Candidates will be compared with the profiles',
          ),
          AtlasDetailSection(title: 'Assessment', body: 'of other candidates.'),
        ],
      );

      expect(formatted.sections, hasLength(1));
      expect(
        formatted.sections.single.kind,
        AtlasDetailSectionKind.competencies,
      );
      expect(
        _paragraphText(formatted.sections.single),
        contains('Assessment of other candidates.'),
      );
    });

    test('splits bullet runs and canonicalizes duplicate titles', () {
      final formatted = AtlasATSDetailFormatter.format(
        sections: const [
          AtlasDetailSection(
            title: 'Responsibilities',
            body:
                'Achieving results such as:• Provide effective direction to staff.• Plan and report on the budget.',
          ),
          AtlasDetailSection(
            title: 'Description of duties',
            body: 'Directs programme execution across regions.',
          ),
        ],
      );

      expect(formatted.sections, hasLength(1));
      expect(
        formatted.sections.single.kind,
        AtlasDetailSectionKind.responsibilities,
      );
      final bullets = _bulletBlocks(formatted.sections.single).expand((x) => x);
      expect(bullets, contains(startsWith('Provide effective direction')));
      expect(
        _paragraphText(formatted.sections.single),
        contains('Directs programme'),
      );
    });

    test('orders canonical sections and falls back to description', () {
      final formatted = AtlasATSDetailFormatter.format(
        sections: const [
          AtlasDetailSection(
            title: 'Education',
            body: 'An advanced university degree is required.',
          ),
          AtlasDetailSection(
            title: 'Org. Setting and Reporting',
            body: 'The Bureau carries out activities under strategic goals.',
          ),
          AtlasDetailSection(
            title: 'Responsibilities',
            body: 'Leads and manages the work of the Bureau directly.',
          ),
        ],
      );

      expect(formatted.sections.map((section) => section.kind), [
        AtlasDetailSectionKind.about,
        AtlasDetailSectionKind.responsibilities,
        AtlasDetailSectionKind.qualifications,
      ]);

      final fallback = AtlasATSDetailFormatter.format(
        sections: const [],
        fallbackDescription: 'Support cash-based assistance data systems.',
      );
      expect(fallback.sections.single.kind, AtlasDetailSectionKind.about);
      expect(fallback.hiddenBoilerplate, isFalse);
    });

    test('splits long walls into readable paragraphs', () {
      final wall = List.filled(
        30,
        'This sentence pads the organizational context for readability.',
      ).join(' ');
      final formatted = AtlasATSDetailFormatter.format(
        sections: [AtlasDetailSection(title: 'Background', body: wall)],
      );

      final paragraphCount = formatted.sections.single.blocks
          .whereType<AtlasDetailParagraphBlock>()
          .length;
      expect(paragraphCount, greaterThan(1));
    });

    test('preserves other sections and splits numbered lists', () {
      final formatted = AtlasATSDetailFormatter.format(
        sections: const [
          AtlasDetailSection(
            title: 'Selection process',
            body:
                'Selection steps 1. Review applications carefully. 2. Interview shortlisted candidates. 3. Complete reference checks.',
          ),
        ],
      );

      expect(formatted.sections.single.kind, AtlasDetailSectionKind.other);
      expect(formatted.sections.single.id, 'other-0');
      final bullets = _bulletBlocks(formatted.sections.single).expand((x) => x);
      expect(bullets, contains(startsWith('1. Review applications')));
    });

    test('keeps leading prose before extracted facts', () {
      final formatted = AtlasATSDetailFormatter.format(
        sections: const [
          AtlasDetailSection(
            title: 'Summary',
            body:
                'This role supports the regional office. Contract type: Staff Duty Station: Rome Level: P-3',
          ),
        ],
      );

      final facts = _factBlocks(formatted.sections.single).expand((x) => x);
      expect(facts, contains(const AtlasDetailFact('Contract type', 'Staff')));
      expect(
        _paragraphText(formatted.sections.single),
        contains('This role supports the regional office.'),
      );
    });

    test('maps compensation languages and application sections', () {
      final formatted = AtlasATSDetailFormatter.format(
        sections: const [
          AtlasDetailSection(title: 'Salary', body: 'Competitive package.'),
          AtlasDetailSection(title: 'Languages', body: 'Fluency in English.'),
          AtlasDetailSection(title: 'How to apply', body: 'Apply online.'),
        ],
      );

      expect(formatted.sections.map((section) => section.kind), [
        AtlasDetailSectionKind.languages,
        AtlasDetailSectionKind.compensation,
        AtlasDetailSectionKind.application,
      ]);
    });
  });
}

String _paragraphText(AtlasFormattedDetailSection section) {
  return section.blocks
      .whereType<AtlasDetailParagraphBlock>()
      .map((block) => block.text)
      .join(' ');
}

Iterable<List<String>> _bulletBlocks(AtlasFormattedDetailSection section) {
  return section.blocks.whereType<AtlasDetailBulletsBlock>().map(
    (block) => block.items,
  );
}

Iterable<List<AtlasDetailFact>> _factBlocks(
  AtlasFormattedDetailSection section,
) {
  return section.blocks.whereType<AtlasDetailFactsBlock>().map(
    (block) => block.facts,
  );
}
