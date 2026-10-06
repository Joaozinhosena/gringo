from datetime import datetime, timedelta
from pathlib import Path
import secrets

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import (
    current_user,
    login_required,
    login_user,
    logout_user,
)
from sqlalchemy.exc import IntegrityError
from werkzeug.security import (
    check_password_hash,
    generate_password_hash,
)

from .email_models import PendingRegistration
from .email_service import send_verification_email
from .extensions import db
from .models import User
from .utils import (
    require_csrf,
    safe_next_url,
    save_avatar,
)


bp = Blueprint("auth", __name__)

_PENDING_SESSION_KEY = "_pending_registration_id"


def _utcnow():
    return datetime.utcnow()


def _verification_code():
    return f"{secrets.randbelow(1_000_000):06d}"


def _mask_email(email):
    if not email or "@" not in email:
        return email or ""

    local, domain = email.split("@", 1)

    if len(local) <= 2:
        masked_local = local[:1] + "***"
    else:
        masked_local = local[:2] + "***"

    return f"{masked_local}@{domain}"


def _get_pending_registration():
    pending_id = session.get(_PENDING_SESSION_KEY)

    if not pending_id:
        return None

    try:
        pending_id = int(pending_id)
    except (TypeError, ValueError):
        session.pop(_PENDING_SESSION_KEY, None)
        return None

    pending = db.session.get(
        PendingRegistration,
        pending_id,
    )

    if not pending:
        session.pop(_PENDING_SESSION_KEY, None)

    return pending


def _prepare_pending_registration(
    *,
    name,
    email,
    password_hash,
    code,
):
    now = _utcnow()

    expires_minutes = int(
        current_app.config.get(
            "MAIL_CODE_EXPIRES_MINUTES",
            10,
        )
    )

    resend_seconds = int(
        current_app.config.get(
            "MAIL_CODE_RESEND_SECONDS",
            60,
        )
    )

    pending = (
        PendingRegistration.query
        .filter_by(email=email)
        .first()
    )

    if pending is None:
        pending = PendingRegistration(
            name=name,
            email=email,
            password_hash=password_hash,
            code_hash=generate_password_hash(code),
            expires_at=(
                now
                + timedelta(
                    minutes=expires_minutes
                )
            ),
            resend_available_at=(
                now
                + timedelta(
                    seconds=resend_seconds
                )
            ),
            attempts=0,
        )

        db.session.add(pending)

    else:
        pending.name = name
        pending.password_hash = password_hash
        pending.code_hash = generate_password_hash(code)
        pending.expires_at = (
            now
            + timedelta(
                minutes=expires_minutes
            )
        )
        pending.resend_available_at = (
            now
            + timedelta(
                seconds=resend_seconds
            )
        )
        pending.attempts = 0
        pending.updated_at = now

    return pending


