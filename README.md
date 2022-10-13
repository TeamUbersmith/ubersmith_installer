# The Ubersmith Installer

The Ubersmith installer requires [Docker](https://docs.docker.com/engine/installation/) 
and some OS level dependencies to be installed. 

# Documentation

Full documentation for the installer can be found [here](https://docs.ubersmith.com/article/ubersmith-installation-and-upgrade-utility-231.html).

# Caveats

This upgrade utility is not compatible with Ubersmith 3.x.

Dependencies installed by `pip` will installed using the `--user` option, which will install to the Python user install directory for your platform; typically `~/.local/`. (See the Python documentation for site.USER_BASE for full details.) This allows for the installer to be executed as a non-`root` user. You may want to add this directory to your PATH shell variable so that the supporting utilities (`docker-compose`, for example) can be run without having to specify the full path to the utility. To do this, run:
```
export PATH="$HOME/.local/bin/:$PATH"
```
