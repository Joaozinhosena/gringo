from run import app
from app.extensions import db
from app.models import User

email = input("Digite o e-mail que será ADMIN: ").strip().lower()

with app.app_context():

    usuario = (
        User.query
        .filter(db.func.lower(User.email) == email)
        .first()
    )

    if not usuario:
        print("❌ Usuário não encontrado.")
        print("Cadastre esse e-mail no site primeiro.")

    elif usuario.role == "admin":
        print("✅ Esse usuário já é administrador.")

    else:
        usuario.role = "admin"

        db.session.commit()

        print("✅ Administrador criado com sucesso!")
        print("Nome:", usuario.name)
        print("E-mail:", usuario.email)