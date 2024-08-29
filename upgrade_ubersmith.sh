#!/usr/bin/env bash
set -e

# See documentation at https://docs.ubersmith.com/article.php?id=231

mkdir -p $HOME/.ubersmith_installer_logs

export PATH="$HOME/.local/bin:$HOME/.local/ubersmith_venv/bin:$PATH"
export ANSIBLE_LOG_PATH=$HOME/.ubersmith_installer_logs/ubersmith_upgrade.`date +%s`.log

rm -rf $HOME/.local/ubersmith_venv

# Requires python3-venv on Ubuntu
echo "Creating Ubersmith Python virtual environment..."
python3 -m venv $HOME/.local/ubersmith_venv

source $HOME/.local/ubersmith_venv/bin/activate

echo "Installing Ansible and dependencies..."
pip3 install -q "ansible-core>=2.13,<2.15"
# pip3 install -q mysqlclient
ansible-galaxy install -r requirements.yml

echo "Upgrading Ubersmith..."
ansible-playbook -i ./hosts -c local -t upgrade,upgrade_only upgrade_ubersmith.yml
