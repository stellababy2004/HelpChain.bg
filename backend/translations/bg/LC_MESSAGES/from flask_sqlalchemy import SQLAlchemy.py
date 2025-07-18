from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class HelpRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), nullable=False)
    message = db.Column(db.Text, nullable=False)

    def __repr__(self):
        return f"<HelpRequest {self.name}>"

# Translations
translations = {
    "Send Request": "Envoyer la demande",
    "Name": "Nom",
    "Email": "E-mail",
    "What kind of help do you need?": "Quel type d'aide vous faut-il ?"
}
