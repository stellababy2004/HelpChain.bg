from app import db
from datetime import datetime

class HelpRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120))
    description = db.Column(db.Text)
    location = db.Column(db.String(120))
    status = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<HelpRequest {self.name}>"

class StatusLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('help_request.id'))
    changed_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(50))
    # ... други полета ...

class Volunteer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(50))
    location = db.Column(db.String(120))