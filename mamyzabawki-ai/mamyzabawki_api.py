#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import html
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# Ustawienia modelu i klucza
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

# ------------------------------------------------------------
# Pomocnicze funkcje
# ------------------------------------------------------------
def _norm(s):
    return "" if s is None else str(s).strip()

def _escape(s):
    return html.escape(_norm(s))

def _call_openai(prompt: str) -> str:
    """Połączenie z OpenAI Chat Completions API"""
    if not OPENAI_API_KEY:
        raise RuntimeError("Brak OPENAI_API_KEY w środowisku")

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
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
- Generuj **czysty HTML**, bez żadnych ``` ani znaczników języka.
- Sekcja <p> z opisem powinna mieć **ok. 1000–1500 znaków**.
- Lista cech: 4–6 naturalnych, konkretnych punktów.
- Jeśli atrybuty są puste, **wyodrębnij parametry techniczne z opisu** (np. wymiary, materiał, kolor, wiek, przeznaczenie).
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
        return jsonify({"response": html_result})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ------------------------------------------------------------
# Uruchomienie serwera
# ------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
