from datetime import datetime, time, timedelta, timezone
from pathlib import Path
import re

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy.exc import IntegrityError

from .extensions import db, socketio
from .models import Appointment, Professional, Service, User
from .utils import admin_required, require_csrf


bp = Blueprint("admin", __name__, url_prefix="/admin")


BUSINESS_TZ = timezone(timedelta(hours=-3), name="America/Recife")
OPEN_TIME = time(7, 0)
CLOSE_TIME = time(20, 0)
SLOT_STEP_MINUTES = 30


def local_now():
    return datetime.now(BUSINESS_TZ).replace(tzinfo=None)


def active_week_range(reference_date=None):
    if reference_date is None:
        reference_date = local_now().date()

    if reference_date.weekday() == 6:  # domingo
        monday = reference_date + timedelta(days=1)
    else:
        monday = reference_date - timedelta(days=reference_date.weekday())

    return monday, monday + timedelta(days=5)


def get_default_professional():
    return (
        Professional.query
        .filter_by(active=True)
        .order_by(Professional.id.asc())
        .first()
    )


def count_available_base_slots():
    """Conta blocos-base de 30 min livres na semana ativa."""
    professional = get_default_professional()
    if not professional:
        return 0

    now = local_now()
    week_start, week_end = active_week_range(now.date())

    appointments = (
        Appointment.query
        .filter(
            Appointment.professional_id == professional.id,
            Appointment.status == "scheduled",
            Appointment.start_at < datetime.combine(week_end + timedelta(days=1), time.min),
            Appointment.end_at > datetime.combine(week_start, time.min),
        )
        .all()
    )

    available = 0
    current_date = week_start

    while current_date <= week_end:
        cursor = datetime.combine(current_date, OPEN_TIME)
        closing = datetime.combine(current_date, CLOSE_TIME)

        while cursor + timedelta(minutes=SLOT_STEP_MINUTES) <= closing:
            end_at = cursor + timedelta(minutes=SLOT_STEP_MINUTES)

            if cursor > now:
                occupied = any(
                    cursor < appointment.end_at and end_at > appointment.start_at
                    for appointment in appointments
                )
                if not occupied:
                    available += 1

            cursor += timedelta(minutes=SLOT_STEP_MINUTES)

        current_date += timedelta(days=1)

    return available


@bp.get("/")
@admin_required
def dashboard():
    appointments = (
        Appointment.query
        .order_by(Appointment.start_at.desc())
        .limit(80)
        .all()
    )

    professionals = Professional.query.order_by(Professional.name.asc()).all()
    services = Service.query.order_by(Service.name.asc()).all()

    now = local_now()

    stats = {
        "clients": User.query.filter_by(role="client").count(),
        "scheduled": (
            Appointment.query
            .filter(
                Appointment.status == "scheduled",
                Appointment.end_at > now,
            )
            .count()
        ),
        "available_slots": count_available_base_slots(),
    }

    whatsapp_status = {
        "access_token": bool(current_app.config.get("WHATSAPP_ACCESS_TOKEN")),
        "phone_number_id": bool(current_app.config.get("WHATSAPP_PHONE_NUMBER_ID")),
        "api_version": bool(current_app.config.get("WHATSAPP_API_VERSION")),
        "confirmation_template": bool(current_app.config.get("WHATSAPP_CONFIRMATION_TEMPLATE")),
        "reminder_template": bool(current_app.config.get("WHATSAPP_REMINDER_TEMPLATE")),
        "reminder_minutes": current_app.config.get("WHATSAPP_REMINDER_MINUTES", 60),
    }
    whatsapp_status["ready"] = all([
        whatsapp_status["access_token"],
        whatsapp_status["phone_number_id"],
        whatsapp_status["api_version"],
        whatsapp_status["confirmation_template"],
        whatsapp_status["reminder_template"],
    ])

    return render_template(
        "admin.html",
        appointments=appointments,
        professionals=professionals,
        services=services,
        stats=stats,
        whatsapp_status=whatsapp_status,
    )


@bp.post("/profissionais")
@admin_required
def add_professional():
    require_csrf()

    name = request.form.get("name", "").strip()
    bio = request.form.get("bio", "").strip()[:300]

    if len(name) < 2:
        flash("Informe o nome do profissional.", "danger")
        return redirect(url_for("admin.dashboard"))

    db.session.add(Professional(name=name, bio=bio, active=True))

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("Não foi possível adicionar o profissional. Verifique se ele já está cadastrado.", "danger")
        return redirect(url_for("admin.dashboard"))

    flash("Profissional adicionado.", "success")
    return redirect(url_for("admin.dashboard"))


