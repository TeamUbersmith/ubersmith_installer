# AGENTS.md — handoff notes for the `python-cli` rewrite

This file is for any agent (AI or human) picking up work on `ubersmith_installer/`, the in-progress
Python CLI rewrite of this repo's Ansible-based installer. Read this before making changes.

## What this is

A ground-up rewrite of the Ubersmith/Ubersmith-Appliance installer as a Python CLI
(`ubersmith-installer`), replacing the Ansible playbooks under `roles/` and the top-level `*.yml` /
`*.sh` files. Docker and Docker Compose remain the runtime — only the installation/configuration
tooling is being reimplemented. The Ansible path stays supported and untouched until Phase 4
(deprecation) is deliberately started; do not delete or "clean up" Ansible files as part of Python
CLI work.

**Full history, architecture rationale, and phase-by-phase decisions live in the plan document:**
`/Users/mstyne/.claude/plans/plan-a-github-actions-piped-curry.md` (yes, the filename is unrelated to
the content — it's just where the plan tool created it). Read it first for context before reading
this file's summary. It documents: why Ansible was replaced, the target architecture, the full phase
breakdown (0-5), every deviation from literal Ansible parity and why, and the currently-known open
issues. **Keep it updated** as you make further architectural decisions or find new issues — it is
the single source of truth for "why is it built this way," not just a historical log.

## Branch and workflow

- All Python CLI work happens on the `python-cli` branch. Never commit this work directly to `master`.
- This has been built phase-by-phase (Phase 0: scaffold, Phase 1: install parity, Phase 2: upgrade
  parity, Phase 3: remaining playbooks + appliance role). Each phase: build the modules, integrate
  them into `cli.py`, add/update CI workflows, then run an independent validation pass before
  considering the phase done — and *actually push and watch the real CI workflows run*, not just
  local test suite green. Several real bugs in this codebase were only found by watching real CI runs
  fail, not by local testing (see "Known issues" below and the plan doc for details).
- Commit frequently with detailed messages explaining *why*, not just what. Push after each
  meaningful change and check CI status before moving on. Never force-push, never skip hooks, never
  amend published commits.

## Module layout

```
ubersmith_installer/
  cli.py                  # click CLI entry point -- all subcommands
  preflight.py             # OS/Docker version/reachability checks
  state.py                 # ~/.ubersmith_installer.ini reader/writer (byte-compatible with Ansible's)
  templates.py              # Jinja2 rendering for all install/upgrade templates (ubersmith + appliance)
  secrets.py                # MySQL/appliance password generation, Ansible lookup('password')-compatible
  certs.py                  # self-signed certificate generation
  docker_ops.py             # ubersmith role's container/filesystem lifecycle operations
  appliance_ops.py          # appliance role's container/filesystem lifecycle operations
  mta.py                    # stop/disable local mail transfer agents
  prompts.py                 # interactive prompts, vars_prompt-equivalent UX
  certbot.py                 # Let's Encrypt request flow (install-time, standalone method)
  retry_letsencrypt.py        # Let's Encrypt retry flow (webroot method, site already serving)
  system_config.py            # systemd journald retention config
  migrations.py               # ubersmith role's upgrade_only version-gated migrations
  patch_cleanup.py             # upgrade-time legacy .patched cleanup (interactive-gated)
  patch_apply.py               # `patch` command's GitHub-release-fetch-and-apply flow
  redis_migration.py           # ubersmith role's redis volume migration dance
  compose_override.py          # ubersmith role's docker-compose.override.yml legacy fixups
  configure_state.py           # `configure` command's reconfiguration logic
  templates/                  # copied .j2 templates (appliance ones get an `appliance-` prefix
                               # where a name would collide with a ubersmith-role template)
  files/                      # copied static files (restart/start scripts, falco rules, mysql
                               # component config, appliance backup script, etc.)
tests/
  test_*.py                   # one file per module, mirroring the layout above
```

Every module follows the same conventions — read an existing one (`docker_ops.py` is the most
mature) before adding a new one:

- **Injectable I/O everywhere**: every function that touches Docker, subprocess, the network, or
  `time.sleep` takes an optional parameter for it (`client=None` defaulting to `docker.from_env()`,
  `runner=None` defaulting to a real `subprocess.run` wrapper, etc.). This is what makes the whole
  test suite runnable without a real Docker daemon.
- **Docstrings cite the exact Ansible task mirrored** — task name and approximate line number in the
  relevant `roles/*/tasks/main.yml`, e.g. `Mirrors the "Pull required images" task (~line 13)`. This
  is not decoration — it's how the next agent (or you, later) verifies a function is still faithful
  to the source after either side changes.
- **Deliberate deviations from literal parity are called out explicitly**, in both the docstring and
  usually a dedicated comment at the call site, explaining *why* the deviation exists. Silent
  deviations are the single biggest risk in this codebase (see "Critical correctness constraint"
  below) — never let one slip in undocumented.

## Critical correctness constraint — read this before touching `upgrade`/`upgrade_appliance`

`docker-compose.override.yml`, the apache virtual host config (`instance_vhost.j2`/
`appliance_vhost.j2`), `rwhois.j2`, and `ubersmith.ini.j2` are **install-only templates** — their
Ansible tasks carry no `upgrade`/`upgrade_only` tag, meaning the real tool never re-renders them
during an upgrade because they may contain customer hand-edits. **The `upgrade` and
`upgrade_appliance` commands must never call `templates.render_docker_compose_override()`,
`render_instance_vhost()`/`render_appliance_vhost()`, `render_rwhois()`, or `render_ubersmith_ini()`.**
Only narrow, in-place text fixups (`compose_override.py` / the appliance equivalent) touch the
existing override file. This has been the single highest-severity risk class found in this codebase
(real customer data loss if violated) and is explicitly tested in both `tests/test_cli_upgrade.py`
and `tests/test_cli_appliance.py` — any change to the upgrade commands must keep those assertions
passing, not just work around them.

More generally: when the Ansible source has tasks tagged plain `upgrade` (not `upgrade_only`), those
run during BOTH install and upgrade and their Python equivalents should be *reused*, not
reimplemented, across both commands. When you're unsure whether a task runs during install, upgrade,
both, or neither, **derive it from the actual tags in the Ansible source** (`grep` the task's `tags:`
block) — don't assume based on the task's name or a prior agent's summary. This exact mistake (an
untested assumption about which tasks are "install-only" vs "also-runs-on-upgrade") is how the
override-file risk was almost gotten wrong more than once during this build.

