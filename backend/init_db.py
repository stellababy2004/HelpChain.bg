from backend.app import app
from backend.extensions import db

with app.app_context():
    db.create_all()
    print("✅ Базата е създадена успешно.")
