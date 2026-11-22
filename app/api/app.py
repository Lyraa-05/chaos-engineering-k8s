import os
import psycopg2
from flask import Flask, jsonify

app = Flask(__name__)

def conn():
    return psycopg2.connect(
        host=os.environ["DB_HOST"], dbname="app", user="app",
        password=os.environ["DB_PASSWORD"], connect_timeout=2)

@app.get("/healthz")          # liveness: is the process alive?
def healthz():
    return "ok"

@app.get("/ready")            # readiness: can it reach the DB?
def ready():
    try:
        conn().close()
        return "ready"
    except Exception:
        return "db unavailable", 503

@app.get("/api/visit")
def visit():
    c = conn(); cur = c.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS visits (id serial PRIMARY KEY, ts timestamptz DEFAULT now())")
    cur.execute("INSERT INTO visits DEFAULT VALUES")
    cur.execute("SELECT count(*) FROM visits")
    n = cur.fetchone()[0]
    c.commit(); c.close()
    return jsonify(visits=n, pod=os.environ.get("HOSTNAME"))
