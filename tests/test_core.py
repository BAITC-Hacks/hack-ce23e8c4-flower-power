import io
import json
import zipfile
from datetime import date

import pytest

from career_quest.demo import demo_dataset
from career_quest.ingestion import load_dataset, merge_profiles
from career_quest.models import Activity, ActivityStatus, Dataset, SkillGain
from career_quest.progress import complete_event, current_skills, export_snapshot, gained_level
from career_quest.recommendations import recommend, skill_gaps


def snapshot_files(dataset: Dataset) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(export_snapshot(dataset))) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def reload_snapshot(files: dict[str, bytes]) -> Dataset:
    return load_dataset(
        files["employees.json"], files["events.json"], files["skills.json"], files["activity_history.csv"]
    )


def test_critical_gap_beats_lowest_skill_and_repeated_no_shows() -> None:
    dataset = demo_dataset()
    employee = dataset.employees[0]
    recommendations = recommend(employee, dataset)
    assert recommendations[0].event.event_id == "DEMO_SYSTEM"
    speaking = next(item for item in recommendations if item.event.event_id == "EV_036")
    assert speaking.avoided_similar == 3
    assert speaking.score < recommendations[0].score
    assert "DEMO_REQUIRED" not in {item.event.event_id for item in recommendations}
    assert "DEMO_CAP" not in {item.event.event_id for item in recommendations}


def test_replay_only_completions_after_review() -> None:
    dataset = demo_dataset()
    employee = dataset.employees[2]
    assert employee.skills["SK_PYTHON"] == 3
    assert current_skills(employee, dataset)["SK_PYTHON"] == 4
    reviewed = employee.model_copy(update={"last_review_date": date(2026, 9, 15)})
    assert current_skills(reviewed, dataset)["SK_PYTHON"] == 3


def test_completion_is_idempotent_and_survives_export() -> None:
    dataset = demo_dataset()
    changed = complete_event(dataset, "DEMO_1", "DEMO_SYSTEM", date(2026, 10, 1))
    assert current_skills(changed.employees[0], changed)["SK_SYSTEM_DESIGN"] == 3
    assert current_skills(dataset.employees[0], dataset)["SK_SYSTEM_DESIGN"] == 2
    assert complete_event(changed, "DEMO_1", "DEMO_SYSTEM", date(2026, 10, 1)) is changed
    restored = reload_snapshot(snapshot_files(changed))
    assert current_skills(restored.employees[0], restored)["SK_SYSTEM_DESIGN"] == 3
    assert all(item.event.event_id != "DEMO_SYSTEM" for item in recommend(restored.employees[0], restored))


def test_recurring_club_gains_once_per_session() -> None:
    dataset = demo_dataset()
    changed = complete_event(dataset, "DEMO_1", "EV_036", date(2026, 10, 8))
    assert current_skills(changed.employees[0], changed)["SK_PUBLIC_SPEAKING"] == 1
    assert complete_event(changed, "DEMO_1", "EV_036", date(2026, 10, 8)) is changed
    second = complete_event(changed, "DEMO_1", "EV_036", date(2026, 10, 22))
    assert current_skills(second.employees[0], second)["SK_PUBLIC_SPEAKING"] == 2
    assert second.meta.as_of_date == date(2026, 10, 22)


@pytest.mark.parametrize(("current", "gain", "cap", "expected"), [(2, 2, 3, 3), (4, 1, 2, 4), (5, 1, 5, 5)])
def test_caps_never_reduce_levels(current: int, gain: int, cap: int, expected: int) -> None:
    assert gained_level(current, SkillGain(skill_id="S", gain=gain, max_level=cap)) == expected


def test_missing_skill_is_zero_per_starter_kit() -> None:
    dataset = demo_dataset()
    employee = dataset.employees[0].model_copy(update={"skills": {}})
    assert all(gap.current == 0 for gap in skill_gaps(employee, dataset))


def test_target_reached_has_no_recommendations() -> None:
    dataset = demo_dataset()
    assert recommend(dataset.employees[1], dataset) == []


def test_mandatory_and_prerequisites_are_enforced_on_completion() -> None:
    dataset = demo_dataset()
    with pytest.raises(ValueError, match="Обязательное"):
        complete_event(dataset, "DEMO_1", "DEMO_REQUIRED", dataset.meta.as_of_date)
    event = dataset.events[0].model_copy(update={"prerequisites": {"SK_PYTHON": 5}})
    blocked = dataset.model_copy(update={"events": [event, *dataset.events[1:]]})
    assert all(item.event.event_id != event.event_id for item in recommend(blocked.employees[0], blocked))
    with pytest.raises(ValueError, match="Для участия"):
        complete_event(blocked, "DEMO_1", event.event_id, blocked.meta.as_of_date)


def test_bad_import_is_rejected_with_context() -> None:
    files = snapshot_files(demo_dataset())
    employee_file = json.loads(files["employees.json"])
    employee_file["employees"][0]["skills"]["UNKNOWN_SKILL"] = 3
    files["employees.json"] = json.dumps(employee_file).encode()
    with pytest.raises(ValueError, match="DEMO_1: неизвестные навыки"):
        reload_snapshot(files)


def test_extra_profiles_are_merged_and_history_import_is_idempotent() -> None:
    dataset = demo_dataset()
    files = snapshot_files(dataset)
    document = json.loads(files["employees.json"])
    newcomer = document["employees"][0]
    newcomer["employee_id"] = "JURY_1"
    document["employees"] = [newcomer]
    profiles = json.dumps(document).encode()
    merged = merge_profiles(dataset, profiles, files["activity_history.csv"])
    assert len(merged.employees) == 4
    assert len(merged.history) == len(dataset.history)
    assert len(merge_profiles(merged, profiles, files["activity_history.csv"]).employees) == 4


def test_review_day_completion_is_not_lost_on_reimport() -> None:
    dataset = demo_dataset()
    employee = dataset.employees[0].model_copy(update={"last_review_date": dataset.meta.as_of_date})
    dataset = dataset.model_copy(update={"employees": [employee, *dataset.employees[1:]]})
    changed = complete_event(dataset, employee.employee_id, "DEMO_SYSTEM", dataset.meta.as_of_date)
    restored = reload_snapshot(snapshot_files(changed))
    assert current_skills(restored.employees[0], restored)["SK_SYSTEM_DESIGN"] == 3


def test_conflicting_history_id_is_rejected() -> None:
    dataset = demo_dataset()
    files = snapshot_files(dataset)
    history = files["activity_history.csv"].replace(b"DEMO_1,EV_036", b"DEMO_2,EV_036", 1)
    with pytest.raises(ValueError, match="идентификатор уже занят"):
        merge_profiles(dataset, files["employees.json"], history)


def test_inconsistent_completion_status_is_rejected() -> None:
    row = demo_dataset().history[0].model_dump()
    row["status"] = ActivityStatus.COMPLETED
    with pytest.raises(ValueError, match="completion_pct=100"):
        Activity.model_validate(row)
