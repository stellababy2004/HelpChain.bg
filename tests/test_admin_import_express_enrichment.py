from __future__ import annotations

from io import BytesIO

import pytest

from backend.helpchain_backend.src.models import ImportBatch, ProfessionalLead, ProfessionalLeadActivity
from backend.models import AdminUser

pytestmark = pytest.mark.spine


def _login_admin(client, admin_user: AdminUser) -> None:
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_user.id)
        sess["user_id"] = admin_user.id
        sess["admin_id"] = admin_user.id
        sess["admin_user_id"] = admin_user.id
        sess["role"] = admin_user.role
        sess["is_authenticated"] = True
        sess["is_admin"] = True
        sess["admin_logged_in"] = True


def _make_admin(session, *, username: str, email: str, role: str = "admin") -> AdminUser:
    row = AdminUser(
        username=username,
        email=email,
        password_hash="x",
        role=role,
        is_active=True,
    )
    session.add(row)
    session.commit()
    return row


def _preview_import(client, *, filename: str, csv_text: str):
    return client.post(
        "/admin/import/preview",
        data={
            "target_type": "professional_leads",
            "file": (BytesIO(csv_text.encode("utf-8")), filename),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )


def _confirm_import(client, *, batch_id: int, headers: list[str], mapping: dict[str, str]):
    data = {"batch_id": str(batch_id)}
    for index, header in enumerate(headers):
        data[f"mapping_{index}"] = mapping.get(header, "")
    return client.post("/admin/import/confirm", data=data, follow_redirects=False)


def test_duplicate_import_enriches_existing_lead_and_preserves_protected_fields(app, session):
    admin = _make_admin(
        session,
        username="import_enrich_admin",
        email="import_enrich_admin@test.local",
    )
    existing = ProfessionalLead(
        email="lead@example.org",
        full_name="Old Lead",
        phone="0101010101",
        city="Lyon",
        profession="Ancienne fonction",
        organization="Old Org",
        availability="busy",
        message="Old message",
        status="qualified",
        notes="Manual admin note",
        owner_admin_id=admin.id,
        source="public_form",
    )
    session.add(existing)
    session.commit()

    client = app.test_client()
    _login_admin(client, admin)

    csv_text = (
        "Email,Nom complet,Telephone,Ville,Fonction,Organisation,Disponibilite,Message\n"
        "lead@example.org,Updated Lead,0202020202,Paris,Coordinatrice,New Org,available,Fresh import message\n"
    )
    preview = _preview_import(client, filename="enrichment.csv", csv_text=csv_text)
    batch = ImportBatch.query.order_by(ImportBatch.id.desc()).first()
    confirm = _confirm_import(
        client,
        batch_id=batch.id,
        headers=[
            "Email",
            "Nom complet",
            "Telephone",
            "Ville",
            "Fonction",
            "Organisation",
            "Disponibilite",
            "Message",
        ],
        mapping={
            "Email": "email",
            "Nom complet": "full_name",
            "Telephone": "phone",
            "Ville": "city",
            "Fonction": "profession",
            "Organisation": "organization",
            "Disponibilite": "availability",
            "Message": "message",
        },
    )

    session.refresh(existing)
    session.refresh(batch)
    activity = (
        ProfessionalLeadActivity.query.filter_by(
            professional_lead_id=existing.id,
            action="import_updated",
        )
        .order_by(ProfessionalLeadActivity.id.desc())
        .first()
    )
    history = client.get("/admin/import/history")
    history_html = history.get_data(as_text=True)

    assert preview.status_code == 200
    assert confirm.status_code == 200
    assert ProfessionalLead.query.count() == 1
    assert existing.full_name == "Updated Lead"
    assert existing.phone == "0202020202"
    assert existing.city == "Paris"
    assert existing.profession == "Coordinatrice"
    assert existing.organization == "New Org"
    assert existing.availability == "available"
    assert existing.message == "Fresh import message"
    assert existing.status == "qualified"
    assert existing.notes == "Manual admin note"
    assert existing.owner_admin_id == admin.id
    assert batch.created_count == 0
    assert batch.updated_count == 1
    assert batch.skipped_duplicate_count == 0
    assert batch.rejected_count == 0
    assert activity is not None
    assert '"updated_fields":["full_name","phone","city","profession","organization","availability","message"]' in activity.payload_json
    assert '"import_batch_id":' in activity.payload_json
    assert '"batch_filename":"enrichment.csv"' in activity.payload_json
    assert history.status_code == 200
    assert "Mis à jour" in history_html
    assert "Doublons ignorés" in history_html
    assert "enrichment.csv" in history_html


def test_repeated_imports_are_idempotent_and_classify_duplicate_skip(app, session):
    admin = _make_admin(
        session,
        username="import_repeat_admin",
        email="import_repeat_admin@test.local",
    )
    client = app.test_client()
    _login_admin(client, admin)

    csv_text = (
        "Email,Nom complet,Telephone,Ville,Fonction,Organisation,Disponibilite,Message\n"
        "repeat@example.org,Repeat Lead,0303030303,Paris,Juriste,Repeat Org,available,Repeat message\n"
    )

    first_preview = _preview_import(client, filename="repeat-1.csv", csv_text=csv_text)
    first_batch = ImportBatch.query.order_by(ImportBatch.id.desc()).first()
    first_confirm = _confirm_import(
        client,
        batch_id=first_batch.id,
        headers=[
            "Email",
            "Nom complet",
            "Telephone",
            "Ville",
            "Fonction",
            "Organisation",
            "Disponibilite",
            "Message",
        ],
        mapping={
            "Email": "email",
            "Nom complet": "full_name",
            "Telephone": "phone",
            "Ville": "city",
            "Fonction": "profession",
            "Organisation": "organization",
            "Disponibilite": "availability",
            "Message": "message",
        },
    )

    second_preview = _preview_import(client, filename="repeat-2.csv", csv_text=csv_text)
    second_batch = ImportBatch.query.order_by(ImportBatch.id.desc()).first()
    second_confirm = _confirm_import(
        client,
        batch_id=second_batch.id,
        headers=[
            "Email",
            "Nom complet",
            "Telephone",
            "Ville",
            "Fonction",
            "Organisation",
            "Disponibilite",
            "Message",
        ],
        mapping={
            "Email": "email",
            "Nom complet": "full_name",
            "Telephone": "phone",
            "Ville": "city",
            "Fonction": "profession",
            "Organisation": "organization",
            "Disponibilite": "availability",
            "Message": "message",
        },
    )

    session.refresh(first_batch)
    session.refresh(second_batch)
    lead = ProfessionalLead.query.filter_by(email="repeat@example.org").one()
    skipped_activity = (
        ProfessionalLeadActivity.query.filter_by(
            professional_lead_id=lead.id,
            action="import_skipped_duplicate",
        )
        .order_by(ProfessionalLeadActivity.id.desc())
        .first()
    )

    assert first_preview.status_code == 200
    assert first_confirm.status_code == 200
    assert second_preview.status_code == 200
    assert second_confirm.status_code == 200
    assert ProfessionalLead.query.filter_by(email="repeat@example.org").count() == 1
    assert first_batch.created_count == 1
    assert first_batch.updated_count == 0
    assert first_batch.skipped_duplicate_count == 0
    assert second_batch.created_count == 0
    assert second_batch.updated_count == 0
    assert second_batch.skipped_duplicate_count == 1
    assert second_batch.rejected_count == 0
    assert skipped_activity is not None


def test_import_classifies_created_updated_skipped_and_rejected_rows(app, session):
    admin = _make_admin(
        session,
        username="import_matrix_admin",
        email="import_matrix_admin@test.local",
    )
    lead_to_update = ProfessionalLead(
        email="update@example.org",
        full_name="Before Update",
        phone="1111111111",
        city="Lille",
        profession="Psychologue",
        organization="Org Update",
        availability="busy",
        message="Old update",
        status="new",
        source="public_form",
    )
    lead_to_skip = ProfessionalLead(
        email="skip@example.org",
        full_name="Skip Lead",
        phone="2222222222",
        city="Paris",
        profession="Juriste",
        organization="Skip Org",
        availability="available",
        message="Same message",
        status="new",
        source="public_form",
    )
    session.add_all([lead_to_update, lead_to_skip])
    session.commit()

    client = app.test_client()
    _login_admin(client, admin)

    csv_text = (
        "Email,Nom complet,Telephone,Ville,Fonction,Organisation,Disponibilite,Message\n"
        "new@example.org,New Lead,3333333333,Paris,Assistante sociale,New Org,available,New message\n"
        "update@example.org,Before Update,1111111111,Nantes,Psychologue,Org Update,busy,Updated message\n"
        "skip@example.org,Skip Lead,2222222222,Paris,Juriste,Skip Org,available,Same message\n"
        "broken-at-example,Rejected Lead,,,,,,\n"
    )
    preview = _preview_import(client, filename="matrix.csv", csv_text=csv_text)
    batch = ImportBatch.query.order_by(ImportBatch.id.desc()).first()
    confirm = _confirm_import(
        client,
        batch_id=batch.id,
        headers=[
            "Email",
            "Nom complet",
            "Telephone",
            "Ville",
            "Fonction",
            "Organisation",
            "Disponibilite",
            "Message",
        ],
        mapping={
            "Email": "email",
            "Nom complet": "full_name",
            "Telephone": "phone",
            "Ville": "city",
            "Fonction": "profession",
            "Organisation": "organization",
            "Disponibilite": "availability",
            "Message": "message",
        },
    )

    session.refresh(batch)
    session.refresh(lead_to_update)
    result_html = confirm.get_data(as_text=True)

    assert preview.status_code == 200
    assert confirm.status_code == 200
    assert batch.created_count == 1
    assert batch.updated_count == 1
    assert batch.skipped_duplicate_count == 1
    assert batch.rejected_count == 1
    assert lead_to_update.city == "Nantes"
    assert lead_to_update.message == "Updated message"
    assert ProfessionalLead.query.filter_by(email="new@example.org").count() == 1
    assert "Créés" in result_html
    assert "Mis à jour" in result_html
    assert "Doublons ignorés" in result_html
    assert "Rejetés" in result_html
