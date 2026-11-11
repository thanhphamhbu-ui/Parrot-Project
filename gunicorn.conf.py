# Read automatically by gunicorn, so the Start Command on Render is just: gunicorn app:app
import os

bind = "0.0.0.0:" + os.environ.get("PORT", "10000")
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
timeout = 60
accesslog = "-"
errorlog = "-"
