from datetime import datetime, time, timedelta, timezone
from threading import Lock

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from .extensions import db, socketio
from .models import (
    Appointment,
    Professional,
    PushSubscription,
    Service,
    User,
)
from .push import send_push_to_user
from .utils import require_csrf


bp = Blueprint("booking", __name__)


# ============================================================
# CONFIGURAÇÃO DA AGENDA
# ============================================================

BUSINESS_TZ = timezone(
    timedelta(hours=-3),
    name="America/Recife",
)

# Grade de horários:
# - primeiro horário: 09:00;
# - último horário que pode INICIAR: 18:00;
# - intervalos de 30 minutos.
OPEN_TIME = time(9, 0)
LAST_START_TIME = time(18, 0)
SLOT_STEP_MINUTES = 30

# Clientes padrão só enxergam a agenda do próprio dia
# a partir das 05:00.
STANDARD_RELEASE_TIME = time(5, 0)

# Nome usado por versões antigas como recurso técnico de agenda.
# Ele não deve aparecer para o cliente como barbeiro selecionável.
INTERNAL_RESOURCE_NAME = "Gringo Barber"
INTERNAL_RESOURCE_BIO_MARKER = "Registro interno da agenda"

WEEKDAY_LABELS = {
    0: "Seg",
    1: "Ter",
    2: "Qua",
    3: "Qui",
    4: "Sex",
    5: "Sáb",
}

# Evita confirmação simultânea dentro do mesmo processo Flask.
_booking_lock = Lock()


# ============================================================
# TEMPO / SEMANA
# ============================================================

def local_now():
    """Retorna o horário local da barbearia como datetime sem tzinfo."""
    return datetime.now(BUSINESS_TZ).replace(tzinfo=None)


def is_business_day(day):
    """Segunda a sábado. Domingo fechado."""
    return day.weekday() <= 5


def active_week_range(reference_date=None):
    """
    Segunda a sábado: semana atual.
    Domingo: abre automaticamente a semana seguinte.
    """
    if reference_date is None:
        reference_date = local_now().date()

    if reference_date.weekday() == 6:
        monday = reference_date + timedelta(days=1)
    else:
        monday = reference_date - timedelta(days=reference_date.weekday())

    return monday, monday + timedelta(days=5)


def user_has_vip_access(user=None):
    """
    VIP:
    acesso antecipado à agenda completa de segunda a sábado.

    Cliente padrão:
    somente o próprio dia, liberado às 05:00.
    """
    if user is None:
        user = current_user

    return bool(
        getattr(
            user,
            "is_vip",
            False,
        )
    )


def booking_dates_for_user(now=None, user=None):
    """
    Retorna somente as datas que o usuário pode visualizar.

    VIP:
        os 6 dias da semana ativa (segunda a sábado).

    PADRÃO:
        somente hoje, desde que:
        - hoje seja segunda a sábado;
        - já sejam 05:00 ou mais.
    """
    if now is None:
        now = local_now()

    if user is None:
        user = current_user

    if user_has_vip_access(user):
        week_start, week_end = active_week_range(
            now.date()
        )

        dates = []
        cursor_date = week_start

        while cursor_date <= week_end:
            dates.append(cursor_date)
            cursor_date += timedelta(days=1)

        return dates

    if not is_business_day(now.date()):
        return []

    if now.time() < STANDARD_RELEASE_TIME:
        return []

    return [now.date()]


def booking_access_message(now=None, user=None):
    if now is None:
        now = local_now()

    if user is None:
        user = current_user

    if user_has_vip_access(user):
        return (
            "Cliente VIP: acesso antecipado aos 6 dias da agenda, "
            "de segunda a sábado."
        )

    if not is_business_day(now.date()):
        return (
            "Clientes padrão agendam somente no próprio dia. "
            "A barbearia não abre aos domingos."
        )

    if now.time() < STANDARD_RELEASE_TIME:
        return (
            "A agenda de hoje será liberada às 05:00. "
            "Clientes padrão podem agendar somente para o próprio dia."
        )

    return (
        "Agenda do dia liberada. Clientes padrão podem agendar "
        "somente para hoje."
    )


