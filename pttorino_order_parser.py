"""
Parser universale per PDF senza layer di testo (es. PT TORINO / COVER 50).
Pipeline: PyMuPDF render pagina -> immagine PNG -> Claude Vision API ->
JSON strutturato -> validazione aritmetica.
"""
import os, re, base64, json
import fitz
from PIL import Image
import anthropic

DPI = 200

def _page_to_base64(doc, page_index):
    pix = doc[page_index].get_pixmap(dpi=DPI)
    return base64.standard_b64encode(pix.tobytes("png")).decode("utf-8")

def _money_it(t):
    if not t: return None
    u = str(t).replace(".", "").replace(",", ".")
    try: return float(u)
    except ValueError: return None

EXTRACTION_PROMPT = """Sei un estrattore preciso di dati da conferme d'ordine fashion.
Estrai TUTTE le righe prodotto dalla tabella. Per ogni riga restituisci JSON con:
modello, articolo, colore, descrizione, taglie (solo qty>0), qta, prezzo, importo.
Valori monetari formato europeo (1.064,00 = 1064.00).
Rispondi SOLO con array JSON, nessun testo prima o dopo."""

def parse_order(path, catalogo_modelli=None):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"valido": False, "errori": [{"globale": "ANTHROPIC_API_KEY non configurata"}], "capi": [], "totale_capi": 0, "totale_importo": 0.0}

    client = anthropic.Anthropic(api_key=api_key)
    doc = fitz.open(path)
    capi, errori = [], []

    for i in range(doc.page_count):
        img_b64 = _page_to_base64(doc, i)
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=4096,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                    {"type": "text", "text": EXTRACTION_PROMPT}
                ]}]
            )
        except Exception as e:
            errori.append({"globale": f"Errore API pagina {i+1}: {e}"})
            continue

        raw = response.content[0].text.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        try: rows = json.loads(raw)
        except json.JSONDecodeError as e:
            errori.append({"globale": f"JSON non valido pagina {i+1}: {e}"})
            continue

        if not isinstance(rows, list): continue
        for row in rows:
            try:
                modello = str(row.get("modello") or "").strip()
                if not modello: continue
                articolo = str(row.get("articolo") or "").strip() or None
                colore = str(row.get("colore") or "").strip() or None
                taglie_raw = row.get("taglie") or {}
                taglie = {str(k): int(v) for k, v in taglie_raw.items() if int(v) > 0}
                qta = int(row["qta"]) if row.get("qta") is not None else None
                prezzo = _money_it(row["prezzo"]) if row.get("prezzo") is not None else None
                importo = _money_it(row["importo"]) if row.get("importo") is not None else None

                capo = {"modello": modello, "articolo": articolo, "colore": colore, "taglie": taglie, "qta": qta, "prezzo": prezzo, "importo": importo}
                somma = sum(taglie.values())
                probs = []
                if qta is not None and somma != qta: probs.append(f"somma taglie {somma} != qta {qta}")
                if prezzo and qta and importo and round(prezzo * qta, 2) != round(importo, 2):
                    probs.append("prezzo*qta != importo")
                if probs: errori.append({"modello": modello, "problemi": probs})
                capi.append(capo)
            except Exception as e:
                errori.append({"globale": f"Errore riga pagina {i+1}: {e}"})

    tot_capi = sum(c["qta"] or 0 for c in capi)
    tot_imp = round(sum(c["importo"] or 0 for c in capi), 2)
    return {"valido": len(errori)==0, "n_capi_righe": len(capi), "totale_capi": tot_capi, "totale_importo": tot_imp, "capi": capi, "errori": errori, "brand": "pttorino"}
