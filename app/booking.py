from datetime import datetime, time, timedelta, timezone
from threading import Lock

from flask import (
    Blueprint,
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
)
from .push import send_push_to_user
from .utils import require_csrf


bp = Blueprint(
    "booking",
    __name__,
)


# ============================================================
# CONFIGURAÇÃO DA AGENDA
# ============================================================

# Horário comercial da barbearia.
BUSINESS_TZ = timezone(
    timedelta(hours=-3),
    name="America/Recife",
)

OPEN_TIME = time(7, 0)
CLOSE_TIME = time(20, 0)

# Horários começam de 30 em 30 minutos.
SLOT_STEP_MINUTES = 30

# Registro interno usado apenas porque Appointment ainda possui
# professional_id no banco/modelo.
#
# IMPORTANTE:
# o cliente NÃO escolhe profissional.
# O profissional NÃO participa mais da regra de disponibilidade.
INTERNAL_RESOURCE_NAME = "Gringo Barber"

WEEKDAY_LABELS = {
    0: "Seg",
    1: "Ter",
    2: "Qua",
    3: "Qui",
    4: "Sex",
    5: "Sáb",
}

# Evita duas confirmações simultâneas no mesmo processo Flask.
_booking_lock = Lock()


# ============================================================
# TEMPO / SEMANA
# ============================================================

def local_now():
    """
    Horário atual da barbearia.

    O projeto trabalha com datetimes sem tzinfo no SQLite.
    Por isso convertemos para UTC-3 e removemos tzinfo.
    """
    return (
        datetime
        .now(BUSINESS_TZ)
        .replace(tzinfo=None)
    )


def is_business_day(day):
    """
    Segunda a sábado.
    Domingo fechado.
    """
    return (
        day.weekday()
        <= 5
    )


def active_week_range(
    reference_date=None,
):
    """
    Retorna a única semana disponível para reserva.

    Segunda a sábado:
        semana atual.

    Domingo:
        próxima semana.
    """
    if reference_date is None:
        reference_date = (
            local_now()
            .date()
        )

    # Domingo.
    if (
        reference_date.weekday()
        == 6
    ):
        monday = (
            reference_date
            + timedelta(days=1)
        )

    else:
        monday = (
            reference_date
            - timedelta(
                days=reference_date.weekday()
            )
        )

    saturday = (
        monday
        + timedelta(days=5)
    )

    return (
        monday,
        saturday,
    )


# ============================================================
# RECURSO INTERNO DA AGENDA
# ============================================================

def get_booking_resource():
    """
    Retorna um registro interno de Professional.

    O sistema antigo permitia escolher profissional.
    Essa escolha foi removida.

    O modelo Appointment ainda exige professional_id, então
    mantemos UM registro somente para compatibilidade com o banco.

    A disponibilidade é global e NÃO é filtrada por profissional.
    """

    # Prioriza um registro com o nome interno.
    resource = (
        Professional.query
        .filter_by(
            name=INTERNAL_RESOURCE_NAME,
        )
        .order_by(
            Professional.id.asc()
        )
        .first()
    )

    if resource:
        if not resource.active:
            resource.active = True

            try:
                db.session.commit()

            except Exception:
                db.session.rollback()

                current_app.logger.exception(
                    "Não foi possível reativar o recurso interno da agenda."
                )

                return None

        return resource

    # Compatibilidade com bancos antigos:
    # se já houver algum profissional, reutiliza o primeiro
    # em vez de criar registros duplicados.
    resource = (
        Professional.query
        .order_by(
            Professional.id.asc()
        )
        .first()
    )

    if resource:
        if not resource.active:
            resource.active = True

            try:
                db.session.commit()

            except Exception:
                db.session.rollback()

                current_app.logger.exception(
                    "Não foi possível ativar o recurso interno da agenda."
                )

                return None

        return resource

    # Banco novo / sem profissionais:
    # cria automaticamente o registro técnico.
    resource = Professional(
        name=INTERNAL_RESOURCE_NAME,
        bio=(
            "Registro interno da agenda. "
            "Não é selecionado pelo cliente."
        ),
        active=True,
    )

    db.session.add(
        resource
    )

    try:
        db.session.commit()

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Não foi possível criar o recurso interno da agenda."
        )

        return None

    return resource


# ============================================================
# REGRA: UM AGENDAMENTO ATIVO POR CLIENTE
# ============================================================

