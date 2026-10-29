"""The browser opens only when the local dashboard is ready."""

from io import StringIO

from app.telemetry import receiver


class ReadyResponse:
    status = 200

    def __enter__(self) -> "ReadyResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_browser_opens_after_local_dashboard_responds(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(receiver, "urlopen", lambda url, timeout: (calls.append(("ready", url)), ReadyResponse())[1])
    monkeypatch.setattr(receiver.webbrowser, "open", lambda url, new: (calls.append(("open", url)), True)[1])
    output = StringIO()
    receiver.open_dashboard_browser(output)
    assert calls == [("ready", "http://127.0.0.1:8765/api/state"),
                     ("open", "http://127.0.0.1:8765/")]
    assert "Dashboard opened" in output.getvalue()
