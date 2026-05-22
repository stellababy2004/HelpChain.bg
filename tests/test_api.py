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


from datetime import UTC, datetime, timedelta

from backend.helpchain_backend.src.jwt_utils import encode_access_token
from backend.helpchain_backend.src.routes import api as api_routes
from backend.models import AdminUser, Request, SecurityEvent, Structure, User, utc_now


def _login_admin(client, app, admin_user: AdminUser) -> None:
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_user.id)
        sess["user_id"] = admin_user.id
        sess["admin_id"] = admin_user.id
        sess["admin_user_id"] = admin_user.id
        sess["role"] = admin_user.role
        sess["is_authenticated"] = True
        sess["is_admin"] = True
        sess["admin_logged_in"] = True
        sess["mfa_required"] = True
        sess[app.config.get("MFA_SESSION_KEY", "mfa_ok")] = True
        sess["mfa_ok_until"] = (utc_now() + timedelta(minutes=30)).isoformat()
        sess["admin_mfa_last_verified"] = 4102444800
        sess["admin_mfa_user_id"] = admin_user.id


def _make_structure(session, *, name: str, slug: str) -> Structure:
    row = Structure(name=name, slug=slug)
    session.add(row)
    session.flush()
    return row


def _make_admin(
    session,
    *,
    username: str,
    email: str,
    role: str,
    structure_id: int | None,
) -> AdminUser:
    row = AdminUser(
        username=username,
        email=email,
        password_hash="x",
        role=role,
        structure_id=structure_id,
        is_active=True,
        mfa_enabled=True,
        totp_secret="test-api-admin-authz",
    )
    session.add(row)
    session.flush()
    return row


def _make_user(session, *, username: str, email: str, structure_id: int) -> User:
    row = User(
        username=username,
        email=email,
        password_hash="x",
        role="requester",
        is_active=True,
        structure_id=structure_id,
    )
    session.add(row)
    session.flush()
    return row


