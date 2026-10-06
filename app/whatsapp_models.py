from datetime import datetime

from .extensions import db


class WhatsAppPreference(db.Model):
    """
    Preferência de WhatsApp por usuário.

    Não altera a tabela users existente, evitando quebrar o projeto atual.
    """
    __tablename__ = "whatsapp_preferences"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        nullable=False,
        unique=True,
        index=True,
    )

    phone = db.Column(
        db.String(20),
        nullable=False,
    )

    opt_in = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        index=True,
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


class WhatsAppNotification(db.Model):
    """
    Histórico técnico das mensagens.

    A restrição única impede que o mesmo tipo de mensagem seja
    enviado repetidamente para o mesmo agendamento.
    """
    __tablename__ = "whatsapp_notifications"

    __table_args__ = (
        db.UniqueConstraint(
            "appointment_id",
            "notification_type",
            name="uq_whatsapp_appointment_type",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)

    appointment_id = db.Column(
        db.Integer,
        nullable=False,
        index=True,
    )

    notification_type = db.Column(
        db.String(30),
        nullable=False,
        index=True,
    )

    status = db.Column(
        db.String(20),
        nullable=False,
        default="pending",
        index=True,
    )

    attempts = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    provider_message_id = db.Column(
        db.String(150),
        nullable=True,
    )

    last_error = db.Column(
        db.Text,
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    sent_at = db.Column(
        db.DateTime,
        nullable=True,
    )
