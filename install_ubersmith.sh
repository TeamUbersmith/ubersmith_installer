#!/usr/bin/env bash

echo "Installing Ansible..."
pip install ansible -qU
echo "Installing Ubersmith..."
ansible-playbook -i ./hosts -c local install_ubersmith.yml