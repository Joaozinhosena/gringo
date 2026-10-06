from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent
PROJECT = Path.cwd()

if not (PROJECT / "run.py").exists():
    raise SystemExit(
        "Execute este instalador dentro da pasta raiz do projeto, "
        "onde está o arquivo run.py."
    )

files = {
    ROOT / "app" / "admin_stats.py":
        PROJECT / "app" / "admin_stats.py",

    ROOT / "templates" / "admin_stats.html":
        PROJECT / "templates" / "admin_stats.html",

    ROOT / "templates" / "admin.html":
        PROJECT / "templates" / "admin.html",
}

for source, target in files.items():
    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if target.exists():
        backup = target.with_suffix(
            target.suffix + ".bak"
        )

        shutil.copy2(
            target,
            backup,
        )

        print(
            "Backup:",
            backup,
        )

    shutil.copy2(
        source,
        target,
    )

    print(
        "Instalado:",
        target,
    )


run_path = PROJECT / "run.py"

run_text = run_path.read_text(
    encoding="utf-8"
)

if "admin_stats_bp" not in run_text:

    import_patterns = [
        (
            "from app.admin import bp as admin_bp",
            "from app.admin import bp as admin_bp\n"
            "    from app.admin_stats import bp as admin_stats_bp"
        ),
        (
            "from app.admin import (\n        bp as admin_bp,\n    )",
            "from app.admin import (\n"
            "        bp as admin_bp,\n"
            "    )\n"
            "    from app.admin_stats import (\n"
            "        bp as admin_stats_bp,\n"
            "    )"
        ),
    ]

    changed = False

    for old, new in import_patterns:
        if old in run_text:
            run_text = run_text.replace(
                old,
                new,
                1,
            )

            changed = True
            break

    if not changed:
        raise SystemExit(
            "Não consegui localizar automaticamente a importação "
            "do admin no run.py. Consulte INTEGRACAO_MANUAL.txt."
        )


if "register_blueprint(\n        admin_stats_bp" not in run_text and \
   "register_blueprint(admin_stats_bp" not in run_text:

    register_patterns = [
        (
            "application.register_blueprint(admin_bp)",
            "application.register_blueprint(admin_bp)\n"
            "    application.register_blueprint(admin_stats_bp)"
        ),
        (
            "application.register_blueprint(\n        admin_bp\n    )",
            "application.register_blueprint(\n"
            "        admin_bp\n"
            "    )\n\n"
            "    application.register_blueprint(\n"
            "        admin_stats_bp\n"
            "    )"
        ),
    ]

    changed = False

    for old, new in register_patterns:
        if old in run_text:
            run_text = run_text.replace(
                old,
                new,
                1,
            )

            changed = True
            break

    if not changed:
        raise SystemExit(
            "Não consegui localizar automaticamente o registro "
            "do admin no run.py. Consulte INTEGRACAO_MANUAL.txt."
        )


run_backup = run_path.with_suffix(
    ".py.bak_estatisticas"
)

shutil.copy2(
    run_path,
    run_backup,
)

run_path.write_text(
    run_text,
    encoding="utf-8",
)

print(
    "Backup run.py:",
    run_backup,
)

print()
print(
    "Estatísticas instaladas."
)

print(
    "Reinicie com: python run.py"
)

print(
    "Depois abra: http://127.0.0.1:5000/admin/estatisticas"
)
