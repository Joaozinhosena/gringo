import secrets
import sys

from run import app
from app.email_service import (
    send_verification_email,
)


if len(sys.argv) < 2:
    raise SystemExit(
        "Uso: python teste_email.py seuemail@exemplo.com"
    )


destination = (
    sys.argv[1]
    .strip()
    .lower()
)

code = (
    f"{secrets.randbelow(1_000_000):06d}"
)


with app.app_context():
    send_verification_email(
        to_email=destination,
        customer_name="Teste",
        code=code,
    )


print(
    "E-mail de teste enviado."
)

print(
    "Código:",
    code,
)
