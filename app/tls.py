"""Self-signed HTTPS certificate for the local network.

Browsers only allow microphone access on secure pages. On the shop PC itself
http://127.0.0.1 counts as secure, but phones and tablets on the Wi-Fi need https.
The certificate is created once in data/tls/ and covers this computer's names and
local IP addresses; each device shows a warning the first time, which is accepted once.
"""

import datetime
import ipaddress
import os
import socket


def local_addresses():
    names = {"localhost", socket.gethostname()}
    ips = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # the address used for outgoing traffic = the LAN address
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(names), sorted(ips)


def ensure_cert(data_dir):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    d = os.path.join(data_dir, "tls")
    cert_path, key_path = os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")
    names, ips = local_addresses()
    if os.path.exists(cert_path) and os.path.exists(key_path):
        with open(cert_path, "rb") as fh:
            cert = x509.load_pem_x509_certificate(fh.read())
        try:
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            covered = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
        except x509.ExtensionNotFound:
            covered = set()
        still_valid = cert.not_valid_after_utc > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)
        if still_valid and set(ips) <= covered:
            return cert_path, key_path
    os.makedirs(d, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[-1] if names else "atolye"),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Atölye ERP (local)")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in names]
                                                   + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]),
                       critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    with open(key_path, "wb") as fh:
        fh.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                   serialization.NoEncryption()))
    with open(cert_path, "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path
