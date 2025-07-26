import os

# Създай папката instance ако не съществува
os.makedirs(os.path.join(os.path.dirname(__file__), "instance"), exist_ok=True)

from flask import Flask, render_template, request, redirect, url_for, flash, session, make_response
# from flask_sqlalchemy import SQLAlchemy  # НЕ ти трябва, ако не ползваш база
import csv
from io import StringIO
from flask import Response
from flask_babel import Babel, _

app = Flask(__name__)

# Езици
app.config['BABEL_DEFAULT_LOCALE'] = 'bg'
app.config['BABEL_SUPPORTED_LOCALES'] = ['bg', 'en']
babel = Babel(app)

# @babel.locale_selector
# def get_locale():
#     return request.cookies.get('language') or request.accept_languages.best_match(['bg', 'en'])

# app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///instance/volunteers.db'
# app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# db = SQLAlchemy(app)

# НЕ импортвай моделите!
# from models import Volunteer, HelpRequest, StatusLog

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

app.config['UPLOAD_FOLDER'] = 'uploads'  # папка за файловете
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # макс. размер 5MB
app.secret_key = 'supersecretkey'  # добави това най-горе, ако го нямаш

# Създай папката uploads ако не съществува
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

@app.route('/')
def index():
    # volunteers_count = Volunteer.query.count()
    volunteers_count = 0
    signals_count = 0
    return render_template('index.html', volunteers_count=volunteers_count, signals_count=signals_count)

@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == 'admin' and password == 'help2025!':
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            error = 'Грешно потребителско име или парола!'
    return render_template('admin_login.html', error=error)

@app.route('/admin_dashboard')
def admin_dashboard():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    # Примерни данни за таблицата
    requests = {
        'items': [
            {'id': 1, 'name': 'Мария', 'status': 'Активен'},
            {'id': 2, 'name': 'Георги', 'status': 'Завършен'},
        ]
    }
    logs_dict = {
        1: [{'status': 'Активен', 'changed_at': '2025-07-22'}],
        2: [{'status': 'Завършен', 'changed_at': '2025-07-21'}],
    }
    return render_template('admin_dashboard.html', requests=requests, logs_dict=logs_dict)

@app.route('/admin_volunteers', methods=['GET', 'POST'])
def admin_volunteers():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    search = request.form.get('search', '')
    # volunteers = Volunteer.query.all()
    volunteers = []  # Фиктивен празен списък
    return render_template('admin_volunteers.html', volunteers=volunteers, search=search)

@app.route('/submit_request', methods=['GET', 'POST'])
def submit_request():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        category = request.form.get('category')
        location = request.form.get('location')
        problem = request.form.get('problem')
        terms = request.form.get('terms')
        captcha = request.form.get('captcha')
        file = request.files.get('file')

        # Проверка за Captcha (примерен код)
        if captcha != '7G5K':
            flash('Грешен код за защита!')
            return redirect(url_for('submit_request'))

        # Запис на файла, ако има
        filename = None
        if file and file.filename:
            if not allowed_file(file.filename):
                flash('Позволени са само изображения и PDF!')
                return redirect(url_for('submit_request'))
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], file.filename))

        # Тук можеш да обработиш/запишеш данните (примерно в база)
        # ...

        return render_template('submit_success.html')
    return render_template('submit_request.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/volunteer_register', methods=['GET', 'POST'])
def volunteer_register():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        location = request.form.get('location')
        # volunteer = Volunteer(name=name, email=email, phone=phone, location=location)
        # db.session.add(volunteer)
        # db.session.commit()
        flash('Успешна регистрация! Ще се свържем с вас при нужда.')
        return redirect(url_for('volunteer_register'))
    return render_template('volunteer_register.html')

@app.route('/delete_volunteer/<int:id>', methods=['POST'])
def delete_volunteer(id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    # volunteer = Volunteer.query.get_or_404(id)
    # db.session.delete(volunteer)
    # db.session.commit()
    flash('Доброволецът е изтрит успешно. (фиктивно)')
    return redirect(url_for('admin_volunteers'))

@app.route('/edit_volunteer/<int:id>', methods=['GET', 'POST'])
def edit_volunteer(id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    # volunteer = Volunteer.query.get_or_404(id)
    volunteer = None  # Фиктивно
    if request.method == 'POST':
        # volunteer.name = request.form.get('name')
        # volunteer.email = request.form.get('email')
        # volunteer.phone = request.form.get('phone')
        # volunteer.location = request.form.get('location')
        # db.session.commit()
        flash('Данните са обновени успешно. (фиктивно)')
        return redirect(url_for('admin_volunteers'))
    return render_template('edit_volunteer.html', volunteer=volunteer)

@app.route('/export_volunteers')
def export_volunteers():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    # volunteers = Volunteer.query.all()
    volunteers = []  # Фиктивен празен списък
    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['Име', 'Имейл', 'Телефон', 'Град/регион'])
    # for v in volunteers:
    #     cw.writerow([v.name, v.email, v.phone, v.location])
    output = si.getvalue()
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=volunteers.csv"}
    )

@app.route('/faq')
def faq():
    return render_template('faq.html')

@app.route('/success_stories')
def success_stories():
    return render_template('success_stories.html')

@app.route('/feedback', methods=['GET', 'POST'])
def feedback():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        message = request.form.get('message')
        # Тук можеш да запишеш във файл, база или да изпратиш имейл
        flash('Благодарим за обратната връзка!')
        return redirect(url_for('feedback'))
    return render_template('feedback.html')

@app.route('/set_language', methods=['POST'])
def set_language():
    lang = request.form['language']
    resp = make_response(redirect(request.referrer or url_for('index')))
    resp.set_cookie('language', lang)
    return resp

@app.route('/admin')
def admin():
    return redirect(url_for('admin_login'))

# Подаване на _ към всички шаблони
@app.context_processor
def inject_gettext():
    return dict(_=_)

@app.context_processor
def inject_get_locale():
    def get_locale():
        return request.cookies.get('language') or request.accept_languages.best_match(['bg', 'en'])
    return dict(get_locale=get_locale)

with app.app_context():
    html_body = render_template('email_templates/admin_notification.html',
                               username='Иван',
                               email='ivan@example.com',
                               registration_date='25.07.2025')

if __name__ == '__main__':
    app.run(debug=True)