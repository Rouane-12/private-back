import os
import requests

BREVO_API_KEY = os.getenv("BREVO_API_KEY")
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_NAME = os.getenv("SENDER_NAME", "Maison d'Or")

ROLE_LABELS = {
    "homme": "Client",
    "femme": "Hôte",
    "professionnel": "Partenaire",
}

PURPOSE_SUBJECTS = {
    "register": "Vérifiez votre compte Maison d'Or",
    "reset": "Réinitialisation de votre mot de passe",
}


def send_otp_email(to_email: str, otp_code: str, purpose: str, role: str = "homme"):
    subject = PURPOSE_SUBJECTS.get(purpose, "Code de vérification")
    role_label = ROLE_LABELS.get(role, "Membre")
    title = "Vérifiez votre compte" if purpose == "register" else "Réinitialisation du mot de passe"

    body = f"""
    <html>
    <body style="font-family:'Segoe UI',sans-serif;background:#0a0a0a;color:#fff;padding:40px;margin:0;">
        <div style="max-width:480px;margin:0 auto;background:#111;border:1px solid #1e1e1e;border-radius:16px;padding:40px;">
            <div style="margin-bottom:24px;">
                <span style="font-size:13px;letter-spacing:2px;color:#a0845c;text-transform:uppercase;">{role_label}</span>
                <h1 style="color:#d4a96a;margin:8px 0 0;font-size:22px;font-weight:400;">{title}</h1>
            </div>
            <p style="color:#888;margin-bottom:32px;font-size:14px;line-height:1.6;">
                Votre code OTP est valable <strong style="color:#d4a96a;">10 minutes</strong>.
            </p>
            <div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;padding:28px;text-align:center;">
                <span style="font-size:42px;font-weight:700;letter-spacing:14px;color:#d4a96a;">{otp_code}</span>
            </div>
            <p style="color:#444;margin-top:32px;font-size:12px;">
                Si vous n'avez pas effectué cette demande, ignorez cet email.
            </p>
            <div style="margin-top:32px;padding-top:24px;border-top:1px solid #1e1e1e;">
                <span style="font-size:12px;color:#333;letter-spacing:1px;">MAISON D'OR · PLATEFORME PRIVÉE</span>
            </div>
        </div>
    </body>
    </html>
    """

    response = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
        json={
            "sender": {"name": SENDER_NAME, "email": SENDER_EMAIL},
            "to": [{"email": to_email}],
            "subject": subject,
            "htmlContent": body,
        },
    )

    if response.status_code not in [200, 201, 202]:
        raise Exception(f"Brevo error: {response.text}")