def validate_user_booking_access(start_at, now=None, user=None):
    """
    Revalidação obrigatória no backend.
    Impede que a regra VIP/padrão seja burlada por chamada manual à API.
    """
    if now is None:
        now = local_now()

    if user is None:
        user = current_user

    if user_has_vip_access(user):
        week_start, week_end = active_week_range(
            now.date()
        )

        if not (
            week_start
            <= start_at.date()
            <= week_end
        ):
            return (
                "Clientes VIP podem reservar somente dentro "
                "dos 6 dias da semana liberada."
            )

        return None

    if start_at.date() != now.date():
        return (
            "Clientes padrão podem agendar somente para o próprio dia."
        )

    if now.time() < STANDARD_RELEASE_TIME:
        return (
            "A agenda de hoje ainda não foi liberada. "
            "Os horários abrem às 05:00."
        )

    return None


# ============================================================
# PROFISSIONAIS
# ============================================================

def is_internal_resource(professional):
    """Identifica o recurso técnico criado pela versão sem escolha de barbeiro."""
    if not professional:
        return False

    return (
        professional.name == INTERNAL_RESOURCE_NAME
        and INTERNAL_RESOURCE_BIO_MARKER.lower()
        in (professional.bio or "").lower()
    )


def get_active_professionals():
    """Lista somente profissionais ativos e realmente selecionáveis."""
    professionals = (
        Professional.query
        .filter_by(active=True)
        .order_by(Professional.name.asc(), Professional.id.asc())
        .all()
    )

    return [
        professional
        for professional in professionals
        if not is_internal_resource(professional)
    ]


def get_selectable_professional(professional_id):
    professional = db.session.get(Professional, professional_id)

    if (
        not professional
        or not professional.active
        or is_internal_resource(professional)
    ):
        return None

    return professional


# ============================================================
# REGRA: UM AGENDAMENTO ATIVO POR CLIENTE
# ============================================================

def get_active_user_appointment(user_id):
    now = local_now()

    return (
        Appointment.query
        .filter(
            Appointment.user_id == user_id,
            Appointment.status == "scheduled",
            Appointment.end_at > now,
        )
        .order_by(Appointment.start_at.asc())
        .first()
    )


# ============================================================
# DISPONIBILIDADE POR PROFISSIONAL
# ============================================================

def scheduled_appointments_for_day(professional_id, selected_date):
    day_start = datetime.combine(selected_date, time.min)
    day_end = day_start + timedelta(days=1)

    return (
        Appointment.query
        .filter(
            Appointment.professional_id == professional_id,
            Appointment.status == "scheduled",
            Appointment.start_at < day_end,
            Appointment.end_at > day_start,
        )
        .order_by(Appointment.start_at.asc())
        .all()
    )


def periods_overlap(start_at, end_at, appointment):
    return (
        start_at < appointment.end_at
        and end_at > appointment.start_at
    )


def build_available_times(service, professional, selected_date):
    """
    Gera os horários livres daquele profissional.

    Regras:
    - segunda a sábado;
    - primeiro início às 09:00;
    - último início às 18:00;
    - inícios de 30 em 30 minutos;
    - respeita a duração real do serviço;
    - não mostra horários passados;
    - ignora conflitos de OUTROS profissionais.
    """
    if not is_business_day(selected_date):
        return []

    if (
        not service
        or not service.active
        or not service.duration_minutes
        or service.duration_minutes <= 0
        or not professional
        or not professional.active
    ):
        return []

    now = local_now()
    opening = datetime.combine(
        selected_date,
        OPEN_TIME,
    )
    last_start = datetime.combine(
        selected_date,
        LAST_START_TIME,
    )

    appointments = scheduled_appointments_for_day(
        professional.id,
        selected_date,
    )

    available = []
    cursor = opening

    while cursor <= last_start:
        end_at = cursor + timedelta(
            minutes=service.duration_minutes
        )

        if cursor > now:
            occupied = any(
                periods_overlap(
                    cursor,
                    end_at,
                    appointment,
                )
                for appointment in appointments
            )

            if not occupied:
                available.append(
                    {
                        "start_at": cursor.strftime(
                            "%Y-%m-%dT%H:%M"
                        ),
                        "start": cursor.strftime(
                            "%H:%M"
                        ),
                        "end": end_at.strftime(
                            "%H:%M"
                        ),
                    }
                )

        cursor += timedelta(
            minutes=SLOT_STEP_MINUTES
        )

    return available


