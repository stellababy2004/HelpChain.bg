from flask import Flask
from flask_mail import Mail, Message

app = Flask(__name__)

# Настройки за Zoho SMTP
app.config['MAIL_SERVER'] = 'smtp.zoho.eu'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'contact@helpchain.live'
app.config['MAIL_PASSWORD'] = 'ТУК_ВМЕСТИ app password-а'
app.config['MAIL_DEFAULT_SENDER'] = 'contact@helpchain.live'

mail = Mail(app)

@app.route('/')
def index():
    try:
        msg = Message("✅ Test Email from Flask", recipients=["ТВОЯ_ЛИЧЕН@имейл.com"])
        msg.body = "Здравей, това е тестово съобщение от Flask-Mail с Zoho."
        mail.send(msg)
        return "✅ Имейлът е изпратен успешно!"
    except Exception as e:
        return f"❌ Грешка: {e}"

if __name__ == '__main__':
    app.run(debug=True)