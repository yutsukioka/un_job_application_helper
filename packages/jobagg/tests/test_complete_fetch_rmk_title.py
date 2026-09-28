from jobagg.adapters.successfactors_rmk import _detail_title


def test_candidate_title_preserves_internal_separator_and_escaped_ampersand():
    page = '''<title>IDB Invest - Information Technology Officer Job Details | interameri</title>
    <meta property="og:title" content="IDB Invest - Information Technology Officer">
    <span itemprop="title" class="rtltextaligneligible">IDB Invest - Information
    Technology Officer - IT Operation &amp; Transformation Team</span>'''
    assert _detail_title(page) == (
        "IDB Invest - Information Technology Officer - IT Operation & Transformation Team"
    )


def test_candidate_title_precedes_site_heading_and_preserves_pipe():
    assert _detail_title('<h1>Careers</h1><h2 itemprop="title">Research | Data Officer</h2>') == "Research | Data Officer"


def test_document_title_fallback_is_unchanged():
    assert _detail_title('<title>Finance Officer | Careers</title>') == "Finance Officer"


def test_ilo_apostrophe_and_location_are_preserved():
    page = '''<meta property="og:title" content="Senior Specialist in Workers' Activities - Beirut">
    <span itemprop="title">Senior Specialist in Workers' Activities - Beirut</span>'''
    assert _detail_title(page) == "Senior Specialist in Workers' Activities - Beirut"


def test_ebrd_commas_colons_and_role_specialism_are_preserved():
    page = '<span itemprop="title">Principal, Solution Architect: Application and Integration</span>'
    assert _detail_title(page) == 'Principal, Solution Architect: Application and Integration'


def test_itu_public_meta_title_preserves_roster_role_and_apostrophe():
    assert _detail_title('<meta property="og:title" content="Roster - Country Project Coordination Consultant">') == 'Roster - Country Project Coordination Consultant'
    assert _detail_title('''<meta property="og:title" content="Chef(fe) de l'Unité de contrôle">''') == "Chef(fe) de l'Unité de contrôle"


def test_single_quoted_meta_attribute_preserves_internal_double_quotes():
    assert _detail_title('''<meta content='Programme Officer - "AI for Good"' property='og:title'>''') == 'Programme Officer - "AI for Good"'
