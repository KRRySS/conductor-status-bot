# Changelog

## 0.3.0

Launch no longer depends on anything in the user's preserved `config.yaml` beyond one
stable path.

- **`scripts/launch.py`** (new, stdlib-only, distribution-owned): `config.yaml` runs
  `python3 <profile>/skills/conductor-status/scripts/launch.py`; the launcher finds `uv`
  (PATH, then `~/.local/bin`, `~/.cargo/bin`, `~/.brew/bin`, Homebrew, `/usr/local/bin`),
  drops a stale `CONDUCTOR_STATUS_HOME`, and starts `server.py` with the adjacent lockfile.
  Every future change to how the server starts ships in this file and lands on update.
- **`scripts/doctor.py`** (new, read-only, stdlib-only): detects the 0.1.x/0.2.x config shapes
  (`${HERMES_HOME}`, `CONDUCTOR_STATUS_HOME`, `--locked`, wrong `--name` path) and prints
  the exact `~/.local/bin/hermes -p <profile> config ...` commands. Exit 0/1/2.
- **SKILL.md**: correct tool name `mcp__conductor_status__conductor_status` (double
  underscores; 0.2.0 said `mcp_conductor_status_conductor_status`); `<profile>/local/...`
  instead of `$HERMES_HOME/local/...`; troubleshooting covers the launcher, the desktop
  app's embedded CLI (`version unknown`) and the tool-name-from-log rule.
- **README**: commands use `~/.local/bin/hermes`; states that `profile update` must run
  from an external terminal; "Upgrading from 0.1.x / 0.2.x" is one doctor run.
- Tests: three end-to-end scenarios through Hermes' own MCP client and tool dispatcher —
  fresh install under `--name`, update over a preserved 0.1.0 config, and the desktop /
  multiplex shape (launch home = default, profile bound by context override) — plus
  no-duplicate-server and tool-payload assertions. 20 tests.
- Unchanged: `mcp==2.2.0`, `server.py.lock`, read-only contract, no tool arguments,
  `include_events` default `false`, `sampling.enabled: false`, local JSON schema.

### Why not an Agent Plugin (`mcp.json` + `${PLUGIN_ROOT}`)

Checked against Hermes 0.21.3 source. `${PLUGIN_ROOT}` would solve the path problem, but:

1. Plugins are **opt-in via `plugins.enabled` in `config.yaml`**
   (`hermes_cli/plugins_discovery.py::gate_manifest`: `enabled is None or not names & enabled`
   → placeholder). That key lives in the same preserved file, so an update could not enable
   the plugin either — the exact lesson 2 problem, moved.
2. `distribution_owned` / `DEFAULT_DIST_OWNED` (`hermes_cli/profile_distribution.py`)
   does not include `plugins/`; the installer copies declared paths, but nothing in the
   distribution flow enables them.
3. The registered tool name would be
   `mcp__agent_plugin_conductor_status_57a1009d__conductor__c5ec9669` — the portable
   namespace (`plugins_manifest._portable_skill_namespace`) pushes it past the 64-char limit
   and `mcp_prefixed_tool_name` hash-clamps it. Opaque in SKILL.md and in logs.
4. The user's existing `mcp_servers.conductor_status` entry would still be in `config.yaml`,
   so both a native and a portable server would start (`_portable_mcp_servers` merges after
   native; native wins on a name clash, but the names differ).

The launcher achieves the same "nothing launch-critical lives in the preserved file"
property with one stable path, no opt-in flag, the readable tool name, and no duplicate.

## 0.2.0

- `config.yaml`: path anchored on `${userHome}/.hermes/profiles/<name>`, not
  `${HERMES_HOME}`; `CONDUCTOR_STATUS_HOME` and `--locked` removed.
- `server.py`: mcp 1.26.0 → 2.2.0 (`add_request_handler` replaces the removed decorators);
  profile root from own file location.
- Multiplex-shaped config-load test; SDK 2.x client attribute names.

## 0.1.0

Initial release: local stdio MCP, read-only DB+WAL snapshot, opt-in event excerpts,
best-effort redaction, pinned dependencies, Hermes profile distribution.
