from datetime import datetime, time, timedelta, timezone
from threading import Lock

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .extensions import db, socketio
from .models import Appointment, Professional, PushSubscription, Service
from .push import send_push_to_user
from .utils import require_csrf
from .whatsapp import get_preference, send_booking_confirmation


bp = Blueprint("booking", __name__)

# ============================================================
# REGRAS DA AGENDA
# ============================================================

# Recife/PE = UTC-03:00.
BUSINESS_TZ = timezone(timedelta(hours=-3), name="America/Recife")

OPEN_TIME = time(7, 0)
CLOSE_TIME = time(20, 0)

# O painel cria inícios a cada 30 minutos.
SLOT_STEP_MINUTES = 30

# Segunda=0 ... sábado=5.
WEEKDAY_LABELS = {
    0: "Seg",
    1: "Ter",
    2: "Qua",
    3: "Qui",
    4: "Sex",
    5: "Sáb",
}

# Protege confirmações concorrentes dentro deste processo Flask.
_booking_lock = Lock()


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def local_now():
    """
    Retorna o horário atual da barbearia como datetime local sem tzinfo.

    O banco deste projeto já trabalha com datetime sem timezone,
    então mantemos o mesmo padrão para evitar comparações incompatíveis.
    """
    return datetime.now(BUSINESS_TZ).replace(tzinfo=None)


def get_default_professional():
    """
    O cliente NÃO escolhe profissional.

    A agenda utiliza automaticamente o primeiro profissional ativo.
    """
    return (
        Professional.query
        .filter_by(active=True)
        .order_by(Professional.id.asc())
        .first()
    )


def get_active_user_appointment(user_id):
    """
    Cada conta pode possuir apenas UM agendamento ativo por vez.
    """
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


def is_business_day(day):
    """
    Segunda a sábado.
    Domingo é fechado.
    """
    return day.weekday() <= 5


def active_week_range(reference_date=None):
    """
    Define a única semana que pode aparecer no aplicativo.

    Segunda a sábado:
        mostra a semana atual.

    Domingo:
        troca automaticamente para a semana seguinte.

    Exemplo:
        domingo 11/10 -> abre segunda 12/10 até sábado 17/10.
    """
    if reference_date is None:
        reference_date = local_now().date()

    # Domingo.
    if reference_date.weekday() == 6:
        monday = reference_date + timedelta(days=1)
        saturday = monday + timedelta(days=5)
        return monday, saturday

    # Segunda a sábado: encontra a segunda-feira da semana atual.
    monday = reference_date - timedelta(days=reference_date.weekday())
    saturday = monday + timedelta(days=5)

    return monday, saturday


def appointment_overlaps(start_at, end_at, appointment):
    """
    Verdadeiro quando o período candidato colide com um agendamento existente.
    """
    return (
        start_at < appointment.end_at
        and end_at > appointment.start_at
    )


def scheduled_appointments_for_day(professional_id, selected_date):
    """
    Busca somente agendamentos ativos do profissional naquele dia.
    """
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


def build_available_times(professional_id, service, selected_date):
    """
    Cria os horários virtualmente.

    Não depende de registros prévios na tabela Slot.

    Regras:
    - segunda a sábado;
    - 07:00 às 20:00;
    - inícios a cada 30 minutos;
    - o serviço precisa terminar até 20:00;
    - horários passados não aparecem;
    - horários que colidem com agendamentos não aparecem.
    """
    if not is_business_day(selected_date):
        return []

    now = local_now()

    opening = datetime.combine(selected_date, OPEN_TIME)
    closing = datetime.combine(selected_date, CLOSE_TIME)

    appointments = scheduled_appointments_for_day(
        professional_id,
        selected_date,
    )

    available = []
    cursor = opening

    while cursor < closing:
        end_at = cursor + timedelta(
            minutes=service.duration_minutes
        )

        # O serviço não pode ultrapassar o fechamento.
        if end_at > closing:
            break

        # Um horário que já começou ou passou nunca é exibido.
        if cursor > now:
            occupied = any(
                appointment_overlaps(
                    cursor,
                    end_at,
                    appointment,
                )
                for appointment in appointments
            )

            if not occupied:
                available.append(
                    {
                        "start_at": cursor.strftime("%Y-%m-%dT%H:%M"),
                        "start": cursor.strftime("%H:%M"),
                        "end": end_at.strftime("%H:%M"),
                    }
                )

        cursor += timedelta(minutes=SLOT_STEP_MINUTES)

    return available


