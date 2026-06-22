from pytorrent import create_app, socketio
from pytorrent.config import ALLOW_UNSAFE_WERKZEUG, DEBUG, HOST, PORT

app = create_app()

if __name__ == "__main__":
    # Note: This entrypoint is kept for local development; production should use gunicorn via wsgi:app.
    socketio.run(
        app,
        host=HOST,
        port=PORT,
        debug=DEBUG,
        allow_unsafe_werkzeug=ALLOW_UNSAFE_WERKZEUG,
    )
