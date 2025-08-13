#!/usr/bin/env bash
set -e

# SSL and Python development libraries are required.
# See documentation at https://docs.ubersmith.com/article.php?id=231

export PATH="$HOME/.local/bin:$HOME/.local/ubersmith_venv/bin:$PATH"

rm -rf $HOME/.local/ubersmith_venv

echo "Checking for Python 3.11 or newer..."

# Check if python3 command exists
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 is not installed or not in your PATH. Please check the Installation "
    exit 1
fi

# Use a short Python script to check its own version.
# sys.version_info is a tuple like (3, 11, 4, ...)
if python3 -c 'import sys; exit(0 if sys.version_info >= (3, 11) else 1)'; then
    VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')
    # echo "OK: Python version $VERSION is 3.11 or newer."
else
    VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null || echo "unknown")
    echo "Error: Python version ($VERSION) is older than 3.11. Please upgrade your Python installation."
    exit 1
fi

# Requires python3-venv on Ubuntu
echo "Creating Ubersmith Python virtual environment..."
python3 -m venv $HOME/.local/ubersmith_venv

source $HOME/.local/ubersmith_venv/bin/activate

echo "Installing Dependencies..."
pip3 install --disable-pip-version-check -q -r requirements_pip.txt
ansible-galaxy install -r requirements_ansible.yml

echo "Installing Ubersmith..."
ansible-playbook -i ./hosts -e ansible_python_interpreter=$(which python3) -c local --skip-tags upgrade_only install_ubersmith.yml
