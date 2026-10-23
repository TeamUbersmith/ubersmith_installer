#!/usr/bin/env bash
set -euo pipefail

# Bumps the appliance and appliance_cron image tags in docker-compose.yml.
#
# Usage:
#   ./patch_appliance_php_cve.sh
#   ./patch_appliance_php_cve.sh -r r6   # override both version tables below

REGISTRY="ghcr.io/teamubersmith"
INSTALLER_INI="$HOME/.ubersmith_installer.ini"

# installed_version:release_version, one per line. Not a bash associative
# array: some hosts old enough to need this script also have a bash too old
# to support them (no declare -A before 4.0).
#
# appliance and appliance_cron are independently versioned -- their
# release numbers for the same installed_version are not always equal.
APPLIANCE_RELEASE_VERSIONS="
5.0.0:r14
5.0.3:r5
5.1.0:r12
5.1.1:r3
5.1.4:r4
5.3.0:r5
"

APPLIANCE_CRON_RELEASE_VERSIONS="
5.0.0:r14
5.0.3:r6
5.1.0:r12
5.1.1:r2
5.1.4:r4
5.3.0:r5
"

usage() {
  cat <<EOF
Usage: $0 [-r target_release_version]

  -r VERSION   Override both release_version tables above (e.g. r6)
EOF
}

TARGET_RELEASE_VERSION_OVERRIDE=""
while getopts ":r:h" opt; do
  case "$opt" in
    r) TARGET_RELEASE_VERSION_OVERRIDE="$OPTARG" ;;
    h) usage; exit 0 ;;
    *) usage; exit 1 ;;
  esac
done

if [ ! -f "$INSTALLER_INI" ]; then
  echo "Error: Ubersmith installer configuration file ($INSTALLER_INI) is not present, please run configure.sh" >&2
  exit 1
fi

if ! command -v curl > /dev/null 2>&1; then
  echo "Error: curl is not available" >&2
  exit 1
fi

