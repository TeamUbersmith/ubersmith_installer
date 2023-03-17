#!/usr/bin/env bash
set -e

export PYTHONUSERBASE="$HOME/.local"
export PATH="$HOME/.local/bin:$PATH"

echo "Checking for pip3..."
if command -v pip3 >/dev/null 2>&1; then
    echo "Installing contemporary version of pip3..."
    pip3 install -q --user --upgrade pip
    echo "Installing Ansible..."
    $HOME/.local/bin/pip3 uninstall ansible -q -y
    $HOME/.local/bin/pip3 uninstall ansible-base -q -y
    $HOME/.local/bin/pip3 install "ansible-core>=2.11,<2.12" -q --upgrade --user
else
    echo "The pip3 utility is missing; please install pip3."
    echo "https://docs.ubersmith.com/display/UbersmithDocumentation/Ubersmith+Installation+and+Upgrade+Utility"
    exit 1
fi
echo "Installing Dependencies..."
ansible-galaxy install -r requirements.yml

echo "Configuring the Ubersmith installer for an existing installation..."
ansible-playbook -i ./hosts -c local configure.yml
