import html
import json
import os
import smtplib
import ssl
import urllib.error
import urllib.request
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




def _send_via_brevo(
    *,
    to_email,
    subject,
    text_body,
    html_body,
    from_name,
    from_email,
):
    """
    Envia e-mail pela API HTTPS da Brevo.

    Não usa SMTP e funciona em hospedagens que bloqueiam
    as portas 25, 465 e 587.
    """
    api_key = (
        os.getenv(
            "BREVO_API_KEY",
            "",
        )
        or current_app.config.get(
            "BREVO_API_KEY",
            "",
        )
        or ""
    ).strip()

    if not api_key:
        raise RuntimeError(
            "BREVO_API_KEY não configurada."
        )

    brevo_from = (
        os.getenv(
            "BREVO_FROM",
            "",
        )
        or current_app.config.get(
            "BREVO_FROM",
            "",
        )
        or from_email
        or ""
    ).strip()

    if (
        not brevo_from
        or "@" not in brevo_from
        or brevo_from.startswith("@")
        or brevo_from.endswith("@")
    ):
        raise RuntimeError(
            (
                "BREVO_FROM inválido. "
                "Use um endereço de e-mail completo "
                "e previamente verificado na Brevo."
            )
        )

    brevo_from_name = (
        os.getenv(
            "BREVO_FROM_NAME",
            "",
        )
        or current_app.config.get(
            "BREVO_FROM_NAME",
            "",
        )
        or from_name
        or "Gringo Barber"
    ).strip()

    payload = {
        "sender": {
            "name": brevo_from_name,
            "email": brevo_from,
        },
        "to": [
            {
                "email": to_email,
            }
        ],
        "subject": subject,
        "htmlContent": html_body,
        "textContent": text_body,
    }

    current_app.logger.info(
        "Brevo: remetente=%s destinatário=%s",
        brevo_from,
        to_email,
    )

    request = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps(
            payload
        ).encode("utf-8"),
        headers={
            "accept": "application/json",
            "api-key": api_key,
            "content-type": "application/json",
            "user-agent": "Gringo-Barber/1.0",
        },
        method="POST",
    )

    timeout = int(
        current_app.config.get(
            "MAIL_TIMEOUT_SECONDS",
            20,
        )
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:
            body = (
                response.read()
                .decode(
                    "utf-8",
                    errors="replace",
                )
            )

            if not (
                200
                <= response.status
                < 300
            ):
                raise RuntimeError(
                    (
                        "Brevo retornou "
                        f"HTTP {response.status}: "
                        f"{body}"
                    )
                )

            result = (
                json.loads(body)
                if body
                else {}
            )

            current_app.logger.info(
                "Brevo aceitou o e-mail. messageId=%s",
                result.get("messageId")
                if isinstance(result, dict)
                else None,
            )

            return result

    except urllib.error.HTTPError as exc:
        body = (
            exc.read()
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        current_app.logger.error(
            "Brevo recusou o envio: HTTP %s - %s",
            exc.code,
            body,
        )

        raise RuntimeError(
            (
                "Falha na API Brevo "
                f"(HTTP {exc.code}): "
                f"{body}"
            )
        ) from exc

    except urllib.error.URLError as exc:
        current_app.logger.error(
            "Falha de conexão com a Brevo: %s",
            exc.reason,
        )

        raise RuntimeError(
            (
                "Não foi possível conectar "
                "à API da Brevo: "
                f"{exc.reason}"
            )
        ) from exc


def _send_via_smtp(
    *,
    message,
):
    """
    SMTP para desenvolvimento local ou Railway Pro+.
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

    timeout = int(
        current_app.config.get(
            "MAIL_TIMEOUT_SECONDS",
            20,
        )
    )

    if use_ssl:
        context = (
            ssl.create_default_context()
        )

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


def _selected_email_provider():
    """
    Define o provedor de e-mail.

    EMAIL_PROVIDER=smtp  -> Gmail/SMTP
    EMAIL_PROVIDER=brevo -> Brevo via HTTPS

    Se EMAIL_PROVIDER não estiver definido:
    - Railway + BREVO_API_KEY -> Brevo
    - ambiente local -> SMTP
    """
    explicit = (
        os.getenv(
            "EMAIL_PROVIDER",
            "",
        )
        or current_app.config.get(
            "EMAIL_PROVIDER",
            "",
        )
        or ""
    ).strip().lower()

    if explicit:
        if explicit not in {
            "smtp",
            "brevo",
        }:
            raise RuntimeError(
                (
                    "EMAIL_PROVIDER inválido: "
                    f"{explicit!r}. "
                    "Use 'smtp' ou 'brevo'."
                )
            )

        return explicit

    is_railway = any(
        os.getenv(name)
        for name in (
            "RAILWAY_PROJECT_ID",
            "RAILWAY_SERVICE_ID",
            "RAILWAY_ENVIRONMENT",
            "RAILWAY_ENVIRONMENT_ID",
            "RAILWAY_PUBLIC_DOMAIN",
            "RAILWAY_PRIVATE_DOMAIN",
        )
    )

    brevo_api_key = (
        os.getenv(
            "BREVO_API_KEY",
            "",
        )
        or current_app.config.get(
            "BREVO_API_KEY",
            "",
        )
        or ""
    ).strip()

    if (
        is_railway
        and brevo_api_key
    ):
        return "brevo"

    return "smtp"


def _deliver_email(
    *,
    to_email,
    subject,
    text_body,
    html_body,
    from_name,
    from_email,
):
    """
    Envia por SMTP ou Brevo conforme EMAIL_PROVIDER.

    Localmente o padrão é SMTP.
    No Railway, se não houver EMAIL_PROVIDER explícito e houver
    BREVO_API_KEY, o padrão passa a ser Brevo.
    """
    provider = (
        _selected_email_provider()
    )

    current_app.logger.info(
        "Enviando e-mail via %s para %s.",
        provider,
        to_email,
    )

    if provider == "brevo":
        return _send_via_brevo(
            to_email=to_email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            from_name=from_name,
            from_email=from_email,
        )

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

    _send_via_smtp(
        message=message
    )

    return {}


def send_verification_email(
    *,
    to_email,
    customer_name,
    code,
):
    """
    Envia o código de confirmação pelo provedor configurado.

    Localmente pode usar Gmail SMTP.
    Em produção/Railway pode usar Brevo via HTTPS.
    """
    username = (
        current_app.config.get(
            "MAIL_USERNAME",
            "",
        )
        or ""
    ).strip()

    from_email = (
        current_app.config.get(
            "MAIL_FROM",
            "",
        )
        or os.getenv(
            "BREVO_FROM",
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

    return _deliver_email(
        to_email=to_email,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        from_name=from_name,
        from_email=from_email,
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
    username = (
        current_app.config.get(
            "MAIL_USERNAME",
            "",
        )
        or ""
    ).strip()

    from_email = (
        current_app.config.get(
            "MAIL_FROM",
            "",
        )
        or os.getenv(
            "BREVO_FROM",
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

    return _deliver_email(
        to_email=to_email,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        from_name=from_name,
        from_email=from_email,
    )