def validate_requested_time(start_at, service):
    now = local_now()

    if start_at <= now:
        return "Esse horário já passou."

    if not is_business_day(start_at.date()):
        return "A barbearia não abre aos domingos."

    access_error = validate_user_booking_access(
        start_at,
        now=now,
        user=current_user,
    )
    if access_error:
        return access_error

    if (
        start_at.minute % SLOT_STEP_MINUTES != 0
        or start_at.second != 0
        or start_at.microsecond != 0
    ):
        return "Horário inválido."

    if (
        not service
        or not service.active
        or not service.duration_minutes
        or service.duration_minutes <= 0
    ):
        return "Serviço inválido ou indisponível."

    opening = datetime.combine(
        start_at.date(),
        OPEN_TIME,
    )
    last_start = datetime.combine(
        start_at.date(),
        LAST_START_TIME,
    )

    if (
        start_at < opening
        or start_at > last_start
    ):
        return (
            "Esse horário está fora da grade de atendimento "
            "(09:00 às 18:00)."
        )

    return None


def find_schedule_conflict(professional_id, start_at, end_at):
    """Procura conflito somente na agenda do profissional escolhido."""
    return (
        Appointment.query
        .filter(
            Appointment.professional_id == professional_id,
            Appointment.status == "scheduled",
            Appointment.start_at < end_at,
            Appointment.end_at > start_at,
        )
        .order_by(Appointment.start_at.asc())
        .first()
    )


# ============================================================
# PÁGINA INICIAL
# ============================================================

@bp.get("/")
def index():
    services = (
        Service.query
        .filter_by(active=True)
        .order_by(Service.price_cents.asc(), Service.name.asc())
        .all()
    )

    return render_template(
        "index.html",
        services=services,
    )


# ============================================================
# PÁGINA DE AGENDAMENTO
# ============================================================

@bp.get("/agendar")
@login_required
def agenda():
    active_appointment = get_active_user_appointment(current_user.id)

    if active_appointment:
        flash(
            (
                "Você já possui um agendamento ativo para "
                f"{active_appointment.start_at.strftime('%d/%m às %H:%M')}. "
                "Cancele ou conclua esse atendimento antes de reservar outro."
            ),
            "warning",
        )
        return redirect(url_for("booking.dashboard"))

    services = (
        Service.query
        .filter_by(active=True)
        .order_by(Service.name.asc())
        .all()
    )

    professionals = get_active_professionals()

    return render_template(
        "agenda.html",
        services=services,
        professionals=professionals,
        has_professional=bool(professionals),
    )


# ============================================================
# PERFIL PÚBLICO DO CLIENTE
# ============================================================

@bp.get("/perfil/<int:user_id>")
def public_profile(user_id):
    user = db.session.get(User, user_id)

    if not user or user.role != "client":
        abort(404)

    return render_template(
        "public_profile.html",
        profile_user=user,
    )


# ============================================================
# DASHBOARD DO CLIENTE
# ============================================================

@bp.get("/dashboard")
@login_required
def dashboard():
    now = local_now()

    upcoming = (
        Appointment.query
        .filter(
            Appointment.user_id == current_user.id,
            Appointment.status == "scheduled",
            Appointment.end_at > now,
        )
        .order_by(Appointment.start_at.asc())
        .all()
    )

    history = (
        Appointment.query
        .filter(Appointment.user_id == current_user.id)
        .order_by(Appointment.start_at.desc())
        .limit(50)
        .all()
    )

    return render_template(
        "dashboard.html",
        upcoming=upcoming,
        history=history,
    )


# ============================================================
# API — PAINEL DA SEMANA
# ============================================================

