from __future__ import annotations

from app.domain import IntakeDraft, IntakeDraftRequest, utc_now


MATERIAL_FIELDS = (
    "location",
    "country",
    "currency",
    "available_capital",
    "protected_reserve",
    "target_monthly_owner_income",
    "max_acceptable_loss",
    "launch_target_months",
)

QUESTIONS = {
    "location": "Where would this business operate? Give the city/town/area if you know it.",
    "country": "Which country governs this venture? If the location is unambiguous I can infer it, but I will not guess an ambiguous place.",
    "currency": "Which currency should I use for the venture model? Use the ISO code if you know it, for example USD, EUR, GBP, AUD or CNY.",
    "available_capital": "How much money can you actually put into this venture?",
    "protected_reserve": "How much of that money must remain untouched as your safety reserve?",
    "target_monthly_owner_income": "What monthly income must the business eventually pay you?",
    "max_acceptable_loss": "What is the maximum amount you are willing to lose before stopping?",
    "launch_target_months": "How many months are you giving yourself to launch?",
}


def plan_intake(request: IntakeDraftRequest, existing: IntakeDraft | None = None) -> IntakeDraft:
    draft = existing or IntakeDraft(idea=request.idea)
    draft.idea = request.idea
    draft.known.update(request.known)
    if isinstance(draft.known.get("currency"), str):
        draft.known["currency"] = draft.known["currency"].upper()
    missing = [field for field in MATERIAL_FIELDS if draft.known.get(field) in (None, "")]
    draft.missing_material_fields = missing
    draft.next_question = QUESTIONS[missing[0]] if missing else None
    draft.updated_at = utc_now()
    return draft
