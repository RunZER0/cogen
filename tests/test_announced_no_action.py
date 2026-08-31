"""Regression coverage for the announced-no-action detector in app/main.py.

Reproducing the underlying model flakiness this guards against is not something a test can force
on demand — verified live instead, where the model's entire response for a turn was one sentence
announcing it was about to run research, with zero tool calls anywhere in that round. This pins the
detection regex itself against the exact strings observed live, plus cases it must NOT flag, since
that's the fragile, easy-to-regress part — a small wording tweak elsewhere in main.py could silently
loosen or tighten the pattern without anything else in the suite noticing.
"""
from app.main import _ANNOUNCED_NO_ACTION_RE


def test_matches_strings_observed_live():
    observed = [
        "Starting the full five-specialist research pass to evaluate the venture's financial, "
        "market, regulatory, execution, and adversary dimensions.",
        "I am starting the full multi-specialist research pass to evaluate the AI training agency "
        "venture across finance, market, regulatory, execution, and risk dimensions.",
    ]
    for text in observed:
        assert _ANNOUNCED_NO_ACTION_RE.match(text.strip()), text


def test_does_not_match_a_substantive_answer():
    substantive = [
        "Recorded the $3,200/month lease quote for monthly rent. The underwriting decision "
        "remains needs_data.",
        "Based on Ohio Secretary of State filings, forming an LLC costs $99.",
        "What is your target average selling price per laptop and your projected initial "
        "monthly unit volume?",
    ]
    for text in substantive:
        assert not _ANNOUNCED_NO_ACTION_RE.match(text.strip()), text
