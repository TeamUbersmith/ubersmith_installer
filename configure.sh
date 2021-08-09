#!/usr/bin/env bash
set -e

export PYTHONUSERBASE="$HOME/.local"
export PATH="$HOME/.local/bin:$PATH"

echo "Checking for pip3..."
if command -v pip3 >/dev/null 2>&1; then
    echo "Installing Ansible..."
    pip3 install "ansible>=2.9,<2.10" -q --upgrade --user
else
    if command -v easy_install 2>/dev/null; then
        easy_install -q --user pip==20.3.4
    else
        echo "Both easy_install and pip3 are missing; please install pip3."
        echo "https://pip3.pypa.io/en/stable/installing/"
        exit 1
    fi
    echo "Installing Ansible..."
    pip3 install ansible -q --upgrade --user
fi

echo "Configuring the Ubersmith installer for an existing installation..."
ansible-playbook -i ./hosts -c local configure.yml
