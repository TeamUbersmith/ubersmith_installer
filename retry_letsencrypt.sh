#!/usr/bin/env bash
set -e

# SSL and Python development libraries are required.
# sudo yum install gcc libffi-devel python-devel openssl-devel
# sudo apt-get install build-essential libssl-dev libffi-dev python-dev

export PYTHONUSERBASE="$HOME/.local"
export PATH="$HOME/.local/bin:$PATH"

echo "Retrying Let's Encrypt request..."
ansible-playbook -i ./hosts -c local -t letsencrypt install_ubersmith.yml
