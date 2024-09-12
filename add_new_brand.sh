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

echo "Installing Ansible..."
pip3 install -q "ansible-core>=2.13,<2.15"

echo "Installing Dependencies..."
ansible-galaxy install -r requirements.yml

echo "Configuring a new brand..."
ansible-playbook -i ./hosts -c local -t new_brand add_new_brand.yml

echo "Retrying Let's Encrypt certificate request..."
ansible-playbook -i ./hosts -c local retry_letsencrypt.yml
