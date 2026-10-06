from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from .extensions import db
from .utils import require_csrf
from .whatsapp import (
    get_preference,
    mask_phone,
    normalize_phone,
    whatsapp_is_configured,
)
from .whatsapp_models import WhatsAppPreference


bp = Blueprint(
    "whatsapp",
    __name__,
    url_prefix="/whatsapp",
)


@bp.route(
    "/configuracao",
    methods=["GET", "POST"],
)
@login_required
def settings():
    preference = get_preference(
        current_user.id
    )

    if request.method == "POST":
        require_csrf()

        opt_in = (
            request.form.get("opt_in")
            == "on"
        )

        phone_raw = (
            request.form.get("phone", "")
            .strip()
        )

        if opt_in:
            try:
                phone = normalize_phone(
                    phone_raw
                )
            except ValueError as exc:
                flash(
                    str(exc),
                    "danger",
                )

                return render_template(
                    "whatsapp_settings.html",
                    preference=preference,
                    masked_phone=(
                        mask_phone(
                            preference.phone
                        )
                        if preference
                        else ""
                    ),
                    whatsapp_configured=
                        whatsapp_is_configured(),
                )

        else:
            phone = (
                preference.phone
                if preference
                else (
                    normalize_phone(phone_raw)
                    if phone_raw
                    else ""
                )
            )

        if not preference:
            preference = WhatsAppPreference(
                user_id=current_user.id,
                phone=phone,
                opt_in=opt_in,
            )

            db.session.add(
                preference
            )
        else:
            preference.phone = phone
            preference.opt_in = opt_in

        db.session.commit()

        flash(
            (
                "WhatsApp ativado para confirmações e lembretes."
                if opt_in
                else "Mensagens de WhatsApp desativadas."
            ),
            "success",
        )

        return redirect(
            url_for(
                "whatsapp.settings"
            )
        )

    return render_template(
        "whatsapp_settings.html",
        preference=preference,
        masked_phone=(
            mask_phone(preference.phone)
            if preference
            else ""
        ),
        whatsapp_configured=
            whatsapp_is_configured(),
    )
