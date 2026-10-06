from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from .extensions import db
from .models import User
from .utils import require_csrf, safe_next_url, save_avatar


bp = Blueprint("auth", __name__)


@bp.route("/entrar", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("booking.dashboard"))

    if request.method == "POST":
        require_csrf()

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()

        if not user or not user.check_password(password):
            flash("E-mail ou senha inválidos.", "danger")
            return render_template("login.html"), 401

        login_user(user, remember=True)

        flash(f"Bem-vindo de volta, {user.name}.", "success")

        next_url = safe_next_url(request.args.get("next"))
        return redirect(next_url or url_for("booking.dashboard"))

    return render_template("login.html")


@bp.route("/criar-conta", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("booking.dashboard"))

    if request.method == "POST":
        require_csrf()

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")

        errors = []

        if len(name) < 2:
            errors.append("Informe seu nome.")
        if "@" not in email or "." not in email.split("@")[-1]:
            errors.append("Informe um e-mail válido.")
        if len(password) < 8:
            errors.append("A senha deve ter pelo menos 8 caracteres.")
        if password != password_confirm:
            errors.append("As senhas não coincidem.")
        if User.query.filter_by(email=email).first():
            errors.append("Já existe uma conta com este e-mail.")

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template("register.html"), 400

        user = User(name=name, email=email)
        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        login_user(user)

        flash("Conta criada com sucesso. Agora escolha seu primeiro horário.", "success")
        return redirect(url_for("booking.agenda"))

    return render_template("register.html")


@bp.post("/sair")
@login_required
def logout():
    require_csrf()
    logout_user()
    flash("Você saiu da sua conta.", "success")
    return redirect(url_for("booking.index"))


@bp.route("/perfil", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        require_csrf()

        name = request.form.get("name", "").strip()

        if len(name) < 2:
            flash("Informe um nome válido.", "danger")
            return render_template("profile.html"), 400

        current_user.name = name

        avatar = request.files.get("avatar")

        if avatar and avatar.filename:
            old_avatar = current_user.avatar

            try:
                current_user.avatar = save_avatar(avatar, current_user.id)
            except Exception:
                db.session.rollback()
                flash("Não foi possível processar a imagem. Use JPG, PNG ou WEBP.", "danger")
                return render_template("profile.html"), 400

            if old_avatar:
                old_path = Path(current_app.config["UPLOAD_FOLDER"]) / old_avatar
                old_path.unlink(missing_ok=True)

        db.session.commit()

        flash("Perfil atualizado.", "success")
        return redirect(url_for("auth.profile"))

    return render_template("profile.html")
