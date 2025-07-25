#!/usr/bin/env bash
set -e

# SSL and Python development libraries are required.
# See documentation at https://docs.ubersmith.com/display/UbersmithDocumentation/Ubersmith+Installation+and+Upgrade+Utility

export PATH="$HOME/.local/bin:$HOME/.local/ubersmith_venv/bin:$PATH"

source $HOME/.local/ubersmith_venv/bin/activate

echo "Installing jmespath..."
pip3 install --disable-pip-version-check -q "jmespath"

echo "Patching Ubersmith..."
ansible-playbook -i ./hosts -e ansible_python_interpreter=$(which python3) -c local --skip-tags remove_patches patch_ubersmith.yml
