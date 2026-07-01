"""Studio AI — pipeline foto-prodotto per ETIENNE."""
import os, json, time, base64, tempfile, shutil
from pathlib import Path

PROMPT_CERVELLO = """Sei un produttore e-commerce senior e copywriter per una boutique multi-brand di lusso (ETIENNE). Ricevi le foto grezze di UN capo e una stringa "brand / modello / variante". Restituisci UN SOLO oggetto JSON valido, senza altro testo/markdown/backtick, con questa struttura: { "analisi": { categoria, sottocategoria, genere(uomo|donna|unisex), colore_principale, colori_secondari[], materiali[], composizione_percentuali(o null), dettagli_distintivi[], vestibilita, stagione, registro_stile, brand, modello_variante }, "contesto_onmodel": { ambientazione, stagione_luce, mood }, "copy_it": { titolo, descrizione_breve, descrizione_lunga_intro, bullet[], consigli_stile, nota_taglia, composizione }, "copy_en": { titolo, descrizione_breve, descrizione_lunga_intro, bullet[], consigli_stile, nota_taglia, composizione }, "prompt_still_life": "", "prompt_on_model": ["","","",""] }.

MAPPA CONTESTO: costume/beachwear->spiaggia/scogliera mediterranea/bordo piscina, estate luce piena; giacca/blazer/completo->quartiere storico europeo/terrazza hotel lusso, golden hour, elegante; cappotto/capospalla->città invernale elegante, luce fredda soffusa; abito sera->location notturna sofisticata, cinematografico; camicia/casual->strada urbana/caffè, luce naturale diurna; maglione->esterni autunnali luce calda; pantalone/jeans->streetstyle urbano elevato; t-shirt/top->lifestyle luminoso; athleisure->urbano moderno/natura dinamica; accessori->still-life+dettaglio lifestyle.

REGOLE COPY: scrivi come copywriter madrelingua, NON come AI. Vietato frasi fatte, superlativi vuoti, triadi clichè, aggettivi generici. Varia la lunghezza delle frasi. Ogni frase dice qualcosa di concreto preso DALLE FOTO. Il copy converte, non elenca, posizionamento luxury resale. SEO: titolo = brand+modello+categoria+attributo; descrizione_breve = meta 150-160 caratteri; nei bullet metti in **grassetto** i concetti costruttivi/estetici. nota_taglia solo se cartellino/misure visibili, altrimenti "".

REGOLE PROMPT GENERAZIONE: priorità assoluta = fedeltà del prodotto. prompt_still_life = "Packshot e-commerce del capo esatto delle immagini di riferimento, fedeltà assoluta a colore/taglio/dettagli/stampa/minuteria/texture. Sfondo bianco puro #FFFFFF. Capo frontale, effetto manichino invisibile, perfettamente stirato, zero pieghe, niente gruccia/etichette/mani. Luce da studio morbida uniforme, colori fedeli, capo centrato e intero, margini ampi, nitidissimo, qualità catalogo, formato 4:5." Ogni prompt_on_model incorpora: "Modello/a {genere} ultrarealistico. Indossa il capo esatto delle immagini di riferimento. Ambientazione {ambientazione}, {stagione_luce}, mood {mood}. Estetica campagna fashion di fascia alta. Full-frame, profondità di campo ridotta, bokeh cinematografico. {inquadratura}, {angolo}, posa editoriale." I 4 prompt_on_model DEVONO essere diversi tra loro. Output: SOLO il JSON."""

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash")
HIGGSFIELD_API_KEY = os.environ.get("HIGGSFIELD_API_KEY", "")
HIGGSFIELD_BASE = "https://platform.higgsfield.ai"


def _analyze_product(photo_paths, label):
    import openai
    client = openai.OpenAI(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1",
        default_headers={"HTTP-Referer": "https://etienne-studio.vercel.app", "X-Title": "ETIENNE Studio"})
    content = []
    for p in photo_paths:
        with open(p, "rb") as f:
            b64 = base64.standard_b64encode(f.read()).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    content.append({"type": "text", "text": f"Etichetta: {label}\n\n{PROMPT_CERVELLO}"})
    resp = client.chat.completions.create(model=OPENROUTER_MODEL, max_tokens=4096, messages=[{"role": "user", "content": content}])
    raw = resp.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


def _higgsfield_generate(model, prompt, ref_b64, aspect_ratio="4:5"):
    import requests as req
    body = {"prompt": prompt, "aspect_ratio": aspect_ratio, "image": f"data:image/jpeg;base64,{ref_b64}"}
    resp = req.post(f"{HIGGSFIELD_BASE}/{model}", headers={
        "Authorization": f"Key {HIGGSFIELD_API_KEY}", "Content-Type": "application/json", "Accept": "application/json"
    }, json=body, timeout=30)
    resp.raise_for_status()
    d = resp.json()
    return d.get("request_id") or d.get("job_id") or d.get("id")


def _higgsfield_poll(job_id, timeout_seconds=120):
    import requests as req
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        resp = req.get(f"{HIGGSFIELD_BASE}/requests/{job_id}/status", headers={"Authorization": f"Key {HIGGSFIELD_API_KEY}"}, timeout=15)
        d = resp.json()
        if d.get("status") in ("completed", "succeeded", "done"): return d
        if d.get("status") in ("failed", "error"): raise RuntimeError(f"Higgsfield job fallito: {d}")
        time.sleep(3)
    raise TimeoutError(f"Higgsfield job {job_id} timeout")


def _generate_images(ref_path, analysis):
    with open(ref_path, "rb") as f:
        ref_b64 = base64.standard_b64encode(f.read()).decode()
    results = []
    model = "higgsfield-ai/nano-banana-pro"

    still = analysis.get("prompt_still_life", "")
    if still:
        try:
            jid = _higgsfield_generate(model, still, ref_b64)
            jr = _higgsfield_poll(jid)
            imgs = jr.get("images") or [{}]
            results.append({"tipo": "still_life", "url": imgs[0].get("url"), "higgsfield_job_id": jid})
        except Exception as e:
            results.append({"tipo": "still_life", "url": None, "error": str(e)})

    for idx, prompt in enumerate(analysis.get("prompt_on_model", [])[:4]):
        if not prompt: continue
        try:
            jid = _higgsfield_generate(model, prompt, ref_b64)
            jr = _higgsfield_poll(jid)
            imgs = jr.get("images") or [{}]
            results.append({"tipo": f"on_model_{idx+1}", "url": imgs[0].get("url"), "higgsfield_job_id": jid})
        except Exception as e:
            results.append({"tipo": f"on_model_{idx+1}", "url": None, "error": str(e)})
    return results


def process_item(photo_paths, label):
    analysis = _analyze_product(photo_paths, label)
    immagini = _generate_images(photo_paths[0], analysis) if photo_paths else []
    return {"analisi": analysis.get("analisi", {}), "copy_it": analysis.get("copy_it", {}), "copy_en": analysis.get("copy_en", {}), "immagini": immagini}
