from flask import Flask, render_template, request

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/robots.txt')
def robots():
    return app.send_static_file('robots.txt')

@app.route('/submit-request', methods=['GET', 'POST'])
def submit_request():
    if request.method == 'POST':
        # обработка на данните от формата
        return render_template('submit_success.html')
    return render_template('submit_request.html')

@app.route('/admin')
def admin_dashboard():
    return render_template('admin_dashboard.html')

if __name__ == "__main__":
    app.run(debug=True)