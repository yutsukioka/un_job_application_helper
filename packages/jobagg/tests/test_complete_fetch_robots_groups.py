from jobagg.robots import BlankLineSafeRobotFileParser


def parser(text):
    p = BlankLineSafeRobotFileParser()
    p.parse(text.splitlines())
    return p


def test_blank_and_comment_lines_do_not_drop_later_disallow_in_wildcard_group():
    p = parser("""User-agent: *
Disallow: /archive/

# Another department's rules in the same group

Disallow: /eusurvey/files
""")
    assert not p.can_fetch("jobagg/0.1", "https://ec.europa.eu/eusurvey/files/public-notice")
    assert p.can_fetch("jobagg/0.1", "https://ec.europa.eu/eusurvey/runner/public-vacancy")


def test_following_user_agent_still_starts_a_separate_group():
    p = parser("""User-agent: OtherBot
Disallow: /

User-agent: *
Disallow: /private

User-agent: AnotherBot
Disallow: /public
""")
    assert p.can_fetch("jobagg/0.1", "https://example.org/public")
    assert not p.can_fetch("jobagg/0.1", "https://example.org/private")


def test_blank_line_between_user_agents_keeps_shared_rules():
    p = parser("""User-agent: ExampleBot

# Both agents share the following rules
User-agent: jobagg

Disallow: /private
""")
    assert not p.can_fetch("jobagg/0.1", "https://example.org/private")
    assert not p.can_fetch("ExampleBot", "https://example.org/private")
