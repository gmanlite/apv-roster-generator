"""
wsgi.py — entry point for a real web server.

The Flask development server in app.py is fine on your own machine but is not
built to face the internet. Hosted, the app is served by gunicorn:

    gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --timeout 60

One worker process holds the roster cache in memory, so a low worker count is
deliberate: two workers share nothing, but each serves many people from its own
cache, and the upstream feed sees far fewer calls than if everyone ran the app
themselves.
"""

from app import app

if __name__ == "__main__":
    app.run()
