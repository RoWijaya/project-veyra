import requests
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend


class PermanentEmailError(OSError):
    pass


class ResendEmailBackend(BaseEmailBackend):
    """Django backend for registration, password reset, and invitations over HTTPS."""

    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages or []:
            if not message.recipients():
                continue
            data = {
                "from": message.from_email,
                "to": list(message.to),
                "text": message.body,
            }
            if message.cc:
                data["cc"] = list(message.cc)
            if message.bcc:
                data["bcc"] = list(message.bcc)
            if message.reply_to:
                data["reply_to"] = list(message.reply_to)
            for alternative in getattr(message, "alternatives", []):
                if alternative.mimetype == "text/html":
                    data["html"] = alternative.content
            headers = {"Authorization": f"Bearer {settings.RESEND_API_KEY}"}
            delivery_id = message.extra_headers.get("X-Veyra-Delivery-ID")
            if delivery_id:
                headers["Idempotency-Key"] = f"verification/{delivery_id}"
            try:
                response = requests.post(
                    "https://api.resend.com/emails",
                    headers=headers,
                    json={**data, "subject": message.subject},
                    timeout=(3.05, 10),
                    allow_redirects=False,
                )
                if response.status_code in {400, 401, 403, 422}:
                    if not self.fail_silently:
                        raise PermanentEmailError("The email provider rejected the message.")
                    continue
                if response.status_code != 200 or not response.json().get("id"):
                    raise ValueError("unexpected email provider response")
            except (requests.RequestException, ValueError, AttributeError) as error:
                if not self.fail_silently:
                    raise OSError("The email provider did not accept the message.") from error
            else:
                sent += 1
        return sent
