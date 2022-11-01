#!/usr/bin/env bash
set -e

# SSL and Python development libraries are required.
# See documentation at https://docs.ubersmith.com/display/UbersmithDocumentation/Ubersmith+Installation+and+Upgrade+Utility

export PYTHONUSERBASE="$HOME/.local"
export PATH="$HOME/.local/bin:$PATH"

echo "Checking for pip3..."
if command -v pip3 >/dev/null 2>&1; then
    echo "Installing contemporary version of pip3..."
    pip3 install -q --user --upgrade pip
    echo "Installing jmespath..."
    $HOME/.local/bin/pip3 install jmespath -q --upgrade --user
else
    echo "The pip3 utility is missing; please install pip3."
    echo "https://docs.ubersmith.com/display/UbersmithDocumentation/Ubersmith+Installation+and+Upgrade+Utility"
    exit 1
fi

echo "Patching Ubersmith..."
ansible-playbook -i ./hosts -c local -t remove_patches,restart patch_ubersmith.yml
