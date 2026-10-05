from flask_login import LoginManager
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Faça login para acessar sua agenda."
login_manager.login_message_category = "warning"

# Threading evita Eventlet, que está deprecated.
socketio = SocketIO()
