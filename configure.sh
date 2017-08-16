#!/usr/bin/env bash
set -e

echo "Configuring the Ubersmith installer for an existing installation..."
ansible-playbook -i ./hosts -c local configure.yml