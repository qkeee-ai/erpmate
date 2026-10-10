"""The Linux/macOS host runbook stays true to the code it describes
(agents .scratch/qkeee-erp-plugin-profile-split, issue 12; ADR 0006).

- Every `qkeee-erp` command in the README section parses with the plugin's
  real CLI parser, so a renamed flag breaks this test, not an Operator.
- The section uses the terminal user and key path that `setup
  --apply-profile-fixes` writes on a host (setup_steps).
- The sshd drop-in keeps the sidecar's rules for the terminal user: key
  login only, no forwarding of any kind.

Run with `python -m pytest tests` from the repo root.
"""

import argparse
import importlib.util
import re
import shlex
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "plugins" / "qkeee-erp"
DROP_IN = REPO / "host" / "hermes-terminal.sshd.conf"
HEADING = "## Host install (Linux/macOS)"

# Sidecar directives that must hold for the host terminal user too.
FORWARDING_OFF = ("AllowTcpForwarding", "AllowStreamLocalForwarding", "AllowAgentForwarding",
                  "X11Forwarding", "PermitTunnel", "GatewayPorts")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _section() -> str:
    text = (REPO / "README.md").read_text(encoding="utf-8")
    assert HEADING in text, f"README lacks {HEADING!r}"
    body = text.split(HEADING, 1)[1]
    return re.split(r"^## ", body, maxsplit=1, flags=re.M)[0]


def _code_lines(section: str) -> list[str]:
    """Lines of fenced blocks, then inline code spans outside them."""
    blocks = re.findall(r"```[a-z]*\n(.*?)```", section, re.S)
    prose = re.sub(r"```.*?```", "", section, flags=re.S)
    return ([line.strip() for block in blocks for line in block.splitlines() if line.strip()]
            + re.findall(r"`([^`\n]+)`", prose))


def _parser() -> argparse.ArgumentParser:
    cli = _load("qkeee_erp_cli_for_runbook", PLUGIN / "cli.py")
    parser = argparse.ArgumentParser(prog="qkeee-erp")
    cli.setup_parser(parser)
    return parser


def _drop_in_match_block() -> dict[str, str]:
    text = DROP_IN.read_text(encoding="utf-8")
    lines = [ln.split("#", 1)[0].strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    match = next(i for i, ln in enumerate(lines) if ln.lower().startswith("match "))
    keyword, patterns = lines[match].split()[1:]
    user = _setup().HOST_TERMINAL_USER
    # The plain user and the per-profile users of the I2 fix (`<user>-<p>`).
    assert (keyword, patterns.split(",")) == ("User", [user, f"{user}-*"]), lines[match]
    return {ln.split()[0]: " ".join(ln.split()[1:]) for ln in lines[match + 1:]}


def _setup():
    # setup_steps uses relative imports: load it inside a throwaway package.
    name = "qkeee_erp_plugin_runbook"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)])
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return importlib.import_module(name + ".setup_steps")


def test_every_qkeee_erp_command_in_the_section_parses():
    parser = _parser()
    commands = [ln for ln in _code_lines(_section()) if " qkeee-erp " in ln and ln.startswith("hermes")]
    assert len(commands) >= 6, commands
    for line in commands:
        line = line.split("#", 1)[0]
        argv = shlex.split(re.sub(r"<[^>]+>", "x", line))
        argv = argv[argv.index("qkeee-erp") + 1:]
        try:
            parser.parse_args(argv)
        except SystemExit:
            raise AssertionError(f"README command does not parse: {line}")


def test_the_section_covers_every_host_install_step():
    section = _section()
    for needed in ("git clone", "profile install", "plugins enable qkeee-erp", "setup --apply-profile-fixes",
                   "authorized_keys", "qkeee-erp.env", "setup status", "chmod 700",
                   str(DROP_IN.relative_to(REPO)).replace("\\", "/")):
        assert needed in section, needed


def test_the_section_names_what_setup_writes_on_a_host():
    setup = _setup()
    section = _section()
    assert setup.HOST_TERMINAL_USER in section
    assert ".terminal-ssh/id_ed25519.pub" in section


def test_the_section_states_the_limits():
    section = _section()
    assert "ADR 0006" in section
    assert "Windows" in section and "not supported" in section
    assert "no ERPNext access" in section


def test_drop_in_sorts_before_distro_drop_ins():
    """sshd keeps the first value per keyword, Match blocks included, and
    reads sshd_config.d/ in name order (50-cloud-init.conf, 100-macos.conf)."""
    names = re.findall(r"sshd_config\.d/(\S+\.conf)", _section())
    assert names and all(n == "10-hermes-terminal.conf" for n in names), names


def test_drop_in_allows_key_login_only():
    block = _drop_in_match_block()
    assert block.get("AuthenticationMethods") == "publickey"
    assert block.get("PasswordAuthentication") == "no"
    assert block.get("KbdInteractiveAuthentication") == "no"


def test_drop_in_forbids_every_forwarding_the_sidecar_forbids():
    block = _drop_in_match_block()
    sidecar = (REPO / "docker" / "terminal" / "sshd_config").read_text(encoding="utf-8")
    for directive in FORWARDING_OFF:
        assert re.search(rf"^{directive} no$", sidecar, re.M), f"sidecar lost {directive}"
        assert block.get(directive) == "no", directive
