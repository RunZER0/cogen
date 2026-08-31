from app.domain import Confidence, EvidenceType, SpecialistRole
from app.agentic_research import AgenticSpecialistResearchProvider


def _parse(text, role=SpecialistRole.REGULATORY, grounded=frozenset()):
    return AgenticSpecialistResearchProvider._parse(text, role, set(grounded))


def test_parses_findings_object_with_findings_key():
    text = '{"findings": [{"assumption_key": "monthly_rent", "claim": "Rent is $3,000/mo", "value": 3000, "unit": "USD/month", "evidence_type": "founder", "confidence": "high", "source_title": "Founder statement"}]}'
    findings = _parse(text, role=SpecialistRole.FINANCE)
    assert len(findings) == 1
    assert findings[0].assumption_key == "monthly_rent"
    assert findings[0].value == 3000.0
    assert findings[0].role == SpecialistRole.FINANCE


def test_source_url_not_actually_browsed_is_dropped_and_confidence_downgraded():
    text = (
        '{"findings": [{"assumption_key": "regulatory_registration_path", '
        '"claim": "LLC filing costs $300", "value": 300, "unit": "USD", '
        '"evidence_type": "official", "confidence": "verified", '
        '"source_title": "Secretary of State", "source_url": "https://sos.state.tx.us/fees"}]}'
    )
    # grounded_urls is empty — the model wrote a URL it never actually browsed this turn.
    findings = _parse(text, grounded=frozenset())
    assert findings[0].source_url is None
    assert findings[0].confidence == Confidence.LOW


def test_source_url_that_was_actually_browsed_is_kept():
    url = "https://sos.state.tx.us/fees"
    text = (
        '{"findings": [{"assumption_key": "regulatory_registration_path", '
        '"claim": "LLC filing costs $300", "value": 300, "unit": "USD", '
        '"evidence_type": "official", "confidence": "high", '
        '"source_title": "Secretary of State", "source_url": "%s"}]}' % url
    )
    findings = _parse(text, grounded=frozenset({url}))
    assert findings[0].source_url == url
    assert findings[0].confidence == Confidence.HIGH


def test_malformed_json_returns_no_findings_not_an_exception():
    assert _parse("not json at all") == []
    assert _parse("") == []
    assert _parse("   ") == []


def test_findings_missing_required_fields_are_dropped():
    text = '{"findings": [{"claim": "no assumption key here"}, {"assumption_key": "x", "claim": ""}, {"assumption_key": "", "claim": "y"}]}'
    assert _parse(text) == []


def test_unknown_evidence_type_and_confidence_fall_back_safely():
    text = (
        '{"findings": [{"assumption_key": "competition_local", "claim": "Several competitors nearby", '
        '"value": 5, "unit": "competitors", "evidence_type": "not-a-real-type", '
        '"confidence": "extremely-sure", "source_title": "Observation"}]}'
    )
    findings = _parse(text, role=SpecialistRole.MARKET)
    assert findings[0].evidence_type == EvidenceType.MODEL
    assert findings[0].confidence == Confidence.LOW


def test_bare_list_payload_is_accepted_alongside_findings_wrapper():
    text = '[{"assumption_key": "average_basket", "claim": "Average ticket is $12", "value": 12, "unit": "USD/transaction", "evidence_type": "observed", "confidence": "medium", "source_title": "Local listings"}]'
    findings = _parse(text, role=SpecialistRole.MARKET)
    assert len(findings) == 1
    assert findings[0].value == 12.0


def test_markdown_code_fence_is_stripped_before_parsing():
    """Verified live: an ADK tool-calling agent (unlike the JSON-mode-forced single-completion
    providers) wrapped a fully valid, well-sourced finding in a ```json fence, silently producing
    zero findings from a successful turn until this was handled."""
    fenced = (
        "```json\n"
        '{"findings": [{"assumption_key": "regulatory_sales_tax_rate", '
        '"claim": "Combined sales tax is 8.25%", "value": 0.0825, "unit": "ratio", '
        '"evidence_type": "official", "confidence": "high", '
        '"source_title": "Texas Comptroller", "source_url": "https://comptroller.texas.gov/x"}]}\n'
        "```"
    )
    findings = _parse(fenced, grounded={"https://comptroller.texas.gov/x"})
    assert len(findings) == 1
    assert findings[0].value == 0.0825
    assert findings[0].confidence == Confidence.HIGH


def test_bare_triple_backtick_fence_without_json_tag_is_also_stripped():
    fenced = '```\n{"findings": [{"assumption_key": "k", "claim": "c", "source_title": "s"}]}\n```'
    assert len(_parse(fenced)) == 1
