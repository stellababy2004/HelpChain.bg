from appy import app

def test_index_route():
    tester = app.test_client()
    response = tester.get('/')
    assert response.status_code == 200
    assert "Добре дошли" in response.data.decode("utf-8")