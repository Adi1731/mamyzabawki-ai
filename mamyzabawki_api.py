#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import html
import tempfile
import time
import requests
import openpyxl
from flask import Flask, request, jsonify, render_template

# ------------------------------------------------------------
# Konfiguracja
# ------------------------------------------------------------
app = Flask(__name__, template_folder="templates", static_folder="static")

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

MAX_RETRIES = 3
RETRY_DELAY = 2


# ------------------------------------------------------------
# Pomocnicze funkcje
# ------------------------------------------------------------
def _norm(s):
    return "" if s is None else str(s).strip()


def _call_openai(prompt: str) -> str:
    """Połączenie z OpenAI Chat Completions API"""
    if not OPENAI_API_KEY:
        raise RuntimeError("Brak OPENAI_API_KEY w środowisku")

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
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
    if resp.status_code != 200:
        raise RuntimeError(f"OpenAI error {resp.status_code}: {resp.text}")

    data = resp.json()
    content = (
        data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    )
    if content.startswith("```"):
        content = content.strip("`").replace("html", "").strip()
    return content


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
            print(f"Błąd pobierania produktu {pid}: {resp.status_code}")
    return products


# ------------------------------------------------------------
# Endpoint API
# ------------------------------------------------------------
@app.route("/get_response", methods=["POST"])
def get_response():
    """Endpoint: przyjmuje JSON z danymi produktu, zwraca HTML opis."""
    try:
        data = request.get_json(force=True)
        name = _norm(data.get("name"))
        description = _norm(data.get("description"))
        attributes = data.get("attributes", [])
        producer_name = _norm(data.get("producer_name"))
        image_url = _norm(data.get("image_url"))

        attrs_str = ", ".join(
            f"{a.get('name')}: {a.get('value')}" for a in attributes if a.get("value")
        )

        prompt = f"""
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
        html_result = _call_openai(prompt)
        if request.args.get("format") == "html":
            return html_result, 200, {"Content-Type": "text/html; charset=utf-8"}
        else:
            return jsonify({"response": html_result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ------------------------------------------------------------
# Formularz webowy i przetwarzanie wsadowe
# ------------------------------------------------------------
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/run", methods=["POST"])
def run_process():
    try:
        shop = request.form["shop"].strip()
        user = request.form["user"].strip()
        password = request.form["pass"].strip()
        model = request.form.get("model", "gpt-4o-mini").strip() or "gpt-4o-mini"
        file = request.files.get("ids_file")

        if not file:
            return render_template("index.html", msg="❌ Brak pliku z ID produktów", success=False)

        # ⚙️ zapisz plik tymczasowo i wczytaj ID produktów
        temp_dir = tempfile.mkdtemp()
        temp_path = os.path.join(temp_dir, "ids.txt")
        file.save(temp_path)

        with open(temp_path, "r", encoding="utf-8") as f:
            product_ids = [line.strip() for line in f if line.strip()]

        if not product_ids:
            return render_template("index.html", msg="❌ Plik nie zawiera żadnych ID produktów", success=False)

        # 🔑 Pobranie danych produktów z Shoper API
        products = _fetch_shoper_products(shop, user, password, product_ids)
        if not products:
            return render_template("index.html", msg="❌ Nie udało się pobrać danych produktów z Shopera", success=False)

        # 📘 Przygotowanie Excela
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Descriptions"
        ws.append(["ID", "Nazwa produktu", "Opis HTML"])

        # 🔄 Przetwarzanie każdego produktu
        for idx, p in enumerate(products, start=1):
            try:
                translations = (p.get("translations") or {}).get("pl_PL") or {}
                name = _norm(translations.get("name") or p.get("name"))
                description = _norm(translations.get("description") or p.get("description"))
                attributes = p.get("attributes") or []
                producer_name = _norm(p.get("producer_id", ""))

                # 🧠 Przygotowanie danych dla modelu
                body = {
                    "name": name,
                    "description": description,
                    "producer_name": producer_name,
                    "attributes": attributes,
                }

                # ✅ Bezpośrednie wywołanie OpenAI (bez zapętlenia HTTP)
                html_code = _call_openai(json.dumps(body, ensure_ascii=False))

                ws.append([p.get("product_id", ""), name, html_code])
                print(f"[{idx}/{len(products)}] ✅ Wygenerowano opis dla: {name}")
                # mały odstęp między zapytaniami, żeby uniknąć rate-limit
                time.sleep(1.5)

            except Exception as e:
                ws.append([p.get("product_id", ""), name or "Brak nazwy", f"Błąd: {e}"])
                print(f"[{idx}/{len(products)}] ⚠️ Błąd dla {name}: {e}")

        # 💾 Zapisz wynikowy plik w static/
        os.makedirs("static", exist_ok=True)
        output_path = os.path.join("static", "generated.xlsx")
        wb.save(output_path)

        return render_template(
            "index.html",
            msg=f"✅ Przetwarzanie zakończone. <a href='/static/generated.xlsx' target='_blank'>📄 Pobierz plik</a>",
            success=True,
        )

    except Exception as e:
        print(f"❌ Błąd krytyczny: {e}")
        return render_template("index.html", msg=f"❌ Błąd: {e}", success=False)


# ------------------------------------------------------------
# Uruchomienie serwera
# ------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
