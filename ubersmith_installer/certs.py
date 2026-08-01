"""Self-signed SSL certificate generation for the Ubersmith installer.

This module reproduces, using the ``cryptography`` library, what
``roles/ubersmith/tasks/main.yml`` does today via three
``community.crypto`` Ansible modules (see the tasks "Create private keys
rsa 4096 bits", "Create certificate signing requests", and "Create self
signed certificates", each looping ``with_items: "{{ virtual_hosts }}"``):

    * ``community.crypto.openssl_privatekey`` -- writes
      ``{{ ubersmith_home }}/conf/ssl/{{ item }}.key``. No ``type`` or
      ``size`` is specified in the playbook, so the module defaults apply:
      RSA, 4096 bits.
    * ``community.crypto.openssl_csr`` -- writes
      ``{{ ubersmith_home }}/conf/ssl/{{ item }}.csr`` with
      ``organization_name: Ubersmith``, ``organizational_unit_name: Hosting``,
      and ``common_name: "{{ item }}"``.
    * ``community.crypto.x509_certificate`` with ``provider: selfsigned`` --
      writes ``{{ ubersmith_home }}/conf/ssl/{{ item }}.pem``, a self-signed
      certificate built from the key + CSR above.

These certificates are a temporary, self-signed placeholder for HTTP/SMTP
TLS termination (per the comment in the Ansible task) and are expected to be
replaced with a CA-issued certificate and key, or with a Let's Encrypt
certificate obtained separately via certbot.

Validity period note: community.crypto's ``x509_certificate`` selfsigned
provider requires the caller to supply ``selfsigned_not_after`` explicitly --
it has no built-in default. The Ansible role's task above does not set it,
which the community.crypto module (as of the versions available at the time
the role was written) treated as "+3650d" (10 years) when omitted in older
releases, and otherwise would fail. Since the exact effective value can't be
determined with certainty from the playbook alone, this implementation picks
a conservative, explicit 365-day validity window (matching the general
industry-standard default OpenSSL's own ``req``/``x509`` commands use), and
callers relying on a longer-lived self-signed cert should regenerate/rotate
accordingly.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

#: RSA key size, matching community.crypto.openssl_privatekey's default.
RSA_KEY_SIZE = 4096

#: Public exponent for RSA key generation (the standard/only sane choice).
RSA_PUBLIC_EXPONENT = 65537

#: Certificate validity period in days. community.crypto's x509_certificate
#: "selfsigned" provider has no built-in default validity -- see module
#: docstring above for the reasoning behind this choice.
CERT_VALIDITY_DAYS = 365


def generate_selfsigned_cert(hostname: str, ssl_dir: Path) -> None:
    """Generate a private key, CSR, and self-signed certificate for a host.

    Mirrors, for a single ``hostname``, one iteration of the
    ``with_items: "{{ virtual_hosts }}"`` loop across the three Ansible
    tasks described in this module's docstring. Writes:

        * ``<ssl_dir>/<hostname>.key``
        * ``<ssl_dir>/<hostname>.csr``
        * ``<ssl_dir>/<hostname>.pem``

    ``ssl_dir`` is created if it does not already exist. Callers with a
    comma-separated ``virtual_hosts`` list should invoke this once per
    hostname, same as the Ansible ``with_items`` loop did.
    """
    ssl_dir.mkdir(parents=True, exist_ok=True)

    key_path = ssl_dir / f"{hostname}.key"
    csr_path = ssl_dir / f"{hostname}.csr"
    pem_path = ssl_dir / f"{hostname}.pem"

    # 1. Private key: RSA 4096 bits (community.crypto.openssl_privatekey
    #    default).
    private_key = rsa.generate_private_key(
        public_exponent=RSA_PUBLIC_EXPONENT,
        key_size=RSA_KEY_SIZE,
    )
    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    # 2. Certificate signing request: organization_name="Ubersmith",
    #    organizational_unit_name="Hosting", common_name=hostname
    #    (community.crypto.openssl_csr fields, matched exactly).
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Ubersmith"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Hosting"),
            x509.NameAttribute(NameOID.COMMON_NAME, hostname),
        ]
    )
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(subject)
        .sign(private_key, hashes.SHA256())
    )
    csr_path.write_bytes(csr.public_bytes(serialization.Encoding.PEM))

    # 3. Self-signed certificate built from the key + CSR above
    #    (community.crypto.x509_certificate, provider: selfsigned).
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(csr.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=CERT_VALIDITY_DAYS))
        .sign(private_key, hashes.SHA256())
    )
    pem_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
