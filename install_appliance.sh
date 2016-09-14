#!/usr/bin/env bash
set -e

echo "Installing Ansible..."
pip install ansible -qU --user
echo "Installing Ubersmith Appliance..."
ansible-playbook -i ./hosts -c local install_appliance.yml