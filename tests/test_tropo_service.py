"""Tests for TropoForecastService and timestep computations."""

from datetime import datetime, timezone
import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from meshcore_tray.core.tropo_service import (
    compute_timestep,
    TropoForecastService,
    TropoDownloadWorker,
)


def test_compute_timestep_rounding():
    """Verify rounding to 3-hour forecast intervals."""
    # 13:45 UTC should round down to 12:00 UTC
    dt = datetime(2026, 9, 4, 13, 45, tzinfo=timezone.utc)
    target_dt, filename, label = compute_timestep(dt=dt, offset_hours=0)
    assert target_dt == datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    assert filename == "ww04-12.tif"
    assert "04 Sep 12:00 UTC" in label

    # +3 hours offset should advance to 15:00 UTC
    target_dt, filename, label = compute_timestep(dt=dt, offset_hours=3)
    assert target_dt == datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc)
    assert filename == "ww04-15.tif"

    # +24 hours offset should advance to next day 12:00 UTC
    target_dt, filename, label = compute_timestep(dt=dt, offset_hours=24)
    assert target_dt == datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    assert filename == "ww05-12.tif"


def test_tropo_download_worker_cached(tmp_path: Path):
    """Verify that cached files are read directly without network request."""
    cache_file = tmp_path / "ww04-12.tif"
    fake_content = b"TIFF_DATA_FOR_TESTING_1234567890" * 500
    cache_file.write_bytes(fake_content)

    worker = TropoDownloadWorker(
        filename="ww04-12.tif",
        display_label="04 Sep 12:00 UTC",
        cache_dir=tmp_path,
    )

    ready_results = []
    worker.ready_signal.connect(lambda grid, fn, lbl: ready_results.append((grid, fn, lbl)))

    with patch("requests.get") as mock_get:
        worker.run()
        # Should NOT make HTTP call since file is in cache
        mock_get.assert_not_called()

    assert len(ready_results) == 1
    grid_res, fn_res, lbl_res = ready_results[0]
    assert fn_res == "ww04-12.tif"
    assert lbl_res == "04 Sep 12:00 UTC"
    assert isinstance(grid_res, dict)
    assert base64.b64decode(grid_res["b64_floats"]) == fake_content


def test_tropo_download_worker_network(tmp_path: Path):
    """Verify download and cache saving when file is not cached."""
    fake_content = b"REMOTE_TIFF_DATA_1234567890" * 500
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = fake_content

    worker = TropoDownloadWorker(
        filename="ww04-15.tif",
        display_label="04 Sep 15:00 UTC",
        cache_dir=tmp_path,
    )

    ready_results = []
    worker.ready_signal.connect(lambda grid, fn, lbl: ready_results.append((grid, fn, lbl)))

    with patch("requests.get", return_value=mock_resp) as mock_get:
        worker.run()
        mock_get.assert_called_once()

    assert len(ready_results) == 1
    grid_res, fn_res, lbl_res = ready_results[0]
    assert isinstance(grid_res, dict)
    assert base64.b64decode(grid_res["b64_floats"]) == fake_content
    # Verify file was written to cache
    cached_file = tmp_path / "ww04-15.tif"
    assert cached_file.exists()
    assert cached_file.read_bytes() == fake_content


def test_tropo_service_stepping(tmp_path: Path):
    """Verify stepping offsets in TropoForecastService."""
    service = TropoForecastService(cache_dir=tmp_path)
    assert service.current_offset_hours == 0

    with patch.object(service, "fetch_forecast") as mock_fetch:
        service.step_forecast(3)
        mock_fetch.assert_called_with(offset_hours=3)

    with patch.object(service, "fetch_forecast") as mock_fetch:
        service._current_offset_hours = 3
        service.step_forecast(-3)
        mock_fetch.assert_called_with(offset_hours=0)
