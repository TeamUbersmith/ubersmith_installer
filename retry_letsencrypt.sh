#!/usr/bin/env bash
set -e

# SSL and Python development libraries are required.
# See documentation at https://docs.ubersmith.com/display/UbersmithDocumentation/Ubersmith+Installation+and+Upgrade+Utility

export PATH="$HOME/.local/bin:$HOME/.local/ubersmith_venv/bin:$PATH"

source $HOME/.local/ubersmith_venv/bin/activate

echo "Retrying Let's Encrypt certificate request..."
ansible-playbook -i ./hosts -c local retry_letsencrypt.yml
