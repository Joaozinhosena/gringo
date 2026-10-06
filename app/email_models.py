from datetime import datetime

from .extensions import db


class PendingRegistration(db.Model):
    """
    Cadastro temporário.

    A conta em users só é criada depois que o código de e-mail for confirmado.
    """
    __tablename__ = "pending_registrations"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    name = db.Column(
        db.String(120),
        nullable=False,
    )

    email = db.Column(
        db.String(180),
        nullable=False,
        unique=True,
        index=True,
    )

    password_hash = db.Column(
        db.String(255),
        nullable=False,
    )

    code_hash = db.Column(
        db.String(255),
        nullable=False,
    )

    expires_at = db.Column(
        db.DateTime,
        nullable=False,
        index=True,
    )

    resend_available_at = db.Column(
        db.DateTime,
        nullable=False,
    )

    attempts = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
