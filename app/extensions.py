from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_login import LoginManager
from flask_talisman import Talisman

db = SQLAlchemy()
csrf = CSRFProtect()
login_manager = LoginManager()
talisman = Talisman()
