from flask import Flask, session, request
from flask_babel import Babel

app = Flask(__name__)
app.config['SECRET_KEY'] = 'test'
app.config['LANGUAGES'] = ['bg', 'en', 'fr']

babel = Babel(app)

@babel.locale_selector
def get_locale():
    return session.get('lang', request.accept_languages.best_match(app.config['LANGUAGES']))

@app.route('/')
def index():
    return "OK"

if __name__ == '__main__':
    app.run(debug=True)