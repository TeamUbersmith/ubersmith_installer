#!/usr/bin/env bash
set -e

export PATH="$HOME/.local/bin:$HOME/.local/ubersmith_venv/bin:$PATH"

if [ ! -d "$HOME/.local/ubersmith_venv" ]; then
    source ./find_python.sh
fi

source "$HOME"/.local/ubersmith_venv/bin/activate

echo "Installing Dependencies..."
pip3 install --disable-pip-version-check -q -r requirements_pip.txt
ansible-galaxy install -r requirements_ansible.yml

echo "Configuring the Ubersmith installer for an existing installation..."
ansible-playbook -i ./hosts -e ansible_python_interpreter=$(which python3) -c local configure.yml
