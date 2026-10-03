import pytest

from ytid import __version__, cli


def test_bare_yt_id_prints_the_command_reference(capsys):
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert out.startswith("USAGE\n")
    assert "COMMANDS\n" in out
    assert "-h, --help" in out
    assert "-v, --version" in out
    assert "NAME\n" not in out
    fetch = next(line for line in out.splitlines() if line.startswith("fetch"))
    assert "Ask yt-dlp for metadata" in fetch
    sleep = next(line for line in out.splitlines() if "--sleep SECONDS" in line)
    assert "Pause between requests" in sleep
    assert "(Live)" in out


def test_help_starts_with_the_summary_then_the_same_reference(capsys):
    assert cli.main(["--help"]) == 0
    help_out = capsys.readouterr().out
    assert cli.main(["-h"]) == 0
    short_out = capsys.readouterr().out
    assert help_out == short_out
    assert help_out.startswith("NAME\n")
    for heading in ("SYNOPSIS", "DESCRIPTION", "FILES", "WORKFLOW", "COMMANDS"):
        assert heading in help_out
    assert cli.main([]) == 0
    bare = capsys.readouterr().out
    assert help_out.endswith(bare)


@pytest.mark.parametrize("argv", [["-v"], ["--version"]])
def test_version_flag(capsys, argv):
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 0
    assert capsys.readouterr().out == f"yt-id {__version__}\n"


def test_command_help_stays_on_argparse(capsys):
    try:
        cli.main(["scan", "-h"])
    except SystemExit as exc:
        assert exc.code == 0
    out = capsys.readouterr().out
    assert "usage: yt-id scan" in out
    assert "--source" in out
