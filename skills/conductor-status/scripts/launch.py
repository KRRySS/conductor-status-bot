#!/usr/bin/env python3
"""Stable MCP launch entry point. Stdlib only; no third-party imports.

Why this file exists: `config.yaml` is preserved by `hermes profile update`, so anything the
launch depends on must live HERE (distribution-owned, overwritten on every update), not there.
The config's only job is to point at this file. Everything else — where the profile is, which
lockfile applies, which interpreter/deps — is decided from this file's own installed location.

Never edit the config to "fix" a launch problem in this layer; fix it here and ship an update.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE / 'server.py'


def find_uv():
    # PATH first (Hermes passes a filtered PATH), then the usual per-user install locations.
    # Hermes' own launcher lives in ~/.local/bin; uv installers use the same directory.
    from shutil import which
    found = which('uv')
    if found:
        return found
    home = Path.home()
    for candidate in (home / '.local/bin/uv', home / '.cargo/bin/uv', home / '.brew/bin/uv',
                      Path('/opt/homebrew/bin/uv'), Path('/usr/local/bin/uv')):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def main():
    uv = find_uv()
    if uv is None:
        sys.stderr.write('conductor-status: `uv` not found on PATH or in ~/.local/bin, ~/.cargo/bin, '
                         '/opt/homebrew/bin, /usr/local/bin. Install uv, then restart the profile.\n')
        return 2
    if not SERVER.is_file():
        sys.stderr.write(f'conductor-status: server.py missing next to launcher ({HERE}). '
                         'Reinstall or update the distribution.\n')
        return 2
    env = dict(os.environ)
    env.setdefault('PYTHONDONTWRITEBYTECODE', '1')
    # A stale CONDUCTOR_STATUS_HOME from an old config (0.1.x) would make server.py refuse to
    # start on a mismatch. The launcher is the authority on the profile location, so drop it.
    env.pop('CONDUCTOR_STATUS_HOME', None)
    # The adjacent server.py.lock is honoured by `uv run --script` without --locked.
    cmd = [uv, 'run', '--no-project', '--script', str(SERVER)]
    os.execve(cmd[0], cmd, env)


if __name__ == '__main__':
    sys.exit(main())
