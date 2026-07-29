from wslc_compose.cli import build_parser, main


def test_ignore_unsupported_is_explicit_global_option():
    ns = build_parser().parse_args(["--ignore-unsupported", "config"])
    assert ns.ignore_unsupported


def test_capability_report_does_not_require_compose_file(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)

    assert main(["config", "--capabilities"]) == 0

    output = capsys.readouterr().out
    assert "runtime: wslc" in output
    assert "read_only:" in output
    assert "restart_policies: false" in output