def validate_requested_time(start_at, service):
    """
    Nunca confia apenas no JavaScript.
    Revalida no servidor antes de salvar.
    """
    now = local_now()

    if start_at <= now:
        return "Esse horário já passou."

    if not is_business_day(start_at.date()):
        return "A barbearia não abre aos domingos."

    week_start, week_end = active_week_range(now.date())

    if not (week_start <= start_at.date() <= week_end):
        return "Esse horário não pertence à semana disponível."

    # Grade de 30 minutos: XX:00 ou XX:30.
    if (
        start_at.minute % SLOT_STEP_MINUTES != 0
        or start_at.second != 0
        or start_at.microsecond != 0
    ):
        return "Horário inválido."

    opening = datetime.combine(start_at.date(), OPEN_TIME)
    closing = datetime.combine(start_at.date(), CLOSE_TIME)

    end_at = start_at + timedelta(
        minutes=service.duration_minutes
    )

    if start_at < opening or end_at > closing:
        return "Esse horário está fora do expediente."

    return None


# ============================================================
# PÁGINAS
# ============================================================

@bp.get("/")
def index():
    services = (
        Service.query
        .filter_by(active=True)
        .order_by(
            Service.price_cents.asc(),
            Service.name.asc(),
        )
        .all()
    )

    return render_template(
        "index.html",
        services=services,
        professionals=[],
    )


@bp.get("/agendar")
@login_required
def agenda():
    active_appointment = get_active_user_appointment(
        current_user.id
    )

    if active_appointment:
        flash(
            (
                "Você já possui um agendamento ativo para "
                f"{active_appointment.start_at.strftime('%d/%m/%Y às %H:%M')}. "
                "Cancele-o antes de escolher outro horário."
            ),
            "warning",
        )

        return redirect(
            url_for("booking.dashboard")
        )

    services = (
        Service.query
        .filter_by(active=True)
        .order_by(Service.name.asc())
        .all()
    )

    professional = get_default_professional()
    whatsapp_preference = get_preference(
        current_user.id
    )

    return render_template(
        "agenda.html",
        services=services,
        has_professional=bool(professional),
        whatsapp_preference=whatsapp_preference,
    )


