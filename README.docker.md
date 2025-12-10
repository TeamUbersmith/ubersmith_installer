# Ubersmith Docker Compose Architecture

This document provides a comprehensive overview of Ubersmith's Docker-based architecture, including service definitions, networking, data persistence, and configuration management.

## Table of Contents

- [Overview](#overview)
- [Services](#services)
  - [Core Application Services](#core-application-services)
  - [Database Services](#database-services)
  - [Communication Services](#communication-services)
  - [Monitoring and Management Services](#monitoring-and-management-services)
  - [Security Services](#security-services)
  - [Utility Services](#utility-services)
- [Networking](#networking)
- [Volume Management](#volume-management)
- [Dependencies and Prerequisites](#dependencies-and-prerequisites)

## Overview

Ubersmith is deployed as a containerized microservices application using Docker Compose. The architecture consists of multiple services that work together to provide a complete billing and customer management platform.

The application uses:
- **Web server** (Apache/HTTPD) for serving HTTP/HTTPS traffic
- **PHP-FPM** for processing PHP application logic
- **MySQL/Percona Server** for relational data storage
- **Redis** for caching, session management, and queue handling
- **Solr** for search functionality
- **Postfix** for email services
- **ClamAV** for antivirus scanning

## Services

### Core Application Services

#### web
- **Image**: `ubersmith:{version}-{release}`
- **Purpose**: Apache HTTPD web server serving as the front-end for the Ubersmith application
- **Ports**: 
  - `80:80` - HTTP traffic (redirects to HTTPS)
  - `443:443` - HTTPS traffic
- **Key Features**:
  - TLS/SSL termination
  - Serves static content
  - Proxies requests to PHP-FPM
  - Enforces HTTPS redirection (`HTTPS_FORCE: 1`)
- **Environment Variables**:
  - `MYSQL_USER`, `MYSQL_DATABASE`, `DATABASE_HOST` - Database connection
  - `LOCK_BACKEND`, `LOCK_SERVERS` - Redis-based advisory locking
  - `STORAGE_BACKEND`, `REDIS_HOST`, `SESSION_HOST` - Redis data stores
  - `QUEUE_HOST`, `PLUGIN_LOG_HOST` - Redis data stores
  - `UBER_HOSTNAME` - Primary hostname
  - `MAINTENANCE` - Maintenance mode toggle
  - `OPENSSL_CIPHERS` - TLS cipher configuration
- **Volumes**:
  - `webroot:/var/www/ubersmith_root` - Application files
  - `logs:/var/www/ubersmith_root/logs` - Application logs
  - `search:/opt/solr` - Solr search index
  - Custom code and patches directories
  - SSL certificates and Apache configuration

#### php
- **Image**: `php{php_version}:{ubersmith_version}-{containers_release_version}`
- **Purpose**: PHP-FPM process manager for executing PHP application code
- **Key Features**:
  - Processes PHP requests from the web service
  - Handles application business logic
- **Dependencies**:
  - `web` (service must be started)
  - `rsyslog` (must be healthy)
- **Volumes**:
  - Shares `webroot` and `logs` with web service
  - rsyslog socket for event logging

#### cron
- **Image**: `cron:{version}-{release}`
- **Purpose**: Scheduled task execution for maintenance and background jobs
- **Key Features**:
  - Runs periodic polling and invoicing tasks
- **Dependencies**: 
  - Depends on `web` service
- **Volumes**:
  - Shares application `webroot` and `logs`
  - Custom code and patches directories

### Database Services

#### db
- **Image**: `ps{mysql_version}:{ubersmith_version}-{containers_release_version}`
- **Purpose**: Primary relational database for application data (Percona Server)
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
- **Capabilities**: 
  - `SYS_NICE` - For resource management

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
- **Purpose**: Additional Redis instance for advisory locking
- **Configuration**:
  - 3-nodes, non-clustered
  - Database 1 used for locking

#### solr
- **Image**: `solr:{version}-{release}`
- **Purpose**: Apache Solr search engine for full-text search capabilities
- **Features**:
  - Indexes application data for fast searching
  - Supports complex queries
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

#### rsyslog
- **Image**: `rsyslog:{version}-{release}`
- **Purpose**: Centralized logging service
- **Features**:
  - Collects logs from all services
  - Provides socket for log forwarding
- **Volumes**:
  - Host directory for log sockets

#### redis-commander
- **Image**: `rediscommander/redis-commander:latest`
- **Purpose**: Web-based Redis management interface
- **Ports**: 
  - `8081` - Web interface
- **Configuration**:
  - Connects to `redis-data` instance
  - Database 3 (plugin logs) by default
- **Restart Policy**: `no` (manual start)

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
- **Volumes**:
  - Certificate storage directories
  - Webroot for HTTP validation
  - SSL certificate deployment directory

### Utility Services

#### backup
- **Image**: `xtrabackup{backup_version}:{ubersmith_version}-{containers_release_version}`
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
- **Volumes**:
  - `redis:/data:ro` - Read-only Redis data
  - Backup destination on host

## Networking

### Default Network
All services communicate through a default Docker bridge network created by Docker Compose. Services can reference each other by their service name (e.g., `db`, `redis-data`).

### Port Exposure
- **Exposed to Host**:
  - `80, 443` (web) - Public HTTP/HTTPS access
  - `25` (mail) - SMTP server
  - `4321` (rwhois) - RWhois protocol
  - `127.0.0.1:3306` (db) - MySQL (localhost only)
  - `8081` (redis-commander) - Redis management (optional)

### Service Discovery
Services use Docker's internal DNS for name resolution. For example:
- Web service connects to database via `DATABASE_HOST=db`
- Redis connections use service names: `ubersmith-redis-data-1:6379`

### Inter-Service Communication

```
web → php (FastCGI)
web → redis-data (Session, Cache, Queue)
web → redis (Advisory Locks)
web → solr (Search)
mail → clamav (Virus Scanning)
php → db (Database Queries)
php → redis-data (Plugin / Session Operations)
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

## Dependencies and Prerequisites

### Host System Requirements

1. **Operating System**: 
   - Linux (Rocky, Ubuntu, Debian)
   - Docker for Mac (unsupported)

2. **Docker Engine**: 
   - Docker Engine 20.10 or later
   - Docker Compose V2 (plugin)

3. **System Resources**:
   - CPU: Minimum 4 cores (8+ recommended)
   - RAM: Minimum 8GB (16GB+ recommended)
   - Disk: 100GB+ available space (more for data growth)

4. **Network Requirements**:
   - Ports 80, 443, 25, 4321 available
   - Internet access for pulling container images

5. **Image Registry Access**:
   - Access to `ghcr.io/teamubersmith` registry
   - Authentication credentials for private images

## Troubleshooting

### Health Checks

Most services have restart policies set to `unless-stopped`, ensuring automatic recovery. The `rsyslog` service includes a health check that dependent services wait for.

