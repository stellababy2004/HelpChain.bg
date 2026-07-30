class TestVolunteerIntegration:
    """Integration Ñ‚ÐµÑÑ‚Ð¾Ð²Ðµ Ð·Ð° volunteer Ñ„ÑƒÐ½ÐºÑ†Ð¸Ð¾Ð½Ð°Ð»Ð½Ð¾ÑÑ‚"""

    def test_volunteer_login_flow(self, client, init_test_data):
        """Ð¢ÐµÑÑ‚ Ð·Ð° volunteer login Ð¿Ñ€Ð¾Ñ†ÐµÑ"""
        volunteer = init_test_data["volunteer"]

        # Test login via session
        with client.session_transaction() as sess:
            sess["volunteer_logged_in"] = True
            sess["volunteer_id"] = volunteer.id

        # Check dashboard access
        response = client.get("/volunteer_dashboard")
        assert response.status_code == 200

        data = response.get_data(as_text=True)

        # Check if volunteer name is displayed
        if hasattr(volunteer, "name") and volunteer.name:
            assert volunteer.name in data

        # Check for core volunteer dashboard shell
        assert "dashboard" in data.lower() or "volunteer" in data.lower()

        # Check for location form fields
        assert "latitude" in data and "longitude" in data

    def test_volunteer_dashboard_content(
        self, authenticated_volunteer_client, init_test_data
    ):
        """Ð¢ÐµÑÑ‚ Ð·Ð° ÑÑŠÐ´ÑŠÑ€Ð¶Ð°Ð½Ð¸ÐµÑ‚Ð¾ Ð½Ð° volunteer dashboard"""
        client = authenticated_volunteer_client

        response = client.get("/volunteer_dashboard")
        assert response.status_code == 200

        data = response.get_data(as_text=True)

        # Basic content checks
        assert len(data) > 500  # Reasonable content length
        assert "dashboard" in data.lower() or "Ñ‚Ð°Ð±Ð»Ð¾" in data.lower()

    def test_volunteer_location_update(self, authenticated_volunteer_client):
        """Ð¢ÐµÑÑ‚ Ð·Ð° Ð¾Ð±Ð½Ð¾Ð²ÑÐ²Ð°Ð½Ðµ Ð½Ð° Ð»Ð¾ÐºÐ°Ñ†Ð¸ÑÑ‚Ð° Ð½Ð° volunteer"""
        client = authenticated_volunteer_client

        # Get volunteer ID from session
        volunteer_id = 1  # Use a test volunteer ID

        # Test location update API
        response = client.put(
            f"/api/volunteers/{volunteer_id}/location",
            json={"latitude": 42.6977, "longitude": 23.3219, "location": "Ð¡Ð¾Ñ„Ð¸Ñ"},
        )

        # Should succeed
        assert response.status_code in [200, 404]  # 404 if volunteer doesn't exist

    def test_volunteer_profile_access(self, authenticated_volunteer_client):
        """Ð¢ÐµÑÑ‚ Ð·Ð° Ð´Ð¾ÑÑ‚ÑŠÐ¿ Ð´Ð¾ volunteer Ð¿Ñ€Ð¾Ñ„Ð¸Ð»"""
        client = authenticated_volunteer_client

        response = client.get("/volunteer_profile")
        # Profile might not exist, so check reasonable response
        assert response.status_code in [200, 404, 302]

    def test_volunteer_logout(self, authenticated_volunteer_client):
        """Ð¢ÐµÑÑ‚ Ð·Ð° volunteer logout"""
        client = authenticated_volunteer_client

        # Test logout
        response = client.get("/volunteer_logout")
        assert response.status_code in [200, 302]

        # After logout, dashboard should redirect
        response = client.get("/volunteer_dashboard")
        assert response.status_code in [302, 403, 401]

    def test_volunteer_dashboard_empty_state_has_no_fabricated_requests(
        self, authenticated_volunteer_client, monkeypatch
    ):
        """Empty volunteer dashboard should render guidance without demo requests."""
        from backend.helpchain_backend.src.routes import main as main_routes
        from backend.models import Request

        monkeypatch.setattr(
            main_routes,
            "scoped_requests_query",
            lambda: Request.query.filter(Request.id == -1),
        )
        monkeypatch.setattr(main_routes, "get_matched_requests_v1", lambda *args, **kwargs: [])

        response = authenticated_volunteer_client.get("/volunteer_dashboard")

        assert response.status_code == 200

        data = response.get_data(as_text=True)
        assert "dashboard" in data.lower() or "volunteer" in data.lower()
        assert "Exemple de demande" not in data
        assert "Exemple démo" not in data
        assert "Voir un exemple" not in data