@bp.post("/profissional/<int:professional_id>/alternar")
@admin_required
def toggle_professional(professional_id):
    require_csrf()

    professional = db.session.get(Professional, professional_id)
    if not professional:
        flash("Profissional não encontrado.", "danger")
        return redirect(url_for("admin.dashboard"))

    professional.active = not professional.active
    db.session.commit()

    socketio.emit("schedule_changed", {"reason": "professional_changed"})
    flash("Profissional ativado." if professional.active else "Profissional desativado.", "success")
    return redirect(url_for("admin.dashboard"))


@bp.post("/servicos")
@admin_required
def add_service():
    require_csrf()

    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()[:300]
    duration = request.form.get("duration_minutes", type=int)
    price_text = request.form.get("price", "0").replace(",", ".").strip()

    try:
        price_cents = int(round(float(price_text) * 100))
    except (TypeError, ValueError):
        price_cents = -1

    if not name or not duration or duration < 10 or duration > 240 or price_cents < 0:
        flash("Dados do serviço inválidos.", "danger")
        return redirect(url_for("admin.dashboard"))

    db.session.add(Service(
        name=name,
        description=description,
        duration_minutes=duration,
        price_cents=price_cents,
        active=True,
    ))

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("Não foi possível adicionar o serviço. Verifique se ele já está cadastrado.", "danger")
        return redirect(url_for("admin.dashboard"))

    socketio.emit("schedule_changed", {"reason": "service_created"})
    flash("Serviço adicionado.", "success")
    return redirect(url_for("admin.dashboard"))


@bp.post("/servico/<int:service_id>/alternar")
@admin_required
def toggle_service(service_id):
    require_csrf()

    service = db.session.get(Service, service_id)
    if not service:
        flash("Serviço não encontrado.", "danger")
        return redirect(url_for("admin.dashboard"))

    service.active = not service.active
    db.session.commit()

    socketio.emit("schedule_changed", {"reason": "service_changed"})
    flash("Serviço ativado." if service.active else "Serviço desativado.", "success")
    return redirect(url_for("admin.dashboard"))


@bp.post("/gerar-horarios")
@admin_required
def generate_slots():
    """Compatibilidade com template antigo: a agenda agora é virtual."""
    require_csrf()
    flash("A agenda agora é automática. Não é mais necessário gerar horários manualmente.", "info")
    return redirect(url_for("admin.dashboard"))


@bp.post("/agendamento/<int:appointment_id>/concluir")
@admin_required
def complete_appointment(appointment_id):
    require_csrf()

    appointment = db.session.get(Appointment, appointment_id)
    if not appointment:
        flash("Agendamento não encontrado.", "danger")
        return redirect(url_for("admin.dashboard"))

    if appointment.status == "scheduled":
        appointment.status = "completed"
        db.session.commit()

        socketio.emit("schedule_changed", {
            "professional_id": appointment.professional_id,
            "date": appointment.start_at.strftime("%Y-%m-%d"),
            "reason": "appointment_completed",
        })

        flash("Atendimento concluído.", "success")
    else:
        flash("Esse agendamento não está ativo.", "warning")

    return redirect(url_for("admin.dashboard"))


@bp.post("/agendamento/<int:appointment_id>/cancelar")
@admin_required
def cancel_appointment(appointment_id):
    require_csrf()

    appointment = db.session.get(Appointment, appointment_id)
    if not appointment:
        flash("Agendamento não encontrado.", "danger")
        return redirect(url_for("admin.dashboard"))

    if appointment.status != "scheduled":
        flash("Esse agendamento não está ativo.", "warning")
        return redirect(url_for("admin.dashboard"))

    appointment.status = "cancelled"
    db.session.commit()

    socketio.emit("schedule_changed", {
        "professional_id": appointment.professional_id,
        "date": appointment.start_at.strftime("%Y-%m-%d"),
        "reason": "appointment_cancelled_by_admin",
    })

    flash("Agendamento cancelado.", "success")
    return redirect(url_for("admin.dashboard"))


# ============================================================
# WHATSAPP CLOUD API
# ============================================================

def _env_path():
    return Path(current_app.instance_path).parent / ".env"


