import io
import zipfile
from pathlib import Path

import pytest

from career_quest.access import Viewer, authenticate, can_view_employee, configured_access
from career_quest.data import DatasetError, load_dataset
from career_quest.snapshot import export_snapshot, import_additions, import_snapshot

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def test_partial_security_config_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CQ_HR_PASSWORD", "test-hr")
    monkeypatch.delenv("CQ_EMPLOYEE_ID", raising=False)
    monkeypatch.delenv("CQ_EMPLOYEE_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="Задайте вместе"):
        configured_access()


def test_employee_scope_and_hr_privileges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CQ_EMPLOYEE_ID", "E0028")
    monkeypatch.setenv("CQ_EMPLOYEE_PASSWORD", "test-employee")
    monkeypatch.setenv("CQ_HR_PASSWORD", "test-hr")
    viewer = authenticate("employee", "test-employee")
    assert viewer is not None
    assert can_view_employee(viewer, "E0028")
    assert not can_view_employee(viewer, "E0001")
    assert authenticate("hr", "test-employee") is None
    assert authenticate("employee", "") is None
    assert authenticate("hr", "test-hr") == Viewer("hr", None, demo=False)


def test_employee_cannot_export_or_import_all_profiles() -> None:
    viewer = Viewer("employee", "E0028", demo=False)
    dataset = load_dataset(DATA_DIR)
    with pytest.raises(PermissionError):
        export_snapshot(viewer, dataset)
    with pytest.raises(PermissionError):
        import_snapshot(viewer, {})
    with pytest.raises(PermissionError):
        import_additions(viewer, dataset, b"", b"")


def test_snapshot_roundtrip_preserves_profiles_history_and_catalog() -> None:
    viewer = Viewer("hr", None, demo=True)
    dataset = load_dataset(DATA_DIR)
    with zipfile.ZipFile(io.BytesIO(export_snapshot(viewer, dataset))) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    restored = import_snapshot(viewer, files)
    assert restored.employees == dataset.employees
    assert restored.history == dataset.history
    assert restored.events == dataset.events
    assert restored.as_of_date == dataset.as_of_date


def test_import_rejects_invalid_encoding_before_changing_dataset() -> None:
    viewer = Viewer("hr", None, demo=True)
    dataset = load_dataset(DATA_DIR)
    with pytest.raises(DatasetError, match="UTF-8"):
        import_additions(viewer, dataset, b"\xff", b"")
    assert len(dataset.employees) == 200
