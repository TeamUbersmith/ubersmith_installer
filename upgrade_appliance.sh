#!/usr/bin/env bash
set -e

# SSL and Python development libraries are required.
# See documentation at https://docs.ubersmith.com/article.php?id=231

read -r -p "Launch the upgrade in a screen session? [y/N] " launch_in_screen

source ./find_python.sh
source "$HOME"/.local/ubersmith_venv/bin/activate

echo "Installing Dependencies..."
pip3 install --disable-pip-version-check -q -r requirements_pip.txt
ansible-galaxy install -r requirements_ansible.yml

if [[ "$launch_in_screen" =~ ^[Yy] ]]; then
    # Check if screen command exists
    if ! command -v screen &> /dev/null; then
        echo "Error: GNU screen is not installed. Please install it and try again."
        exit 1
    fi

    echo "Starting screen and upgrading Ubersmith Appliance..."

    TIMESTAMP=$(date +%s)
    screen -L -Logfile "ubersmith_appliance_upgrade_${TIMESTAMP}.log" -dmS ubersmith_appliance_upgrade bash -c 'ansible-playbook -i ./hosts -e ansible_python_interpreter=$(which python3) -c local -t upgrade,upgrade_only upgrade_appliance.yml; exec bash'
    screen -r ubersmith_appliance_upgrade
else
    echo "Upgrading Ubersmith Appliance..."
    ansible-playbook -i ./hosts -e ansible_python_interpreter=$(which python3) -c local -t upgrade,upgrade_only upgrade_appliance.yml
fi
