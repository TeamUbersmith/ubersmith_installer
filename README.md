# The Ubersmith Installer

The Ubersmith installer requires [Docker](https://docs.docker.com/engine/installation/) 
and some OS level dependencies to be installed. 

## CentOS 7

`sudo yum install gcc libffi-devel python-devel openssl-devel`

## Debian 8 / Ubuntu LTS

`sudo apt-get install build-essential libssl-dev libffi-dev python-dev python-setuptools`

## Install

Run `./install_ubersmith.sh` to install Ubersmith Core.

Run `./install_appliance.sh` to install the Ubersmith Appliance.

Ubersmith Core and the Ubersmith Appliance should not be deployed to the same host.
See [our documentation](https://docs.ubersmith.com/display/UbersmithDocumentation/Ubersmith%27s+System+Requirements) for more details and system requirements.
