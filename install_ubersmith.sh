#!/usr/bin/env bash
set -e

# See documentation at https://docs.ubersmith.com/article.php?id=231

mkdir -p $HOME/.ubersmith_installer_logs

export PATH="$HOME/.local/bin:$HOME/.local/ubersmith_venv/bin:$PATH"
export ANSIBLE_LOG_PATH=$HOME/.ubersmith_installer_logs/ubersmith_install.`date +%s`.log

rm -rf $HOME/.local/ubersmith_venv

# Requires python3-venv on Ubuntu
echo "Creating Ubersmith Python virtual environment..."
python3 -m venv $HOME/.local/ubersmith_venv

source $HOME/.local/ubersmith_venv/bin/activate

echo "Installing Ansible and dependencies..."
pip3 install -q "ansible-core>=2.13,<2.15"
pip3 install -q PyMySQL
ansible-galaxy install -r requirements.yml
ansible-config init --disabled > ansible.cfg

echo "Installing Ubersmith..."
ansible-playbook -i ./hosts -c local --skip-tags upgrade_only install_ubersmith.yml