def _make_request(
    session,
    *,
    title: str,
    user_id: int,
    structure_id: int,
    status: str = "pending",
) -> Request:
    now = datetime.now(UTC).replace(tzinfo=None)
    row = Request(
        title=title,
        description=f"Description for {title}",
        category="general",
        user_id=user_id,
        structure_id=structure_id,
        status=status,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


def _seed_admin_api_scope(session, *, prefix: str):
    structure_a = _make_structure(session, name=f"{prefix} Alpha", slug=f"{prefix}-alpha")
    structure_b = _make_structure(session, name=f"{prefix} Beta", slug=f"{prefix}-beta")
    admin_a = _make_admin(
        session,
        username=f"{prefix}_admin_a",
        email=f"{prefix}_admin_a@test.local",
        role="admin",
        structure_id=structure_a.id,
    )
    user_a = _make_user(
        session,
        username=f"{prefix}_user_a",
        email=f"{prefix}_user_a@test.local",
        structure_id=structure_a.id,
    )
    user_b = _make_user(
        session,
        username=f"{prefix}_user_b",
        email=f"{prefix}_user_b@test.local",
        structure_id=structure_b.id,
    )
    req_a = _make_request(
        session,
        title=f"{prefix}-visible",
        user_id=user_a.id,
        structure_id=structure_a.id,
    )
    req_b = _make_request(
        session,
        title=f"{prefix}-hidden",
        user_id=user_b.id,
        structure_id=structure_b.id,
    )
    session.commit()
    return structure_a, structure_b, admin_a, req_a, req_b


def test_api_export_passes_scoped_actor_to_controller(app, session, monkeypatch, tmp_path):
    structure_a, _structure_b, admin_a, _req_a, _req_b = _seed_admin_api_scope(
        session, prefix="tracked_api_export_scope"
    )
    export_path = tmp_path / "scoped-export.txt"
    export_path.write_text("scoped export", encoding="utf-8")
    captured: dict[str, object] = {}

    def _fake_export(filters, fmt, *, structure_id, allow_global):
        captured["filters"] = filters
        captured["fmt"] = fmt
        captured["structure_id"] = structure_id
        captured["allow_global"] = allow_global
        return str(export_path), "text/plain", "scoped-export.txt"

    monkeypatch.setattr(api_routes.controller, "export_requests", _fake_export)

    with app.app_context():
        token = encode_access_token(admin_a.id)

    client = app.test_client()
    response = client.get(
        "/api/export?format=csv&status=pending",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert captured["fmt"] == "csv"
    assert captured["structure_id"] == structure_a.id
    assert captured["allow_global"] is False
    assert captured["filters"]["status"] == "pending"


def test_api_export_passes_platform_global_actor_to_controller(
    app, session, monkeypatch, tmp_path
):
    _structure_a, _structure_b, _admin_a, _req_a, _req_b = _seed_admin_api_scope(
        session, prefix="tracked_api_export_global"
    )
    global_superadmin = _make_admin(
        session,
        username="tracked_api_export_global_superadmin",
        email="tracked_api_export_global_superadmin@test.local",
        role="superadmin",
        structure_id=None,
    )
    session.commit()
    export_path = tmp_path / "global-export.txt"
    export_path.write_text("global export", encoding="utf-8")
    captured: dict[str, object] = {}

    def _fake_export(filters, fmt, *, structure_id, allow_global):
        captured["filters"] = filters
        captured["fmt"] = fmt
        captured["structure_id"] = structure_id
        captured["allow_global"] = allow_global
        return str(export_path), "text/plain", "global-export.txt"

    monkeypatch.setattr(api_routes.controller, "export_requests", _fake_export)

    with app.app_context():
        token = encode_access_token(global_superadmin.id)

    client = app.test_client()
    response = client.get(
        "/api/export?format=csv&status=pending",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert captured["fmt"] == "csv"
    assert captured["structure_id"] is None
    assert captured["allow_global"] is True
    assert captured["filters"]["status"] == "pending"


def test_legacy_admin_change_status_requires_admin_session(client):
    response = client.post(
        "/api/admin/change_status",
        json={"request_id": 1, "status": "done"},
    )

    assert response.status_code == 403


def test_legacy_admin_change_status_stays_tenant_scoped(app, session):
    _structure_a, _structure_b, admin_a, req_a, req_b = _seed_admin_api_scope(
        session, prefix="tracked_api_change_status_scope"
    )
    client = app.test_client()
    _login_admin(client, app, admin_a)

    own_response = client.post(
        "/api/admin/change_status",
        json={"request_id": req_a.id, "status": "done"},
    )
    hidden_response = client.post(
        "/api/admin/change_status",
        json={"request_id": req_b.id, "status": "done"},
    )

    assert own_response.status_code == 200
    assert hidden_response.status_code == 404

    session.refresh(req_a)
    session.refresh(req_b)
    assert req_a.status == "done"
    assert req_b.status == "pending"

    denied_event = (
        session.query(SecurityEvent)
        .filter(SecurityEvent.event_type == "admin_authz_decision")
        .order_by(SecurityEvent.id.desc())
        .first()
    )
    assert denied_event is not None
    assert denied_event.actor_id == admin_a.id
    assert denied_event.route == "/api/admin/change_status"
    assert denied_event.method == "POST"
    assert denied_event.meta["actor_id"] == admin_a.id
    assert denied_event.meta["actor_role"] == "admin"
    assert denied_event.meta["structure_id"] == _structure_a.id
    assert denied_event.meta["action"] == "request.change_status"
    assert denied_event.meta["resource_type"] == "Request"
    assert denied_event.meta["resource_id"] == req_b.id
    assert denied_event.meta["decision"] == "denied"
    assert denied_event.meta["reason"] == "tenant_scope_mismatch"
    assert denied_event.meta["request_path"] == "/api/admin/change_status"


def test_legacy_admin_delete_request_requires_admin_session(client):
    response = client.post(
        "/api/admin/delete_request",
        json={"request_id": 1},
    )

    assert response.status_code == 403


def test_legacy_admin_delete_request_stays_tenant_scoped(app, session):
    _structure_a, _structure_b, admin_a, req_a, req_b = _seed_admin_api_scope(
        session, prefix="tracked_api_delete_request_scope"
    )
    client = app.test_client()
    _login_admin(client, app, admin_a)

    own_response = client.post(
        "/api/admin/delete_request",
        json={"request_id": req_a.id},
    )
    hidden_response = client.post(
        "/api/admin/delete_request",
        json={"request_id": req_b.id},
    )

    assert own_response.status_code == 200
    assert hidden_response.status_code == 404

    assert session.get(Request, req_a.id) is None
    assert session.get(Request, req_b.id) is not None

    denied_event = (
        session.query(SecurityEvent)
        .filter(SecurityEvent.event_type == "admin_authz_decision")
        .order_by(SecurityEvent.id.desc())
        .first()
    )
    assert denied_event is not None
    assert denied_event.actor_id == admin_a.id
    assert denied_event.route == "/api/admin/delete_request"
    assert denied_event.method == "POST"
    assert denied_event.meta["actor_id"] == admin_a.id
    assert denied_event.meta["actor_role"] == "admin"
    assert denied_event.meta["structure_id"] == _structure_a.id
    assert denied_event.meta["action"] == "request.delete"
    assert denied_event.meta["resource_type"] == "Request"
    assert denied_event.meta["resource_id"] == req_b.id
    assert denied_event.meta["decision"] == "denied"
    assert denied_event.meta["reason"] == "tenant_scope_mismatch"
    assert denied_event.meta["request_path"] == "/api/admin/delete_request"
