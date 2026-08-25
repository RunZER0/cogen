from __future__ import annotations

from copy import deepcopy

from app.domain import ForkVentureRequest, Venture, VentureFork, new_id, utc_now
from app.evidence import invalidate_for_location_fork


def fork_venture(parent: Venture, request: ForkVentureRequest) -> tuple[Venture, VentureFork]:
    child = deepcopy(parent)
    child.id = new_id()
    child.parent_venture_id = parent.id
    child.fork_label = request.label
    child.fork_reason = request.reason
    child.archived = False
    child.created_at = utc_now()
    child.updated_at = child.created_at

    invalidated: list[str] = []
    changed_fields: dict[str, object] = {}
    if request.location and request.location != parent.intake.location:
        child.intake.location = request.location
        changed_fields["location"] = request.location
        invalidated.extend(invalidate_for_location_fork(child))
    if request.jurisdiction and request.jurisdiction != parent.intake.jurisdiction:
        child.intake.jurisdiction = request.jurisdiction
        changed_fields["jurisdiction"] = request.jurisdiction.model_dump(mode="json")
        invalidated.extend(invalidate_for_location_fork(child))
        currency = request.jurisdiction.money_unit()
        assumptions = child.assumption_map()
        unit_updates = {
            "setup_costs": currency,
            "monthly_rent": f"{currency}/month",
            "monthly_payroll": f"{currency}/month",
            "monthly_utilities": f"{currency}/month",
            "average_basket": f"{currency}/transaction",
        }
        for key, unit in unit_updates.items():
            if key in assumptions:
                assumptions[key].unit = unit
    if request.business_type and request.business_type != parent.intake.business_type:
        child.intake.business_type = request.business_type
        changed_fields["business_type"] = request.business_type

    assumptions = child.assumption_map()
    for key, value in request.assumption_overrides.items():
        if key not in assumptions:
            raise KeyError(f"Unknown assumption: {key}")
        assumptions[key].value = value
        assumptions[key].stale = False
        changed_fields[f"assumption:{key}"] = value

    fork = VentureFork(
        parent_venture_id=parent.id,
        child_venture_id=child.id,
        label=request.label,
        reason=request.reason,
        changed_fields=changed_fields,
        invalidated_assumptions=sorted(set(invalidated)),
    )
    return child, fork
