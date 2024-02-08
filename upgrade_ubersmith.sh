#!/usr/bin/env bash
set -e

# SSL and Python development libraries are required.
# See documentation at https://docs.ubersmith.com/article/ubersmith-installation-and-upgrade-utility-231.html

export PATH="$HOME/.local/bin:$HOME/.local/ubersmith_venv/bin:$PATH"

# Requires python3-venv on Ubuntu
if [ ! -d "$HOME/.local/ubersmith_venv" ]; then
    echo "Creating Ubersmith Python virtual environment..."
    python -m venv $HOME/.local/ubersmith_venv
fi

source $HOME/.local/ubersmith_venv/bin/activate

echo "Installing Ansible..."
pip3 install -q "ansible-core>=2.13,<2.15"

echo "Installing Dependencies..."
ansible-galaxy install -r requirements.yml

echo "Upgrading Ubersmith..."
ansible-playbook -i ./hosts -c local -t upgrade,upgrade_only upgrade_ubersmith.yml

