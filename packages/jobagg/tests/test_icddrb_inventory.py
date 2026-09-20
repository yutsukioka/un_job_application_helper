from pathlib import Path
import pytest
from jobagg.adapters.icddrb_inventory import all_board_links
from jobagg.adapters.icddrb import ICDDRBAdapter
from jobagg.adapters.base import AdapterContext
from jobagg.models import OrganizationSource

FIXTURE = Path(__file__).parent / "fixtures/icddrb/all_20260918.html"


def test_captured_all_table_avoids_redundant_category_posts():
    class HTTP:
        def get(self, url):
            return type("Response", (), {"text": FIXTURE.read_text()})()

        def post_form(self, *args, **kwargs):
            raise AssertionError("Validated All board needs no category requests")

    s = OrganizationSource(
        "icddrb_custom_html",
        "icddrb",
        "icddrb_custom_html",
        "https://career.icddrb.org/",
        extra={"fetch_details": False},
    )
    a = ICDDRBAdapter(AdapterContext(s, HTTP()))
    assert {j.external_id for j in a.fetch_jobs()} == {
        "32288",
        "32287",
        "32286",
        "32285",
        "32284",
        "32280",
    }
    assert a.run_diagnostics.pagination_complete


@pytest.mark.parametrize(
    "control", ['<a href="?page=2">2</a>', "<button>Load more</button>", '<a rel="next">Next</a>']
)
def test_unhandled_continuation_fails(control):
    with pytest.raises(ValueError):
        all_board_links(FIXTURE.read_text() + control, "https://career.icddrb.org/")
