import os

from sqlalchemy import text

from .extensions import db
from .models import Professional, Service, User


DEFAULT_PROFESSIONALS = [
    {
        "name": "",
        "bio": "",
        "active": True,
    },
]


DEFAULT_SERVICES = [
    {
        "name": "Corte",
        "description": "",
        "duration_minutes": 30,
        "price_cents": 3500,
        "active": True,
    },
    {
        "name": "Barba",
        "description": "",
        "duration_minutes": 30,
        "price_cents": 2500,
        "active": True,
    },
    {
        "name": "Corte + Barba",
        "description": "",
        "duration_minutes": 60,
        "price_cents": 5500,
        "active": True,
    },
]


def ensure_default_catalog(application):
    """
    Cria os dados iniciais apenas se as tabelas estiverem vazias.
    """

    with application.app_context():
        db.create_all()

        changed = False

        if Professional.query.count() == 0:
            db.session.add_all(
                [
                    Professional(**item)
                    for item in DEFAULT_PROFESSIONALS
                ]
            )
            changed = True

        if Service.query.count() == 0:
            db.session.add_all(
                [
                    Service(**item)
                    for item in DEFAULT_SERVICES
                ]
            )
            changed = True

        if changed:
            db.session.commit()
            print(
                "Catálogo inicial criado automaticamente."
            )

        print(
            f"Serviços: {Service.query.count()} | "
            f"Profissionais: {Professional.query.count()}"
        )


def ensure_admin_account(application):
    """
    Cria ou atualiza o administrador usando somente o .env.
    """

    admin_name = os.getenv(
        "ADMIN_NAME",
        "",
    ).strip()

    admin_email = os.getenv(
        "ADMIN_EMAIL",
        "",
    ).strip().lower()

    admin_password = os.getenv(
        "ADMIN_PASSWORD",
        "",
    )

    if not admin_email or not admin_password:
        print(
            "Administrador não configurado no .env."
        )
        return

    with application.app_context():
        admin = User.query.filter_by(
            email=admin_email
        ).first()

        if not admin:
            admin = User(
                name=admin_name or admin_email,
                email=admin_email,
                role="admin",
            )

            db.session.add(admin)

        else:
            admin.role = "admin"

            if admin_name:
                admin.name = admin_name

        admin.set_password(
            admin_password
        )

        db.session.commit()

        print(
            f"Administrador configurado: {admin_email}"
        )


def ensure_sqlite_schema_compatibility(application):
    """
    Atualiza bancos SQLite antigos.
    """

    database_url = str(
        application.config.get(
            "SQLALCHEMY_DATABASE_URI",
            "",
        )
    )

    if not database_url.startswith("sqlite:"):
        return

    with application.app_context():
        columns = {
            row[1]
            for row in db.session.execute(
                text(
                    "PRAGMA table_info(slots)"
                )
            ).fetchall()
        }

        if not columns:
            return

        changed = False

        if "hold_user_id" not in columns:
            db.session.execute(
                text(
                    "ALTER TABLE slots "
                    "ADD COLUMN hold_user_id INTEGER"
                )
            )
            changed = True

        if "hold_expires_at" not in columns:
            db.session.execute(
                text(
                    "ALTER TABLE slots "
                    "ADD COLUMN hold_expires_at DATETIME"
                )
            )
            changed = True

        if changed:
            db.session.commit()

            print(
                "Banco SQLite atualizado para o schema atual."
            )
