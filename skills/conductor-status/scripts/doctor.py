#!/usr/bin/env python3
"""Read-only diagnosis of the MCP launch config for this installed profile. Stdlib only.

Prints what is wrong and the exact, copy-pasteable fix commands (full launcher path). Never
edits config.yaml itself — that is the user's file, changed only through `hermes config`.
Never touches the Conductor database.

Usage:  python3 <profile>/skills/conductor-status/scripts/doctor.py [--profile-name NAME]
Exit:   0 = launch config is correct, 1 = fix needed (commands printed), 2 = cannot diagnose.
"""
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROFILE = HERE.parents[2]
LAUNCHER = HERE / 'launch.py'
SERVER = HERE / 'server.py'
SERVER_NAME = 'conductor_status'


def hermes_cli():
    # Hermes' installer puts the launcher in ~/.local/bin; the desktop app's embedded CLI reports
    # version "unknown" and cannot run `profile update`, so always name the external launcher.
    candidate = Path.home() / '.local/bin/hermes'
    return str(candidate) if candidate.exists() else 'hermes'


def load_yaml(path):
    try:
        import yaml  # available inside Hermes' venv; fall back to a lenient scan otherwise
    except ImportError:
        return None
    try:
        return yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except Exception as exc:
        print(f'cannot parse {path}: {exc}')
        return None


def scan_without_yaml(text):
    """Lenient extraction when PyYAML is unavailable: enough to spot the known-bad shapes."""
    block = re.search(r'^\s*conductor_status:\n((?:[ \t]+.*\n?)*)', text, re.M)
    body = block.group(1) if block else ''
    return {'present': bool(block), 'body': body}


def main():
    argv = sys.argv[1:]
    name = PROFILE.name
    if '--profile-name' in argv:
        name = argv[argv.index('--profile-name') + 1]
    cfg_path = PROFILE / 'config.yaml'
    cli = hermes_cli()
    expected_args = ['python3', str(LAUNCHER)]
    print(f'profile dir : {PROFILE}')
    print(f'profile name: {name}')
    print(f'config      : {cfg_path}')
    print(f'launcher    : {LAUNCHER}  ({"ok" if LAUNCHER.is_file() else "MISSING"})')
    print(f'server      : {SERVER}  ({"ok" if SERVER.is_file() else "MISSING"})')
    if not cfg_path.is_file():
        print('config.yaml missing — reinstall the distribution.')
        return 2
    problems = []
    has_home_var = False
    text = cfg_path.read_text(encoding='utf-8')
    data = load_yaml(cfg_path)
    if data is None:
        found = scan_without_yaml(text)
        if not found['present']:
            problems.append('mcp_servers.conductor_status is not configured')
        else:
            body = found['body']
            if '${HERMES_HOME}' in body:
                problems.append('launch path uses ${HERMES_HOME} (resolves to the LAUNCH profile under the desktop app / multiplexing)')
            if 'CONDUCTOR_STATUS_HOME' in body:
                has_home_var = True
                problems.append('env sets CONDUCTOR_STATUS_HOME (redundant; a mismatch kills the server)')
            if '--locked' in body:
                problems.append('args contain --locked (no effect with --script; noise in logs)')
            if 'launch.py' not in body:
                problems.append('args do not go through launch.py (the update-proof entry point)')
            else:
                m = re.search(r'^\s*-\s*(\S*launch\.py)\s*$', body, re.M)
                if m:
                    configured = Path(m.group(1).replace('${userHome}', str(Path.home()))).expanduser()
                    if configured.resolve() != LAUNCHER:
                        problems.append(f'launch.py path is {m.group(1)!r} which is not this profile '
                                        f'({LAUNCHER}); typical after `--name <other>` or a moved profile')
    else:
        entry = ((data.get('mcp_servers') or {}).get(SERVER_NAME)) if isinstance(data, dict) else None
        if not isinstance(entry, dict):
            problems.append('mcp_servers.conductor_status is not configured')
        else:
            args = [str(a) for a in (entry.get('args') or [])]
            env = entry.get('env') or {}
            joined = ' '.join(args)
            if '${HERMES_HOME}' in joined:
                problems.append('launch path uses ${HERMES_HOME} (resolves to the LAUNCH profile under the desktop app / multiplexing)')
            if 'CONDUCTOR_STATUS_HOME' in env:
                has_home_var = True
                problems.append('env sets CONDUCTOR_STATUS_HOME (redundant; a mismatch kills the server)')
            if '--locked' in args:
                problems.append('args contain --locked (no effect with --script; noise in logs)')
            if not (args and args[-1].endswith('launch.py')):
                problems.append('args do not go through launch.py (the update-proof entry point)')
            else:
                # Expand ${userHome} exactly as Hermes does, then compare with THIS profile's launcher.
                configured = Path(args[-1].replace('${userHome}', str(Path.home()))).expanduser()
                if configured.resolve() != LAUNCHER:
                    problems.append(f'launch.py path is {args[-1]!r} which is not this profile '
                                    f'({LAUNCHER}); typical after `--name <other>` or a moved profile')
            if entry.get('command') not in ('python3', 'python'):
                problems.append(f'command is {entry.get("command")!r}; expected python3 (launcher is stdlib-only)')
            sampling = entry.get('sampling') or {}
            if sampling.get('enabled', True):
                problems.append('sampling.enabled is not false')
    if not problems:
        print('OK: launch config is correct for this profile.')
        return 0
    print('\nPROBLEMS:')
    for p in problems:
        print(f'  - {p}')
    print('\nFIX (run from an external terminal, not the desktop app; then restart the profile):')
    args_json = json.dumps(expected_args[1:])
    print(f"  {cli} -p {name} config set mcp_servers.{SERVER_NAME}.command python3")
    print(f"  {cli} -p {name} config set mcp_servers.{SERVER_NAME}.args '{args_json}'")
    if has_home_var:
        print(f"  {cli} -p {name} config unset mcp_servers.{SERVER_NAME}.env.CONDUCTOR_STATUS_HOME")
    print(f"  {cli} -p {name} config set mcp_servers.{SERVER_NAME}.sampling.enabled false")
    print(f'\nThen check: {PROFILE}/logs/mcp-stderr.log and "registered 1 tool(s)" in {PROFILE}/logs/agent.log')
    return 1


if __name__ == '__main__':
    sys.exit(main())
