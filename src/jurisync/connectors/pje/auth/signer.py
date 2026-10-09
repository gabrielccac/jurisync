"""Sign the TRF5 SSO challenge with an A1 certificate, without PJeOffice."""

import argparse
import base64
import json
import os
from pathlib import Path

SSO_CALLBACK = "https://sso.cloud.pje.jus.br/auth/realms/pje/pjeoffice-rest"


def a1_path():
    """The A1 file configured for this customer (PJE_A1_P12)."""
    path = os.environ.get("PJE_A1_P12")
    if not path:
        raise RuntimeError("Set PJE_A1_P12 to the A1 .p12/.pfx file")
    return Path(path.strip().strip('"')).expanduser()


def load_a1(p12_path, pin):
    """Load (key, cert, chain) from the A1 file. PIN from caller/env only."""
    from cryptography.hazmat.primitives.serialization import pkcs12

    data = Path(p12_path).read_bytes()
    key, cert, chain = pkcs12.load_key_and_certificates(data, (pin or "").encode("utf-8"))
    if key is None or cert is None:
        raise ValueError("A1 file has no private key/certificate (wrong PIN?)")
    return key, cert, list(chain or [])


def _der_length(n):
    if n < 128:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def pkipath_encode(certs_der):
    """Java CertPath PkiPath: DER SEQUENCE OF Certificate."""
    body = b"".join(certs_der)
    return b"\x30" + _der_length(len(body)) + body


def sign_mensagem(mensagem, key):
    """PJeOffice SSO sends raw MD5withRSA bytes, not a CMS envelope."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    if isinstance(mensagem, str):
        mensagem = mensagem.encode("utf-8")
    return key.sign(mensagem, padding.PKCS1v15(), hashes.MD5())


def certificate_chain64(cert, chain):
    """Base64 PkiPath (Java CertPath): DER SEQUENCE OF certificates.

    Matches signer4j ``Certificates.toByteArray`` (``PkiPath`` encoding),
    NOT raw DER concatenation.
    """
    from cryptography.hazmat.primitives.serialization import Encoding

    blobs = [cert.public_bytes(Encoding.DER)]
    blobs.extend(c.public_bytes(Encoding.DER) for c in chain)
    return base64.b64encode(pkipath_encode(blobs)).decode("ascii")


def build_payload(token, mensagem, signature, chain64):
    return {
        "uuid": token,
        "mensagem": mensagem,
        "assinatura": base64.b64encode(signature).decode("ascii"),
        "certChain": chain64,
    }


def post_result(session, payload):
    """Send the callback with the SSO cookies from the challenge session."""
    response = session.post(
        SSO_CALLBACK,
        json=payload,
        headers={"Accept": "application/json", "versao": "2.5.16"},
        impersonate="chrome",
        allow_redirects=False,
        timeout=30,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"SSO rejected the certificate callback (HTTP {response.status_code})")
    return response


def main():
    parser = argparse.ArgumentParser(description="Preview a native PJe SSO signature offline")
    parser.add_argument("--p12", type=Path, default=None)
    parser.add_argument("--pin", default=os.environ.get("PJE_A1_PIN"))
    parser.add_argument("--mensagem", required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    if not args.pin:
        parser.error("PIN via --pin ou PJE_A1_PIN")
    key, cert, chain = load_a1(args.p12 or a1_path(), args.pin)
    signature = sign_mensagem(args.mensagem, key)
    payload = build_payload(args.token, args.mensagem, signature, certificate_chain64(cert, chain))
    payload["assinatura"] = payload["assinatura"][:64] + "..."
    print(json.dumps({"ok": True, "payload_preview": payload, "signature_bytes": len(signature)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
