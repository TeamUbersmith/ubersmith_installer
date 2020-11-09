#!/usr/bin/env bash
set -e

# SSL and Python development libraries are required.
# sudo yum install gcc libffi-devel python-devel openssl-devel
# sudo apt-get install build-essential libssl-dev libffi-dev python-dev

export PYTHONUSERBASE="$HOME/.local"
export PATH="$HOME/.local/bin:$PATH"

echo "Checking for pip3..."
if command -v pip3 >/dev/null 2>&1; then
    echo "Installing Ansible..."
    pip3 install "ansible>=2.9,<2.10" -q --upgrade --user --force
else
    if command -v easy_install 2>/dev/null; then
        easy_install --user pip3
    else
        echo "Both easy_install and pip3 are missing; please install pip3."
        echo "https://pip3.pypa.io/en/stable/installing/"
        exit 1
    fi
    echo "Installing Ansible..."
    pip3 install "ansible>=2.9,<2.10" -q --upgrade --user --force
fi
echo "Installing Ubersmith Appliance..."
ansible-playbook -i ./hosts -c local --skip-tags upgrade_only install_appliance.yml
