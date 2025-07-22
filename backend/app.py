from flask import Flask, render_template, request
from backend.extensions import db, mail, babel
from backend.models import HelpRequest, StatusLog
from flask_babel import gettext as _, ngettext
from flask_mail import Message
from flask_admin import Admin
from datetime import datetime

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///backend/helpchain.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Email конфигурация (Zoho)
app.config['MAIL_SERVER'] = 'smtp.zoho.eu'
app.config['MAIL_PORT'] = 465
app.config['MAIL_USE_SSL'] = True
app.config['MAIL_USERNAME'] = 'contact@helpchain.live'  
app.config['MAIL_PASSWORD'] = 'wWAfCk9A1gZU'
app.config['MAIL_DEFAULT_SENDER'] = 'contact@helpchain.live'

db.init_app(app)
babel.init_app(app)
mail.init_app(app)

# Добави i18n разширението за Jinja2 и глобалните функции за превод
app.jinja_env.add_extension('jinja2.ext.i18n')
app.jinja_env.globals.update(_=_, ngettext=ngettext)

admin = Admin(app, name="Admin", template_mode="bootstrap4")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/send_registration', methods=['POST'])
def send_registration():
    try:
        name = request.form['name']
        email = request.form['email']

        msg = Message(
            subject="Welcome to HelpChain!",
            sender="contact@helpchain.live",
            recipients=[email]
        )
        msg.html = render_template("email_templates/welcome_email.html", name=name)
        mail.send(msg)

        # Изпрати админ известие
        send_admin_notification(email, name)

        return "Email sent successfully!"
    except Exception as e:
        return f"Error: {e}"

def send_admin_notification(user_email, user_name):
    subject = "🔔 Нова регистрация в HelpChain"
    recipients = ["contact@helpchain.live"]  # Имейлът на администратора
    html_body = render_template("email_templates/admin_notification.html", user_email=user_email, user_name=user_name)
    
    msg = Message(subject=subject,
                  recipients=recipients,
                  html=html_body)
    mail.send(msg)

@app.route('/register')
def register():
    return render_template('register.html')

@app.route('/preview_email')
def preview_email():
    return render_template('email_templates/welcome_email.html', name="ТестПотребител")

@app.route('/send_admin')
def send_admin_test():
    try:
        send_admin_notification(user_email="test@example.com", user_name="ТестПотребител")
        return "Admin email sent successfully!"
    except Exception as e:
        return f"Error: {e}"