@bp.get("/api/painel-agenda")
@login_required
def schedule_panel():
    """Retorna os horários permitidos para o usuário atual."""
    active_appointment = get_active_user_appointment(
        current_user.id
    )

    if active_appointment:
        return jsonify(
            {
                "ok": False,
                "error": "Você já possui um agendamento ativo.",
                "active_appointment": {
                    "id": active_appointment.id,
                    "date": active_appointment.start_at.strftime(
                        "%d/%m/%Y"
                    ),
                    "start": active_appointment.start_at.strftime(
                        "%H:%M"
                    ),
                    "end": active_appointment.end_at.strftime(
                        "%H:%M"
                    ),
                },
            }
        ), 409

    service_id = request.args.get(
        "service_id",
        type=int,
    )
    professional_id = request.args.get(
        "professional_id",
        type=int,
    )

    if not service_id:
        return jsonify(
            {
                "ok": False,
                "error": "Selecione um serviço.",
            }
        ), 400

    if not professional_id:
        return jsonify(
            {
                "ok": False,
                "error": "Selecione um profissional.",
            }
        ), 400

    service = db.session.get(
        Service,
        service_id,
    )
    professional = get_selectable_professional(
        professional_id
    )

    if not service or not service.active:
        return jsonify(
            {
                "ok": False,
                "error": "Serviço indisponível.",
            }
        ), 404

    if not professional:
        return jsonify(
            {
                "ok": False,
                "error": "Profissional indisponível.",
            }
        ), 404

    now = local_now()
    allowed_dates = booking_dates_for_user(
        now=now,
        user=current_user,
    )

    days = []

    for selected_date in allowed_dates:
        days.append(
            {
                "date": selected_date.isoformat(),
                "weekday": WEEKDAY_LABELS[
                    selected_date.weekday()
                ],
                "label": selected_date.strftime(
                    "%d/%m"
                ),
                "full_label": selected_date.strftime(
                    "%d/%m/%Y"
                ),
                "slots": build_available_times(
                    service,
                    professional,
                    selected_date,
                ),
            }
        )

    is_vip = user_has_vip_access(
        current_user
    )

    if is_vip:
        week_start, week_end = active_week_range(
            now.date()
        )
    else:
        week_start = now.date()
        week_end = now.date()

    return jsonify(
        {
            "ok": True,
            "days": days,
            "message": booking_access_message(
                now=now,
                user=current_user,
            ),
            "access": {
                "is_vip": is_vip,
                "type": (
                    "vip_week"
                    if is_vip
                    else "standard_same_day"
                ),
                "standard_release_time": (
                    STANDARD_RELEASE_TIME.strftime(
                        "%H:%M"
                    )
                ),
                "vip_days": 6,
            },
            "week": {
                "start": week_start.strftime(
                    "%d/%m/%Y"
                ),
                "end": week_end.strftime(
                    "%d/%m/%Y"
                ),
            },
            "service": {
                "id": service.id,
                "name": service.name,
                "duration": service.duration_minutes,
                "price_cents": service.price_cents,
            },
            "professional": {
                "id": professional.id,
                "name": professional.name,
                "bio": professional.bio or "",
            },
            "business_hours": {
                "open": OPEN_TIME.strftime(
                    "%H:%M"
                ),
                "last_start": LAST_START_TIME.strftime(
                    "%H:%M"
                ),
            },
            "rules": {
                "single_active_booking": True,
                "professional_selection": True,
                "slot_step_minutes": SLOT_STEP_MINUTES,
                "vip_week_access": True,
                "standard_same_day_only": True,
                "standard_release_at": "05:00",
            },
        }
    )


# ============================================================
# API — CRIAR AGENDAMENTO
# ============================================================

@bp.post("/api/agendar-rapido")
@login_required
def quick_book():
    """
    Espera JSON:
        service_id
        professional_id
        start_at  (YYYY-MM-DDTHH:MM)
    """
    require_csrf()

    data = request.get_json(silent=True) or {}

    try:
        service_id = int(data.get("service_id"))
        professional_id = int(data.get("professional_id"))
        start_at = datetime.strptime(
            str(data.get("start_at")),
            "%Y-%m-%dT%H:%M",
        )
    except (TypeError, ValueError):
        return jsonify(
            {
                "ok": False,
                "error": "Dados do agendamento inválidos.",
            }
        ), 400

    service = db.session.get(Service, service_id)
    professional = get_selectable_professional(professional_id)

    if not service or not service.active:
        return jsonify({"ok": False, "error": "Serviço indisponível."}), 404

    if not professional:
        return jsonify({"ok": False, "error": "Profissional indisponível."}), 404

    validation_error = validate_requested_time(start_at, service)
    if validation_error:
        return jsonify({"ok": False, "error": validation_error}), 409

    end_at = start_at + timedelta(minutes=service.duration_minutes)

    with _booking_lock:
        active_appointment = get_active_user_appointment(current_user.id)

        if active_appointment:
            return jsonify(
                {
                    "ok": False,
                    "error": "Você já possui um agendamento ativo.",
                    "active_appointment": {
                        "id": active_appointment.id,
                        "date": active_appointment.start_at.strftime("%d/%m/%Y"),
                        "start": active_appointment.start_at.strftime("%H:%M"),
                    },
                }
            ), 409

        # Revalida o profissional e o serviço dentro do lock.
        service = db.session.get(Service, service_id)
        professional = get_selectable_professional(professional_id)

        if not service or not service.active:
            return jsonify({"ok": False, "error": "Serviço indisponível."}), 404

        if not professional:
            return jsonify({"ok": False, "error": "Profissional indisponível."}), 404

        validation_error = validate_requested_time(start_at, service)
        if validation_error:
            return jsonify({"ok": False, "error": validation_error}), 409

        end_at = start_at + timedelta(minutes=service.duration_minutes)

        conflict = find_schedule_conflict(
            professional.id,
            start_at,
            end_at,
        )

        if conflict:
            return jsonify(
                {
                    "ok": False,
                    "error": (
                        f"{professional.name} acabou de receber outro agendamento "
                        "nesse horário. Escolha outro horário."
                    ),
                }
            ), 409

        appointment = Appointment(
            user_id=current_user.id,
            professional_id=professional.id,
            service_id=service.id,
            start_at=start_at,
            end_at=end_at,
            status="scheduled",
            lembrete_enviado=False,
        )

        db.session.add(appointment)

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Erro ao criar agendamento.")
            return jsonify(
                {
                    "ok": False,
                    "error": "Não foi possível concluir o agendamento. Tente novamente.",
                }
            ), 500

    socketio.emit(
        "schedule_changed",
        {
            "professional_id": professional.id,
            "date": start_at.strftime("%Y-%m-%d"),
            "actor_user_id": current_user.id,
            "reason": "appointment_created",
        },
    )

    try:
        send_push_to_user(
            current_user.id,
            current_app.config.get("APP_NAME", "Gringo Barber"),
            (
                f"Agendamento com {professional.name} confirmado para "
                f"{start_at.strftime('%d/%m às %H:%M')}."
            ),
            "/dashboard",
        )
    except Exception:
        current_app.logger.exception(
            "Falha ao enviar push do agendamento %s.",
            appointment.id,
        )

    return jsonify(
        {
            "ok": True,
            "appointment_id": appointment.id,
            "date": start_at.strftime("%d/%m/%Y"),
            "start": start_at.strftime("%H:%M"),
            "end": end_at.strftime("%H:%M"),
            "service": service.name,
            "price_cents": service.price_cents,
            "professional": {
                "id": professional.id,
                "name": professional.name,
            },
            "is_vip": bool(getattr(current_user, "is_vip", False)),
        }
    ), 201


