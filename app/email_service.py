import html
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from flask import current_app


def _required_config(name):
    value = (
        current_app.config.get(
            name,
            "",
        )
        or ""
    )

    if isinstance(value, str):
        value = value.strip()

    if not value:
        raise RuntimeError(
            f"Configuração de e-mail ausente: {name}"
        )

    return value


def send_verification_email(
    *,
    to_email,
    customer_name,
    code,
):
    """
    Envia o código de confirmação usando SMTP.

    Funciona com Gmail e também com outros provedores SMTP,
    desde que as variáveis MAIL_* estejam configuradas.
    """
    server = _required_config(
        "MAIL_SERVER"
    )

    port = int(
        current_app.config.get(
            "MAIL_PORT",
            587,
        )
    )

    username = _required_config(
        "MAIL_USERNAME"
    )

    password = _required_config(
        "MAIL_PASSWORD"
    )

    from_email = (
        current_app.config.get(
            "MAIL_FROM",
            "",
        )
        or username
    ).strip()

    from_name = (
        current_app.config.get(
            "MAIL_FROM_NAME",
            "",
        )
        or current_app.config.get(
            "APP_NAME",
            "Gringo du Corte",
        )
    ).strip()

    use_tls = bool(
        current_app.config.get(
            "MAIL_USE_TLS",
            True,
        )
    )

    use_ssl = bool(
        current_app.config.get(
            "MAIL_USE_SSL",
            False,
        )
    )

    app_name = (
        current_app.config.get(
            "APP_NAME",
            "Gringo du Corte",
        )
        or "Gringo du Corte"
    )

    expires_minutes = int(
        current_app.config.get(
            "MAIL_CODE_EXPIRES_MINUTES",
            10,
        )
    )

    safe_name = html.escape(
        customer_name or "cliente"
    )

    safe_code = html.escape(
        str(code)
    )

    subject = (
        f"{app_name} — código de verificação"
    )

    text_body = f"""Olá, {customer_name}!

Seu código de verificação é:

{code}

Ele expira em {expires_minutes} minutos.

Se você não iniciou este cadastro, ignore este e-mail.

{app_name}
"""

    html_body = f"""<!doctype html>
<html lang="pt-BR">
<head>
    <meta charset="utf-8">
</head>
<body style="margin:0;background:#09090b;font-family:Arial,sans-serif;color:#f4f4f5;">
    <div style="max-width:560px;margin:0 auto;padding:32px 18px;">
        <div style="background:#18181b;border:1px solid #27272a;border-radius:24px;padding:32px;">
            <div style="font-size:30px;margin-bottom:18px;">✂</div>

            <div style="font-size:12px;font-weight:700;letter-spacing:.14em;color:#fbbf24;">
                VERIFICAÇÃO DE E-MAIL
            </div>

            <h1 style="font-size:26px;line-height:1.2;margin:12px 0 8px;">
                Confirme seu cadastro
            </h1>

            <p style="color:#a1a1aa;line-height:1.65;">
                Olá, {safe_name}. Use o código abaixo para confirmar seu e-mail no {html.escape(app_name)}.
            </p>

            <div style="margin:26px 0;background:#09090b;border:1px solid #3f3f46;border-radius:18px;padding:22px;text-align:center;">
                <div style="font-size:36px;font-weight:900;letter-spacing:10px;color:#fbbf24;">
                    {safe_code}
                </div>
            </div>

            <p style="color:#a1a1aa;line-height:1.65;">
                Este código expira em <strong style="color:#fff;">{expires_minutes} minutos</strong>.
            </p>

            <p style="font-size:13px;color:#71717a;line-height:1.6;margin-top:28px;">
                Se você não iniciou este cadastro, pode ignorar esta mensagem.
            </p>
        </div>
    </div>
</body>
</html>
"""

    message = EmailMessage()

    message["Subject"] = subject

    message["From"] = formataddr(
        (
            from_name,
            from_email,
        )
    )

    message["To"] = to_email

    message.set_content(
        text_body
    )

    message.add_alternative(
        html_body,
        subtype="html",
    )

    timeout = int(
        current_app.config.get(
            "MAIL_TIMEOUT_SECONDS",
            20,
        )
    )

    if use_ssl:
        context = ssl.create_default_context()

        with smtplib.SMTP_SSL(
            server,
            port,
            timeout=timeout,
            context=context,
        ) as smtp:
            smtp.login(
                username,
                password,
            )

            smtp.send_message(
                message
            )

        return

    with smtplib.SMTP(
        server,
        port,
        timeout=timeout,
    ) as smtp:
        smtp.ehlo()

        if use_tls:
            context = (
                ssl.create_default_context()
            )

            smtp.starttls(
                context=context
            )

            smtp.ehlo()

        smtp.login(
            username,
            password,
        )

        smtp.send_message(
            message
        )


