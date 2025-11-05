#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import tempfile
import time
import uuid
import requests
import openpyxl
from threading import Thread
from datetime import datetime
from flask import Flask, request, jsonify, render_template

# ------------------------------------------------------------
# Konfiguracja
# ------------------------------------------------------------
app = Flask(__name__, template_folder="templates", static_folder="static")

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
MAX_RETRIES = 3
RETRY_DELAY = 2

tasks = {}  # pamięć postępu zadań


# ------------------------------------------------------------
# Pomocnicze funkcje
# ------------------------------------------------------------
def _norm(s):
    return "" if s is None else str(s).strip()


def _call_openai(prompt: str) -> str:
    """Połączenie z OpenAI Chat Completions API z retry"""
    if not OPENAI_API_KEY:
        raise RuntimeError("Brak OPENAI_API_KEY w środowisku")

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    for attempt in range(MAX_RETRIES):
        try:
            body = {
                "model": OPENAI_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Jesteś ekspertem od tworzenia profesjonalnych, technicznych opisów produktów. "
                            "Zawsze zwracasz czysty kod HTML zgodny z wymaganym układem, bez znaczników ```."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 1500,
            }

            resp = requests.post(url, headers=headers, json=body, timeout=120)
            if resp.status_code == 200:
                data = resp.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
                if content.startswith("```"):
                    content = content.strip("`").replace("html", "").strip()
                return content

            print(f"⚠️ Błąd OpenAI ({resp.status_code}), próba {attempt + 1}")
            time.sleep(RETRY_DELAY)
        except Exception as e:
            print(f"⚠️ Wyjątek OpenAI ({attempt + 1}/3): {e}")
            time.sleep(RETRY_DELAY)

    raise RuntimeError("Nie udało się uzyskać odpowiedzi z OpenAI po 3 próbach")


def _fetch_shoper_products(shop, user, password, ids):
    """Pobiera dane produktów z Shopera"""
    base_url = f"https://{shop}.shoparena.pl/webapi/rest"
    auth_url = f"{base_url}/auth"

    token_resp = requests.post(auth_url, auth=(user, password))
    if token_resp.status_code != 200:
        raise RuntimeError("Błąd logowania do Shopera")

    token = token_resp.json().get("access_token")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    products = []
    for pid in ids:
        resp = requests.get(f"{base_url}/products/{pid}", headers=headers)
        if resp.status_code == 200:
            products.append(resp.json())
        else:
            print(f"⚠️ Błąd pobierania produktu {pid}: {resp.status_code}")
    return products


def _build_prompt(name, description, attributes, producer_name, image_url=""):
    """Buduje prompt do generowania opisu produktu"""
    attrs_str = ", ".join(
        f"{a.get('name')}: {a.get('value')}" for a in attributes if a.get("value")
    )

    return f"""
Stwórz kompletny opis HTML produktu w następującym układzie (bez ```):

<div class="new-desc-wrapper">

<div class="new-desc-data-wrapper">
<h3>[nazwa produktu i jego zastosowanie]</h3>
<p>[opis produktu w 4–6 zdaniach, długość ok. 1000–1500 znaków]</p>
</div>

<div class="new-desc-listing-wrapper">
<p>Najważniejsze cechy</p>
<ul class="new-desc-custom-list">
<li>[cecha 1]</li>
<li>[cecha 2]</li>
<li>[cecha 3]</li>
<li>[cecha 4]</li>
<li>[cecha 5]</li>
</ul>
</div>

<div class="attr-table-data">
<h4>Parametry</h4>
<div class="attr-table-wrapper">
<div class="attr-table-grey"><div>[nazwa parametru]</div><div>[wartość]</div></div>
<div class="attr-table-normal"><div>[nazwa parametru]</div><div>[wartość]</div></div>
[... kolejne parametry naprzemiennie grey/normal ...]
</div>
</div>

</div>

Zasady:
- Generuj czysty HTML, bez ``` ani znaczników języka.
- Sekcja <p> z opisem powinna mieć ok. 1000–1500 znaków.
- Lista cech: 4–6 naturalnych, konkretnych punktów.
- Jeśli atrybuty są puste, wyodrębnij parametry techniczne z opisu.
- Nie dodawaj stylów inline, komentarzy ani innych elementów.
- Język polski, profesjonalny, przyjazny, techniczny, bez przesady marketingowej.

Dane produktu:
Nazwa: {name}
Opis: {description}
Producent: {producer_name}
Atrybuty: {attrs_str}
Zdjęcie: {image_url}
"""


