# The Ubersmith Installer

The Ubersmith installer requires [Docker](https://docs.docker.com/engine/installation/) 
and some OS level dependencies to be installed. 

## CentOS 7

`sudo yum install gcc libffi-devel python-devel openssl-devel`

## Debian 8 / Ubuntu LTS

`sudo apt-get install build-essential libssl-dev libffi-dev python-dev python-setuptools`

## Install

Run `./install_ubersmith.sh` to install Ubersmith Core.

Follow the prompts to complete the installation.

On a separate host, run `./install_appliance.sh` to install the Ubersmith Appliance.

Ubersmith Core and the Ubersmith Appliance should not be deployed to the same host.
See [our documentation](https://docs.ubersmith.com/display/UbersmithDocumentation/Ubersmith%27s+System+Requirements) for more details and system requirements.

# Caveats

Do not install the OS provided `pip` packages for Ubuntu and Debian as the version of `pip` provided is older and has difficulty installing the dependencies for the Ubersmith installer. `python-setuptools` will install the `easy_install` utility, which will allow the installer to install a more contemporary version of `pip`.
