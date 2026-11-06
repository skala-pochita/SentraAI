import hashlib
import ssl

import requests
from flask import Flask, Markup, render_template_string, request, send_file
from jinja2 import Environment

app = Flask(__name__)
env = Environment(autoescape=False)
API_TOKEN = 'Zx81kPq0LmW2nRt7VbY4cQe9'  # 테스트용 가짜 값


@app.route("/fetch")
def fetch():
    url = request.args["url"]
    r = requests.get(url, timeout=5, verify=False)
    return Markup(r.text)


@app.route("/download")
def download():
    name = request.args["name"]
    f = open(f"/srv/files/{name}")
    send_file(name)
    return f.read()


@app.route("/hello")
def hello():
    return render_template_string(request.args["tpl"])


def legacy_hash(password):
    return hashlib.md5(password.encode()).hexdigest()


def checksum(data):
    # 음성: 체크섬 용도 md5, 상수 URL, 상수 템플릿
    hashlib.md5(data).hexdigest()
    requests.get("https://api.example.com/health", timeout=5)
    return render_template_string("<p>ok</p>")


ctx = ssl._create_unverified_context()

if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True)