# ------------------------------------------------------------
# Endpoint API – pojedynczy opis
# ------------------------------------------------------------
@app.route("/get_response", methods=["POST"])
def get_response():
    """Generuje pojedynczy opis produktu"""
    try:
        data = request.get_json(force=True)
        name = _norm(data.get("name"))
        description = _norm(data.get("description"))
        attributes = data.get("attributes", [])
        producer_name = _norm(data.get("producer_name"))
        image_url = _norm(data.get("image_url"))

        prompt = _build_prompt(name, description, attributes, producer_name, image_url)
        html_result = _call_openai(prompt)

        if request.args.get("format") == "html":
            return html_result, 200, {"Content-Type": "text/html; charset=utf-8"}
        return jsonify({"response": html_result})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ------------------------------------------------------------
# Asynchroniczne przetwarzanie wsadowe
# ------------------------------------------------------------
def process_task(task_id, shop, user, password, model, file_path):
    start_time = datetime.now()
    tasks[task_id] = {"progress": 0, "status": "started", "elapsed": 0}

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            product_ids = [line.strip() for line in f if line.strip()]

        products = _fetch_shoper_products(shop, user, password, product_ids)
        if not products:
            raise RuntimeError("Nie udało się pobrać danych produktów z Shopera")

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Descriptions"
        ws.append(["ID", "Nazwa produktu", "Opis HTML"])

        total = len(products)
        for i, p in enumerate(products, 1):
            try:
                translations = (p.get("translations") or {}).get("pl_PL") or {}
                name = _norm(translations.get("name") or p.get("name"))
                description = _norm(translations.get("description") or p.get("description"))
                attributes = p.get("attributes") or []
                producer_name = _norm(p.get("producer_id", ""))

                prompt = _build_prompt(name, description, attributes, producer_name)
                html_code = _call_openai(prompt)

                ws.append([p.get("product_id", ""), name, html_code])
                print(f"[{i}/{total}] ✅ {name}")

            except Exception as e:
                ws.append([p.get("product_id", ""), name or "Brak nazwy", f"Błąd: {e}"])
                print(f"[{i}/{total}] ⚠️ Błąd dla {name}: {e}")

            tasks[task_id]["progress"] = int(i / total * 100)
            tasks[task_id]["elapsed"] = (datetime.now() - start_time).seconds
            time.sleep(1.0)

        os.makedirs("static", exist_ok=True)
        output_path = os.path.join("static", f"generated_{task_id}.xlsx")
        wb.save(output_path)

        tasks[task_id]["status"] = "done"
        tasks[task_id]["file"] = f"/{output_path}"
        tasks[task_id]["elapsed"] = (datetime.now() - start_time).seconds

    except Exception as e:
        tasks[task_id]["status"] = "error"
        tasks[task_id]["error"] = str(e)


# ------------------------------------------------------------
# Endpoints asynchroniczne
# ------------------------------------------------------------
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/run_async", methods=["POST"])
def run_async():
    """Uruchamia przetwarzanie wsadowe asynchronicznie"""
    try:
        shop = request.form["shop"].strip()
        user = request.form["user"].strip()
        password = request.form["pass"].strip()
        model = request.form.get("model", "gpt-4o-mini").strip() or "gpt-4o-mini"
        file = request.files.get("ids_file")

        if not file:
            return jsonify({"error": "Brak pliku"}), 400

        temp_dir = tempfile.mkdtemp()
        file_path = os.path.join(temp_dir, "ids.txt")
        file.save(file_path)

        task_id = str(uuid.uuid4())
        thread = Thread(target=process_task, args=(task_id, shop, user, password, model, file_path))
        thread.start()

        return jsonify({"task_id": task_id})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/status/<task_id>")
def status(task_id):
    """Zwraca status postępu"""
    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "Nie znaleziono zadania"}), 404
    return jsonify(task)


# ------------------------------------------------------------
# Uruchomienie serwera
# ------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
