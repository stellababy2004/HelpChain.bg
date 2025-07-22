# -*- coding: utf-8 -*-
from flask import Flask, render_template
from flask_mail import Mail, Message

app = Flask(__name__)

app.config['MAIL_SERVER'] = 'smtp.zoho.eu'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'contact@helpchain.live'
app.config['MAIL_PASSWORD'] = 'kaHa5fsY5Aph'
app.config['MAIL_DEFAULT_SENDER'] = 'contact@helpchain.live'

mail = Mail(app)

@app.route('/')
def index():
    try:
        subject = "Test email"
        body = "Hello, this is a test message in plain English. Regards, HelpChain Team"
        msg = Message(
            subject=subject,
            recipients=["your_email@gmail.com"]
        )
        msg.body = body
        mail.send(msg)
        return "Email sent successfully!"
    except Exception as e:
        return f"Error: {e}"

def send_registration_email(user_email, user_name):
    msg = Message(
        subject="Welcome to HelpChain!",
        recipients=[user_email],
        sender="contact@helpchain.live"
    )
    # Използвай HTML шаблон за имейла
    msg.html = render_template('email_templates/welcome_email.html', name=user_name)
    mail.send(msg)

@app.route('/send_registration')
def send_reg():
    try:
        send_registration_email("your_email@gmail.com", "TestUser")
        return "Registration email sent!"
    except Exception as e:
        return f"Error: {e}"

if __name__ == '__main__':
    app.run(debug=True)