@bp.get("/dashboard")
@login_required
def dashboard():
    now = local_now()

    upcoming = (
        Appointment.query
        .filter(
            Appointment.user_id == current_user.id,
            Appointment.status == "scheduled",
            Appointment.start_at >= now,
        )
        .order_by(Appointment.start_at.asc())
        .all()
    )

    history = (
        Appointment.query
        .filter(
            Appointment.user_id == current_user.id,
        )
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
# API DO PAINEL SEMANAL
# ============================================================

@bp.get("/api/painel-agenda")
@login_required
def schedule_panel():
    """
    Retorna SOMENTE a semana ativa.

    O cliente escolhe apenas o serviço.
    O profissional é definido internamente pelo sistema.
    """
    service_id = request.args.get(
        "service_id",
        type=int,
    )

    if not service_id:
        return jsonify(
            {
                "error":
                    "Nenhum serviço foi selecionado."
            }
        ), 400

    service = db.session.get(
        Service,
        service_id,
    )

    if not service or not service.active:
        return jsonify(
            {
                "error":
                    "Serviço não encontrado ou inativo."
            }
        ), 400

    active_appointment = get_active_user_appointment(
        current_user.id
    )

    if active_appointment:
        return jsonify(
            {
                "error": (
                    "Você já possui um agendamento ativo para "
                    f"{active_appointment.start_at.strftime('%d/%m/%Y às %H:%M')}."
                ),
                "has_active_appointment": True,
            }
        ), 409

    professional = get_default_professional()

    if not professional:
        return jsonify(
            {
                "error":
                    "A agenda ainda não possui um profissional ativo."
            }
        ), 400

    now = local_now()

    week_start, week_end = active_week_range(
        now.date()
    )

    days = []
    cursor_date = week_start

    while cursor_date <= week_end:
        slots = build_available_times(
            professional.id,
            service,
            cursor_date,
        )

        days.append(
            {
                "date": cursor_date.isoformat(),
                "weekday": WEEKDAY_LABELS[
                    cursor_date.weekday()
                ],
                "label": cursor_date.strftime("%d/%m"),
                "full_label":
                    cursor_date.strftime("%d/%m/%Y"),
                "slots": slots,
            }
        )

        cursor_date += timedelta(days=1)

    return jsonify(
        {
            "days": days,
            "week": {
                "start":
                    week_start.strftime("%d/%m/%Y"),
                "end":
                    week_end.strftime("%d/%m/%Y"),
            },
            "service": {
                "id": service.id,
                "name": service.name,
                "duration":
                    service.duration_minutes,
                "price_cents":
                    service.price_cents,
            },
            "business_hours": {
                "open":
                    OPEN_TIME.strftime("%H:%M"),
                "close":
                    CLOSE_TIME.strftime("%H:%M"),
            },
        }
    )


# ============================================================
# CRIAR AGENDAMENTO
# ============================================================

@bp.post("/api/agendar-rapido")
@login_required
def quick_book():
    """
    Cria um agendamento respeitando a regra:
    uma conta pode possuir apenas UM agendamento ativo por vez.
    """
    require_csrf()

    data = request.get_json(
        silent=True
    ) or {}

    try:
        service_id = int(
            data.get("service_id")
        )

        start_at = datetime.strptime(
            str(data.get("start_at")),
            "%Y-%m-%dT%H:%M",
        )

    except (TypeError, ValueError):
        return jsonify(
            {
                "ok": False,
                "error":
                    "Dados do agendamento inválidos."
            }
        ), 400

    service = db.session.get(
        Service,
        service_id,
    )

    if not service or not service.active:
        return jsonify(
            {
                "ok": False,
                "error":
                    "Serviço indisponível."
            }
        ), 400

    professional = get_default_professional()

    if not professional:
        return jsonify(
            {
                "ok": False,
                "error":
                    "A agenda ainda não possui um profissional ativo."
            }
        ), 400

    validation_error = validate_requested_time(
        start_at,
        service,
    )

    if validation_error:
        return jsonify(
            {
                "ok": False,
                "error": validation_error,
            }
        ), 409

    end_at = start_at + timedelta(
        minutes=service.duration_minutes
    )

    # Serializa confirmações concorrentes no servidor local.
    with _booking_lock:
        active_appointment = get_active_user_appointment(
            current_user.id
        )

        if active_appointment:
            return jsonify(
                {
                    "ok": False,
                    "error": (
                        "Você já possui um agendamento ativo para "
                        f"{active_appointment.start_at.strftime('%d/%m/%Y às %H:%M')}. "
                        "Cancele-o antes de marcar outro horário."
                    ),
                }
            ), 409

        # Revalida imediatamente antes do INSERT.
        conflict = (
            Appointment.query
            .filter(
                Appointment.professional_id
                    == professional.id,
                Appointment.status
                    == "scheduled",
                Appointment.start_at < end_at,
                Appointment.end_at > start_at,
            )
            .first()
        )

        if conflict:
            return jsonify(
                {
                    "ok": False,
                    "error":
                        "Outro cliente reservou esse horário primeiro.",
                }
            ), 409

        # A validação acima impede mais de um agendamento ativo
        # para a mesma conta.
        appointment = Appointment(
            user_id=current_user.id,
            professional_id=professional.id,
            service_id=service.id,
            start_at=start_at,
            end_at=end_at,
            status="scheduled",
        )

        db.session.add(
            appointment
        )

        try:
            db.session.commit()

        except Exception:
            db.session.rollback()

            return jsonify(
                {
                    "ok": False,
                    "error":
                        "Não foi possível concluir o agendamento."
                }
            ), 500

    # WhatsApp é secundário: uma falha na API nunca desfaz o agendamento.
    try:
        send_booking_confirmation(
            appointment,
            current_user,
            service,
        )
    except Exception:
        current_app.logger.exception(
            "Falha ao enviar confirmação WhatsApp "
            "do agendamento %s.",
            appointment.id,
        )

    socketio.emit(
        "schedule_changed",
        {
            "professional_id":
                professional.id,
            "date":
                start_at.strftime("%Y-%m-%d"),
            "actor_user_id":
                current_user.id,
            "reason":
                "appointment_created",
        },
    )

    send_push_to_user(
        current_user.id,
        current_app.config["APP_NAME"],
        (
            "Agendamento confirmado para "
            f"{start_at.strftime('%d/%m às %H:%M')}."
        ),
        "/dashboard",
    )

    return jsonify(
        {
            "ok": True,
            "appointment_id":
                appointment.id,
            "date":
                start_at.strftime("%d/%m/%Y"),
            "start":
                start_at.strftime("%H:%M"),
            "end":
                end_at.strftime("%H:%M"),
            "service":
                service.name,
        }
    )


# ============================================================
# CANCELAMENTO
# ============================================================

@bp.post("/agendamento/<int:appointment_id>/cancelar")
@login_required
def cancel_appointment(appointment_id):
    require_csrf()

    appointment = db.session.get(
        Appointment,
        appointment_id,
    )

    if not appointment:
        flash(
            "Agendamento não encontrado.",
            "danger",
        )

        return redirect(
            url_for("booking.dashboard")
        )

    if (
        appointment.user_id
        != current_user.id
        and not current_user.is_admin
    ):
        return "Acesso negado.", 403

    if appointment.status != "scheduled":
        flash(
            "Esse agendamento não pode mais ser cancelado.",
            "warning",
        )

        return redirect(
            url_for("booking.dashboard")
        )

    if appointment.start_at <= local_now():
        flash(
            "Não é possível cancelar um atendimento que já começou.",
            "warning",
        )

        return redirect(
            url_for("booking.dashboard")
        )

    appointment.status = "cancelled"

    db.session.commit()

    socketio.emit(
        "schedule_changed",
        {
            "professional_id":
                appointment.professional_id,
            "date":
                appointment.start_at.strftime(
                    "%Y-%m-%d"
                ),
            "actor_user_id":
                current_user.id,
            "reason":
                "appointment_cancelled",
        },
    )

    flash(
        "Agendamento cancelado. O horário voltou a ficar disponível.",
        "success",
    )

    return redirect(
        url_for("booking.dashboard")
    )


# ============================================================
# PUSH
# ============================================================

@bp.post("/api/push/subscribe")
@login_required
def push_subscribe():
    require_csrf()

    data = request.get_json(
        silent=True
    ) or {}

    endpoint = data.get(
        "endpoint"
    )

    keys = data.get(
        "keys"
    ) or {}

    if (
        not endpoint
        or not keys.get("p256dh")
        or not keys.get("auth")
    ):
        return jsonify(
            {
                "ok": False,
                "error":
                    "Assinatura inválida."
            }
        ), 400

    subscription = (
        PushSubscription.query
        .filter_by(endpoint=endpoint)
        .first()
    )

    if not subscription:
        subscription = PushSubscription(
            user_id=current_user.id,
            endpoint=endpoint,
            p256dh=keys["p256dh"],
            auth=keys["auth"],
        )

        db.session.add(
            subscription
        )

    else:
        subscription.user_id = (
            current_user.id
        )

        subscription.p256dh = (
            keys["p256dh"]
        )

        subscription.auth = (
            keys["auth"]
        )

    db.session.commit()

    return jsonify(
        {"ok": True}
    )
