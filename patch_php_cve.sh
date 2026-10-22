#!/usr/bin/env bash
set -euo pipefail

# Bumps the php/mail/cron/rwhois image tags in docker-compose.yml.
#
# Usage:
#   ./patch_php_cve.sh
#   ./patch_php_cve.sh -r r6   # override the version table below

REGISTRY="ghcr.io/teamubersmith"
INSTALLER_INI="$HOME/.ubersmith_installer.ini"

# installed_version:release_version, one per line. Not a bash associative
# array: some hosts old enough to need this script also have a bash too old
# to support them (no declare -A before 4.0).
PHP_CVE_RELEASE_VERSIONS="
4.6.1:r2
4.6.2:r12
4.6.3:r5
4.6.4:r5
5.0.0:r14
5.0.1:r4
5.0.2:r2
5.0.3:r6
5.1.0:r12
5.1.1:r2
5.1.2:r3
5.1.3:r3
5.1.4:r4
5.2.0:r4
5.2.1:r3
5.2.2:r4
5.2.3:r3
5.3.0:r5
"

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
    return
  fi
  printf '%s\n' "$PHP_CVE_RELEASE_VERSIONS" | awk -F: -v v="$installed_version" '$1==v{print $2}'
}

# image_exists <repo> <tag> -- queries the registry API directly via curl
# rather than `docker manifest inspect`, whose auth/manifest-list handling
# is inconsistent across older Docker CLI versions (confirmed broken
# against ghcr.io on Docker 19.03, reporting "no such manifest" for an
# image that genuinely exists).
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

confirm_images_published() {
  local new_tag="$1"
  shift
  local repo
  for repo in "$@"; do
    echo "Confirming ${REGISTRY}/${repo}:${new_tag} is published..."
    if ! image_exists "$repo" "$new_tag"; then
      echo "Error: ${REGISTRY}/${repo}:${new_tag} does not exist" >&2
      exit 1
    fi
  done
}

# bump_image_tag <compose_file> <new_tag> <php_repo> <other_repo1> [<other_repo2> ...]
# Rewrites the whole image reference (registry + repo + tag) to
# $REGISTRY/<repo>:<new_tag>, regardless of what registry or repo name the
# line currently uses -- installs old enough to need this patch have used
# several: quay.io/ubersmith/php:7.3 (bare, no version number in the repo
# name at all), quay.io/ubersmith/php73:X.Y.Z.0, and the current
# ghcr.io/teamubersmith/php73:X.Y.Z-rN. The php service is matched by its
# generic "php[0-9]*" shape and always rewritten to the canonical
# <php_repo> passed in; the others are matched by their literal (stable)
# names. Returns 0 if the file actually changed, 1 otherwise.
bump_image_tag() {
  local compose_file="$1" new_tag="$2" php_repo="$3"
  shift 3
  local other_repos=("$@")
  local other_pattern
  other_pattern=$(printf '%s|' "${other_repos[@]}")
  other_pattern="${other_pattern%|}"

  cp -p "$compose_file" "${compose_file}.bak-$(date +%Y%m%d%H%M%S)"

  local tmp
  tmp=$(mktemp)
  sed -E \
    -e "s#^([[:space:]]*image:[[:space:]]*)[^[:space:]]*/php[0-9]*:[^[:space:]]+\$#\1${REGISTRY}/${php_repo}:${new_tag}#" \
    -e "s#^([[:space:]]*image:[[:space:]]*)[^[:space:]]*/(${other_pattern}):[^[:space:]]+\$#\1${REGISTRY}/\2:${new_tag}#" \
    "$compose_file" > "$tmp"

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
if [ ! -f "$ubersmith_compose_file" ]; then
  echo "Error: $ubersmith_compose_file not found" >&2
  exit 1
fi

# The php image repo has used several names over the years in the compose
# file itself (bare "php", "php73", "php82", ...), so it's determined here
# from the installed major version instead -- same mapping as
# roles/ubersmith/vars/main.yml's ubersmith_release table.
case "$UBERSMITH_INSTALLED_VERSION" in
  4.*) php_repo="php73" ;;
  5.*) php_repo="php84" ;;
  *)
    echo "Error: Don't know which php image repo to use for ubersmith_installed_version $UBERSMITH_INSTALLED_VERSION" >&2
    exit 1
    ;;
esac

# Sanity check: the running php container's image version should match
# ubersmith_installed_version. A mismatch means .ubersmith_installer.ini is
# stale (e.g. a manual upgrade that didn't update it), and we'd compute the
# wrong target tag.
php_container_id=$(cd "$UBERSMITH_HOME" && compose ps -q php) || true
if [ -z "$php_container_id" ]; then
  echo "Error: No running php container found; is Ubersmith up?" >&2
  exit 1
fi
running_image=$(docker inspect --format '{{.Config.Image}}' "$php_container_id")
running_tag="${running_image##*:}"
# The version is always a leading major.minor.revision; what follows it has
# varied (current: "-rN", legacy quay.io-era: ".0"). Only the release number
# is needed beyond that, and only if present.
running_version=""
running_release=""
if [[ "$running_tag" =~ ^([0-9]+\.[0-9]+\.[0-9]+) ]]; then
  running_version="${BASH_REMATCH[1]}"
fi
if [[ "$running_tag" =~ -r([0-9]+)$ ]]; then
  running_release="r${BASH_REMATCH[1]}"
fi
# Some legacy tags (e.g. "7.3") carry no Ubersmith version at all -- just
# the php language version -- so there's nothing to compare in that case.
if [ -n "$running_version" ] && [ "$running_version" != "$UBERSMITH_INSTALLED_VERSION" ]; then
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
