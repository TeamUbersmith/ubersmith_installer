#!/usr/bin/env bash
set -e

export PATH="$HOME/.local/bin:$HOME/.local/ubersmith_venv/bin:$PATH"

# Requires python3-venv on Ubuntu
if [ ! -d "$HOME/.local/ubersmith_venv" ]; then
    echo "Creating Ubersmith Python virtual environment..."
    python3-m venv $HOME/.local/ubersmith_venv
fi

source $HOME/.local/ubersmith_venv/bin/activate

echo "Installing Ansible..."
pip3 install -q "ansible-core>=2.14,<2.15"

echo "Installing Dependencies..."
ansible-galaxy install -r requirements.yml

echo "Configuring the Ubersmith installer for an existing installation..."
ansible-playbook -i ./hosts -c local configure.yml
