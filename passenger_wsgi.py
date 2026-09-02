"""
passenger_wsgi.py — entry point for cPanel hosting (GoDaddy, Namecheap, etc).

cPanel's "Setup Python App" runs Flask under Phusion Passenger rather than
gunicorn, and Passenger looks for a module-level object named `application`.
That is the only difference from wsgi.py; the app itself is identical.

Setting it up in cPanel:
  1. Software -> Setup Python App -> Create Application
  2. Application root:  pm-roster-web        (wherever you upload these files)
     Application URL:   your domain or subdomain
     Entry point:       passenger_wsgi.py
  3. Add requirements.txt under Configuration files, then Run Pip Install
  4. Restart the app after any code change — Passenger caches the module, the
     same way the local run.bat window does.

If the app 500s, cPanel's error log is the first place to look, not the browser.
"""

from app import app as application

# Some cPanel images invoke this file directly rather than through Passenger.
if __name__ == "__main__":
    application.run()
