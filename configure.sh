#!/usr/bin/env bash
set -e

export PYTHONUSERBASE="$HOME/.local"
export PATH="$HOME/.local/bin:$PATH"

echo "Checking for pip..."
if command -v pip >/dev/null 2>&1; then
    echo "Installing Ansible..."
    pip install ansible -q --upgrade --user
else
    if command -v easy_install 2>/dev/null; then
        easy_install -q --user pip
    else
        echo "Both easy_install and pip are missing; please install pip."
        echo "https://pip.pypa.io/en/stable/installing/"
        exit 1
    fi
    echo "Installing Ansible..."
    pip install ansible -q --upgrade --user
fi

echo "Configuring the Ubersmith installer for an existing installation..."
ansible-playbook -i ./hosts -c local configure.yml