#!/usr/bin/env bash

echo "Installing Ansible..."
pip install ansible -qU
echo "Installing Ubersmith Appliance..."
ansible-playbook -i ./hosts install_appliance.yml