import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "agents/apex/skills/deterministic-skill-router/scripts/route_named_output.py"
spec = importlib.util.spec_from_file_location("named_router", SCRIPT)
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)


def test_output_wins_over_cv_and_strategy_inputs():
    cases = {
        "Can you prepare UNESCO Employment History Form from my CV?": router.NAMES[0],
        "Fill my EHF using the supplied template": router.NAMES[0],
        "Recommend my Domain of Expertise using my strategy report": router.NAMES[1],
        "Review my publications using my CV": router.NAMES[2],
        "Use $apex-select-domain-of-expertise to assess this evidence": router.NAMES[1],
        "$apex-curate-publications": router.NAMES[2],
    }
    for request, expected in cases.items():
        assert router.route(request)["skill"] == expected


def test_research_status_and_installation_do_not_generate_content():
    for request in ["Research UNESCO pre-screening questions", "Explain Domain of Expertise",
                    "Are $apex-curate-publications and the EHF skill installed?",
                    "Can you make these UNESCO skills ready to use?",
                    "Install apex-select-domain-of-expertise"]:
        assert router.route(request)["decision"] == "NO_MATCH"


def test_other_requested_outputs_keep_their_existing_routes():
    for request in ["Update my CV publication section", "Create a context pack for UNESCO",
                    "Generate Option 9 from my CV", "Prepare a competency map with expertise notes",
                    "Create a strategy report using my publications", "Draft my cover letter"]:
        assert router.route(request)["decision"] == "DEFER"


def test_unops_fit_outputs_and_option10():
    for request in ["Prepare UNOPS fit plan from my CV", "Generate Phase 8 Option 10 using my strategy report",
                    "Recommend my UNOPS skills from my job history", "Review UNOPS Position Areas",
                    "$apex-unops-application-fit"]:
        assert router.route(request)["skill"] == "apex-unops-application-fit"


def test_unops_setup_is_not_content_generation():
    for request in ["Install apex-unops-application-fit", "Explain Option 10",
                    "Create a UNOPS skill", "Adapt the UNOPS skill package to this environment",
                    "Can you set up the UNOPS skill?"]:
        assert router.route(request)["decision"] == "NO_MATCH"


def test_unops_document_requests_keep_generator_routes():
    for request in ["Update my CV using UNOPS skills", "Draft a cover letter for UNOPS",
                    "Build a context pack for UNOPS", "Generate Option 6 for UNOPS",
                    "Generate Option 9 from my UNOPS vacancy"]:
        assert router.route(request)["decision"] == "DEFER"
