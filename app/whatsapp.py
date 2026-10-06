import re
from datetime import datetime

import requests
from flask import current_app

from .extensions import db
from .whatsapp_models import (
    WhatsAppNotification,
    WhatsAppPreference,
)


def normalize_phone(value):
    """
    Normaliza número brasileiro para o formato usado pela API.

    Exemplos:
        (81) 99999-9999 -> 5581999999999
        +55 81 99999-9999 -> 5581999999999
    """
    digits = re.sub(r"\D+", "", value or "")

    if digits.startswith("0"):
        digits = digits.lstrip("0")

    if len(digits) in (10, 11):
        digits = "55" + digits

    if not digits.startswith("55"):
        raise ValueError(
            "Informe um número brasileiro válido com DDD."
        )

    if len(digits) not in (12, 13):
        raise ValueError(
            "Informe um WhatsApp válido com DDD."
        )

    return digits


def mask_phone(phone):
    digits = re.sub(r"\D+", "", phone or "")

    if len(digits) >= 13:
        return (
            f"+{digits[:2]} "
            f"({digits[2:4]}) "
            f"{digits[4:9]}-{digits[9:]}"
        )

    return phone or ""


def get_preference(user_id):
    return (
        WhatsAppPreference.query
        .filter_by(user_id=user_id)
        .first()
    )


def whatsapp_is_configured():
    config = current_app.config

    required = (
        config.get("WHATSAPP_ACCESS_TOKEN"),
        config.get("WHATSAPP_PHONE_NUMBER_ID"),
        config.get("WHATSAPP_API_VERSION"),
        config.get("WHATSAPP_CONFIRMATION_TEMPLATE"),
        config.get("WHATSAPP_REMINDER_TEMPLATE"),
    )

    return all(
        bool(str(value or "").strip())
        for value in required
    )


def _template_payload(
    *,
    phone,
    template_name,
    customer_name,
    service_name,
    date_text,
    time_text,
):
    """
    Os templates da Meta devem possuir quatro variáveis no BODY:

        {{1}} nome do cliente
        {{2}} serviço
        {{3}} data
        {{4}} hora
    """
    return {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {
                "code": current_app.config.get(
                    "WHATSAPP_TEMPLATE_LANGUAGE",
                    "pt_BR",
                )
            },
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {
                            "type": "text",
                            "text": customer_name,
                        },
                        {
                            "type": "text",
                            "text": service_name,
                        },
                        {
                            "type": "text",
                            "text": date_text,
                        },
                        {
                            "type": "text",
                            "text": time_text,
                        },
                    ],
                }
            ],
        },
    }


def _send_template(
    *,
    phone,
    template_name,
    customer_name,
    service_name,
    date_text,
    time_text,
):
    if not whatsapp_is_configured():
        return {
            "ok": False,
            "error": (
                "WhatsApp Cloud API ainda não configurada."
            ),
        }

    config = current_app.config

    api_version = config[
        "WHATSAPP_API_VERSION"
    ].strip()

    phone_number_id = config[
        "WHATSAPP_PHONE_NUMBER_ID"
    ].strip()

    access_token = config[
        "WHATSAPP_ACCESS_TOKEN"
    ].strip()

    url = (
        "https://graph.facebook.com/"
        f"{api_version}/"
        f"{phone_number_id}/messages"
    )

    payload = _template_payload(
        phone=phone,
        template_name=template_name,
        customer_name=customer_name,
        service_name=service_name,
        date_text=date_text,
        time_text=time_text,
    )

    try:
        response = requests.post(
            url,
            json=payload,
            headers={
                "Authorization":
                    f"Bearer {access_token}",
                "Content-Type":
                    "application/json",
            },
            timeout=20,
        )

        try:
            data = response.json()
        except Exception:
            data = {}

        if response.ok:
            messages = data.get("messages") or []

            message_id = (
                messages[0].get("id")
                if messages
                else None
            )

            return {
                "ok": True,
                "message_id": message_id,
            }

        error_message = (
            data.get("error", {}).get("message")
            or f"HTTP {response.status_code}"
        )

        return {
            "ok": False,
            "error": error_message,
        }

    except requests.RequestException as exc:
        return {
            "ok": False,
            "error": str(exc),
        }


def _notification_record(
    appointment_id,
    notification_type,
):
    record = (
        WhatsAppNotification.query
        .filter_by(
            appointment_id=appointment_id,
            notification_type=notification_type,
        )
        .first()
    )

    if record:
        return record

    record = WhatsAppNotification(
        appointment_id=appointment_id,
        notification_type=notification_type,
        status="pending",
        attempts=0,
    )

    db.session.add(record)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()

        record = (
            WhatsAppNotification.query
            .filter_by(
                appointment_id=appointment_id,
                notification_type=notification_type,
            )
            .first()
        )

    return record


def send_appointment_template(
    *,
    appointment,
    user,
    service,
    notification_type,
):
    """
    Envia confirmação ou lembrete.

    Falha do WhatsApp nunca desfaz o agendamento.
    """
    preference = get_preference(user.id)

    if (
        not preference
        or not preference.opt_in
        or not preference.phone
    ):
        return {
            "ok": False,
            "skipped": True,
            "error":
                "Cliente não ativou mensagens no WhatsApp.",
        }

    record = _notification_record(
        appointment.id,
        notification_type,
    )

    if (
        record
        and record.status == "sent"
    ):
        return {
            "ok": True,
            "already_sent": True,
            "message_id":
                record.provider_message_id,
        }

    if notification_type == "confirmation":
        template_name = current_app.config.get(
            "WHATSAPP_CONFIRMATION_TEMPLATE",
            "",
        )
    elif notification_type == "reminder":
        template_name = current_app.config.get(
            "WHATSAPP_REMINDER_TEMPLATE",
            "",
        )
    else:
        raise ValueError(
            "Tipo de notificação inválido."
        )

    result = _send_template(
        phone=preference.phone,
        template_name=template_name,
        customer_name=user.name,
        service_name=service.name,
        date_text=appointment.start_at.strftime(
            "%d/%m/%Y"
        ),
        time_text=appointment.start_at.strftime(
            "%H:%M"
        ),
    )

    if not record:
        return result

    record.attempts += 1

    if result.get("ok"):
        record.status = "sent"
        record.provider_message_id = (
            result.get("message_id")
        )
        record.sent_at = datetime.utcnow()
        record.last_error = None
    else:
        record.status = "failed"
        record.last_error = (
            result.get("error")
            or "Falha não informada."
        )

    db.session.commit()

    return result


def send_booking_confirmation(
    appointment,
    user,
    service,
):
    return send_appointment_template(
        appointment=appointment,
        user=user,
        service=service,
        notification_type="confirmation",
    )


def send_booking_reminder(
    appointment,
    user,
    service,
):
    return send_appointment_template(
        appointment=appointment,
        user=user,
        service=service,
        notification_type="reminder",
    )
