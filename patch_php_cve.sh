#!/usr/bin/env bash
set -euo pipefail

# Targeted security patch for a PHP CVE (e.g. OPS-862).
#
# Unlike upgrade_ubersmith.sh, this does NOT re-render docker-compose.yml from
# the role templates (that would re-template every service and implicitly
# require an ubersmith_version bump). It only bumps the image tag on the
# services that are actually built on a php8X base, leaving web/db/redis/solr/
# backup/rsyslog/etc. exactly as they are.
#
# Deliberately pure bash/sed/grep/docker, no ansible and no python -- this may
# be run on hosts whose Python is too old for the installer's venv
# (find_python.sh requires 3.11+), which is exactly the kind of host most
# likely to need an urgent out-of-band CVE patch.
#
# Services touched, and why:
#   ubersmith docker-compose.yml: php, mail, cron, rwhois (image repo: xinetd)
#
# All of mail/cron/xinetd are hardcoded to build FROM whichever php8X image
# this host's "php" service uses -- see docker/{mail,cron,xinetd}/Dockerfile
# in the containers repo.
#
# The appliance stack (app_cron / appliance_cron) is handled separately, not
# by this script.
#
# Usage:
#   ./patch_php_cve.sh
#   ./patch_php_cve.sh -r r6   # override the version table below

REGISTRY="ghcr.io/teamubersmith"
INSTALLER_INI="$HOME/.ubersmith_installer.ini"

# Last known-good containers.release_version per ubersmith_release_version.
# Keep this updated as each line is actually rebuilt and pushed -- a host
# whose installed version isn't a key here will fail cleanly below rather
# than guessing.
declare -A PHP_CVE_RELEASE_VERSIONS=(
  ["4.6.1"]="r2"
  ["4.6.2"]="r12"
  ["4.6.3"]="r5"
  ["4.6.4"]="r5"
  ["5.0.0"]="r14"
  ["5.0.1"]="r4"
  ["5.0.2"]="r2"
  ["5.0.3"]="r6"
  ["5.1.0"]="r12"
  ["5.1.1"]="r2"
  ["5.1.2"]="r3"
  ["5.1.3"]="r3"
  ["5.1.4"]="r4"
  ["5.2.0"]="r4"
  ["5.2.1"]="r3"
  ["5.2.2"]="r4"
  ["5.2.3"]="r3"
  ["5.3.0"]="r5"
)

# Image repos that are built on a php8X base and need the same bump as
# whichever php8X image this host's "php" service uses.
PHP_BASED_IMAGE_REPOS=(mail cron xinetd)

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

# ini_get <section> <key> -- reads a "key = value" line from a [section] block
# in $INSTALLER_INI (the same file community.general.ini_file writes).
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

UBERSMITH_INSTALLED_VERSION=$(ini_get ubersmith_installer ubersmith_installed_version)
UBERSMITH_HOME=$(ini_get ubersmith_installer ubersmith_home)

if [ -z "$UBERSMITH_INSTALLED_VERSION" ]; then
  echo "Error: ubersmith_installed_version is not set in $INSTALLER_INI" >&2
  exit 1
fi

target_release_version_for() {
  local installed_version="$1"
  if [ -n "$TARGET_RELEASE_VERSION_OVERRIDE" ]; then
    echo "$TARGET_RELEASE_VERSION_OVERRIDE"
  else
    echo "${PHP_CVE_RELEASE_VERSIONS[$installed_version]:-}"
  fi
}

confirm_images_published() {
  local new_tag="$1"
  shift
  local repo
  for repo in "$@"; do
    echo "Confirming ${REGISTRY}/${repo}:${new_tag} is published..."
    if ! docker manifest inspect "${REGISTRY}/${repo}:${new_tag}" > /dev/null; then
      echo "Error: ${REGISTRY}/${repo}:${new_tag} does not exist" >&2
      exit 1
    fi
  done
}

# bump_image_tag <compose_file> <new_tag> <repo1> [<repo2> ...]
# Always backs up the compose file first (matching the previous ansible
# behavior), then rewrites only image: lines for the given repos under
# $REGISTRY. Returns 0 if the file actually changed, 1 otherwise.
bump_image_tag() {
  local compose_file="$1" new_tag="$2"
  shift 2
  local repos=("$@")
  local pattern
  pattern=$(printf '%s|' "${repos[@]}")
  pattern="${pattern%|}"

  local escaped_registry
  escaped_registry=$(printf '%s' "$REGISTRY" | sed -e 's/[.[\*^$()+?{|]/\\&/g')

  cp -p "$compose_file" "${compose_file}.bak-$(date +%Y%m%d%H%M%S)"

  local tmp
  tmp=$(mktemp)
  sed -E "s#^([[:space:]]*image:[[:space:]]*${escaped_registry}/(${pattern}):)[^[:space:]]+\$#\1${new_tag}#" "$compose_file" > "$tmp"

  if cmp -s "$compose_file" "$tmp"; then
    rm -f "$tmp"
    return 1
  fi

  mv "$tmp" "$compose_file"
  return 0
}

echo "=== Ubersmith stack (installed version: $UBERSMITH_INSTALLED_VERSION) ==="

ubersmith_target_release_version=$(target_release_version_for "$UBERSMITH_INSTALLED_VERSION")
if [ -z "$ubersmith_target_release_version" ]; then
  echo "Error: No known PHP CVE patch release_version for ubersmith_installed_version $UBERSMITH_INSTALLED_VERSION. Either this line hasn't been patched yet, or it's out of scope for this script. Contact support@ubersmith.com, or re-run with -r rN once you've confirmed the correct value." >&2
  exit 1
fi

ubersmith_compose_file="${UBERSMITH_HOME}/docker-compose.yml"
php_repo=$(grep -oE "${REGISTRY}/php[0-9]+:" "$ubersmith_compose_file" | head -1 | sed -E 's#.*/(php[0-9]+):#\1#')
if [ -z "$php_repo" ]; then
  echo "Error: Could not find a ${REGISTRY}/phpNN image in $ubersmith_compose_file" >&2
  exit 1
fi

ubersmith_new_tag="${UBERSMITH_INSTALLED_VERSION}-${ubersmith_target_release_version}"
ubersmith_image_repos=("$php_repo" "${PHP_BASED_IMAGE_REPOS[@]}")

confirm_images_published "$ubersmith_new_tag" "${ubersmith_image_repos[@]}"

if bump_image_tag "$ubersmith_compose_file" "$ubersmith_new_tag" "${ubersmith_image_repos[@]}"; then
  echo "Updated ${ubersmith_image_repos[*]} to $ubersmith_new_tag"
  (cd "$UBERSMITH_HOME" && docker compose pull php mail cron rwhois)
  (cd "$UBERSMITH_HOME" && docker compose up -d --no-deps php mail cron rwhois)
else
  echo "docker-compose.yml already references $ubersmith_new_tag (nothing to do)"
fi