# ini_get <section> <key>
ini_get() {
  local section="$1" key="$2"
  awk -v section="[$section]" -v key="$key" '
    $0 == section { insec = 1; next }
    /^\[/ { insec = 0 }
    insec {
      line = $0
      sub(/^[ \t]+/, "", line)
      if (line ~ "^" key "[ \t]*=") {
        sub("^" key "[ \t]*=[ \t]*", "", line)
        print line
        exit
      }
    }
  ' "$INSTALLER_INI"
}

APPLIANCE_INSTALLED_VERSION=$(ini_get ubersmith_installer appliance_installed_version)
APPLIANCE_HOME=$(ini_get ubersmith_installer appliance_home)

if [ -z "$APPLIANCE_INSTALLED_VERSION" ]; then
  echo "Error: appliance_installed_version is not set in $INSTALLER_INI" >&2
  exit 1
fi

if [ -z "$APPLIANCE_HOME" ]; then
  echo "Error: appliance_home is not set in $INSTALLER_INI" >&2
  exit 1
fi

compose() {
  docker compose "$@"
}

# target_release_version_for <table> <installed_version>
target_release_version_for() {
  local table="$1" installed_version="$2"
  if [ -n "$TARGET_RELEASE_VERSION_OVERRIDE" ]; then
    echo "$TARGET_RELEASE_VERSION_OVERRIDE"
    return
  fi
  printf '%s\n' "$table" | awk -F: -v v="$installed_version" '$1==v{print $2}'
}

# image_exists <repo> <tag> -- queries the registry API directly via curl
# rather than `docker manifest inspect`, whose auth/manifest-list handling
# is inconsistent across older Docker CLI versions.
image_exists() {
  local repo="$1" tag="$2"
  local host="${REGISTRY%%/*}" org="${REGISTRY#*/}"
  local token
  token=$(curl -fs "https://${host}/token?scope=repository:${org}/${repo}:pull" | sed -E 's/.*"token":"([^"]+)".*/\1/') || return 1
  [ -n "$token" ] || return 1
  local http_code
  http_code=$(curl -s -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer ${token}" \
    -H "Accept: application/vnd.docker.distribution.manifest.v2+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.oci.image.manifest.v1+json,application/vnd.oci.image.index.v1+json" \
    "https://${host}/v2/${org}/${repo}/manifests/${tag}")
  [ "$http_code" = "200" ]
}

confirm_image_published() {
  local repo="$1" new_tag="$2"
  echo "Confirming ${REGISTRY}/${repo}:${new_tag} is published..."
  if ! image_exists "$repo" "$new_tag"; then
    echo "Error: ${REGISTRY}/${repo}:${new_tag} does not exist" >&2
    exit 1
  fi
}

# bump_image_tag <compose_file> <repo> <new_tag>
# Rewrites the image reference for <repo> to $REGISTRY/<repo>:<new_tag>.
# Returns 0 if the file actually changed, 1 otherwise.
bump_image_tag() {
  local compose_file="$1" repo="$2" new_tag="$3"

  cp -p "$compose_file" "${compose_file}.bak-$(date +%Y%m%d%H%M%S)"

  local tmp
  tmp=$(mktemp)
  sed -E "s#^([[:space:]]*image:[[:space:]]*)[^[:space:]]*/${repo}:[^[:space:]]+\$#\1${REGISTRY}/${repo}:${new_tag}#" "$compose_file" > "$tmp"

  if cmp -s "$compose_file" "$tmp"; then
    rm -f "$tmp"
    return 1
  fi

  mv "$tmp" "$compose_file"
  return 0
}

# running_version_of <container_id> -- echoes "version release" (release
# may be empty) parsed from the container's image tag. The version is
# always a leading major.minor.revision; the release number is needed
# too, but only if present.
running_version_of() {
  local container_id="$1"
  local running_image running_tag version="" release=""
  running_image=$(docker inspect --format '{{.Config.Image}}' "$container_id")
  running_tag="${running_image##*:}"
  if [[ "$running_tag" =~ ^([0-9]+\.[0-9]+\.[0-9]+) ]]; then
    version="${BASH_REMATCH[1]}"
  fi
  if [[ "$running_tag" =~ -r([0-9]+)$ ]]; then
    release="r${BASH_REMATCH[1]}"
  fi
  echo "$running_image $version $release"
}

echo "=== Appliance (installed version: $APPLIANCE_INSTALLED_VERSION) ==="

appliance_target_release_version=$(target_release_version_for "$APPLIANCE_RELEASE_VERSIONS" "$APPLIANCE_INSTALLED_VERSION")
if [ -z "$appliance_target_release_version" ]; then
  echo "Error: No known PHP CVE patch for Appliance $APPLIANCE_INSTALLED_VERSION. Contact support@ubersmith.com." >&2
  exit 1
fi

appliance_cron_target_release_version=$(target_release_version_for "$APPLIANCE_CRON_RELEASE_VERSIONS" "$APPLIANCE_INSTALLED_VERSION")
if [ -z "$appliance_cron_target_release_version" ]; then
  echo "Error: No known PHP CVE patch for Appliance Cron $APPLIANCE_INSTALLED_VERSION. Contact support@ubersmith.com." >&2
  exit 1
fi

appliance_compose_file="${APPLIANCE_HOME}/docker-compose.yml"
if [ ! -f "$appliance_compose_file" ]; then
  echo "Error: $appliance_compose_file not found" >&2
  exit 1
fi

# Sanity check: the running containers' image versions should match
# appliance_installed_version. A mismatch means .ubersmith_installer.ini is
# stale (e.g. a manual upgrade that didn't update it), and we'd compute the
# wrong target tag.
app_web_container_id=$(cd "$APPLIANCE_HOME" && compose ps -q app_web) || true
if [ -z "$app_web_container_id" ]; then
  echo "Error: No running app_web container found; is the Appliance up?" >&2
  exit 1
fi
read -r running_web_image running_web_version running_web_release < <(running_version_of "$app_web_container_id")
if [ -n "$running_web_version" ] && [ "$running_web_version" != "$APPLIANCE_INSTALLED_VERSION" ]; then
  echo "Error: appliance_installed_version in $INSTALLER_INI ($APPLIANCE_INSTALLED_VERSION) does not match the running app_web container's image ($running_web_image). Refusing to patch; check for a stale .ubersmith_installer.ini." >&2
  exit 1
fi

app_cron_container_id=$(cd "$APPLIANCE_HOME" && compose ps -q app_cron) || true
if [ -z "$app_cron_container_id" ]; then
  echo "Error: No running app_cron container found; is the Appliance up?" >&2
  exit 1
fi
read -r running_cron_image running_cron_version running_cron_release < <(running_version_of "$app_cron_container_id")
if [ -n "$running_cron_version" ] && [ "$running_cron_version" != "$APPLIANCE_INSTALLED_VERSION" ]; then
  echo "Error: appliance_installed_version in $INSTALLER_INI ($APPLIANCE_INSTALLED_VERSION) does not match the running app_cron container's image ($running_cron_image). Refusing to patch; check for a stale .ubersmith_installer.ini." >&2
  exit 1
fi

# Refuse to downgrade either running release.
if [[ "$running_web_release" =~ ^r[0-9]+$ ]] && [[ "$appliance_target_release_version" =~ ^r[0-9]+$ ]]; then
  if [ "${running_web_release#r}" -gt "${appliance_target_release_version#r}" ]; then
    echo "Error: running app_web container is already $running_web_release, newer than the target $appliance_target_release_version. Refusing to downgrade." >&2
    exit 1
  fi
fi
if [[ "$running_cron_release" =~ ^r[0-9]+$ ]] && [[ "$appliance_cron_target_release_version" =~ ^r[0-9]+$ ]]; then
  if [ "${running_cron_release#r}" -gt "${appliance_cron_target_release_version#r}" ]; then
    echo "Error: running app_cron container is already $running_cron_release, newer than the target $appliance_cron_target_release_version. Refusing to downgrade." >&2
    exit 1
  fi
fi

appliance_new_tag="${APPLIANCE_INSTALLED_VERSION}-${appliance_target_release_version}"
appliance_cron_new_tag="${APPLIANCE_INSTALLED_VERSION}-${appliance_cron_target_release_version}"

confirm_image_published "appliance" "$appliance_new_tag"
confirm_image_published "appliance_cron" "$appliance_cron_new_tag"

echo "About to update appliance to $appliance_new_tag and appliance_cron to $appliance_cron_new_tag, then recreate app_web and app_cron."
read -r -p "Continue? [y/N] " confirm_patch
if [[ ! "$confirm_patch" =~ ^[Yy] ]]; then
  echo "Aborted; no changes made."
  exit 0
fi

appliance_changed=1
bump_image_tag "$appliance_compose_file" "appliance" "$appliance_new_tag" && appliance_changed=0 || true
appliance_cron_changed=1
bump_image_tag "$appliance_compose_file" "appliance_cron" "$appliance_cron_new_tag" && appliance_cron_changed=0 || true

if [ "$appliance_changed" -eq 0 ]; then
  echo "Updated appliance to $appliance_new_tag"
else
  echo "docker-compose.yml already references appliance:$appliance_new_tag (nothing to do)"
fi
if [ "$appliance_cron_changed" -eq 0 ]; then
  echo "Updated appliance_cron to $appliance_cron_new_tag"
else
  echo "docker-compose.yml already references appliance_cron:$appliance_cron_new_tag (nothing to do)"
fi

if [ "$appliance_changed" -eq 0 ] || [ "$appliance_cron_changed" -eq 0 ]; then
  (cd "$APPLIANCE_HOME" && compose pull app_web app_cron)
  (cd "$APPLIANCE_HOME" && compose rm -sf app_web app_cron)
  (cd "$APPLIANCE_HOME" && compose up -d --no-deps app_web app_cron)
fi
