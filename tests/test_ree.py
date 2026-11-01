import pathlib

from dd2mcp import ree


def test_winpath_passes_windows_paths_through():
    assert ree.winpath(r"D:\DD2Modding\x.mesh") == r"D:\DD2Modding\x.mesh"


def test_run_reports_missing_sdk(monkeypatch, tmp_path):
    monkeypatch.setattr(ree, "DOTNET", tmp_path / "nope" / "dotnet.exe")
    assert "private dotnet SDK missing" in ree.run("mesh-info", "x")


def test_project_sources_exist():
    assert (ree.PROJECT / "Program.cs").is_file() and (ree.PROJECT / "reetool.csproj").is_file()
