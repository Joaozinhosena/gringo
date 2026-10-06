import json

from flask import current_app
from pywebpush import WebPushException, webpush

from .extensions import db
from .models import PushSubscription


def send_push_to_user(user_id, title, body, url="/dashboard"):
    public_key = current_app.config.get("VAPID_PUBLIC_KEY")
    private_key = current_app.config.get("VAPID_PRIVATE_KEY")
    claims_email = current_app.config.get("VAPID_CLAIMS_EMAIL")

    if not public_key or not private_key or not claims_email:
        return

    payload = json.dumps(
        {"title": title, "body": body, "url": url},
        ensure_ascii=False,
    )

    subscriptions = PushSubscription.query.filter_by(user_id=user_id).all()

    for subscription in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {
                        "p256dh": subscription.p256dh,
                        "auth": subscription.auth,
                    },
                },
                data=payload,
                vapid_private_key=private_key,
                vapid_claims={"sub": claims_email},
            )
        except WebPushException as exc:
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)

            if status_code in (404, 410):
                db.session.delete(subscription)
                db.session.commit()
