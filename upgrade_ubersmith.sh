#!/usr/bin/env bash
set -e

# SSL and Python development libraries are required.
# See documentation at https://docs.ubersmith.com/display/UbersmithDocumentation/Ubersmith+Installation+and+Upgrade+Utility

export PATH="$HOME/.local/bin:$HOME/.local/ubersmith_venv/bin:$PATH"

if [ ! -d "$HOME/.local/ubersmith_venv" ]; then
    echo "Installing Python virtualenv..."
    python -m pip install -q --user virtualenv

    echo "Creating Ubersmith Python virtualenv..."
    $HOME/.local/bin/virtualenv -q $HOME/.local/ubersmith_venv
fi

source $HOME/.local/ubersmith_venv/bin/activate

echo "Installing Ansible..."
pip3 install -q "ansible-core>=2.14,<2.15"

echo "Installing Dependencies..."
ansible-galaxy install -r requirements.yml

echo "Upgrading Ubersmith..."
ansible-playbook -i ./hosts -c local -t upgrade,upgrade_only upgrade_ubersmith.yml

