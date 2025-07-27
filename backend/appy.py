import os

# Създай папката instance ако не съществува
os.makedirs(os.path.join(os.path.dirname(__file__), "instance"), exist_ok=True)

from flask import Flask, render_template, request, redirect, url_for, flash, session, make_response, jsonify, Response
import csv
from io import StringIO
from flask_babel import Babel, _
from backend.models import db, Volunteer
from flask_mail import Mail, Message
from flask_migrate import Migrate

app = Flask(__name__)

# Абсолютен път до базата за по-голяма сигурност
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(basedir, 'instance', 'volunteers.db')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
migrate = Migrate(app, db)

# Езици
app.config['BABEL_DEFAULT_LOCALE'] = 'bg'
app.config['BABEL_SUPPORTED_LOCALES'] = ['bg', 'en']
babel = Babel(app)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024
app.secret_key = 'supersecretkey'

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

mail = Mail(app)

@app.route('/')
def index():
    volunteers_count = Volunteer.query.count()
    signals_count = 0  # Ако имаш HelpRequest, може да го смениш
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
    volunteers = Volunteer.query.all()
    return render_template('admin_volunteers.html', volunteers=volunteers)

@app.route('/admin_volunteers/add', methods=['GET', 'POST'])
def add_volunteer():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        phone = request.form['phone']
        location = request.form['location']  # <-- нов ред
        volunteer = Volunteer(name=name, email=email, phone=phone, location=location)
        db.session.add(volunteer)
        db.session.commit()
        flash('Доброволецът е добавен успешно!', 'success')
        return redirect(url_for('admin_volunteers'))
    return render_template('add_volunteer.html')

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

        if captcha != '7G5K':
            flash('Грешен код за защита!')
            return redirect(url_for('submit_request'))

        filename = None
        if file and file.filename:
            if not allowed_file(file.filename):
                flash('Позволени са само изображения и PDF!')
                return redirect(url_for('submit_request'))
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], file.filename))

        # Тук можеш да обработиш/запишеш данните (примерно в база)
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
        volunteer = Volunteer(name=name, email=email, phone=phone, location=location)
        db.session.add(volunteer)
        db.session.commit()
        flash('Успешна регистрация! Ще се свържем с вас при нужда.')
        return redirect(url_for('volunteer_register'))
    return render_template('volunteer_register.html')

@app.route('/delete_volunteer/<int:id>', methods=['POST'])
def delete_volunteer(id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    volunteer = Volunteer.query.get_or_404(id)
    db.session.delete(volunteer)
    db.session.commit()
    flash('Доброволецът е изтрит успешно!', 'success')
    return redirect(url_for('admin_volunteers'))

@app.route('/admin_volunteers/edit/<int:id>', methods=['GET', 'POST'])
def edit_volunteer(id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    volunteer = Volunteer.query.get_or_404(id)
    if request.method == 'POST':
        volunteer.name = request.form['name']
        volunteer.email = request.form['email']
        volunteer.phone = request.form['phone']
        volunteer.location = request.form['location']  # Добави и локацията тук
        db.session.commit()
        flash('Промените са запазени!', 'success')
        return redirect(url_for('admin_volunteers'))
    return render_template('edit_volunteer.html', volunteer=volunteer)

@app.route('/export_volunteers')
def export_volunteers():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    volunteers = Volunteer.query.all()
    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['Име', 'Имейл', 'Телефон', 'Град/регион'])
    for v in volunteers:
        cw.writerow([v.name, v.email, v.phone, v.location])
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

@app.route('/update_status/<int:req_id>', methods=['POST'])
def update_status(req_id):
    return jsonify({"success": True})

@app.route('/admin_volunteers/<int:id>')
def volunteer_detail(id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    volunteer = Volunteer.query.get_or_404(id)
    return render_template('volunteer_detail.html', volunteer=volunteer)

@app.route('/admin_volunteers/search')
def search_volunteers():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    q = request.args.get('q', '')
    volunteers = Volunteer.query.filter(
        (Volunteer.name.ilike(f'%{q}%')) | (Volunteer.email.ilike(f'%{q}%'))
    ).all()
    return render_template('admin_volunteers.html', volunteers=volunteers, q=q)

@app.context_processor
def inject_gettext():
    return dict(_=_)

@app.context_processor
def inject_get_locale():
    def get_locale():
        return request.cookies.get('language') or request.accept_languages.best_match(['bg', 'en'])
    return dict(get_locale=get_locale)

# Debug принтове за mail настройките
print("MAIL_SERVER:", os.getenv('MAIL_SERVER'))
print("MAIL_PORT:", os.getenv('MAIL_PORT'))
print("MAIL_USE_SSL:", os.getenv('MAIL_USE_SSL'))
print("MAIL_USE_TLS:", os.getenv('MAIL_USE_TLS'))
print("MAIL_USERNAME:", os.getenv('MAIL_USERNAME'))
print("MAIL_PASSWORD:", os.getenv('MAIL_PASSWORD'))

if os.getenv('MAIL_PORT'):
    app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER')
    app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT'))
    app.config['MAIL_USE_SSL'] = os.getenv('MAIL_USE_SSL') == 'True'
    app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS') == 'True'
    app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
    app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
else:
    print("Warning: MAIL_PORT environment variable is not set! Имейл функционалността няма да работи.")

if __name__ == "__main__":
    app.run(debug=True)