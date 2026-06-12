import XCTest
@testable import AtlasUI

/// Validates the display normalizer against real ATS pathologies documented in
/// packages/jobagg/docs/ats_detail_display_real_examples/.
final class ATSDetailFormatterTests: XCTestCase {

    // MARK: PageUp pathologies

    func testDropsChromeDominantSummary() {
        let formatted = ATSDetailFormatter.format(sections: [
            AtlasDetailSection(
                title: "Summary",
                body: "Vacancies | UNICEF Careers Skip to main content Global Links Toggle navigation Candidate login Main navigation Current vacancies Explore our current job opportunities Filter results Search using keywords"
            ),
        ])
        XCTAssertTrue(formatted.sections.isEmpty)
        XCTAssertTrue(formatted.hiddenBoilerplate)
    }

    func testTrimsTrailingChromeFromLongSection() {
        let useful = String(repeating: "Real responsibilities content here. ", count: 10)
        let formatted = ATSDetailFormatter.format(sections: [
            AtlasDetailSection(
                title: "Responsibilities",
                body: useful + "Back to search results Apply now Whatsapp Facebook LinkedIn Send me jobs like these"
            ),
        ])
        XCTAssertEqual(formatted.sections.count, 1)
        XCTAssertTrue(formatted.hiddenBoilerplate)
        let text = paragraphText(formatted.sections[0])
        XCTAssertFalse(text.contains("Back to search results"))
        XCTAssertTrue(text.contains("Real responsibilities content"))
    }

    func testHealsOrphanFragmentByRejoiningTitle() {
        let formatted = ATSDetailFormatter.format(sections: [
            AtlasDetailSection(
                title: "Competencies",
                body: "Professionalism: Shows pride in work and achievements. Candidates will be compared with the profiles"
            ),
            AtlasDetailSection(title: "Assessment", body: "of other candidates."),
        ])
        // Orphan "Assessment of other candidates." is merged into Competencies.
        XCTAssertEqual(formatted.sections.count, 1)
        XCTAssertEqual(formatted.sections[0].kind, .competencies)
        let text = paragraphText(formatted.sections[0])
        XCTAssertTrue(text.contains("Assessment of other candidates."))
    }

    func testExtractsFactsFromKeyValueRun() {
        let formatted = ATSDetailFormatter.format(sections: [
            AtlasDetailSection(
                title: "Contract type",
                body: "Temporary Appointment Duty Station: San Jose Level: G-5 Location: Costa Rica Categories: Administration UNICEF trabaja en más de 190 países y territorios para salvar la vida de niños y niñas, defender sus derechos y ayudarles a desarrollar su máximo potencial desde la primera infancia."
            ),
        ])
        XCTAssertEqual(formatted.sections.count, 1)
        let facts = factsBlocks(formatted.sections[0]).flatMap { $0 }
        XCTAssertTrue(facts.contains(DetailFact(label: "Contract type", value: "Temporary Appointment")))
        XCTAssertTrue(facts.contains(DetailFact(label: "Duty Station", value: "San Jose")))
        XCTAssertTrue(facts.contains(DetailFact(label: "Level", value: "G-5")))
        XCTAssertTrue(facts.contains(DetailFact(label: "Location", value: "Costa Rica")))
        // The long Spanish prose tail must survive as a paragraph.
        XCTAssertTrue(paragraphText(formatted.sections[0]).contains("UNICEF trabaja"))
    }

    // MARK: Inspira / UNV pathologies

    func testMergesDuplicateSectionTitlesIntoOneCanonicalSection() {
        let formatted = ATSDetailFormatter.format(sections: [
            AtlasDetailSection(title: "Responsibilities", body: "Leads the bureau. Plans budgets."),
            AtlasDetailSection(title: "Description of duties", body: "Directs programme execution across regions."),
        ])
        XCTAssertEqual(formatted.sections.count, 1)
        XCTAssertEqual(formatted.sections[0].kind, .responsibilities)
        // Unique stable IDs (the raw title-as-ID scheme produced duplicates).
        XCTAssertEqual(Set(formatted.sections.map(\.id)).count, formatted.sections.count)
    }

    func testSplitsBulletRunsIntoBulletBlocks() {
        let formatted = ATSDetailFormatter.format(sections: [
            AtlasDetailSection(
                title: "Responsibilities",
                body: "Achieving results such as:• Provide effective direction to staff.• Plan and report on the budget.• Oversee recruitment for the Bureau."
            ),
        ])
        let bullets = bulletBlocks(formatted.sections[0]).flatMap { $0 }
        XCTAssertEqual(bullets.count, 3)
        XCTAssertTrue(bullets[0].hasPrefix("Provide effective direction"))
    }

    func testCanonicalOrderingAcrossATSVariants() {
        let formatted = ATSDetailFormatter.format(sections: [
            AtlasDetailSection(title: "Education", body: "An advanced university degree is required."),
            AtlasDetailSection(title: "Org. Setting and Reporting", body: "The Bureau carries out activities under strategic goals."),
            AtlasDetailSection(title: "Responsibilities", body: "Leads and manages the work of the Bureau directly."),
        ])
        XCTAssertEqual(formatted.sections.map(\.kind), [.about, .responsibilities, .qualifications])
    }

    func testFallbackDescriptionWhenNoSections() {
        let formatted = ATSDetailFormatter.format(
            sections: [],
            fallbackDescription: "Support cash-based assistance beneficiary data systems."
        )
        XCTAssertEqual(formatted.sections.count, 1)
        XCTAssertEqual(formatted.sections[0].kind, .about)
        XCTAssertFalse(formatted.hiddenBoilerplate)
    }

    func testLongWallSplitsIntoReadableParagraphs() {
        let wall = String(repeating: "This sentence pads the organizational context for readability. ", count: 30)
        let formatted = ATSDetailFormatter.format(sections: [
            AtlasDetailSection(title: "Background", body: wall),
        ])
        let paragraphCount = formatted.sections[0].blocks.filter {
            if case .paragraph = $0 { return true } else { return false }
        }.count
        XCTAssertGreaterThan(paragraphCount, 1)
    }

    // MARK: Helpers

    private func paragraphText(_ section: FormattedDetailSection) -> String {
        section.blocks.compactMap {
            if case .paragraph(let text) = $0 { return text } else { return nil }
        }.joined(separator: " ")
    }

    private func bulletBlocks(_ section: FormattedDetailSection) -> [[String]] {
        section.blocks.compactMap {
            if case .bullets(let items) = $0 { return items } else { return nil }
        }
    }

    private func factsBlocks(_ section: FormattedDetailSection) -> [[DetailFact]] {
        section.blocks.compactMap {
            if case .facts(let facts) = $0 { return facts } else { return nil }
        }
    }
}