def get_active_user_appointment(
    user_id,
):
    """
    Retorna o agendamento ainda ativo do cliente.

    Um agendamento permanece ativo enquanto:
    - status == scheduled;
    - ainda não terminou.
    """
    now = local_now()

    return (
        Appointment.query
        .filter(
            Appointment.user_id
            == user_id,

            Appointment.status
            == "scheduled",

            Appointment.end_at
            > now,
        )
        .order_by(
            Appointment.start_at.asc()
        )
        .first()
    )


# ============================================================
# DISPONIBILIDADE
# ============================================================

def scheduled_appointments_for_day(
    selected_date,
):
    """
    Retorna todos os horários ocupados do estabelecimento naquele dia.

    Não filtra profissional.

    Agora existe uma agenda única da barbearia.
    """
    day_start = datetime.combine(
        selected_date,
        time.min,
    )

    day_end = (
        day_start
        + timedelta(days=1)
    )

    return (
        Appointment.query
        .filter(
            Appointment.status
            == "scheduled",

            Appointment.start_at
            < day_end,

            Appointment.end_at
            > day_start,
        )
        .order_by(
            Appointment.start_at.asc()
        )
        .all()
    )


def periods_overlap(
    start_at,
    end_at,
    appointment,
):
    """
    Verifica colisão entre dois períodos.
    """
    return (
        start_at
        < appointment.end_at

        and

        end_at
        > appointment.start_at
    )


def build_available_times(
    service,
    selected_date,
):
    """
    Gera virtualmente os horários disponíveis.

    Regras:
    - segunda a sábado;
    - 07:00 às 20:00;
    - início de 30 em 30 minutos;
    - respeita duração real do serviço;
    - serviço deve terminar até 20:00;
    - não mostra horários passados;
    - não mostra horários que colidem;
    - NÃO existe seleção de profissional.
    """
    if not is_business_day(
        selected_date
    ):
        return []

    if (
        not service
        or not service.active
        or not service.duration_minutes
        or service.duration_minutes <= 0
    ):
        return []

    now = local_now()

    opening = datetime.combine(
        selected_date,
        OPEN_TIME,
    )

    closing = datetime.combine(
        selected_date,
        CLOSE_TIME,
    )

    appointments = (
        scheduled_appointments_for_day(
            selected_date
        )
    )

    available = []

    cursor = opening

    while cursor < closing:

        end_at = (
            cursor
            + timedelta(
                minutes=(
                    service
                    .duration_minutes
                )
            )
        )

        # Se já ultrapassa o fechamento,
        # nenhum horário posterior será válido.
        if end_at > closing:
            break

        # Somente horários futuros.
        if cursor > now:

            occupied = any(
                periods_overlap(
                    cursor,
                    end_at,
                    appointment,
                )
                for appointment
                in appointments
            )

            if not occupied:
                available.append(
                    {
                        "start_at":
                            cursor.strftime(
                                "%Y-%m-%dT%H:%M"
                            ),

                        "start":
                            cursor.strftime(
                                "%H:%M"
                            ),

                        "end":
                            end_at.strftime(
                                "%H:%M"
                            ),
                    }
                )

        cursor += timedelta(
            minutes=SLOT_STEP_MINUTES
        )

    return available


def validate_requested_time(
    start_at,
    service,
):
    """
    Revalida no servidor o horário recebido pelo cliente.

    Nunca depende apenas do JavaScript.
    """
    now = local_now()

    if start_at <= now:
        return (
            "Esse horário já passou."
        )

    if not is_business_day(
        start_at.date()
    ):
        return (
            "A barbearia não abre aos domingos."
        )

    week_start, week_end = (
        active_week_range(
            now.date()
        )
    )

    if not (
        week_start
        <= start_at.date()
        <= week_end
    ):
        return (
            "Esse horário não pertence à semana disponível."
        )

    # Grade de 30 minutos.
    if (
        start_at.minute
        % SLOT_STEP_MINUTES
        != 0

        or start_at.second != 0

        or start_at.microsecond != 0
    ):
        return (
            "Horário inválido."
        )

    if (
        not service
        or not service.active
        or not service.duration_minutes
        or service.duration_minutes <= 0
    ):
        return (
            "Serviço inválido ou indisponível."
        )

    opening = datetime.combine(
        start_at.date(),
        OPEN_TIME,
    )

    closing = datetime.combine(
        start_at.date(),
        CLOSE_TIME,
    )

    end_at = (
        start_at
        + timedelta(
            minutes=(
                service
                .duration_minutes
            )
        )
    )

    if (
        start_at < opening
        or end_at > closing
    ):
        return (
            "Esse horário está fora do expediente."
        )

    return None