def _update_env_file(values):
    path = _env_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    values = {
        key: str(value or "").strip().replace("\r", "").replace("\n", "")
        for key, value in values.items()
    }

    found = set()
    new_lines = []

    for line in lines:
        stripped = line.strip()
        replaced = False

        for key, value in values.items():
            if stripped.startswith(f"{key}="):
                new_lines.append(f"{key}={value}")
                found.add(key)
                replaced = True
                break

        if not replaced:
            new_lines.append(line)

    for key, value in values.items():
        if key not in found:
            new_lines.append(f"{key}={value}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


@bp.post("/whatsapp/configurar")
@admin_required
def configure_whatsapp():
    require_csrf()

    access_token = request.form.get("access_token", "").strip()
    phone_number_id = request.form.get("phone_number_id", "").strip()
    api_version = request.form.get("api_version", "").strip()
    confirmation_template = request.form.get("confirmation_template", "").strip()
    reminder_template = request.form.get("reminder_template", "").strip()
    reminder_minutes = request.form.get("reminder_minutes", type=int)

    if not access_token:
        access_token = current_app.config.get("WHATSAPP_ACCESS_TOKEN", "")

    if not phone_number_id or not phone_number_id.isdigit():
        flash("Phone Number ID inválido.", "danger")
        return redirect(url_for("admin.dashboard") + "#whatsapp")

    if not re.fullmatch(r"v\d+\.\d+", api_version):
        flash("Versão da API inválida. Use o formato vXX.X.", "danger")
        return redirect(url_for("admin.dashboard") + "#whatsapp")

    template_pattern = r"[a-z0-9_]+"

    if not re.fullmatch(template_pattern, confirmation_template):
        flash("Nome do template de confirmação inválido.", "danger")
        return redirect(url_for("admin.dashboard") + "#whatsapp")

    if not re.fullmatch(template_pattern, reminder_template):
        flash("Nome do template de lembrete inválido.", "danger")
        return redirect(url_for("admin.dashboard") + "#whatsapp")

    if not reminder_minutes or reminder_minutes < 5 or reminder_minutes > 1440:
        flash("O lembrete deve ficar entre 5 e 1440 minutos.", "danger")
        return redirect(url_for("admin.dashboard") + "#whatsapp")

    values = {
        "WHATSAPP_ACCESS_TOKEN": access_token,
        "WHATSAPP_PHONE_NUMBER_ID": phone_number_id,
        "WHATSAPP_API_VERSION": api_version,
        "WHATSAPP_CONFIRMATION_TEMPLATE": confirmation_template,
        "WHATSAPP_REMINDER_TEMPLATE": reminder_template,
        "WHATSAPP_TEMPLATE_LANGUAGE": "pt_BR",
        "WHATSAPP_REMINDER_MINUTES": reminder_minutes,
    }

    try:
        _update_env_file(values)
    except OSError:
        current_app.logger.exception("Não foi possível atualizar o .env.")
        flash("Não foi possível salvar o arquivo .env.", "danger")
        return redirect(url_for("admin.dashboard") + "#whatsapp")

    current_app.config.update({
        key: int(value) if key == "WHATSAPP_REMINDER_MINUTES" else value
        for key, value in values.items()
    })

    flash("Configuração do WhatsApp salva.", "success")
    return redirect(url_for("admin.dashboard") + "#whatsapp")


@bp.post("/whatsapp/testar")
@admin_required
def test_whatsapp():
    require_csrf()

    test_phone = request.form.get("test_phone", "").strip()

    try:
        from .whatsapp import _send_template, normalize_phone, whatsapp_is_configured
    except ImportError:
        flash("Os arquivos da integração WhatsApp ainda não foram instalados.", "danger")
        return redirect(url_for("admin.dashboard") + "#whatsapp")

    if not whatsapp_is_configured():
        flash("Complete a configuração do WhatsApp antes do teste.", "warning")
        return redirect(url_for("admin.dashboard") + "#whatsapp")

    try:
        phone = normalize_phone(test_phone)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.dashboard") + "#whatsapp")

    now = local_now()

    result = _send_template(
        phone=phone,
        template_name=current_app.config["WHATSAPP_CONFIRMATION_TEMPLATE"],
        customer_name="Teste",
        service_name="Mensagem de teste",
        date_text=now.strftime("%d/%m/%Y"),
        time_text=now.strftime("%H:%M"),
    )

    if result.get("ok"):
        flash("Mensagem de teste enviada com sucesso pelo WhatsApp.", "success")
    else:
        flash(f"Falha no teste do WhatsApp: {result.get('error', 'erro desconhecido')}", "danger")

    return redirect(url_for("admin.dashboard") + "#whatsapp")
