#!/usr/bin/env bash
set -euo pipefail

# Bumps the php/mail/cron/rwhois image tags in docker-compose.yml.
#
# Usage:
#   ./patch_php_cve.sh
#   ./patch_php_cve.sh -r r6   # override the version table below

REGISTRY="ghcr.io/teamubersmith"
INSTALLER_INI="$HOME/.ubersmith_installer.ini"

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

UBERSMITH_INSTALLED_VERSION=$(ini_get ubersmith_installer ubersmith_installed_version)
UBERSMITH_HOME=$(ini_get ubersmith_installer ubersmith_home)

if [ -z "$UBERSMITH_INSTALLED_VERSION" ]; then
  echo "Error: ubersmith_installed_version is not set in $INSTALLER_INI" >&2
  exit 1
fi

# compose <args...> -- 4.6.x hosts use the legacy docker-compose binary;
# everything newer uses the docker compose plugin.
if [[ "$UBERSMITH_INSTALLED_VERSION" == 4.6.* ]]; then
  if ! command -v docker-compose > /dev/null 2>&1; then
    echo "Error: ubersmith_installed_version is $UBERSMITH_INSTALLED_VERSION but docker-compose is not available" >&2
    exit 1
  fi
  COMPOSE=(docker-compose)
else
  COMPOSE=(docker compose)
fi
compose() {
  "${COMPOSE[@]}" "$@"
}

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
# Returns 0 if the file actually changed, 1 otherwise.
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

echo "=== Ubersmith (installed version: $UBERSMITH_INSTALLED_VERSION) ==="

ubersmith_target_release_version=$(target_release_version_for "$UBERSMITH_INSTALLED_VERSION")
if [ -z "$ubersmith_target_release_version" ]; then
  echo "Error: No known PHP CVE patch for Ubersmith $UBERSMITH_INSTALLED_VERSION. Contact support@ubersmith.com." >&2
  exit 1
fi

ubersmith_compose_file="${UBERSMITH_HOME}/docker-compose.yml"
php_repo=$(grep -oE "${REGISTRY}/php[0-9]+:" "$ubersmith_compose_file" | head -1 | sed -E 's#.*/(php[0-9]+):#\1#')
if [ -z "$php_repo" ]; then
  echo "Error: Could not find a ${REGISTRY}/phpNN image in $ubersmith_compose_file" >&2
  exit 1
fi

# Sanity check: the running php container's image version should match
# ubersmith_installed_version. A mismatch means .ubersmith_installer.ini is
# stale (e.g. a manual upgrade that didn't update it), and we'd compute the
# wrong target tag.
php_container_id=$(cd "$UBERSMITH_HOME" && compose ps -q php)
if [ -z "$php_container_id" ]; then
  echo "Error: No running php container found; is Ubersmith up?" >&2
  exit 1
fi
running_image=$(docker inspect --format '{{.Config.Image}}' "$php_container_id")
running_tag="${running_image##*:}"
running_version="${running_tag%-r*}"
running_release="${running_tag##*-}"
if [ "$running_version" != "$UBERSMITH_INSTALLED_VERSION" ]; then
  echo "Error: ubersmith_installed_version in $INSTALLER_INI ($UBERSMITH_INSTALLED_VERSION) does not match the running php container's image ($running_image). Refusing to patch; check for a stale .ubersmith_installer.ini." >&2
  exit 1
fi

# Refuse to downgrade the running release.
if [[ "$running_release" =~ ^r[0-9]+$ ]] && [[ "$ubersmith_target_release_version" =~ ^r[0-9]+$ ]]; then
  if [ "${running_release#r}" -gt "${ubersmith_target_release_version#r}" ]; then
    echo "Error: running php container is already $running_tag, newer than the target $UBERSMITH_INSTALLED_VERSION-$ubersmith_target_release_version. Refusing to downgrade." >&2
    exit 1
  fi
fi

ubersmith_new_tag="${UBERSMITH_INSTALLED_VERSION}-${ubersmith_target_release_version}"
ubersmith_image_repos=("$php_repo" "${PHP_BASED_IMAGE_REPOS[@]}")

confirm_images_published "$ubersmith_new_tag" "${ubersmith_image_repos[@]}"

echo "About to update ${ubersmith_image_repos[*]} to $ubersmith_new_tag and recreate php, mail, cron, and rwhois."
read -r -p "Continue? [y/N] " confirm_patch
if [[ ! "$confirm_patch" =~ ^[Yy] ]]; then
  echo "Aborted; no changes made."
  exit 0
fi

if bump_image_tag "$ubersmith_compose_file" "$ubersmith_new_tag" "${ubersmith_image_repos[@]}"; then
  echo "Updated ${ubersmith_image_repos[*]} to $ubersmith_new_tag"
  (cd "$UBERSMITH_HOME" && compose pull php mail cron rwhois)
  (cd "$UBERSMITH_HOME" && compose rm -sf php mail cron rwhois)
  (cd "$UBERSMITH_HOME" && compose up -d --no-deps php mail cron rwhois)
else
  echo "docker-compose.yml already references $ubersmith_new_tag (nothing to do)"
fi
