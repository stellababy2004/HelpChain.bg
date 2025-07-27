# filepath: backend/models.py
from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()

class Volunteer(db.Model):
    __tablename__ = 'volunteers'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(20))
    location = db.Column(db.String(100))