## User preferences (confirmed across this session)

- **Faithful port by default, but bugs found in the Ansible source get fixed, not silently
  reproduced** — when a real defect in the *original* Ansible role is found (not just something the
  Python port got wrong), ask before deciding whether to fix it or preserve it for parity. Three real
  upstream bugs were found and fixed this way in Phase 2/3 (see the plan doc's "Known issue" section
  and closed issues for full writeups): a destructive `patch` cleanup that didn't match real-world
  `--skip-tags` usage, a dead `sql_mode` safety net, and an ini-tracking bug that silently defeated a
  MySQL migration for every real customer appliance.
- **Independent verification, always** — don't trust a sub-agent's or workflow's self-reported "tests
  pass" / "this works." Re-run the full test suite yourself in a fresh venv after any batch of changes
  lands. Build and install from a real (non-editable) wheel periodically, not just an editable
  checkout — this caught a real packaging bug (templates/files not shipped in a built wheel) that
  editable-install testing alone would never surface.
- **Push and watch real CI, don't stop at local green** — several real bugs (interactive-mode hangs,
  the MySQL step-up gating bugs, the InnoDB clean-shutdown gap) were only found by pushing to
  `python-cli` and watching the actual GitHub Actions runs fail. Local `pytest` passing is necessary
  but not sufficient evidence a phase is done.
- **When CI fails and the cause isn't obvious from the first log read, add diagnostic logging and
  re-run rather than guessing repeatedly** — get real data from the actual environment before
  hypothesizing further. This was explicitly asked for and used successfully to root-cause the
  appliance mysql step-up gating bug.
