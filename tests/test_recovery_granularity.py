import pytest

from app.domain import SpecialistRole, VentureIntake, WorkflowStatus
from app.workflow import SimulatedCrash, WorkflowRunner


def test_research_resumes_after_last_persisted_specialist(service, intake_payload):
    venture = service.create_venture(VentureIntake.model_validate(intake_payload))
    runner = WorkflowRunner(service.repository, service.state, service.orchestrator, service.engine)
    workflow = runner.start(venture.id, "specialist-resume-test")

    with pytest.raises(SimulatedCrash):
        runner.run(workflow.id, stop_after_specialist=SpecialistRole.MARKET)

    checkpoint = service.state.get_workflow(workflow.id)
    assert checkpoint.status == WorkflowStatus.RETRYABLE
    first_reports = [
        report
        for report in service.state.list_specialist_reports(venture.id)
        if report.workflow_id == workflow.id
    ]
    assert [report.role for report in first_reports] == [
        SpecialistRole.FINANCE,
        SpecialistRole.MARKET,
    ]

    completed = runner.run(workflow.id)
    assert completed.status == WorkflowStatus.COMPLETE
    final_reports = [
        report
        for report in service.state.list_specialist_reports(venture.id)
        if report.workflow_id == workflow.id
    ]
    assert {report.role for report in final_reports} == set(SpecialistRole)
    assert len(final_reports) == len(SpecialistRole)

    specialist_events = [
        event
        for event in service.state.list_events(venture.id)
        if event.event_type.value == "specialist_completed"
    ]
    assert len(specialist_events) == len(SpecialistRole)
