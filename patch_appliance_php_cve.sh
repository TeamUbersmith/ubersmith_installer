#!/usr/bin/env bash
set -euo pipefail

# Bumps the appliance image tag in docker-compose.yml.
#
# Usage:
#   ./patch_appliance_php_cve.sh
#   ./patch_appliance_php_cve.sh -r r6   # override the version table below

REGISTRY="ghcr.io/teamubersmith"
INSTALLER_INI="$HOME/.ubersmith_installer.ini"
APPLIANCE_REPO="appliance"

# installed_version:release_version, one per line. Not a bash associative
# array: some hosts old enough to need this script also have a bash too old
# to support them (no declare -A before 4.0).
APPLIANCE_PHP_CVE_RELEASE_VERSIONS="
5.0.0:r14
5.0.3:r5
5.1.0:r12
5.1.1:r3
5.1.4:r4
5.3.0:r5
"

usage() {
  cat <<EOF
Usage: $0 [-r target_release_version]

  -r VERSION   Override the release_version table above (e.g. r6)
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

target_release_version_for() {
  local installed_version="$1"
  if [ -n "$TARGET_RELEASE_VERSION_OVERRIDE" ]; then
    echo "$TARGET_RELEASE_VERSION_OVERRIDE"
    return
  fi
  printf '%s\n' "$APPLIANCE_PHP_CVE_RELEASE_VERSIONS" | awk -F: -v v="$installed_version" '$1==v{print $2}'
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

echo "=== Appliance (installed version: $APPLIANCE_INSTALLED_VERSION) ==="

appliance_target_release_version=$(target_release_version_for "$APPLIANCE_INSTALLED_VERSION")
if [ -z "$appliance_target_release_version" ]; then
  echo "Error: No known PHP CVE patch for Appliance $APPLIANCE_INSTALLED_VERSION. Contact support@ubersmith.com." >&2
  exit 1
fi

appliance_compose_file="${APPLIANCE_HOME}/docker-compose.yml"
if [ ! -f "$appliance_compose_file" ]; then
  echo "Error: $appliance_compose_file not found" >&2
  exit 1
fi

# Sanity check: the running app_web container's image version should match
# appliance_installed_version. A mismatch means .ubersmith_installer.ini is
# stale (e.g. a manual upgrade that didn't update it), and we'd compute the
# wrong target tag.
app_web_container_id=$(cd "$APPLIANCE_HOME" && compose ps -q app_web) || true
if [ -z "$app_web_container_id" ]; then
  echo "Error: No running app_web container found; is the Appliance up?" >&2
  exit 1
fi
running_image=$(docker inspect --format '{{.Config.Image}}' "$app_web_container_id")
running_tag="${running_image##*:}"
# The version is always a leading major.minor.revision; the release number
# is needed too, but only if present.
running_version=""
running_release=""
if [[ "$running_tag" =~ ^([0-9]+\.[0-9]+\.[0-9]+) ]]; then
  running_version="${BASH_REMATCH[1]}"
fi
if [[ "$running_tag" =~ -r([0-9]+)$ ]]; then
  running_release="r${BASH_REMATCH[1]}"
fi
if [ -n "$running_version" ] && [ "$running_version" != "$APPLIANCE_INSTALLED_VERSION" ]; then
  echo "Error: appliance_installed_version in $INSTALLER_INI ($APPLIANCE_INSTALLED_VERSION) does not match the running app_web container's image ($running_image). Refusing to patch; check for a stale .ubersmith_installer.ini." >&2
  exit 1
fi

# Refuse to downgrade the running release.
if [[ "$running_release" =~ ^r[0-9]+$ ]] && [[ "$appliance_target_release_version" =~ ^r[0-9]+$ ]]; then
  if [ "${running_release#r}" -gt "${appliance_target_release_version#r}" ]; then
    echo "Error: running app_web container is already $running_tag, newer than the target $APPLIANCE_INSTALLED_VERSION-$appliance_target_release_version. Refusing to downgrade." >&2
    exit 1
  fi
fi

appliance_new_tag="${APPLIANCE_INSTALLED_VERSION}-${appliance_target_release_version}"

confirm_image_published "$APPLIANCE_REPO" "$appliance_new_tag"

echo "About to update $APPLIANCE_REPO to $appliance_new_tag and recreate app_web."
read -r -p "Continue? [y/N] " confirm_patch
if [[ ! "$confirm_patch" =~ ^[Yy] ]]; then
  echo "Aborted; no changes made."
  exit 0
fi

if bump_image_tag "$appliance_compose_file" "$APPLIANCE_REPO" "$appliance_new_tag"; then
  echo "Updated $APPLIANCE_REPO to $appliance_new_tag"
  (cd "$APPLIANCE_HOME" && compose pull app_web)
  (cd "$APPLIANCE_HOME" && compose rm -sf app_web)
  (cd "$APPLIANCE_HOME" && compose up -d --no-deps app_web)
else
  echo "docker-compose.yml already references $appliance_new_tag (nothing to do)"
fi