# ============================================================
# CANCELAMENTO DO CLIENTE
# ============================================================

@bp.post("/agendamento/<int:appointment_id>/cancelar")
@login_required
def cancel_appointment(appointment_id):
    require_csrf()

    appointment = db.session.get(Appointment, appointment_id)

    if not appointment:
        flash("Agendamento não encontrado.", "danger")
        return redirect(url_for("booking.dashboard"))

    if (
        appointment.user_id != current_user.id
        and not getattr(current_user, "is_admin", False)
    ):
        return "Acesso negado.", 403

    if appointment.status != "scheduled":
        flash("Esse agendamento não pode mais ser cancelado.", "warning")
        return redirect(url_for("booking.dashboard"))

    now = local_now()

    if appointment.start_at <= now:
        flash(
            "Não é possível cancelar um atendimento que já começou.",
            "warning",
        )
        return redirect(url_for("booking.dashboard"))

    appointment.status = "cancelled"

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Erro ao cancelar agendamento %s.",
            appointment_id,
        )
        flash(
            "Não foi possível cancelar o agendamento. Tente novamente.",
            "danger",
        )
        return redirect(url_for("booking.dashboard"))

    socketio.emit(
        "schedule_changed",
        {
            "professional_id": appointment.professional_id,
            "date": appointment.start_at.strftime("%Y-%m-%d"),
            "actor_user_id": current_user.id,
            "reason": "appointment_cancelled",
        },
    )

    flash(
        "Agendamento cancelado. O horário voltou a ficar disponível.",
        "success",
    )

    return redirect(url_for("booking.dashboard"))


# ============================================================
# PUSH
# ============================================================

@bp.post("/api/push/subscribe")
@login_required
def push_subscribe():
    require_csrf()

    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")
    keys = data.get("keys") or {}
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")

    if not endpoint or not p256dh or not auth:
        return jsonify(
            {
                "ok": False,
                "error": "Assinatura de notificação inválida.",
            }
        ), 400

    subscription = PushSubscription.query.filter_by(endpoint=endpoint).first()

    if not subscription:
        subscription = PushSubscription(
            user_id=current_user.id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
        )
        db.session.add(subscription)
    else:
        subscription.user_id = current_user.id
        subscription.p256dh = p256dh
        subscription.auth = auth

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Erro ao salvar assinatura push.")
        return jsonify(
            {
                "ok": False,
                "error": "Não foi possível ativar as notificações.",
            }
        ), 500

    return jsonify({"ok": True})
