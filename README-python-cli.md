# Ubersmith Installer (Python CLI) - Work in Progress

This directory (`ubersmith_installer/`) contains an in-progress, ground-up rewrite of the Ubersmith installer as a Python command-line application, replacing the existing Ansible-based playbooks under `roles/`. Docker and Docker Compose remain the runtime for Ubersmith itself; only the installation and configuration tooling is being reimplemented. The existing Ansible installer under `roles/` remains the supported installation method until this rewrite reaches parity.

## Usage

Requires Python 3.9+. On Debian/Ubuntu (and other distros following PEP 668), a plain `pip install`
outside a virtual environment fails with `error: externally-managed-environment` -- create a venv
first, the same way `install_ubersmith.sh` bootstraps one today:

```bash
python3 -m venv ~/.local/ubersmith_venv
source ~/.local/ubersmith_venv/bin/activate
```

Then, from the repo root, install the CLI (into that venv, or any other Python environment you
already manage):

```bash
pip install -e .
```

This installs an `ubersmith-installer` command with one subcommand per playbook. Run
`ubersmith-installer --help` for the full list, or `ubersmith-installer <command> --help` for any
individual command's flags. If you open a new shell later, re-run the `source .../activate` line
above before using `ubersmith-installer` again.

**Install Ubersmith** (interactively -- prompts for version, install directory, virtual host(s),
admin email, and whether to request a Let's Encrypt certificate, exactly like `install_ubersmith.sh`
does today):

```bash
ubersmith-installer install
```

Or fully non-interactively (e.g. for scripted/CI use -- all 5 values must be supplied):

```bash
ubersmith-installer install \
  --ubersmith-major-version 5 \
  --ubersmith-home /usr/local/ubersmith \
  --virtual-host ubersmith.example.com \
  --admin-email admin@example.org \
  --lets-encrypt-certificate yes \
  --non-interactive
```

**Upgrade an existing install** (reads its configuration from the state file `install` wrote, no
re-prompting for install-time values -- always upgrades to the current major-5 release):

```bash
ubersmith-installer upgrade
```

**Install the Ubersmith Appliance** (same shape as `install`, 3 prompts instead of 5):

```bash
ubersmith-installer install-appliance \
  --ubersmith-major-version 5 \
  --appliance-home /usr/local/ubersmith \
  --app-virtual-host appliance.example.com \
  --non-interactive
```

**Upgrade an existing Appliance install:**

```bash
ubersmith-installer upgrade-appliance
```

Other commands, run against an existing install/appliance (reading the same state file):
`ubersmith-installer configure`, `retry-letsencrypt`, `add-brand`, and `patch` -- see each one's
`--help` for its specific flags.

Every command accepts `--state-file PATH` (default `~/.ubersmith_installer.ini`, the same file and
format the Ansible tool reads/writes -- an existing Ansible-managed install can be upgraded by this
CLI unmodified) and `--skip-preflight` (skip OS/Docker version checks, mainly for testing in
non-standard environments). `install`/`install-appliance` also accept `--dry-run`, which renders
config and writes state for real but skips Docker/network/service side effects.

## Status: Phase 3 complete -- every playbook has a Python CLI equivalent

`ubersmith-installer install` is a real, working installer: it runs OS/Docker preflight checks, gathers the same 5 install-time values as `install_ubersmith.yml`'s `vars_prompt` (interactively by default, or non-interactively via flags), generates/reuses MySQL passwords and self-signed certificates, renders every fresh-install config template, creates the Ubersmith directory tree, stops/disables local MTAs, sets the systemd journal retention policy, pulls images and brings up containers via `docker compose`, backs up the MySQL keyring, optionally requests Let's Encrypt certificates (installing a daily renewal cron task), and writes the installer state file -- reaching parity with `install_ubersmith.yml` + `roles/common` + every fresh-install-scope task (i.e. every task NOT tagged `upgrade_only`) in `roles/ubersmith/tasks/main.yml`. See `ubersmith-installer install --help` for the full flag reference, and `.github/workflows/test-install-python-cli.yml` for an end-to-end CI run against a real Docker daemon.

