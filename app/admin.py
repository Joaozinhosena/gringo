from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from .extensions import db, socketio
from .models import Appointment, Professional, Service, Slot, User
from .utils import admin_required, require_csrf


bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.get("/")
@admin_required
def dashboard():
    appointments = (
        Appointment.query.order_by(Appointment.start_at.desc())
        .limit(80)
        .all()
    )

    professionals = Professional.query.order_by(Professional.name.asc()).all()
    services = Service.query.order_by(Service.name.asc()).all()

    stats = {
        "clients": User.query.filter_by(role="client").count(),
        "scheduled": Appointment.query.filter_by(status="scheduled").count(),
        "available_slots": (
            Slot.query.filter(
                Slot.active.is_(True),
                Slot.appointment_id.is_(None),
                Slot.start_at > datetime.now(),
            ).count()
        ),
    }

    return render_template(
        "admin.html",
        appointments=appointments,
        professionals=professionals,
        services=services,
        stats=stats,
    )


@bp.post("/profissionais")
@admin_required
def add_professional():
    require_csrf()

    name = request.form.get("name", "gringo").strip()
    bio = request.form.get("bio", "").strip()[:300]

    if len(name) < 2:
        flash("Informe o nome do profissional.", "danger")
        return redirect(url_for("admin.dashboard"))

    db.session.add(Professional(name=name, bio=bio))
    db.session.commit()

    flash("Profissional adicionado.", "success")
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
    except ValueError:
        price_cents = -1

    if not name or not duration or duration < 10 or price_cents < 0:
        flash("Dados do serviço inválidos.", "danger")
        return redirect(url_for("admin.dashboard"))

    db.session.add(
        Service(
            name=name,
            description=description,
            duration_minutes=duration,
            price_cents=price_cents,
        )
    )
    db.session.commit()

    flash("Serviço adicionado.", "success")
    return redirect(url_for("admin.dashboard"))


@bp.post("/gerar-horarios")
@admin_required
def generate_slots():
    require_csrf()

    professional_id = request.form.get("professional_id", type=int)
    date_from = request.form.get("date_from", "").strip()
    date_to = request.form.get("date_to", "").strip()
    start_text = request.form.get("start_time", "").strip()
    end_text = request.form.get("end_time", "").strip()
    interval = request.form.get("interval", type=int)
    weekdays = {int(v) for v in request.form.getlist("weekdays") if v.isdigit()}

    professional = db.session.get(Professional, professional_id) if professional_id else None

    if not professional or not weekdays or not interval or interval < 10:
        flash("Preencha profissional, dias da semana e intervalo.", "danger")
        return redirect(url_for("admin.dashboard"))

    try:
        start_date = datetime.strptime(date_from, "%Y-%m-%d").date()
        end_date = datetime.strptime(date_to, "%Y-%m-%d").date()
        start_time = datetime.strptime(start_text, "%H:%M").time()
        end_time = datetime.strptime(end_text, "%H:%M").time()
    except ValueError:
        flash("Datas ou horários inválidos.", "danger")
        return redirect(url_for("admin.dashboard"))

    if end_date < start_date:
        flash("A data final não pode ser anterior à inicial.", "danger")
        return redirect(url_for("admin.dashboard"))

    if end_date - start_date > timedelta(days=90):
        flash("Gere no máximo 90 dias por vez.", "warning")
        return redirect(url_for("admin.dashboard"))

    created = 0
    current_date = start_date

    while current_date <= end_date:
        if current_date.weekday() in weekdays:
            cursor = datetime.combine(current_date, start_time)
            limit = datetime.combine(current_date, end_time)

            while cursor + timedelta(minutes=interval) <= limit:
                slot = Slot(
                    professional_id=professional.id,
                    start_at=cursor,
                    end_at=cursor + timedelta(minutes=interval),
                )

                db.session.add(slot)

                try:
                    db.session.commit()
                    created += 1
                except IntegrityError:
                    db.session.rollback()

                cursor += timedelta(minutes=interval)

        current_date += timedelta(days=1)

    socketio.emit(
        "schedule_changed",
        {"professional_id": professional.id},
    )

    flash(f"{created} horário(s) criado(s).", "success")
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
        flash("Atendimento concluído.", "success")

    return redirect(url_for("admin.dashboard"))
