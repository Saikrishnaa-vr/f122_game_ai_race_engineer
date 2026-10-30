"""The offline benchmark must measure real capture and HTTP paths."""

from scripts.benchmark_telemetry import benchmark
from app.telemetry import capture


def test_benchmark_reads_without_changing_capture_and_serves_snapshot(
    packet_factory, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(capture, "CAPTURE_DIRECTORY", tmp_path / "recordings")
    writer = capture.CaptureWriter("small.f1cap")
    packets = [(0.0, packet_factory()), (1.0, packet_factory(frame=2))]
    try:
        for elapsed, raw in packets:
            assert writer.write(elapsed, raw)
    finally:
        writer.close()
    before = (tmp_path / "recordings" / "small.f1cap").read_bytes()
    result = benchmark("small.f1cap", http_requests=2)
    assert result["packets"] == 2
    assert result["valid_headers"] == 2
    assert result["dashboard_frames"] == 2
    assert result["stages"]["receiver_snapshot_copy"]["samples"] == 2
    assert result["http_loopback"]["samples"] == 2
    assert (tmp_path / "recordings" / "small.f1cap").read_bytes() == before