@bp.route("/entrar", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(
            url_for("booking.dashboard")
        )

    if request.method == "POST":
        require_csrf()

        email = (
            request.form
            .get("email", "")
            .strip()
            .lower()
        )

        password = request.form.get(
            "password",
            "",
        )

        user = (
            User.query
            .filter_by(email=email)
            .first()
        )

        if (
            not user
            or not user.check_password(password)
        ):
            flash(
                "E-mail ou senha inválidos.",
                "danger",
            )

            return render_template(
                "login.html"
            ), 401

        login_user(
            user,
            remember=True,
        )

        flash(
            f"Bem-vindo de volta, {user.name}.",
            "success",
        )

        next_url = safe_next_url(
            request.args.get("next")
        )

        return redirect(
            next_url
            or url_for(
                "booking.dashboard"
            )
        )

    return render_template(
        "login.html"
    )


@bp.route(
    "/criar-conta",
    methods=["GET", "POST"],
)
def register():
    if current_user.is_authenticated:
        return redirect(
            url_for("booking.dashboard")
        )

    if request.method == "POST":
        require_csrf()

        name = (
            request.form
            .get("name", "")
            .strip()
        )

        email = (
            request.form
            .get("email", "")
            .strip()
            .lower()
        )

        password = request.form.get(
            "password",
            "",
        )

        password_confirm = (
            request.form
            .get(
                "password_confirm",
                "",
            )
        )

        errors = []

        if len(name) < 2:
            errors.append(
                "Informe seu nome."
            )

        if (
            "@" not in email
            or "." not in email.split("@")[-1]
        ):
            errors.append(
                "Informe um e-mail válido."
            )

        if len(password) < 8:
            errors.append(
                "A senha deve ter pelo menos 8 caracteres."
            )

        if password != password_confirm:
            errors.append(
                "As senhas não coincidem."
            )

        if (
            email
            and User.query
            .filter_by(email=email)
            .first()
        ):
            errors.append(
                "Já existe uma conta com este e-mail."
            )

        if errors:
            for error in errors:
                flash(
                    error,
                    "danger",
                )

            return render_template(
                "register.html",
                form_name=name,
                form_email=email,
            ), 400

        existing_pending = (
            PendingRegistration.query
            .filter_by(email=email)
            .first()
        )

        now = _utcnow()

        # Evita que o formulário de cadastro seja usado para disparar
        # vários e-mails em poucos segundos.
        if (
            existing_pending
            and existing_pending.resend_available_at
            and existing_pending.resend_available_at > now
        ):
            session[
                _PENDING_SESSION_KEY
            ] = existing_pending.id

            flash(
                "Já enviamos um código para este e-mail. "
                "Aguarde alguns segundos para solicitar outro.",
                "info",
            )

            return redirect(
                url_for(
                    "auth.verify_email"
                )
            )

        code = _verification_code()

        pending = _prepare_pending_registration(
            name=name,
            email=email,
            password_hash=generate_password_hash(
                password
            ),
            code=code,
        )

        try:
            # Gera o ID sem confirmar a transação ainda.
            db.session.flush()

            send_verification_email(
                to_email=email,
                customer_name=name,
                code=code,
            )

            db.session.commit()

        except Exception:
            db.session.rollback()

            current_app.logger.exception(
                "Falha ao enviar o código de verificação de e-mail."
            )

            flash(
                "Não foi possível enviar o código de verificação. "
                "Confira a configuração de e-mail do servidor e tente novamente.",
                "danger",
            )

            return render_template(
                "register.html",
                form_name=name,
                form_email=email,
            ), 502

        session[
            _PENDING_SESSION_KEY
        ] = pending.id

        flash(
            "Enviamos um código de 6 dígitos para o seu e-mail.",
            "success",
        )

        return redirect(
            url_for(
                "auth.verify_email"
            )
        )

    return render_template(
        "register.html"
    )


@bp.route(
    "/verificar-email",
    methods=["GET", "POST"],
)
def verify_email():
    if current_user.is_authenticated:
        return redirect(
            url_for("booking.dashboard")
        )

    pending = _get_pending_registration()

    if not pending:
        flash(
            "Inicie o cadastro para receber um código de verificação.",
            "info",
        )

        return redirect(
            url_for("auth.register")
        )

    # Se uma conta foi criada por outro fluxo enquanto a confirmação
    # estava aberta, não criamos uma duplicata.
    existing_user = (
        User.query
        .filter_by(
            email=pending.email
        )
        .first()
    )

    if existing_user:
        db.session.delete(pending)
        db.session.commit()

        session.pop(
            _PENDING_SESSION_KEY,
            None,
        )

        flash(
            "Este e-mail já possui uma conta. Entre com sua senha.",
            "info",
        )

        return redirect(
            url_for("auth.login")
        )

    now = _utcnow()

    if request.method == "POST":
        require_csrf()

        code = (
            request.form
            .get("code", "")
            .strip()
        )

        max_attempts = int(
            current_app.config.get(
                "MAIL_CODE_MAX_ATTEMPTS",
                5,
            )
        )

        if (
            len(code) != 6
            or not code.isdigit()
        ):
            flash(
                "Digite os 6 números do código.",
                "danger",
            )

            return render_template(
                "verify_email.html",
                pending=pending,
                masked_email=_mask_email(
                    pending.email
                ),
                expired=(
                    pending.expires_at
                    <= now
                ),
                attempts_left=max(
                    0,
                    max_attempts
                    - pending.attempts,
                ),
            ), 400

        if pending.expires_at <= now:
            flash(
                "Este código expirou. Solicite um novo código.",
                "danger",
            )

            return render_template(
                "verify_email.html",
                pending=pending,
                masked_email=_mask_email(
                    pending.email
                ),
                expired=True,
                attempts_left=max(
                    0,
                    max_attempts
                    - pending.attempts,
                ),
            ), 410

        if pending.attempts >= max_attempts:
            flash(
                "O limite de tentativas deste código foi atingido. "
                "Solicite um novo código.",
                "danger",
            )

            return render_template(
                "verify_email.html",
                pending=pending,
                masked_email=_mask_email(
                    pending.email
                ),
                expired=False,
                attempts_left=0,
            ), 429

        if not check_password_hash(
            pending.code_hash,
            code,
        ):
            pending.attempts += 1
            pending.updated_at = now

            db.session.commit()

            attempts_left = max(
                0,
                max_attempts
                - pending.attempts,
            )

            if attempts_left:
                flash(
                    f"Código incorreto. Restam {attempts_left} tentativa(s).",
                    "danger",
                )
            else:
                flash(
                    "Código incorreto. O limite de tentativas foi atingido. "
                    "Solicite um novo código.",
                    "danger",
                )

            return render_template(
                "verify_email.html",
                pending=pending,
                masked_email=_mask_email(
                    pending.email
                ),
                expired=False,
                attempts_left=attempts_left,
            ), 400

        user = User(
            name=pending.name,
            email=pending.email,
        )

        # A senha já foi transformada em hash quando o cadastro começou.
        user.password_hash = (
            pending.password_hash
        )

        db.session.add(user)
        db.session.delete(pending)

        try:
            db.session.commit()

        except IntegrityError:
            db.session.rollback()

            session.pop(
                _PENDING_SESSION_KEY,
                None,
            )

            flash(
                "Este e-mail já foi cadastrado. Entre com sua conta.",
                "info",
            )

            return redirect(
                url_for("auth.login")
            )

        session.pop(
            _PENDING_SESSION_KEY,
            None,
        )

        login_user(user)

        flash(
            "E-mail confirmado. Sua conta foi criada com sucesso.",
            "success",
        )

        return redirect(
            url_for("booking.agenda")
        )

    max_attempts = int(
        current_app.config.get(
            "MAIL_CODE_MAX_ATTEMPTS",
            5,
        )
    )

    return render_template(
        "verify_email.html",
        pending=pending,
        masked_email=_mask_email(
            pending.email
        ),
        expired=(
            pending.expires_at <= now
        ),
        attempts_left=max(
            0,
            max_attempts
            - pending.attempts,
        ),
    )


@bp.post("/reenviar-codigo-email")
def resend_email_code():
    if current_user.is_authenticated:
        return redirect(
            url_for("booking.dashboard")
        )

    require_csrf()

    pending = _get_pending_registration()

    if not pending:
        flash(
            "Inicie novamente o cadastro.",
            "info",
        )

        return redirect(
            url_for("auth.register")
        )

    if (
        User.query
        .filter_by(email=pending.email)
        .first()
    ):
        db.session.delete(pending)
        db.session.commit()

        session.pop(
            _PENDING_SESSION_KEY,
            None,
        )

        flash(
            "Este e-mail já possui uma conta.",
            "info",
        )

        return redirect(
            url_for("auth.login")
        )

    now = _utcnow()

    if (
        pending.resend_available_at
        and pending.resend_available_at > now
    ):
        seconds = int(
            (
                pending.resend_available_at
                - now
            ).total_seconds()
        ) + 1

        flash(
            f"Aguarde {seconds} segundo(s) para solicitar outro código.",
            "info",
        )

        return redirect(
            url_for(
                "auth.verify_email"
            )
        )

    code = _verification_code()

    expires_minutes = int(
        current_app.config.get(
            "MAIL_CODE_EXPIRES_MINUTES",
            10,
        )
    )

    resend_seconds = int(
        current_app.config.get(
            "MAIL_CODE_RESEND_SECONDS",
            60,
        )
    )

    pending.code_hash = (
        generate_password_hash(code)
    )

    pending.expires_at = (
        now
        + timedelta(
            minutes=expires_minutes
        )
    )

    pending.resend_available_at = (
        now
        + timedelta(
            seconds=resend_seconds
        )
    )

    pending.attempts = 0
    pending.updated_at = now

    try:
        send_verification_email(
            to_email=pending.email,
            customer_name=pending.name,
            code=code,
        )

        db.session.commit()

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Falha ao reenviar o código de verificação."
        )

        flash(
            "Não foi possível reenviar o código agora. Tente novamente.",
            "danger",
        )

        return redirect(
            url_for(
                "auth.verify_email"
            )
        )

    flash(
        "Um novo código foi enviado para o seu e-mail.",
        "success",
    )

    return redirect(
        url_for(
            "auth.verify_email"
        )
    )


