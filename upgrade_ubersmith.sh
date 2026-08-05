#!/usr/bin/env bash
set -e

# SSL and Python development libraries are required.
# See documentation at https://docs.ubersmith.com/article.php?id=231

# Check if screen command exists
if ! command -v screen &> /dev/null; then
    echo "Error: GNU screen is not installed. Please install it and try again."
    exit 1
fi

source ./find_python.sh
source "$HOME"/.local/ubersmith_venv/bin/activate

echo "Installing Dependencies..."
pip3 install --disable-pip-version-check -q -r requirements_pip.txt
ansible-galaxy install -r requirements_ansible.yml

echo "Starting screen and upgrading Ubersmith..."

TIMESTAMP=$(date +%s)
screen -L -Logfile "ubersmith_upgrade_${TIMESTAMP}.log" -dmS ubersmith_upgrade bash -c 'ansible-playbook -i ./hosts -e ansible_python_interpreter=$(which python3) -c local -t upgrade,upgrade_only upgrade_ubersmith.yml; exec bash'
screen -r ubersmith_upgrade