`ubersmith-installer upgrade` reaches parity with `upgrade_ubersmith.yml -t upgrade,upgrade_only`: it reads configuration from the installer state file written by a prior install (never re-prompting), always targets the current major-5 release (matching `upgrade_ubersmith.yml`'s hardcoded `ubersmith_major_version: "5"`), runs the pre-upgrade/license/compose-override reminders, cleans up legacy `patch_ubersmith.sh` artifacts, migrates the redis volume where needed, runs version-gated migrations (e.g. `caching_sha2_password`, a defensive `sql_mode` fixup), re-renders every upgrade-tagged template, applies narrow in-place fixups to the existing `docker-compose.override.yml`, cycles containers through maintenance mode, runs `updatedb.php`, backs up the MySQL keyring, prunes old images, and writes back the updated state. **Critically, it never wholesale re-renders `docker-compose.override.yml`, the apache virtual host config, `rwhois`, or `ubersmith.ini`** -- those are install-only templates that may contain customer hand-edits, and clobbering them would be a real data-loss bug; this constraint is explicitly tested. See `ubersmith-installer upgrade --help`, and `.github/workflows/test-upgrade-python-cli.yml` for an end-to-end CI run that installs Ubersmith via the *original Ansible tool*, hand-customizes `docker-compose.override.yml` the way a real admin would, then upgrades via this CLI and verifies that customization survives verbatim.

The remaining playbooks are now covered too:

- `ubersmith-installer configure` -- reconfigure `ubersmith_home`/`virtual_host`/`admin_email` for an existing install, mirroring `configure.yml`.
- `ubersmith-installer retry-letsencrypt` -- re-request Let's Encrypt certificates via the webroot method (site already serving, unlike install's standalone method) and reload Apache, mirroring `retry_letsencrypt.yml`.
- `ubersmith-installer add-brand` -- add a new virtual host to an existing install: generates its self-signed cert + apache vhost config, then requests Let's Encrypt certificates for the full combined host list, mirroring `add_new_brand.sh`'s two-playbook sequence.
- `ubersmith-installer patch` -- fetch and apply an official Ubersmith patch release from GitHub, mirroring `patch_ubersmith.yml` as actually invoked (`patch_ubersmith.sh` always passes `--skip-tags remove_patches`, so this warns rather than destructively clearing prior patch state).
- `ubersmith-installer install-appliance` / `ubersmith-installer upgrade-appliance` -- full install/upgrade parity for the Ubersmith Appliance product, mirroring `install_appliance.yml`/`upgrade_appliance.yml` + `roles/appliance`, including the MySQL 5.6/5.7-origin step-up migration (gated on the actual running database image, not any version-label metadata -- see `ubersmith_installer/appliance_ops.py`) and the same install-only-template exclusion as `upgrade`. See `.github/workflows/test-install-appliance-python-cli.yml` and `.github/workflows/test-upgrade-appliance-python-cli.yml` (the latter exercises a genuine 4.x -> 5.x appliance upgrade, seeded via the real Ansible tool).

**Known issue:** the appliance upgrade path has one open gap, tracked in [issue #36](https://github.com/TeamUbersmith/ubersmith_installer/issues/36) -- `app_db` isn't guaranteed a clean InnoDB shutdown before it's stopped/replaced during upgrade, which can cause the MySQL step-up migration to fail with an "Upgrade is not supported after a crash or shutdown" error. This needs real MySQL/InnoDB shutdown-sequencing work, not a quick logic fix, and is left open rather than papered over.

**Not yet implemented / next up:** Phase 4 (a deprecation timeline for the Ansible playbooks, once real-world confidence is high) and the optional, low-priority Phase 5 (Rich-based progress bars/InquirerPy prompts for the interactive UX -- explicitly scoped to never affect non-interactive/CI output).
