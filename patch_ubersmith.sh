#!/usr/bin/env bash
set -e

# SSL and Python development libraries are required.
# See documentation at https://docs.ubersmith.com/article.php?id=231

export PATH="$HOME/.local/bin:$HOME/.local/ubersmith_venv/bin:$PATH"

if [ ! -d "$HOME/.local/ubersmith_venv" ]; then
    source ./find_python.sh
fi

source "$HOME"/.local/ubersmith_venv/bin/activate

echo "Installing Dependencies..."
pip3 install --disable-pip-version-check -q -r requirements_pip.txt
ansible-galaxy install -r requirements_ansible.yml

echo "Patching Ubersmith..."
ansible-playbook -i ./hosts -e ansible_python_interpreter=$(which python3) -c local --skip-tags remove_patches patch_ubersmith.yml
