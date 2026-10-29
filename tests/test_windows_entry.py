import json

from tools.windows_entry import main


def test_windows_entry_runs_machine_readable_cli_without_opening_gui(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("A CLI invocation must not open the desktop window")

    monkeypatch.setattr("tools.windows_entry.runpy.run_module", forbidden)
    assert main(["video", "schema"]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["ok"] is True
    assert response["command"] == "video schema"
    assert response["result"]
