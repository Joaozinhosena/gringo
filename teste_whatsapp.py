import os
import requests

from dotenv import load_dotenv


load_dotenv()


token = os.getenv("WHATSAPP_ACCESS_TOKEN")
phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
version = os.getenv("WHATSAPP_API_VERSION")


# COLOQUE AQUI O SEU WHATSAPP
# Exemplo: 5581999999999
destinatario = "5581986948982"


url = (
    f"https://graph.facebook.com/"
    f"{version}/"
    f"{phone_id}/messages"
)


payload = {

    "messaging_product": "whatsapp",

    "to": destinatario,

    "type": "template",

    "template": {

        "name": "hello_world",

        "language": {
            "code": "en_US"
        }

    }

}


response = requests.post(

    url,

    headers={
        "Authorization":
            f"Bearer {token}",

        "Content-Type":
            "application/json"
    },

    json=payload,

    timeout=20,
)


print(
    "STATUS:",
    response.status_code
)

print(
    response.text
)