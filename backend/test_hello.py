import pytest
from appy import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_homepage(client):
    response = client.get('/')
    assert response.status_code == 200

def test_hello():
    assert True

def test_submit_request(client):
    response = client.post('/submit_request', data={
        'full_name': 'Test User',
        'email': 'test@example.com',
        'phone': '1234567890',
        'help_type': 'Храна',
        'description': 'Нуждая се от помощ',
        'location': 'Пловдив',
        'captcha': '7G5K'  # ако има captcha
    })
    assert response.status_code in [200, 302]  # 302 ако има redirect

def test_admin_panel_access(client):
    response = client.get('/admin')
    assert response.status_code in [200, 302, 401, 403]