def find_schedule_conflict(
    start_at,
    end_at,
):
    """
    Procura qualquer atendimento ativo que colida.

    Não considera profissional.
    A agenda da barbearia é única.
    """
    return (
        Appointment.query
        .filter(
            Appointment.status
            == "scheduled",

            Appointment.start_at
            < end_at,

            Appointment.end_at
            > start_at,
        )
        .order_by(
            Appointment.start_at.asc()
        )
        .first()
    )


# ============================================================
# PÁGINA INICIAL
# ============================================================

@bp.get("/")
def index():

    services = (
        Service.query
        .filter_by(
            active=True
        )
        .order_by(
            Service.price_cents.asc(),
            Service.name.asc(),
        )
        .all()
    )

    # Nenhuma lista de profissionais é enviada ao template.
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

    # Regra do sistema:
    # uma conta só pode ter um atendimento ativo.
    active_appointment = (
        get_active_user_appointment(
            current_user.id
        )
    )

    if active_appointment:

        flash(
            (
                "Você já possui um agendamento ativo para "
                f"{active_appointment.start_at.strftime('%d/%m às %H:%M')}. "
                "Cancele ou conclua esse atendimento antes de reservar outro."
            ),
            "warning",
        )

        return redirect(
            url_for(
                "booking.dashboard"
            )
        )

    services = (
        Service.query
        .filter_by(
            active=True
        )
        .order_by(
            Service.name.asc()
        )
        .all()
    )

    # O template atual ainda verifica has_professional.
    # Mantemos o campo apenas por compatibilidade visual.
    # O cliente NÃO escolhe profissional.
    resource = (
        get_booking_resource()
    )

    return render_template(
        "agenda.html",
        services=services,
        has_professional=bool(
            resource
        ),
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
            Appointment.user_id
            == current_user.id,

            Appointment.status
            == "scheduled",

            Appointment.end_at
            > now,
        )
        .order_by(
            Appointment.start_at.asc()
        )
        .all()
    )

    history = (
        Appointment.query
        .filter(
            Appointment.user_id
            == current_user.id,
        )
        .order_by(
            Appointment.start_at.desc()
        )
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
    """
    Retorna a semana ativa e os horários livres.

    Entrada:
        service_id

    NÃO aceita:
        professional_id

    O sistema antigo de escolha de profissional foi removido.
    """

    active_appointment = (
        get_active_user_appointment(
            current_user.id
        )
    )

    if active_appointment:

        return jsonify(
            {
                "ok": False,

                "error":
                    (
                        "Você já possui um agendamento ativo."
                    ),

                "active_appointment": {
                    "id":
                        active_appointment.id,

                    "date":
                        active_appointment
                        .start_at
                        .strftime(
                            "%d/%m/%Y"
                        ),

                    "start":
                        active_appointment
                        .start_at
                        .strftime(
                            "%H:%M"
                        ),

                    "end":
                        active_appointment
                        .end_at
                        .strftime(
                            "%H:%M"
                        ),
                },
            }
        ), 409

    service_id = request.args.get(
        "service_id",
        type=int,
    )

    if not service_id:

        return jsonify(
            {
                "ok": False,
                "error":
                    "Nenhum serviço foi selecionado.",
            }
        ), 400

    service = db.session.get(
        Service,
        service_id,
    )

    if (
        not service
        or not service.active
    ):

        return jsonify(
            {
                "ok": False,
                "error":
                    "Serviço não encontrado ou inativo.",
            }
        ), 404

    # Garante somente o registro técnico
    # necessário ao foreign key de Appointment.
    if not get_booking_resource():

        return jsonify(
            {
                "ok": False,
                "error":
                    (
                        "A agenda não pôde ser inicializada. "
                        "Tente novamente."
                    ),
            }
        ), 500

    now = local_now()

    week_start, week_end = (
        active_week_range(
            now.date()
        )
    )

    days = []

    cursor_date = week_start

    while (
        cursor_date
        <= week_end
    ):

        slots = (
            build_available_times(
                service,
                cursor_date,
            )
        )

        days.append(
            {
                "date":
                    cursor_date.isoformat(),

                "weekday":
                    WEEKDAY_LABELS[
                        cursor_date.weekday()
                    ],

                "label":
                    cursor_date.strftime(
                        "%d/%m"
                    ),

                "full_label":
                    cursor_date.strftime(
                        "%d/%m/%Y"
                    ),

                "slots":
                    slots,
            }
        )

        cursor_date += timedelta(
            days=1
        )

    return jsonify(
        {
            "ok": True,

            "days":
                days,

            "week": {
                "start":
                    week_start.strftime(
                        "%d/%m/%Y"
                    ),

                "end":
                    week_end.strftime(
                        "%d/%m/%Y"
                    ),
            },

            "service": {
                "id":
                    service.id,

                "name":
                    service.name,

                "duration":
                    service.duration_minutes,

                "price_cents":
                    service.price_cents,
            },

            "business_hours": {
                "open":
                    OPEN_TIME.strftime(
                        "%H:%M"
                    ),

                "close":
                    CLOSE_TIME.strftime(
                        "%H:%M"
                    ),
            },

            # Informação útil para o frontend.
            "rules": {
                "single_active_booking":
                    True,

                "professional_selection":
                    False,

                "slot_step_minutes":
                    SLOT_STEP_MINUTES,
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
    Cria um agendamento.

    O cliente envia apenas:
        service_id
        start_at

    professional_id é ignorado/rejeitado.
    """

    require_csrf()

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    # Remove definitivamente a API antiga.
    if (
        "professional_id"
        in data

        or "professional"
        in data
    ):
        return jsonify(
            {
                "ok": False,

                "error":
                    (
                        "A seleção de profissional foi removida. "
                        "Envie apenas serviço e horário."
                    ),
            }
        ), 400

    try:

        service_id = int(
            data.get(
                "service_id"
            )
        )

        start_at = datetime.strptime(
            str(
                data.get(
                    "start_at"
                )
            ),
            "%Y-%m-%dT%H:%M",
        )

    except (
        TypeError,
        ValueError,
    ):

        return jsonify(
            {
                "ok": False,

                "error":
                    "Dados do agendamento inválidos.",
            }
        ), 400

    service = db.session.get(
        Service,
        service_id,
    )

    if (
        not service
        or not service.active
    ):

        return jsonify(
            {
                "ok": False,

                "error":
                    "Serviço indisponível.",
            }
        ), 404

    validation_error = (
        validate_requested_time(
            start_at,
            service,
        )
    )

    if validation_error:

        return jsonify(
            {
                "ok": False,
                "error":
                    validation_error,
            }
        ), 409

    end_at = (
        start_at
        + timedelta(
            minutes=(
                service
                .duration_minutes
            )
        )
    )

    # A confirmação é revalidada dentro do lock
    # para evitar clique duplo / concorrência local.
    with _booking_lock:

        # Regra:
        # uma conta = um atendimento ativo.
        active_appointment = (
            get_active_user_appointment(
                current_user.id
            )
        )

        if active_appointment:

            return jsonify(
                {
                    "ok": False,

                    "error":
                        (
                            "Você já possui um agendamento ativo."
                        ),

                    "active_appointment": {
                        "id":
                            active_appointment.id,

                        "date":
                            active_appointment
                            .start_at
                            .strftime(
                                "%d/%m/%Y"
                            ),

                        "start":
                            active_appointment
                            .start_at
                            .strftime(
                                "%H:%M"
                            ),
                    },
                }
            ), 409

        # Revalida colisão imediatamente
        # antes do INSERT.
        conflict = (
            find_schedule_conflict(
                start_at,
                end_at,
            )
        )

        if conflict:

            return jsonify(
                {
                    "ok": False,

                    "error":
                        (
                            "Outro cliente reservou esse horário primeiro. "
                            "Escolha outro horário."
                        ),
                }
            ), 409

        resource = (
            get_booking_resource()
        )

        if not resource:

            return jsonify(
                {
                    "ok": False,

                    "error":
                        (
                            "Não foi possível inicializar a agenda."
                        ),
                }
            ), 500

        appointment = Appointment(
            user_id=current_user.id,

            # Campo mantido somente porque o banco ainda exige.
            professional_id=resource.id,

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

            current_app.logger.exception(
                "Erro ao criar agendamento."
            )

            return jsonify(
                {
                    "ok": False,

                    "error":
                        (
                            "Não foi possível concluir o agendamento. "
                            "Tente novamente."
                        ),
                }
            ), 500

    # ========================================================
    # EVENTOS / NOTIFICAÇÕES
    # ========================================================

    socketio.emit(
        "schedule_changed",
        {
            "date":
                start_at.strftime(
                    "%Y-%m-%d"
                ),

            "actor_user_id":
                current_user.id,

            "reason":
                "appointment_created",
        },
    )

    # Push não pode derrubar o agendamento.
    try:

        send_push_to_user(
            current_user.id,

            current_app.config.get(
                "APP_NAME",
                "Gringo Barber",
            ),

            (
                "Agendamento confirmado para "
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

            "appointment_id":
                appointment.id,

            "date":
                start_at.strftime(
                    "%d/%m/%Y"
                ),

            "start":
                start_at.strftime(
                    "%H:%M"
                ),

            "end":
                end_at.strftime(
                    "%H:%M"
                ),

            "service":
                service.name,

            "price_cents":
                service.price_cents,
        }
    ), 201


# ============================================================
# CANCELAMENTO DO CLIENTE
# ============================================================

@bp.post(
    "/agendamento/<int:appointment_id>/cancelar"
)
@login_required
def cancel_appointment(
    appointment_id,
):

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
            url_for(
                "booking.dashboard"
            )
        )

    if (
        appointment.user_id
        != current_user.id

        and not getattr(
            current_user,
            "is_admin",
            False,
        )
    ):

        return (
            "Acesso negado.",
            403,
        )

    if (
        appointment.status
        != "scheduled"
    ):

        flash(
            (
                "Esse agendamento não pode mais ser cancelado."
            ),
            "warning",
        )

        return redirect(
            url_for(
                "booking.dashboard"
            )
        )

    now = local_now()

    if (
        appointment.start_at
        <= now
    ):

        flash(
            (
                "Não é possível cancelar um atendimento "
                "que já começou."
            ),
            "warning",
        )

        return redirect(
            url_for(
                "booking.dashboard"
            )
        )

    appointment.status = (
        "cancelled"
    )

    try:
        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "Erro ao cancelar agendamento %s.",
            appointment_id,
        )

        flash(
            (
                "Não foi possível cancelar o agendamento. "
                "Tente novamente."
            ),
            "danger",
        )

        return redirect(
            url_for(
                "booking.dashboard"
            )
        )

    socketio.emit(
        "schedule_changed",
        {
            "date":
                appointment
                .start_at
                .strftime(
                    "%Y-%m-%d"
                ),

            "actor_user_id":
                current_user.id,

            "reason":
                "appointment_cancelled",
        },
    )

    flash(
        (
            "Agendamento cancelado. "
            "O horário voltou a ficar disponível."
        ),
        "success",
    )

    return redirect(
        url_for(
            "booking.dashboard"
        )
    )


# ============================================================
# PUSH
# ============================================================

@bp.post("/api/push/subscribe")
@login_required
def push_subscribe():

    require_csrf()

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    endpoint = (
        data.get(
            "endpoint"
        )
    )

    keys = (
        data.get(
            "keys"
        )
        or {}
    )

    p256dh = (
        keys.get(
            "p256dh"
        )
    )

    auth = (
        keys.get(
            "auth"
        )
    )

    if (
        not endpoint
        or not p256dh
        or not auth
    ):

        return jsonify(
            {
                "ok": False,

                "error":
                    "Assinatura de notificação inválida.",
            }
        ), 400

    subscription = (
        PushSubscription.query
        .filter_by(
            endpoint=endpoint
        )
        .first()
    )

    if not subscription:

        subscription = (
            PushSubscription(
                user_id=current_user.id,
                endpoint=endpoint,
                p256dh=p256dh,
                auth=auth,
            )
        )

        db.session.add(
            subscription
        )

    else:

        subscription.user_id = (
            current_user.id
        )

        subscription.p256dh = (
            p256dh
        )

        subscription.auth = (
            auth
        )

    try:
        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "Erro ao salvar assinatura push."
        )

        return jsonify(
            {
                "ok": False,

                "error":
                    (
                        "Não foi possível ativar as notificações."
                    ),
            }
        ), 500

    return jsonify(
        {
            "ok": True,
        }
    )


# ============================================================
# COMPATIBILIDADE / OBSERVAÇÕES
# ============================================================

# Não existem mais rotas como:
#
# /api/profissionais
# /api/horarios?professional_id=...
#
# Também não é necessário enviar professional_id ao agendar.
#
# Fluxo atual:
#
# 1. cliente escolhe serviço;
# 2. backend calcula horários livres da agenda única;
# 3. cliente escolhe horário;
# 4. backend revalida conflitos;
# 5. backend garante um único agendamento ativo por conta;
# 6. professional_id é preenchido internamente somente para
#    manter compatibilidade com o modelo/banco atual.
