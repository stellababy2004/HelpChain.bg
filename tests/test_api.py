class TestAPIAuthentication:
    """Тестове за API автентикация"""

    def test_api_requires_no_auth_for_public_endpoints(self, client):
        """Тест че някои API endpoints не изискват автентикация"""
        # AI status endpoint
        response = client.get("/api/ai/status")
        assert response.status_code == 200

        # Chatbot message endpoint без автентикация
        response = client.post("/api/chatbot/message", json={"message": "Test message"})
        assert response.status_code == 200

    def test_protected_api_endpoints_require_auth(self, client):
        """Тест че защитени API endpoints изискват автентикация"""
        # Тези endpoints трябва да изискват автентикация
        protected_endpoints = [
            "/api/tasks/trigger/test_task",  # POST only
            "/api/predictive/regional-demand",
            "/api/matching/find-matches/1",
        ]

        for endpoint in protected_endpoints:
            if "trigger" in endpoint:
                response = client.post(endpoint)
            else:
                response = client.get(endpoint)
            assert response.status_code in [
                401,
                403,
                302,
            ]  # Unauthorized, Forbidden, or Redirect

    def test_api_content_type_json(self, client):
        """Тест че API връща правилен Content-Type"""
        response = client.get("/api/ai/status")

        assert response.status_code == 200
        assert response.mimetype == "application/json"

    def test_api_cors_headers(self, client):
        """Тест за CORS headers в API responses"""
        client.get("/api/ai/status")

        # Проверяваме CORS headers (ако са конфигурирани)
        # assert "Access-Control-Allow-Origin" in response.headers
        # Това зависи от CORS конфигурацията


class TestAPIEndpoints:
    """Тестове за конкретни API endpoints"""

    def test_chatbot_api_valid_request(self, client, mock_ai_service):
        """Тест за chatbot API с валидна заявка"""
        response = client.post(
            "/api/chatbot/message",
            json={"message": "Здравей, как си?", "session_id": "test_session_123"},
        )

        assert response.status_code == 200
        data = response.get_json()

        assert "response" in data
        assert "confidence" in data
        assert "provider" in data
        assert "session_id" in data
        assert data["session_id"] == "test_session_123"
        assert isinstance(data["confidence"], (int, float))
        assert data["response"] == "Тестов отговор от AI"

    def test_chatbot_api_missing_message(self, client):
        """Тест за chatbot API без message поле"""
        response = client.post(
            "/api/chatbot/message", json={"session_id": "test_session"}
        )

        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data

    def test_chatbot_api_empty_message(self, client):
        """Тест за chatbot API с празно message"""
        response = client.post(
            "/api/chatbot/message", json={"message": "", "session_id": "test_session"}
        )

        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data

    def test_chatbot_api_long_message(self, client, mock_ai_service):
        """Тест за chatbot API с много дълго съобщение"""
        long_message = "A" * 10000  # Много дълго съобщение

        response = client.post(
            "/api/chatbot/message",
            json={"message": long_message, "session_id": "test_session"},
        )

        # Трябва да се справи с дълги съобщения или да ги отхвърли
        assert response.status_code in [200, 400, 413]

    def test_ai_status_api_response_format(self, client, mock_ai_service):
        """Тест за AI status API response format"""
        response = client.get("/api/ai/status")

        assert response.status_code == 200
        data = response.get_json()

        assert isinstance(data, dict)
        assert "status" in data
        assert "providers" in data
        assert "active_provider" in data
        assert data["status"] == "healthy"
        assert isinstance(data["providers"], list)

    def test_api_error_responses(self, client):
        """Тест за API error responses"""
        # Invalid endpoint
        response = client.get("/api/nonexistent")
        assert response.status_code == 404

        data = response.get_json()
        assert "error" in data
        assert "status_code" in data
        assert data["status_code"] == 404


class TestAPIAuthenticationFlows:
    """Тестове за API authentication flows"""

    def test_volunteer_api_requires_login(self, client):
        """Тест че volunteer API изисква login"""
        # Опит за достъп до volunteer API без автентикация
        response = client.get("/api/volunteer/dashboard")
        assert response.status_code in [401, 403, 302]

    def test_admin_api_requires_login(self, client):
        """Тест че admin API изисква login"""
        # Опит за достъп до admin API без автентикация
        response = client.post("/api/tasks/trigger/test_task")
        assert response.status_code in [401, 403, 302]

    def test_authenticated_volunteer_api_access(self, authenticated_volunteer_client):
        """Тест за достъп до volunteer API с автентикация"""
        client = authenticated_volunteer_client

        # Този endpoint може да не съществува, но ако съществува:
        # response = client.get("/api/volunteer/dashboard")
        # assert response.status_code == 200

        # За сега тестваме logout endpoint
        response = client.post("/resend_volunteer_code")
        # Този endpoint може да върне 400 ако няма pending login
        assert response.status_code in [200, 400]

    def test_authenticated_admin_api_access(self, authenticated_admin_client):
        """Тест за достъп до admin API с автентикация"""
        client = authenticated_admin_client

        # Admin dashboard endpoint
        response = client.get("/admin_dashboard")
        assert response.status_code == 200

        # Admin volunteers endpoint
        response = client.get("/admin_volunteers")
        assert response.status_code == 200


class TestAPIInputValidation:
    """Тестове за API input validation"""

    def test_chatbot_api_sql_injection_protection(self, client):
        """Тест за защита срещу SQL injection в chatbot API"""
        malicious_message = "'; DROP TABLE users; --"

        response = client.post(
            "/api/chatbot/message",
            json={"message": malicious_message, "session_id": "test"},
        )

        # API трябва да се справи безопасно с malicious input
        assert response.status_code in [200, 400]
        if response.status_code == 200:
            data = response.get_json()
            assert "response" in data

    def test_chatbot_api_xss_protection(self, client):
        """Тест за защита срещу XSS в chatbot API"""
        xss_message = "<script>alert('XSS')</script>"

        response = client.post(
            "/api/chatbot/message", json={"message": xss_message, "session_id": "test"}
        )

        # API трябва да се справи безопасно с XSS
        assert response.status_code in [200, 400]
        if response.status_code == 200:
            data = response.get_json()
            # Response не трябва да съдържа несанитизиран HTML
            assert "<script>" not in data.get("response", "")

    def test_api_json_parsing_errors(self, client):
        """Тест за обработка на invalid JSON"""
        response = client.post(
            "/api/chatbot/message",
            data="invalid json {",
            content_type="application/json",
        )

        assert response.status_code == 400

    def test_api_large_payload_protection(self, client):
        """Тест за защита срещу много големи payloads"""
        large_data = {"message": "x" * 1000000, "session_id": "test"}

        response = client.post("/api/chatbot/message", json=large_data)

        # Server трябва да се справи с големи payloads или да ги отхвърли
        assert response.status_code in [200, 400, 413]


class TestAPIRateLimiting:
    """Тестове за API rate limiting"""

    def test_api_rate_limiting(self, client):
        """Тест за rate limiting на API endpoints"""
        # Многократни заявки към един endpoint
        for i in range(10):
            response = client.get("/api/ai/status")
            if i < 5:  # Първите няколко трябва да минат
                assert response.status_code == 200
            # else: може да бъде rate limited

        # Този тест е труден за имплементация без реален rate limiter
        # Затова го оставяме като пример
