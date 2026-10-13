from pathlib import Path
import re
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "run.py"
UTILS = ROOT / "app" / "utils.py"

MAX_UPLOAD_MB = 25


def backup(path):
    if not path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = path.with_name(path.name + f".backup-{stamp}")
    shutil.copy2(path, backup_path)
    print(f"Backup criado: {backup_path}")
    return backup_path


def patch_run():
    text = RUN.read_text(encoding="utf-8")

    pattern = re.compile(
        r'MAX_CONTENT_LENGTH\s*=\s*'
        r'(?:\d+\s*\*\s*1024\s*\*\s*1024|\d+)',
        re.M,
    )

    replacement = (
        f"MAX_CONTENT_LENGTH=\n"
        f"            {MAX_UPLOAD_MB} * 1024 * 1024"
    )

    new_text, count = pattern.subn(
        replacement,
        text,
        count=1,
    )

    if count != 1:
        raise RuntimeError(
            "Não encontrei MAX_CONTENT_LENGTH no run.py."
        )

    RUN.write_text(new_text, encoding="utf-8")
    print(f"run.py: limite global alterado para {MAX_UPLOAD_MB} MB.")


def ensure_imageops_import(text):
    if re.search(
        r'from\s+PIL\s+import\s+[^\n]*\bImageOps\b',
        text,
    ):
        return text

    match = re.search(
        r'from\s+PIL\s+import\s+([^\n]+)',
        text,
    )

    if match:
        imported = match.group(1).strip()
        replacement = f"from PIL import {imported}, ImageOps"
        return (
            text[:match.start()]
            + replacement
            + text[match.end():]
        )

    return "from PIL import Image, ImageOps\n" + text


def patch_utils():
    text = UTILS.read_text(encoding="utf-8")
    text = ensure_imageops_import(text)

    start_match = re.search(
        r'^def\s+save_avatar\s*\(',
        text,
        flags=re.M,
    )

    if not start_match:
        raise RuntimeError(
            "Não encontrei def save_avatar(...) em app/utils.py."
        )

    next_def = re.search(
        r'^def\s+\w+\s*\(',
        text[start_match.end():],
        flags=re.M,
    )

    if next_def:
        end = start_match.end() + next_def.start()
    else:
        end = len(text)

    new_function = """def save_avatar(file_storage, user_id):
    if not file_storage or not file_storage.filename:
        return None

    ext = Path(file_storage.filename).suffix.lower()

    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError(
            "Formato de imagem não permitido."
        )

    upload_dir = Path(
        current_app.config["UPLOAD_FOLDER"]
    )
    upload_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    filename = (
        f"user_{user_id}_"
        f"{secrets.token_hex(6)}.jpg"
    )
    path = upload_dir / filename

    try:
        image = Image.open(
            file_storage.stream
        )

        image = ImageOps.exif_transpose(
            image
        )

        image.load()

    except Exception as exc:
        raise ValueError(
            "Imagem inválida."
        ) from exc

    if image.width < 50 or image.height < 50:
        raise ValueError(
            "A imagem é pequena demais."
        )

    image = image.convert("RGB")

    if (
        image.width > 4096
        or image.height > 4096
    ):
        image.thumbnail(
            (4096, 4096),
            Image.Resampling.LANCZOS,
        )

    image.save(
        path,
        format="JPEG",
        quality=92,
        optimize=True,
        progressive=True,
    )

    return filename


"""

    new_text = (
        text[:start_match.start()]
        + new_function
        + text[end:]
    )

    UTILS.write_text(
        new_text,
        encoding="utf-8",
    )

    print(
        "app/utils.py: avatar ajustado para alta resolução/4K."
    )


def main():
    print("Atualizando suporte a avatar HD/4K...")
    backup(RUN)
    backup(UTILS)

    try:
        patch_run()
        patch_utils()
    except Exception as exc:
        print(f"ERRO: {exc}")
        sys.exit(1)

    print()
    print("Concluído.")
    print(
        "Substitua templates/profile.html "
        "pelo arquivo fornecido no pacote."
    )
    print("Depois execute: python run.py")


if __name__ == "__main__":
    main()
