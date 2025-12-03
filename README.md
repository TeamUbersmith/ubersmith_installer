# The Ubersmith Installer

The Ubersmith installer requires [Docker](https://docs.docker.com/engine/installation/) 
and some OS level dependencies to be installed. 

# Documentation

Full documentation for the installer can be found [here](https://docs.ubersmith.com/article.php?id=231).

# Development

## Ansible Lint

This project uses ansible-lint to ensure playbook quality. To run the linter:

```bash
# Install ansible-lint and required collections
pip install ansible-lint
ansible-galaxy collection install -r requirements_ansible.yml

# Run the linter
ansible-lint
```

**Note:** Some syntax-check warnings may appear if Ansible collections are not installed. These are expected and do not indicate actual errors in the playbooks. All required collections are specified in `requirements_ansible.yml` and will be installed when users run the installer.

# Please Note

This upgrade utility is not compatible with Ubersmith 3.x.

If you have a version of Ubersmith older than 4.6.x, you will need to upgrade to 4.6.4 first before upgrading to 5.0.0 to ensure that you have a compatible version of MySQL which can be upgraded to MySQL 8.