- **Known issues get opened as GitHub issues, not just left as code comments** — when something is
  found but deliberately not fixed in the moment (too deep, needs different expertise, out of
  scope), open a tracked issue (see #36) and reference it from both the code comment and the plan doc,
  rather than letting it become an untracked TODO.
- **`README-python-cli.md` must be kept current every phase** — it was allowed to go stale (still
  said "Phase 2 complete" after Phase 3 landed) and had to be caught and fixed retroactively. Update
  it as part of finishing a phase, not as an afterthought.
- **CI output must stay "plain jane"** — no Rich/InquirerPy rendering (progress bars, colored/ANSI
  output, interactive widgets) in non-interactive mode. This is an explicit, hard requirement for the
  planned Phase 5 visual-polish work: gate any Rich/InquirerPy usage on the command's actual
  `--non-interactive` flag, not just a TTY check (CI runners can present a pty in some
  configurations), so CI logs stay parseable and clean.
- Ask before any destructive/judgment-call decision (reproducing vs. fixing an upstream bug, force
  operations, etc.) rather than assuming. This user is engaged and responsive — use `AskUserQuestion`
  for real forks in direction rather than guessing silently.

## Unit testing requirements

- **Every new function that touches Docker, subprocess, the filesystem, or the network needs a unit
  test with all of that mocked** — no test should require a real Docker daemon, real network access,
  or real credentials to pass. Use `tmp_path` for filesystem tests, `MagicMock()`/`monkeypatch` for
  Docker clients and subprocess runners.
- **Full suite must pass before every commit** — run `pip install -e ".[dev]"` in a fresh venv, then
  `pytest -v`, and confirm the count only goes up (never down) unless you're deliberately replacing a
  test (e.g. because a destructive-cleanup test was replaced with a warn-only one — document why in
  the commit message when this happens).
- **CLI-level integration tests belong alongside unit tests** — `tests/test_cli_install.py`,
  `tests/test_cli_upgrade.py`, `tests/test_cli_appliance.py`, `tests/test_cli_remaining_commands.py`
  use Click's `CliRunner` with every side-effecting call mocked, to prove the commands wire the
  underlying modules together correctly (argument order, call sequencing, etc.) without needing real
  infrastructure. When you add a new side-effecting call to an existing command, you MUST add it to
  that command's test mock helper too, or the test will silently start exercising the real
  Docker/subprocess/network call — this has caused real test hangs in this session (a test
  accidentally invoking the real local Docker daemon because a newly-added call wasn't mocked).
- **The true end-to-end proof is the GitHub Actions workflows**, not the unit tests:
  `test-install-python-cli.yml`, `test-upgrade-python-cli.yml`,
  `test-install-appliance-python-cli.yml`, `test-upgrade-appliance-python-cli.yml`. These run the
  real CLI against a real Docker daemon on a GitHub-hosted runner, and the upgrade ones seed a real
  install via the *actual, unmodified Ansible tool* first — this is what actually validates the
  compatibility promise this whole rewrite depends on. Before declaring a phase "done," these must
  have been pushed and watched to a real pass/fail result, not just written and assumed correct.
- Before picking an image/version to seed a CI fixture with (e.g. "install major version 4, then
  upgrade"), verify with `docker manifest inspect <image>:<tag>` that the image actually still exists
  in the registry — old tags do get removed (this happened with the ubersmith role's `redis7`
  major-4 image), and a workflow built against a since-removed image will fail for reasons that have
  nothing to do with your code.

## Documentation requirements

- Module and function docstrings must name the exact Ansible task(s) being mirrored (see "Module
  layout" conventions above) — this is the primary way correctness gets re-verified later.
- Any deliberate deviation from literal Ansible parity needs a comment at the point of deviation
  explaining what the Ansible source does, why this codebase does something different, and (if
  relevant) why that's safe/correct. "Trust me" comments without the actual reasoning are not
  sufficient — a future agent needs enough context to re-derive whether the deviation is still valid
  after the Ansible source changes.
- `README-python-cli.md` is the user-facing status document — keep its "Status" section current with
  which phase just landed, what each new command does in a sentence or two, and a "known issues"
  callout for anything still open (with a link to the tracking GitHub issue).
- The plan doc (see top of this file) is the internal architecture/decision log — update it when you
  make a new architectural call, discover a new correctness constraint, or close out a phase. Don't
  let it go stale the way `README-python-cli.md` briefly did.

## Current status (as of Phase 3 completion)

- **Done**: Phases 0-3. `install`, `upgrade`, `install-appliance`, `upgrade-appliance`, `configure`,
  `retry-letsencrypt`, `add-brand`, `patch` are all implemented, tested, and validated against real
  CI (see the workflow list above). 310 unit tests passing.
- **Known open issue**: [#36](https://github.com/TeamUbersmith/ubersmith_installer/issues/36) —
  `appliance_ops.stop_containers()` doesn't guarantee a clean InnoDB shutdown before `app_db` is
  stopped/replaced during `upgrade-appliance`, which can make the mysql step-up migration fail.
  Needs real MySQL/InnoDB shutdown-sequencing work (e.g. an explicit in-container `mysqladmin
  shutdown` with grace time, verified via the server log, before the container is stopped). This is
  the next concrete thing to fix if picking up appliance-upgrade work.
- **Not started**: Phase 4 (deprecation timeline for the Ansible playbooks — a change-management
  phase, not a coding phase; don't start it without explicit direction, since it involves decisions
  about customer-facing timelines) and Phase 5 (optional Rich/InquirerPy visual polish, see the
  "plain jane CI" constraint above).
