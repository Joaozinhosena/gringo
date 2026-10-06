import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
INSTANCE_DIR = BASE_DIR / "instance"
UPLOAD_DIR = INSTANCE_DIR / "uploads"
DATABASE_FILE = INSTANCE_DIR / "barber_pro.db"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

load_dotenv(BASE_DIR / ".env")

from app.extensions import db, login_manager, socketio
from app.models import User
from app.utils import get_csrf_token

# Importa os modelos extras antes de db.create_all().
from app.whatsapp_models import (
    WhatsAppNotification,
    WhatsAppPreference,
)


def create_app():
    INSTANCE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    UPLOAD_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    STATIC_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    application = Flask(
        __name__,
        template_folder=str(TEMPLATE_DIR),
        static_folder=str(STATIC_DIR),
        static_url_path="/static",
        instance_path=str(INSTANCE_DIR),
    )

    database_url = os.getenv(
        "DATABASE_URL",
        "",
    ).strip()

    if not database_url:
        database_url = (
            f"sqlite:///{DATABASE_FILE.resolve().as_posix()}"
        )

    application.config.update(
        SECRET_KEY=os.getenv(
            "SECRET_KEY",
            "dev-change-this-key",
        ),
        SQLALCHEMY_DATABASE_URI=
            database_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=
            False,
        MAX_CONTENT_LENGTH=
            4 * 1024 * 1024,
        UPLOAD_FOLDER=
            str(UPLOAD_DIR),

        APP_NAME=os.getenv(
            "APP_NAME",
            "",
        ).strip(),

        VAPID_PUBLIC_KEY=os.getenv(
            "VAPID_PUBLIC_KEY",
            "",
        ).strip(),

        VAPID_PRIVATE_KEY=os.getenv(
            "VAPID_PRIVATE_KEY",
            "",
        ).strip(),

        VAPID_CLAIMS_EMAIL=os.getenv(
            "VAPID_CLAIMS_EMAIL",
            "",
        ).strip(),

        WHATSAPP_ACCESS_TOKEN=os.getenv(
            "WHATSAPP_ACCESS_TOKEN",
            "",
        ).strip(),

        WHATSAPP_PHONE_NUMBER_ID=os.getenv(
            "WHATSAPP_PHONE_NUMBER_ID",
            "",
        ).strip(),

        WHATSAPP_API_VERSION=os.getenv(
            "WHATSAPP_API_VERSION",
            "",
        ).strip(),

        WHATSAPP_CONFIRMATION_TEMPLATE=os.getenv(
            "WHATSAPP_CONFIRMATION_TEMPLATE",
            "",
        ).strip(),

        WHATSAPP_REMINDER_TEMPLATE=os.getenv(
            "WHATSAPP_REMINDER_TEMPLATE",
            "",
        ).strip(),

        WHATSAPP_TEMPLATE_LANGUAGE=os.getenv(
            "WHATSAPP_TEMPLATE_LANGUAGE",
            "pt_BR",
        ).strip(),

        WHATSAPP_REMINDER_MINUTES=int(
            os.getenv(
                "WHATSAPP_REMINDER_MINUTES",
                "60",
            )
        ),

        WHATSAPP_REMINDER_CHECK_SECONDS=int(
            os.getenv(
                "WHATSAPP_REMINDER_CHECK_SECONDS",
                "60",
            )
        ),
    )

    db.init_app(application)
    login_manager.init_app(application)

    socketio.init_app(
        application,
        async_mode="threading",
        cors_allowed_origins="*",
        logger=False,
        engineio_logger=False,
    )

    from app.auth import bp as auth_bp
    from app.booking import bp as booking_bp
    from app.admin import bp as admin_bp
    from app.whatsapp_routes import bp as whatsapp_bp

    application.register_blueprint(
        auth_bp
    )

    application.register_blueprint(
        booking_bp
    )

    application.register_blueprint(
        admin_bp
    )

    application.register_blueprint(
        whatsapp_bp
    )

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(
                User,
                int(user_id),
            )
        except (
            TypeError,
            ValueError,
        ):
            return None

    @application.context_processor
    def inject_globals():
        return {
            "csrf_token":
                get_csrf_token,
            "app_name":
                application.config[
                    "APP_NAME"
                ],
            "vapid_public_key":
                application.config[
                    "VAPID_PUBLIC_KEY"
                ],
        }

    @application.template_filter("money")
    def money(value_cents):
        try:
            cents = int(
                value_cents or 0
            )
        except (
            TypeError,
            ValueError,
        ):
            cents = 0

        return (
            f"R$ {cents / 100:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    @application.get(
        "/uploads/<path:filename>"
    )
    def uploaded_file(filename):
        return send_from_directory(
            application.config[
                "UPLOAD_FOLDER"
            ],
            filename,
        )

    @application.get(
        "/service-worker.js"
    )
    def service_worker():
        app_name_json = json.dumps(
            application.config.get(
                "APP_NAME",
                "",
            )
        )

        code = """
self.addEventListener("push", (event) => {
    let data = {
        title: __APP_NAME__,
        body: "",
        url: "/dashboard"
    };

    if (event.data) {
        try {
            data = event.data.json();
        } catch (_) {}
    }

    event.waitUntil(
        self.registration.showNotification(
            data.title || __APP_NAME__,
            {
                body: data.body || "",
                data: {
                    url: data.url || "/dashboard"
                }
            }
        )
    );
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();

    const url =
        (event.notification.data && event.notification.data.url)
        || "/dashboard";

    event.waitUntil(
        clients.openWindow(url)
    );
});
""".replace(
            "__APP_NAME__",
            app_name_json,
        )

        return Response(
            code,
            mimetype=
                "application/javascript",
            headers={
                "Cache-Control":
                    "no-cache, no-store, must-revalidate",
                "Service-Worker-Allowed":
                    "/",
            },
        )

    with application.app_context():
        db.create_all()

    return application


app = create_app()


if __name__ == "__main__":
    from app.reminders import (
        start_reminder_worker,
    )

    start_reminder_worker(
        app
    )

    print(
        "Servidor: "
        "http://127.0.0.1:5000"
    )

    socketio.run(
        app,
        host="127.0.0.1",
        port=5000,
        debug=True,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )
