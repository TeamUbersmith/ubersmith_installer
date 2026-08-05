#!/usr/bin/env bash
set -e

# SSL and Python development libraries are required.
# See documentation at https://docs.ubersmith.com/display/UbersmithDocumentation/Ubersmith+Installation+and+Upgrade+Utility

export PATH="$HOME/.local/bin:$HOME/.local/ubersmith_venv/bin:$PATH"

if [ ! -d "$HOME/.local/ubersmith_venv" ]; then
    source ./find_python.sh
fi

source "$HOME"/.local/ubersmith_venv/bin/activate

echo "Installing Dependencies..."
pip3 install --disable-pip-version-check -q -r requirements_pip.txt
ansible-galaxy install -r requirements_ansible.yml

echo "Installing jmespath..."
pip3 install --disable-pip-version-check -q "jmespath"

echo "Patching Ubersmith..."
ansible-playbook -i ./hosts -e ansible_python_interpreter=$(which python3) -c local -t remove_patches,restart patch_ubersmith.yml
