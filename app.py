"""
Servizio Railway: parsing ordini PDF multi-brand.

Pattern sincrono vs asincrono:
  - MaxMara: parsing deterministico, < 1s -> risposta sincrona (invariato per Vercel).
  - Brand con OCR (es. PT Torino): misurato 60-110s su un ordine di 4 pagine -> NESSUNA
    richiesta HTTP sincrona regge questo tempo in modo affidabile. Si usa un pattern a
    job: il client invia il PDF, riceve subito un job_id, e fa polling su /jobs/<id>
    finche' lo stato non e' "done".

Espone:
  GET  /health              -> verifica dipendenze (tesseract)
  POST /parse-maxmara        -> SINCRONO, compatibilita' con l'integrazione gia' attiva
  POST /parse                -> riconosce il brand; se rapido risponde subito (200),
                                 se richiede OCR risponde 202 + job_id
  GET  /jobs/<job_id>        -> stato/risultato di un job asincrono
"""
import os
import shutil
import tempfile
import threading
import traceback
import uuid

from flask import Flask, request, jsonify

from order_router import parse as router_parse, detect_brand
from maxmara_order_parser import parse_order as parse_maxmara

app = Flask(__name__)

_jobs = {}
_jobs_lock = threading.Lock()


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

MAX_UPLOAD_MB = 25
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


@app.get("/health")
def health():
    tesseract_path = shutil.which("tesseract")
    return jsonify({
        "status": "ok",
        "tesseract_installato": tesseract_path is not None,
        "tesseract_path": tesseract_path,
    })


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
    """
    Riconosce il brand. MaxMara (veloce, deterministico) risponde subito (200).
    Brand OCR (es. PT Torino) avviano un job in background e rispondono 202 con
    job_id: il client deve fare polling su GET /jobs/<job_id>.
    """
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

    if brand is None:
        os.unlink(path)
        return jsonify({
            "valido": False,
            "errori": [{"globale": "template PDF non riconosciuto: nessun parser per questo brand"}],
        }), 422

    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {"status": "processing"}

    def runner():
        _run_job(job_id, path, lambda p: {**router_parse(p)})

    threading.Thread(target=runner, daemon=True).start()
    return jsonify({
        "status": "processing",
        "job_id": job_id,
        "brand": brand,
        "messaggio": "Richiede OCR (tipicamente 1-2 minuti). Fai polling su GET /jobs/<job_id>.",
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
    """Mantenuto per compatibilita' con l'integrazione gia' attiva su Vercel."""
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
