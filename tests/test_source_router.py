import pytest

from app.source_router import is_likely_official_authority_url


@pytest.mark.parametrize(
    "url",
    [
        "https://www.gov.uk/set-up-business",
        "https://www.austintexas.gov/health/programs/fixed-food-establishments",
        "https://www.gov.au/business",
        "https://www.india.gov.in/",
        "https://www.gov.ng/",
        "https://www.gov.za/",
        "https://www.impots.gouv.fr/",
        "https://www.canada.gc.ca/",
        # "go.<cc>" convention used instead of "gov" by many countries.
        "https://www.kra.go.ke/",  # Kenya Revenue Authority
        "https://www.brs.go.ke/",  # Kenya Business Registration Service
        "https://www.nta.go.jp/",  # Japan National Tax Agency
        "https://www.nts.go.kr/",  # South Korea National Tax Service
        "https://www.rd.go.th/",  # Thailand Revenue Department
        "https://www.pajak.go.id/",  # Indonesia tax authority
        # Named exceptions that follow neither convention.
        "https://www.admin.ch/",
        "https://www.bundesfinanzministerium.bund.de/",
        "https://ec.europa.eu/taxation_customs/",
    ],
)
def test_recognizes_official_authority_domains_across_jurisdictions(url):
    assert is_likely_official_authority_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "https://www.legalzoom.com/business-registration-guide",
        "https://www.some-consultancy-blog.co.ke/kenya-business-permits",
        "https://www.go.com/",  # commercial portal, not a government domain
        "https://www.lego.com/",
        "https://www.reddit.com/r/smallbusiness/",
        "https://www.diego.co.jp/",
    ],
)
def test_rejects_non_official_domains(url):
    assert is_likely_official_authority_url(url) is False
