import hashlib
import hmac
import json
from datetime import datetime

from flask import (
    Blueprint,
    current_app,
    request,
)

from .extensions import db

try:
    from .whatsapp_models import WhatsAppNotification
except ImportError:
    WhatsAppNotification = None


bp = Blueprint(
    "whatsapp_webhook",
    __name__,
    url_prefix="/webhooks/whatsapp",
)


def _safe_compare_signature(raw_body):
    """
    Valida X-Hub-Signature-256 quando META_APP_SECRET estiver configurado.

    Em testes, META_APP_SECRET pode ficar vazio. Nesse caso a rota continua
    funcionando, mas a validação criptográfica fica desativada.
    """
    app_secret = (
        current_app.config.get("META_APP_SECRET", "")
        or ""
    ).strip()

    if not app_secret:
        return True

    received = (
        request.headers.get("X-Hub-Signature-256", "")
        or ""
    ).strip()

    if not received.startswith("sha256="):
        return False

    expected = (
        "sha256="
        + hmac.new(
            app_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
    )

    return hmac.compare_digest(
        received,
        expected,
    )


def _format_errors(errors):
    if not errors:
        return ""

    parts = []

    for item in errors:
        if not isinstance(item, dict):
            continue

        code = item.get("code")
        title = item.get("title")
        message = item.get("message")

        details = (
            (item.get("error_data") or {})
            .get("details")
        )

        text = " | ".join(
            str(value)
            for value in (
                f"code={code}" if code is not None else None,
                title,
                message,
                details,
            )
            if value
        )

        if text:
            parts.append(text)

    return " || ".join(parts)


def _update_local_notification(message_id, provider_status, errors):
    """
    Atualiza apenas o estado técnico necessário sem quebrar a lógica
    de deduplicação já usada em whatsapp.py.

    Para sent/delivered/read, mantemos status='sent'.
    Para failed, usamos status='failed'.
    """
    if not message_id or WhatsAppNotification is None:
        return

    try:
        record = (
            WhatsAppNotification.query
            .filter_by(
                provider_message_id=message_id
            )
            .first()
        )

        if not record:
            return

        if provider_status in {
            "sent",
            "delivered",
            "read",
        }:
            record.status = "sent"

            if not record.sent_at:
                record.sent_at = datetime.utcnow()

            record.last_error = None

        elif provider_status == "failed":
            record.status = "failed"
            record.last_error = (
                _format_errors(errors)
                or "Falha informada pela Meta."
            )

        db.session.commit()

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Falha ao atualizar WhatsAppNotification."
        )


def _print_status(status):
    message_id = (
        status.get("id")
        or "sem-id"
    )

    provider_status = (
        status.get("status")
        or "desconhecido"
    )

    recipient_id = (
        status.get("recipient_id")
        or "desconhecido"
    )

    timestamp = status.get("timestamp")

    conversation = (
        status.get("conversation")
        or {}
    )

    pricing = (
        status.get("pricing")
        or {}
    )

    errors = (
        status.get("errors")
        or []
    )

    print()
    print("=" * 72)
    print("WHATSAPP STATUS")
    print("Mensagem:", message_id)
    print("Destinatário:", recipient_id)
    print("Status:", provider_status)

    if timestamp:
        print("Timestamp Meta:", timestamp)

    if conversation:
        print(
            "Conversation ID:",
            conversation.get("id"),
        )

    if pricing:
        print(
            "Categoria:",
            pricing.get("category"),
        )

    if errors:
        print("ERRO(S):")

        for error in errors:
            print(
                json.dumps(
                    error,
                    ensure_ascii=False,
                    indent=2,
                )
            )

    print("=" * 72)
    print()


@bp.get("")
@bp.get("/")
def verify():
    """
    Verificação inicial feita pela Meta.

    A Meta chama algo parecido com:
    GET /webhooks/whatsapp
        ?hub.mode=subscribe
        &hub.verify_token=...
        &hub.challenge=...
    """
    mode = request.args.get(
        "hub.mode",
        "",
    )

    token = request.args.get(
        "hub.verify_token",
        "",
    )

    challenge = request.args.get(
        "hub.challenge",
        "",
    )

    expected_token = (
        current_app.config.get(
            "WHATSAPP_WEBHOOK_VERIFY_TOKEN",
            "",
        )
        or ""
    ).strip()

    if (
        mode == "subscribe"
        and expected_token
        and hmac.compare_digest(
            token,
            expected_token,
        )
    ):
        current_app.logger.info(
            "Webhook do WhatsApp verificado pela Meta."
        )

        return challenge, 200, {
            "Content-Type":
                "text/plain; charset=utf-8"
        }

    current_app.logger.warning(
        "Tentativa de verificação inválida do webhook."
    )

    return "Forbidden", 403


@bp.post("")
@bp.post("/")
def receive():
    """
    Recebe eventos do WhatsApp Cloud API.

    Para status de mensagens imprime no terminal:
        sent
        delivered
        read
        failed

    Também mostra detalhes de erro quando a Meta informar falha.
    """
    raw_body = request.get_data(
        cache=True,
    )

    if not _safe_compare_signature(
        raw_body
    ):
        current_app.logger.warning(
            "Webhook rejeitado: assinatura inválida."
        )

        return "Invalid signature", 401

    payload = request.get_json(
        silent=True
    ) or {}

    try:
        entries = (
            payload.get("entry")
            or []
        )

        for entry in entries:
            changes = (
                entry.get("changes")
                or []
            )

            for change in changes:
                value = (
                    change.get("value")
                    or {}
                )

                statuses = (
                    value.get("statuses")
                    or []
                )

                for status in statuses:
                    _print_status(
                        status
                    )

                    _update_local_notification(
                        message_id=
                            status.get("id"),

                        provider_status=
                            status.get("status"),

                        errors=
                            status.get("errors")
                            or [],
                    )

                # Opcional: mostra mensagens recebidas do usuário.
                messages = (
                    value.get("messages")
                    or []
                )

                for message in messages:
                    print()
                    print("=" * 72)
                    print("WHATSAPP MENSAGEM RECEBIDA")
                    print(
                        "De:",
                        message.get("from"),
                    )
                    print(
                        "ID:",
                        message.get("id"),
                    )
                    print(
                        "Tipo:",
                        message.get("type"),
                    )

                    if (
                        message.get("type")
                        == "text"
                    ):
                        print(
                            "Texto:",
                            (
                                message
                                .get("text", {})
                                .get("body")
                            ),
                        )

                    print("=" * 72)
                    print()

    except Exception:
        # A Meta espera resposta rápida. Registramos o erro e ainda devolvemos 200
        # para evitar retries infinitos por falha do nosso processamento.
        current_app.logger.exception(
            "Erro processando webhook WhatsApp."
        )

    return "EVENT_RECEIVED", 200
