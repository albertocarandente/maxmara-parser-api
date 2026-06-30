"""
Servizio Railway: parsing ordini PDF multi-brand.
- MaxMara: deterministico (<1s, risposta sincrona)
- Altri brand: AI vision su Railway (GPT-4o-mini, job async, nessun timeout)
"""
import os
import base64
import io
import json
import tempfile
import threading
import traceback
import uuid

from flask import Flask, request, jsonify
import fitz

from maxmara_order_parser import parse_order as parse_maxmara
from order_router import detect_brand

app = Flask(__name__)

_jobs = {}
_jobs_lock = threading.Lock()

MAX_UPLOAD_MB = 25
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# ── AI analysis (for non-MaxMara brands) ────────────────────────────────────

AI_SYSTEM_PROMPT = """Sei un assistente specializzato nell'estrazione dati da documenti di ordine per brand di moda.
Restituisci ESCLUSIVAMENTE un JSON valido:
{"products":[{"modello":"...","descrizione":"...","variante":"...","prezzoAcquisto":"...","taglie":"1 - 30  1 - 31"}],"numeroOrdine":null,"brand":null,"fornitore":null,"stagione":null,"genere":null}
Ogni prodotto DEVE avere: modello, variante, prezzoAcquisto, taglie.
Le taglie in formato: "1 - 30  2 - 31  1 - 32"."""

def _analyze_with_ai(path):
    """Convert PDF to images, send to OpenRouter GPT-4o-mini."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY non configurata su Railway")

    doc = fitz.open(path)
    images_b64 = []
    for i in range(min(doc.page_count, 5)):  # max 5 pages
        pix = doc[i].get_pixmap(dpi=150)
        images_b64.append(base64.b64encode(pix.tobytes("png")).decode())

    content = []
    for b64 in images_b64:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    content.append({"type": "text", "text": "Estrai TUTTI i prodotti da questo ordine in formato JSON."})

    import urllib.request
    body = json.dumps({
        "model": "openai/gpt-4o-mini",
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": AI_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
    }).encode()

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://etienne-studio.vercel.app",
            "X-Title": "ETIENNE Studio",
        },
    )

    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
        raw = data["choices"][0]["message"]["content"]
        # Parse JSON from response
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
        result = json.loads(raw)
        products = result.get("products", [])
        capi = []
        for p in products:
            capi.append({
                "modello": p.get("modello", ""),
                "nome": p.get("descrizione"),
                "var": p.get("variante", ""),
                "art": p.get("motivo"),
                "scala": "A",
                "taglie": _parse_sizes(p.get("taglie", "")),
                "tot_capi": _count_sizes(p.get("taglie", "")),
                "prezzo": _parse_price(p.get("prezzoAcquisto", "")),
                "importo": None,
            })
        tot_capi = sum(c["tot_capi"] or 0 for c in capi)
        return {
            "valido": True,
            "brand": "ai",
            "n_capi_righe": len(capi),
            "totale_capi": tot_capi,
            "totale_importo": 0,
            "capi": capi,
            "errori": [],
            "products": products,
        }

def _parse_sizes(s):
    """'1 - 30  2 - 31' -> {'30': 1, '31': 2}"""
    import re
    sizes = {}
    for m in re.finditer(r'(\d+)\s*-\s*(\w+)', s):
        sizes[m.group(2)] = int(m.group(1))
    return sizes

def _count_sizes(s):
    import re
    return sum(int(m.group(1)) for m in re.finditer(r'(\d+)\s*-\s*\w+', s))

def _parse_price(s):
    try:
        return float(s.replace(",", "."))
    except:
        return None

# ── Endpoints ───────────────────────────────────────────────────────────────

def _run_job(job_id, path, parse_fn):
    try:
        result = parse_fn(path)
        with _jobs_lock:
            _jobs[job_id] = {"status": "done", "result": result}
    except Exception as e:
        with _jobs_lock:
            _jobs[job_id] = {
                "status": "error",
                "error": str(e),
                "trace": traceback.format_exc(limit=3),
            }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


@app.get("/health")
def health():
    api = bool(os.environ.get("OPENROUTER_API_KEY", ""))
    return jsonify({"status": "ok", "openrouter": api})


def _save_upload():
    if "file" not in request.files:
        return None, ("nessun file ricevuto (campo atteso: 'file')", 400)
    f = request.files["file"]
    if not f.filename:
        return None, ("file vuoto", 400)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    f.save(tmp.name)
    return tmp.name, None


@app.post("/parse")
def parse_any_brand():
    path, err = _save_upload()
    if err:
        msg, code = err
        return jsonify({"valido": False, "errori": [{"globale": msg}]}), code

    brand = detect_brand(path)

    if brand == "maxmara":
        try:
            result = parse_maxmara(path)
            result["brand"] = "maxmara"
            return jsonify(result)
        except Exception as e:
            return jsonify({"valido": False, "errori": [{"globale": str(e)}]}), 500
        finally:
            os.unlink(path)

    # Non-MaxMara: job asincrono con AI su Railway
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {"status": "processing"}

    def runner():
        _run_job(job_id, path, _analyze_with_ai)

    threading.Thread(target=runner, daemon=True).start()
    brand_name = brand or "sconosciuto"
    return jsonify({
        "status": "processing",
        "job_id": job_id,
        "brand": brand_name,
        "messaggio": f"Sto leggendo l'ordine {brand_name}...",
    }), 202


@app.get("/jobs/<job_id>")
def job_status(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return jsonify({"status": "not_found"}), 404
    if job["status"] == "processing":
        return jsonify({"status": "processing"}), 202
    if job["status"] == "error":
        return jsonify({"status": "error", "errori": [{"globale": job["error"]}]}), 500
    return jsonify(job["result"])


@app.post("/parse-maxmara")
def parse_maxmara_endpoint():
    path, err = _save_upload()
    if err:
        msg, code = err
        return jsonify({"valido": False, "errori": [{"globale": msg}]}), code
    try:
        result = parse_maxmara(path)
        return jsonify(result)
    except Exception as e:
        return jsonify({"valido": False, "errori": [{"globale": f"errore interno: {e}"}]}), 500
    finally:
        os.unlink(path)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
