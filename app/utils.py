import secrets
from functools import wraps
from pathlib import Path

from flask import abort, current_app, request, session
from flask_login import current_user
from PIL import Image


ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def get_csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def validate_csrf(token):
    expected = session.get("_csrf_token")
    return bool(
        token
        and expected
        and secrets.compare_digest(str(token), str(expected))
    )


def require_csrf():
    token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not validate_csrf(token):
        abort(400, description="Token CSRF inválido ou expirado.")


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def save_avatar(file_storage, user_id):
    if not file_storage or not file_storage.filename:
        return None

    ext = Path(file_storage.filename).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError("Formato de imagem não permitido.")

    upload_dir = Path(current_app.config["UPLOAD_FOLDER"])
    upload_dir.mkdir(parents=True, exist_ok=True)

    filename = f"user_{user_id}_{secrets.token_hex(6)}.jpg"
    path = upload_dir / filename

    image = Image.open(file_storage.stream)
    image = image.convert("RGB")

    side = min(image.width, image.height)
    left = (image.width - side) // 2
    top = (image.height - side) // 2

    image = image.crop((left, top, left + side, top + side))
    image = image.resize((256, 256), Image.Resampling.LANCZOS)
    image.save(path, format="JPEG", quality=88, optimize=True)

    return filename


def safe_next_url(value):
    if not value:
        return None
    if value.startswith("/") and not value.startswith("//"):
        return value
    return None
