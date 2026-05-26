def test_simulation_operationnelle_page_renders(client):
    response = client.get("/simulation-operationnelle")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Une situation ne dispara\u00eet plus dans le silence op\u00e9rationnel" in html
    assert "Vue op\u00e9rationnelle concr\u00e8te" in html
    assert "Situation \u00e0 relancer" in html
    assert "Les \u00e9quipes retrouvent une lecture commune des suivis \u00e0 reprendre." in html
    assert 'data-hc-event="simulation_pilot_cta_clicked"' in html
    assert 'data-hc-event="simulation_deployment_cta_clicked"' in html
    assert 'data-hc-event="simulation_timeline_engaged"' in html
    assert 'data-hc-event="simulation_operational_interest"' in html
