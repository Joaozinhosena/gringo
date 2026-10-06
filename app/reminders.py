import threading
import time as time_module
from datetime import datetime, timedelta, timezone

from .extensions import db
from .models import Appointment, Service, User
from .whatsapp import send_booking_reminder
from .whatsapp_models import WhatsAppNotification


_worker_started = False
_worker_lock = threading.Lock()


def _local_now():
    return datetime.now(
        timezone(
            timedelta(hours=-3)
        )
    ).replace(tzinfo=None)


def process_due_reminders(application):
    """
    Envia lembrete uma única vez para atendimentos que começam
    dentro do período configurado.
    """
    with application.app_context():
        minutes = int(
            application.config.get(
                "WHATSAPP_REMINDER_MINUTES",
                60,
            )
        )

        now = _local_now()
        limit = now + timedelta(
            minutes=minutes
        )

        appointments = (
            Appointment.query
            .filter(
                Appointment.status == "scheduled",
                Appointment.start_at > now,
                Appointment.start_at <= limit,
            )
            .order_by(
                Appointment.start_at.asc()
            )
            .all()
        )

        for appointment in appointments:
            already_sent = (
                WhatsAppNotification.query
                .filter_by(
                    appointment_id=
                        appointment.id,
                    notification_type=
                        "reminder",
                    status="sent",
                )
                .first()
            )

            if already_sent:
                continue

            user = db.session.get(
                User,
                appointment.user_id,
            )

            service = db.session.get(
                Service,
                appointment.service_id,
            )

            if not user or not service:
                continue

            try:
                send_booking_reminder(
                    appointment,
                    user,
                    service,
                )
            except Exception:
                application.logger.exception(
                    "Falha no lembrete WhatsApp "
                    "do agendamento %s.",
                    appointment.id,
                )


def _worker_loop(application):
    interval = max(
        30,
        int(
            application.config.get(
                "WHATSAPP_REMINDER_CHECK_SECONDS",
                60,
            )
        ),
    )

    while True:
        try:
            process_due_reminders(
                application
            )
        except Exception:
            application.logger.exception(
                "Erro no worker de lembretes."
            )

        time_module.sleep(
            interval
        )


def start_reminder_worker(application):
    """
    Inicia um único worker no processo atual.

    Adequado ao servidor local/threading usado neste projeto.
    """
    global _worker_started

    with _worker_lock:
        if _worker_started:
            return

        _worker_started = True

        thread = threading.Thread(
            target=_worker_loop,
            args=(application,),
            name="whatsapp-reminder-worker",
            daemon=True,
        )

        thread.start()

        application.logger.info(
            "Worker de lembretes WhatsApp iniciado."
        )
