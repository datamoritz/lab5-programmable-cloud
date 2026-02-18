#!/bin/bash
set -euxo pipefail

apt-get update
apt-get install -y python3 python3-pip git

pip3 install flask

# simple flask app on port 5000 (replace with your real flaskr install if required)
cat >/srv/app.py <<'PY'
from flask import Flask
app = Flask(__name__)

@app.get("/")
def hello():
    return "hello from vm2"
PY

python3 /srv/app.py --host=0.0.0.0 --port=5000