def send_password_reset_email(
    *,
    to_email,
    customer_name,
    reset_url,
    expires_minutes=30,
):
    """
    Envia um link assinado para redefinição de senha.

    O link é validado pelo Flask e expira automaticamente.
    """
    server = _required_config(
        "MAIL_SERVER"
    )

    port = int(
        current_app.config.get(
            "MAIL_PORT",
            587,
        )
    )

    username = _required_config(
        "MAIL_USERNAME"
    )

    password = _required_config(
        "MAIL_PASSWORD"
    )

    from_email = (
        current_app.config.get(
            "MAIL_FROM",
            "",
        )
        or username
    ).strip()

    from_name = (
        current_app.config.get(
            "MAIL_FROM_NAME",
            "",
        )
        or current_app.config.get(
            "APP_NAME",
            "Gringo Barber",
        )
    ).strip()

    use_tls = bool(
        current_app.config.get(
            "MAIL_USE_TLS",
            True,
        )
    )

    use_ssl = bool(
        current_app.config.get(
            "MAIL_USE_SSL",
            False,
        )
    )

    app_name = (
        current_app.config.get(
            "APP_NAME",
            "Gringo Barber",
        )
        or "Gringo Barber"
    )

    safe_name = html.escape(
        customer_name
        or "cliente"
    )

    safe_app_name = html.escape(
        app_name
    )

    safe_url = html.escape(
        reset_url,
        quote=True,
    )

    subject = (
        f"{app_name} — redefinição de senha"
    )

    text_body = f"""Olá, {customer_name or 'cliente'}!

Recebemos uma solicitação para redefinir a senha da sua conta no {app_name}.

Abra o link abaixo para criar uma nova senha:

{reset_url}

Este link expira em {expires_minutes} minutos e deixa de funcionar depois que a senha é alterada.

Se você não solicitou a redefinição, ignore este e-mail.

{app_name}
"""

    html_body = f"""<!doctype html>
<html lang="pt-BR">
<head>
    <meta charset="utf-8">
</head>

<body style="margin:0;background:#09090a;font-family:Arial,sans-serif;color:#f4f4f5;">

    <div style="max-width:600px;margin:0 auto;padding:34px 18px;">

        <div style="background:#18181b;border:1px solid #27272a;border-radius:26px;padding:32px;">

            <div style="height:5px;border-radius:999px;background:linear-gradient(90deg,#df2610,#f5f5f4,#3f6d87,#f4600d);margin-bottom:26px;"></div>

            <div style="font-size:12px;font-weight:900;letter-spacing:.16em;color:#f58b46;">
                SEGURANÇA DA CONTA
            </div>

            <h1 style="font-size:28px;line-height:1.2;margin:12px 0 8px;color:#ffffff;">
                Redefina sua senha
            </h1>

            <p style="color:#a1a1aa;line-height:1.7;">
                Olá, {safe_name}. Recebemos uma solicitação para criar uma nova senha para sua conta no {safe_app_name}.
            </p>

            <div style="margin:28px 0;text-align:center;">
                <a
                    href="{safe_url}"
                    style="display:inline-block;background:linear-gradient(90deg,#f4600d,#ef9e34);color:#111111;text-decoration:none;font-weight:900;padding:15px 24px;border-radius:16px;"
                >
                    Redefinir minha senha
                </a>
            </div>

            <p style="color:#a1a1aa;line-height:1.7;">
                O link expira em <strong style="color:#ffffff;">{int(expires_minutes)} minutos</strong> e deixa de funcionar após a alteração da senha.
            </p>

            <p style="font-size:13px;color:#71717a;line-height:1.65;margin-top:28px;">
                Se você não solicitou a redefinição, ignore esta mensagem. Sua senha atual continuará funcionando.
            </p>

        </div>

    </div>

</body>
</html>
"""

    message = EmailMessage()

    message["Subject"] = subject

    message["From"] = formataddr(
        (
            from_name,
            from_email,
        )
    )

    message["To"] = to_email

    message.set_content(
        text_body
    )

    message.add_alternative(
        html_body,
        subtype="html",
    )

    timeout = int(
        current_app.config.get(
            "MAIL_TIMEOUT_SECONDS",
            20,
        )
    )

    if use_ssl:
        context = ssl.create_default_context()

        with smtplib.SMTP_SSL(
            server,
            port,
            timeout=timeout,
            context=context,
        ) as smtp:
            smtp.login(
                username,
                password,
            )

            smtp.send_message(
                message
            )

        return

    with smtplib.SMTP(
        server,
        port,
        timeout=timeout,
    ) as smtp:
        smtp.ehlo()

        if use_tls:
            context = (
                ssl.create_default_context()
            )

            smtp.starttls(
                context=context
            )

            smtp.ehlo()

        smtp.login(
            username,
            password,
        )

        smtp.send_message(
            message
        )