@bp.post("/sair")
@login_required
def logout():
    require_csrf()

    logout_user()

    flash(
        "Você saiu da sua conta.",
        "success",
    )

    return redirect(
        url_for("booking.index")
    )


@bp.route(
    "/perfil",
    methods=["GET", "POST"],
)
@login_required
def profile():
    if request.method == "POST":
        require_csrf()

        name = (
            request.form
            .get("name", "")
            .strip()
        )

        if len(name) < 2:
            flash(
                "Informe um nome válido.",
                "danger",
            )

            return render_template(
                "profile.html"
            ), 400

        current_user.name = name

        avatar = request.files.get(
            "avatar"
        )

        if (
            avatar
            and avatar.filename
        ):
            old_avatar = (
                current_user.avatar
            )

            try:
                current_user.avatar = (
                    save_avatar(
                        avatar,
                        current_user.id,
                    )
                )

            except Exception:
                db.session.rollback()

                flash(
                    "Não foi possível processar a imagem. "
                    "Use JPG, PNG ou WEBP.",
                    "danger",
                )

                return render_template(
                    "profile.html"
                ), 400

            if old_avatar:
                old_path = (
                    Path(
                        current_app.config[
                            "UPLOAD_FOLDER"
                        ]
                    )
                    / old_avatar
                )

                old_path.unlink(
                    missing_ok=True
                )

        db.session.commit()

        flash(
            "Perfil atualizado.",
            "success",
        )

        return redirect(
            url_for("auth.profile")
        )

    return render_template(
        "profile.html"
    )
