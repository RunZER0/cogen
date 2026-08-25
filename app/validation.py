from __future__ import annotations

from app.domain import Confidence, ValidationTask, Venture


def design_validation_tasks(venture: Venture) -> list[ValidationTask]:
    tasks: list[ValidationTask] = []
    for assumption in venture.assumptions:
        if not assumption.critical:
            continue
        if assumption.value is not None and assumption.confidence not in {
            Confidence.UNKNOWN,
            Confidence.LOW,
        }:
            continue
        if assumption.key == "transactions_per_day":
            protocol = [
                "Count relevant customer/footfall activity at the actual location in at least three time blocks.",
                "Repeat on at least two different trading days.",
                "Record raw counts and time windows; do not convert estimates into observations.",
                "Submit the raw counts so Cogen can replace the demand assumption and rerun underwriting.",
            ]
            title = "Validate real demand at the proposed location"
        else:
            protocol = [
                f"Obtain one current independent source for {assumption.label}.",
                "Capture the source, date, raw value and any conditions attached to it.",
                "Submit it to Cogen as evidence; the model will rerun automatically.",
            ]
            title = f"Resolve: {assumption.label}"
        tasks.append(
            ValidationTask(
                venture_id=venture.id,
                assumption_key=assumption.key,
                title=title,
                protocol=protocol,
                reason=(
                    f"{assumption.label} is material and currently has "
                    f"{assumption.confidence.value} confidence."
                ),
            )
        )
    return tasks
