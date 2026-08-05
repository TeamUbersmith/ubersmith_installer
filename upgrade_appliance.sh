#!/usr/bin/env bash
set -e

# SSL and Python development libraries are required.
# See documentation at https://docs.ubersmith.com/article.php?id=231

source ./find_python.sh
source "$HOME"/.local/ubersmith_venv/bin/activate

echo "Installing Dependencies..."
pip3 install --disable-pip-version-check -q -r requirements_pip.txt
ansible-galaxy install -r requirements_ansible.yml

echo "Upgrading Ubersmith Appliance..."
ansible-playbook -i ./hosts -e ansible_python_interpreter=$(which python3) -c local -t upgrade,upgrade_only upgrade_appliance.yml
