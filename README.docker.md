# Ubersmith Docker Compose Architecture

This document provides a comprehensive overview of the Ubersmith application's Docker-based architecture, including service definitions, networking, data persistence, and configuration management.

## Table of Contents

- [Overview](#overview)
- [Architecture Diagram](#architecture-diagram)
- [Services](#services)
  - [Core Application Services](#core-application-services)
  - [Database Services](#database-services)
  - [Communication Services](#communication-services)
  - [Monitoring and Management Services](#monitoring-and-management-services)
  - [Security Services](#security-services)
  - [Utility Services](#utility-services)
- [Networking](#networking)
- [Volume Management](#volume-management)
- [Environment Variables](#environment-variables)
- [Dependencies and Prerequisites](#dependencies-and-prerequisites)
- [Configuration Files](#configuration-files)

## Overview

Ubersmith is deployed as a containerized microservices application using Docker Compose. The architecture consists of multiple specialized services that work together to provide a complete billing and customer management platform. The deployment is managed through Ansible templates that generate Docker Compose configuration files.

The application uses:
- **Web servers** (Apache/HTTPD) for serving HTTP/HTTPS traffic
- **PHP-FPM** for processing PHP application logic
- **MySQL/Percona Server** for relational data storage
- **Redis** for caching, session management, and queue handling
- **Solr** for search functionality
- **Postfix** for email services
- **ClamAV** for antivirus scanning

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         External Access                          │
│  HTTP:80/HTTPS:443  │  SMTP:25  │  RWhois:4321  │  MySQL:3306  │
└──────────┬────────────────┬────────────┬──────────────┬─────────┘
           │                │            │              │
    ┌──────▼──────┐  ┌──────▼──────┐  ┌─▼────┐   ┌────▼────┐
    │     web     │  │    mail     │  │rwhois│   │   db    │
    │  (Apache)   │  │  (Postfix)  │  │(xinetd)│  │(Percona)│
    └──────┬──────┘  └──────┬──────┘  └──────┘   └─────────┘
           │                │
    ┌──────▼──────┐  ┌──────▼──────┐
    │     php     │  │   clamav    │
    │  (PHP-FPM)  │  │ (Antivirus) │
    └──────┬──────┘  └─────────────┘
           │
    ┌──────▼──────────────────────┐
    │   Shared Services Layer     │
    ├─────────────────────────────┤
    │ redis-data  │  solr  │ cron │
    └─────────────────────────────┘
           │
    ┌──────▼──────────────────────┐
    │  Management & Monitoring    │
    ├─────────────────────────────┤
    │ rsyslog │ pmm │ falco       │
    └─────────────────────────────┘
```

## Services

### Core Application Services

#### web
- **Image**: `ubersmith:{version}-{release}`
- **Purpose**: Apache HTTPD web server serving as the front-end for the Ubersmith application
- **Ports**: 
  - `80:80` - HTTP traffic
  - `443:443` - HTTPS traffic
- **Key Features**:
  - TLS/SSL termination
  - Serves static content
  - Proxies dynamic requests to PHP-FPM
  - Enforces HTTPS redirection (`HTTPS_FORCE: 1`)
- **Environment Variables**:
  - `MYSQL_USER`, `MYSQL_DATABASE`, `DATABASE_HOST` - Database connection
  - `LOCK_BACKEND`, `LOCK_SERVERS` - Redis-based distributed locking
  - `STORAGE_BACKEND`, `REDIS_HOST`, `SESSION_HOST` - Redis data stores
  - `QUEUE_HOST`, `PLUGIN_LOG_HOST` - Queue and logging backends
  - `UBER_HOSTNAME` - Primary hostname
  - `MAINTENANCE` - Maintenance mode toggle
  - `OPENSSL_CIPHERS` - TLS cipher configuration (Mozilla Intermediate profile)
- **Volumes**:
  - `webroot:/var/www/ubersmith_root` - Application files
  - `logs:/var/www/ubersmith_root/logs` - Application logs
  - `search:/opt/solr` - Solr search index
  - Custom code and patches directories
  - SSL certificates and Apache configuration

#### php
- **Image**: `php{version}:{ubersmith_version}-{release}`
- **Purpose**: PHP-FPM process manager for executing PHP application code
- **Key Features**:
  - Processes PHP requests from the web service
  - Handles application business logic
  - Configured for long-running operations (timeouts up to 3600s)
- **Configuration**:
  - Memory limit: 512M
  - Max execution time: 3600s
  - Max input time: 6000s
  - Upload max filesize: 16M
  - Post max size: 50M
- **Dependencies**:
  - `web` (service must be started)
  - `rsyslog` (must be healthy)
- **Volumes**:
  - Shares `webroot` and `logs` with web service
  - Custom PHP configuration via `ubersmith.ini`
  - rsyslog socket for logging

#### cron
- **Image**: `cron:{version}-{release}`
- **Purpose**: Scheduled task execution for maintenance and background jobs
- **Key Features**:
  - Runs periodic maintenance tasks
  - Data cleanup and aggregation
  - Report generation
- **Dependencies**: 
  - Depends on `web` service
- **Volumes**:
  - Shares application `webroot` and `logs`
  - Custom code and patches directories

### Database Services

#### db
- **Image**: `ps{mysql_version}:{version}-{release}` (Percona Server)
- **Purpose**: Primary relational database for application data
- **Ports**: 
  - `127.0.0.1:3306:3306` - MySQL protocol (localhost only)
- **Versions Supported**:
  - MySQL 5.7 for Ubersmith 4.x
  - MySQL 8.0 for Ubersmith 5.x
- **Environment Variables**:
  - `MYSQL_ROOT_PASSWORD` - Root user password (auto-generated)
  - `MYSQL_USER` - Application user (`ubersmith`)
  - `MYSQL_PASSWORD` - Application user password (auto-generated)
  - `MYSQL_DATABASE` - Application database name (`ubersmith`)
- **Volumes**:
  - `database:/var/lib/mysql` - Database data files
  - `database_keyring:/var/lib/mysql-keyring` - Encryption keyring
  - MySQL configuration files from host
- **Capabilities**: `SYS_NICE` - For resource management

#### redis-data
- **Image**: `redis7:{version}-{release}`
- **Purpose**: Primary Redis instance for application data storage
- **Use Cases**:
  - Session storage (database 0)
  - Application storage (database 2)
  - Plugin logs (database 3)
  - Queue management (database 4)
- **Volumes**:
  - `redis:/data` - Persistent Redis data

#### redis
- **Image**: `redis7:{version}-{release}`
- **Purpose**: Additional Redis instance for distributed locking
- **Configuration**:
  - Part of a 3-node lock cluster (`ubersmith-redis-1:6379:1`)
  - Database 1 used for locking

#### solr
- **Image**: `solr:{version}-{release}`
- **Purpose**: Apache Solr search engine for full-text search capabilities
- **Features**:
  - Indexes application data for fast searching
  - Supports complex queries and faceted search
- **Volumes**:
  - `search:/opt/solr` - Search index data
- **Limits**:
  - File descriptors: 65536 (soft and hard)

### Communication Services

#### mail
- **Image**: `mail:{version}-{release}`
- **Purpose**: Postfix SMTP server for sending and receiving emails
- **Ports**: 
  - `25:25` - SMTP
- **Features**:
  - TLS encryption support
  - Integrated with ClamAV for virus scanning
  - Custom mail relay configuration support
- **Environment Variables**:
  - `POSTCONF_SMTPD_TLS_CERT_FILE` - TLS certificate path
  - `POSTCONF_SMTPD_TLS_KEY_FILE` - TLS private key path
  - `POSTCONF_SMTP_TLS_SECURITY_LEVEL` - TLS security level (may)
  - `ANTIVIRUS` - Enable/disable ClamAV integration
- **Network Aliases**:
  - `ubersmith.mail` - Internal mail routing
- **Dependencies**: 
  - `web`, `clamav`
- **Volumes**:
  - Shares `webroot` and `logs`
  - SSL certificates
  - Custom Postfix configuration (optional)

#### rwhois
- **Image**: `xinetd:{version}-{release}`
- **Purpose**: RWhois server for IP address registration queries (RFC 2167)
- **Ports**: 
  - `4321:4321` - RWhois protocol
- **Features**:
  - Provides IP allocation information
  - Used by ISPs and hosting providers
- **Dependencies**: 
  - `web`
- **Volumes**:
  - Custom xinetd configuration
  - Access to application `webroot`
- **Limits**:
  - File descriptors: 65536

### Monitoring and Management Services

#### pmm
- **Image**: `percona/pmm-server:{pmm_version}`
- **Purpose**: Percona Monitoring and Management for database performance monitoring
- **Ports**: 
  - `8443:443` - PMM web interface (HTTPS)
- **Features**:
  - Real-time database metrics
  - Query analytics
  - Performance dashboards
- **Volumes**:
  - `pmm-data:/srv` - PMM data storage
  - SSL certificates for secure access

#### rsyslog
- **Image**: `rsyslog:{version}-{release}`
- **Purpose**: Centralized logging service
- **Features**:
  - Collects logs from all services
  - Provides socket for log forwarding
- **Volumes**:
  - Host directory for log sockets
- **Health Check**: Service provides health status for dependent services

#### redis-commander
- **Image**: `rediscommander/redis-commander:latest`
- **Purpose**: Web-based Redis management interface
- **Ports**: 
  - `8081` - Web interface
- **Configuration**:
  - Connects to `redis-data` instance
  - Database 3 (plugin logs) by default
- **Restart Policy**: `no` (manual start)

#### haproxy
- **Image**: `haproxy:{version}`
- **Purpose**: Load balancer for database connections
- **Ports**: 
  - `3306` - MySQL load balancing
  - `3307` - Alternative MySQL port
- **Features**:
  - Database connection pooling
  - Health checking
  - High availability support
- **Volumes**:
  - Custom HAProxy configuration

### Security Services

#### falco
- **Image**: `falcosecurity/falco-no-driver:latest`
- **Purpose**: Runtime security monitoring and threat detection
- **Features**:
  - Monitors system calls and container activity
  - Detects anomalous behavior
  - Security event logging
- **Configuration**:
  - Privileged mode required for system monitoring
  - Custom rules via `falco_rules.local.yaml`
- **Volumes**:
  - `/var/run/docker.sock` - Docker socket access
  - `/proc` - Process monitoring
  - `/etc` - System configuration monitoring
- **Dependencies**: 
  - `web`

#### clamav
- **Image**: `clamav/clamav:1.3_base`
- **Purpose**: Antivirus engine for scanning emails and uploads
- **Features**:
  - Automatic signature updates
  - Integration with mail service
  - On-demand file scanning
- **Volumes**:
  - `clamav_signatures:/var/lib/clamav` - Virus definition database

#### certbot
- **Image**: `ghcr.io/teamubersmith/certbot:{certbot_version}`
- **Purpose**: Let's Encrypt SSL/TLS certificate management
- **Features**:
  - Automatic certificate renewal
  - Supports multiple DNS providers (NSOne, Cloudflare)
  - Webroot and standalone validation methods
- **Command**: `renew -vvv -n --webroot --webroot-path /var/www/ubersmith_root/app/www`
- **Volumes**:
  - Certificate storage directories
  - Webroot for HTTP validation
  - SSL certificate deployment directory

### Utility Services

#### backup
- **Image**: `xtrabackup{backup_version}:{version}-{release}`
- **Purpose**: MySQL database backup using Percona XtraBackup
- **Versions**:
  - XtraBackup 2.x for MySQL 5.7
  - XtraBackup 8.x for MySQL 8.0
- **Features**:
  - Hot backups (no downtime)
  - Incremental backup support
  - Point-in-time recovery
- **Environment Variables**:
  - `DATABASE_HOST` - MySQL host (`db`)
  - `MYSQL_ROOT_PASSWORD` - Database root password
- **Volumes**:
  - Backup destination directory on host
  - Read access to database volume
- **Dependencies**: 
  - `db`

#### redis-backup
- **Image**: `busybox:latest`
- **Purpose**: Redis data backup utility
- **Features**:
  - Creates compressed backup archives
  - Scheduled backup execution
- **Command**: `tar -czf /backup/ubersmith_redis_backup.tar.gz -C /data .`
- **Volumes**:
  - `redis:/data:ro` - Read-only Redis data
  - Backup destination on host

## Networking

### Default Network
All services communicate through a default Docker bridge network created by Docker Compose. Services can reference each other by their service name (e.g., `db`, `redis-data`).

### Network Aliases
- **mail**: Additional alias `ubersmith.mail` for internal mail routing

### Port Exposure
- **Exposed to Host**:
  - `80, 443` (web) - Public HTTP/HTTPS access
  - `25` (mail) - SMTP server
  - `4321` (rwhois) - RWhois protocol
  - `127.0.0.1:3306` (db) - MySQL (localhost only)
  - `8443` (pmm) - Monitoring interface
  - `8081` (redis-commander) - Redis management (optional)

### Service Discovery
Services use Docker's internal DNS for name resolution. For example:
- Web service connects to database via `DATABASE_HOST=db`
- Redis connections use service names: `ubersmith-redis-data-1:6379`

### Inter-Service Communication

```
web → php (FastCGI)
web → db (MySQL)
web → redis-data (Session, Cache, Queue)
web → redis (Distributed Locks)
web → solr (Search)
mail → clamav (Virus Scanning)
php → db (Database Queries)
php → redis-data (Cache Operations)
backup → db (Backup Operations)
```

## Volume Management

### Named Volumes (Docker-Managed)

These volumes are managed by Docker and provide persistent storage:

| Volume | Purpose | Used By |
|--------|---------|---------|
| `database` | MySQL data files | db, backup |
| `database_keyring` | MySQL encryption keys | db |
| `redis` | Redis persistent data | redis-data, redis-backup |
| `webroot` | Application files and code | web, php, cron, mail, certbot, rwhois |
| `logs` | Application logs | web, php, cron, mail |
| `search` | Solr search indexes | solr, web |
| `clamav_signatures` | Antivirus definitions | clamav |
| `pmm-data` | Monitoring data | pmm |

### Host-Mounted Volumes

These directories are mounted from the host system (typically under `/usr/local/ubersmith/`):

| Host Path | Container Path | Purpose |
|-----------|---------------|---------|
| `{ubersmith_home}/conf/mysql` | `/etc/ubersmith.conf.d` | MySQL configuration |
| `{ubersmith_home}/conf/ssl` | Various SSL paths | TLS certificates |
| `{ubersmith_home}/conf/httpd` | Apache config paths | Web server configuration |
| `{ubersmith_home}/conf/php` | PHP config paths | PHP-FPM configuration |
| `{ubersmith_home}/app/custom` | `/var/www/ubersmith_root/app/custom` | Custom application code |
| `{ubersmith_home}/app/patches` | `/var/www/ubersmith_root/app/patches` | Application patches |
| `{ubersmith_home}/backup` | `/backup` | Backup storage |
| `{ubersmith_home}/logs/rsyslog` | Rsyslog paths | Centralized logging |
| `{ubersmith_home}/conf/rwhois` | `/etc/xinetd.d` | RWhois configuration |
| `{ubersmith_home}/conf/certbot` | Certbot paths | SSL certificate management |
| `{timezone_file}` | `/etc/localtime` | System timezone |

### Data Persistence Strategy

- **Database**: All MySQL data is stored in the `database` named volume, ensuring persistence across container recreations
- **Redis**: Persistent data storage using the `redis` volume with RDB snapshots
- **Application Files**: The `webroot` volume contains the application code and is shared across multiple services
- **Logs**: Centralized in the `logs` volume and additionally forwarded to host via rsyslog
- **Backups**: Stored on the host filesystem for external access and disaster recovery

## Environment Variables

### Database Configuration
- `MYSQL_ROOT_PASSWORD` - Auto-generated root password (stored in `~/.ubersmith_{hostname}_root_db_pass`)
- `MYSQL_PASSWORD` - Auto-generated application password (stored in `~/.ubersmith_{hostname}_ubersmith_db_pass`)
- `MYSQL_USER` - Database user (`ubersmith`)
- `MYSQL_DATABASE` - Database name (`ubersmith`)
- `DATABASE_HOST` - Database hostname (`db`)

### Redis Configuration
- `LOCK_BACKEND=redis` - Distributed locking backend
- `LOCK_SERVERS` - Redis nodes for locking (3-node cluster)
- `STORAGE_BACKEND=redis` - Storage backend type
- `REDIS_HOST` - Primary Redis connection (database 2)
- `SESSION_HOST` - Session storage Redis (database 0)
- `PLUGIN_LOG_HOST` - Plugin logging Redis (database 3)
- `QUEUE_HOST` - Queue system Redis (database 4)
- `REDIS_SENTINEL_ENABLE=0` - Sentinel mode disabled by default

### Application Configuration
- `UBER_HOSTNAME` - Primary application hostname
- `MAINTENANCE` - Maintenance mode flag (0=normal, 1=maintenance)
- `HTTPS_FORCE=1` - Force HTTPS redirects
- `REASON_LOG=0` - Reason logging disabled
- `APPLIANCE_TIMEOUT=30` - API timeout in seconds
- `ALIPAY_ENABLE=1` - Alipay payment gateway
- `TIMEZONE` - System timezone (from host)

### Security Configuration
- `OPENSSL_CIPHERS` - TLS cipher suite (Mozilla Intermediate profile)
- `POSTCONF_SMTP_TLS_SECURITY_LEVEL=may` - Opportunistic TLS for outbound mail
- `ANTIVIRUS=0` - ClamAV integration toggle

### PHP Configuration (via ubersmith.ini)
- `memory_limit=512M`
- `max_execution_time=3600`
- `max_input_time=6000`
- `max_input_vars=2000`
- `upload_max_filesize=16M`
- `post_max_size=50M`
- `session.gc_maxlifetime=86400`
- `default_socket_timeout=6000`

## Dependencies and Prerequisites

### Host System Requirements

1. **Operating System**: 
   - Linux (CentOS, RHEL, Ubuntu, Debian)
   - Docker for Mac (limited support)

2. **Docker Engine**: 
   - Docker Engine 20.10 or later
   - Docker Compose V2 (plugin) or V1.29+

3. **System Resources**:
   - CPU: Minimum 4 cores (8+ recommended)
   - RAM: Minimum 8GB (16GB+ recommended)
   - Disk: 100GB+ available space (more for data growth)

4. **Network Requirements**:
   - Ports 80, 443, 25, 4321 available
   - Internet access for pulling container images
   - DNS resolution configured

### Software Dependencies

1. **Ansible** (for deployment):
   - Ansible 2.9 or later
   - Python 3.6+
   - Required Ansible collections and modules

2. **Image Registry Access**:
   - Access to `ghcr.io/teamubersmith` registry
   - Authentication credentials for private images

### Service Dependencies

The services have the following startup dependencies:

```
rsyslog (must be healthy)
  ↓
db
  ↓
web → redis-data
  ↓
php (depends on web + rsyslog)
  ↓
cron, mail (depends on web)
  ↓
rwhois (depends on web)
  ↓
falco (depends on web)
```

- **php** requires both `web` (started) and `rsyslog` (healthy)
- **mail** requires both `web` and `clamav`
- **backup** requires `db`
- **redis-data** has no dependencies
- **solr** has no dependencies

### Version Compatibility

The system supports two major versions:

**Ubersmith 4.x**:
- MySQL 5.7 (Percona Server)
- PHP 7.3
- XtraBackup 2.x

**Ubersmith 5.x**:
- MySQL 8.0 (Percona Server)
- PHP 8.4
- XtraBackup 8.x

**Note**: Direct upgrade from Ubersmith 3.x is not supported. Must upgrade to 4.6.4 first.

## Configuration Files

### Ansible Variables
The Docker Compose files are generated from Jinja2 templates using variables defined in:
- `roles/ubersmith/vars/main.yml` - Default variables
- Host-specific variables in inventory or playbooks

### Key Configuration Locations

1. **MySQL**: `{ubersmith_home}/conf/mysql/`
   - `ubersmith.cnf.{version}.j2` - MySQL server configuration
   - `ubersmith_extra.cnf.j2` - Additional MySQL settings
   - Component configuration for keyring

2. **Apache/Web**: `{ubersmith_home}/conf/httpd/`
   - `instance_vhost.j2` - Virtual host configuration
   - `sites-enabled/` - Enabled site configurations

3. **PHP**: `{ubersmith_home}/conf/php/`
   - `ubersmith.ini` - PHP configuration overrides
   - `www.conf` - PHP-FPM pool configuration

4. **SSL/TLS**: `{ubersmith_home}/conf/ssl/`
   - Certificate and key files
   - Managed by certbot

5. **Environment**: 
   - `.env` file in deployment directory
   - `MAINTENANCE` variable for maintenance mode

### Logging Configuration

Logging is handled at multiple levels:

1. **Container Logs** (Linux only):
   - Driver: `journald`
   - Tagged per service (e.g., `ubersmith/web`, `ubersmith/db`)
   - Accessible via `journalctl -t ubersmith/web`

2. **Application Logs**:
   - Stored in `logs` volume
   - Accessible at `{ubersmith_home}/logs/`

3. **Rsyslog**:
   - Centralized logging service
   - Socket at `{ubersmith_home}/logs/rsyslog/`

### Maintenance Mode

To enable maintenance mode:
```bash
echo "MAINTENANCE=1" > .env
docker compose restart web
```

To disable:
```bash
echo "MAINTENANCE=0" > .env
docker compose restart web
```

## Deployment Commands

### Starting Services
```bash
docker compose up -d
```

### Stopping Services
```bash
docker compose down
```

### Viewing Logs
```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f web

# Via journald (Linux)
journalctl -t ubersmith/web -f
```

### Backup Operations
```bash
# Database backup
docker compose run --rm backup

# Redis backup
docker compose run --rm redis-backup
```

### Certificate Renewal
```bash
docker compose run --rm certbot
```

### Accessing Redis Commander
```bash
docker compose up -d redis-commander
# Access via http://localhost:8081
```

## Security Considerations

1. **Sensitive Data**:
   - Database passwords are auto-generated and stored in user's home directory
   - Never commit passwords to version control
   - Use secrets management for production deployments

2. **Network Security**:
   - MySQL only exposed to localhost by default
   - TLS/SSL required for HTTPS
   - Mail server uses opportunistic TLS

3. **Runtime Security**:
   - Falco monitors for anomalous behavior
   - ClamAV scans emails for malware
   - Services run with minimal required privileges

4. **Updates**:
   - Regular container image updates required
   - ClamAV signature updates automatic
   - SSL certificates auto-renewed via certbot

## Troubleshooting

### Common Issues

1. **Service won't start**: Check dependencies with `docker compose ps` and logs with `docker compose logs [service]`
2. **Database connection errors**: Verify `db` service is healthy and passwords are correct
3. **Permission issues**: Ensure host volumes have correct ownership
4. **Port conflicts**: Check if required ports are already in use on host

### Health Checks

Most services have restart policies set to `unless-stopped`, ensuring automatic recovery. The `rsyslog` service includes a health check that dependent services wait for.

### Resource Monitoring

Use PMM (Percona Monitoring and Management) at `https://hostname:8443` for:
- Database performance metrics
- Query analysis
- System resource usage

---

*This documentation is generated from Ansible templates. For the latest version, refer to the template files in `roles/ubersmith/templates/`.*
