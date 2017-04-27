# The Ubersmith Installer

The Ubersmith installer requires [Docker](https://docs.docker.com/engine/installation/) 
and some OS level dependencies to be installed. 

## CentOS 7

`sudo yum install gcc libffi-devel python-devel openssl-devel`

If the installer is unable to install `pip`, you may need the [EPEL yum repository](https://fedoraproject.org/wiki/EPEL).

Once EPEL is configured, run:

`sudo yum install python-pip`

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

Dependencies installed by `pip` will installed using the `--user` option, which will install to the Python user install directory for your platform; typically `~/.local/`. (See the Python documentation for site.USER_BASE for full details.) This allows for the installer to be executed as a non-`root` user.
