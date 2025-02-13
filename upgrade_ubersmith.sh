#!/usr/bin/env bash
set -e

# SSL and Python development libraries are required.
# See documentation at https://docs.ubersmith.com/article/ubersmith-installation-and-upgrade-utility-231.html

export PATH="$HOME/.local/bin:$HOME/.local/ubersmith_venv/bin:$PATH"

rm -rf $HOME/.local/ubersmith_venv

# Requires python3-venv on Ubuntu
echo "Creating Ubersmith Python virtual environment..."
python3 -m venv $HOME/.local/ubersmith_venv

source $HOME/.local/ubersmith_venv/bin/activate

echo "Installing Dependencies..."
pip3 install -q -r requirements_pip.txt
ansible-galaxy install -r requirements_ansible.yml

echo "Upgrading Ubersmith..."
ansible-playbook -i ./hosts -e ansible_python_interpreter=$(which python3) -c local -t upgrade,upgrade_only upgrade_ubersmith.yml
