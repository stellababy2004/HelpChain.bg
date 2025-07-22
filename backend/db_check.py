from backend.extensions import db
from backend.models import HelpRequest
from backend.app import app

with app.app_context():
    # Покажи всички таблици
    print("Таблици в базата:", db.engine.table_names())

    # Покажи всички заявки
    requests = HelpRequest.query.all()
    print("Брой заявки в HelpRequest:", len(requests))
    for req in requests:
        print("ID:", req.id, "Name:", getattr(req, "name", None), "Status:", getattr(req, "status", None))