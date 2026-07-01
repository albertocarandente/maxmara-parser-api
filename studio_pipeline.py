"""Studio AI — pipeline foto-prodotto per ETIENNE. OpenRouter per analisi + immagini."""
import os, json, time, base64, tempfile, shutil, uuid
from pathlib import Path

PROMPT_CERVELLO = """Sei un produttore e-commerce senior e copywriter per una boutique multi-brand di lusso (ETIENNE). Ricevi le foto grezze di UN capo e una stringa "brand / modello / variante". Restituisci UN SOLO oggetto JSON valido, senza altro testo/markdown/backtick, con questa struttura: { "analisi": { categoria, sottocategoria, genere(uomo|donna|unisex), colore_principale, colori_secondari[], materiali[], composizione_percentuali(o null), dettagli_distintivi[], vestibilita, stagione, registro_stile, brand, modello_variante }, "contesto_onmodel": { ambientazione, stagione_luce, mood }, "copy_it": { titolo, descrizione_breve, descrizione_lunga_intro, bullet[], consigli_stile, nota_taglia, composizione }, "copy_en": { titolo, descrizione_breve, descrizione_lunga_intro, bullet[], consigli_stile, nota_taglia, composizione }, "prompt_still_life": "", "prompt_on_model": ["","","",""] }.

MAPPA CONTESTO: costume/beachwear->spiaggia/scogliera mediterranea/bordo piscina, estate luce piena; giacca/blazer/completo->quartiere storico europeo/terrazza hotel lusso, golden hour, elegante; cappotto/capospalla->città invernale elegante, luce fredda soffusa; abito sera->location notturna sofisticata, cinematografico; camicia/casual->strada urbana/caffè, luce naturale diurna; maglione->esterni autunnali luce calda; pantalone/jeans->streetstyle urbano elevato; t-shirt/top->lifestyle luminoso; athleisure->urbano moderno/natura dinamica; accessori->still-life+dettaglio lifestyle.

REGOLE COPY: scrivi come copywriter madrelingua, NON come AI. Vietato frasi fatte, superlativi vuoti, triadi clichè, aggettivi generici. Varia la lunghezza delle frasi. Ogni frase dice qualcosa di concreto preso DALLE FOTO. Il copy converte, non elenca, posizionamento luxury resale. SEO: titolo = brand+modello+categoria+attributo; descrizione_breve = meta 150-160 caratteri; nei bullet metti in **grassetto** i concetti costruttivi/estetici. nota_taglia solo se cartellino/misure visibili, altrimenti "".

REGOLE PROMPT GENERAZIONE: priorità assoluta = fedeltà del prodotto. prompt_still_life = "Packshot e-commerce del capo esatto delle immagini di riferimento, fedeltà assoluta a colore/taglio/dettagli/stampa/minuteria/texture. Sfondo bianco puro #FFFFFF. Capo frontale, effetto manichino invisibile, perfettamente stirato, zero pieghe, niente gruccia/etichette/mani. Luce da studio morbida uniforme, colori fedeli, capo centrato e intero, margini ampi, nitidissimo, qualità catalogo, formato 4:5." Ogni prompt_on_model incorpora: "Modello/a {genere} ultrarealistico. Indossa il capo esatto delle immagini di riferimento. Ambientazione {ambientazione}, {stagione_luce}, mood {mood}. Estetica campagna fashion di fascia alta. Full-frame, profondità di campo ridotta, bokeh cinematografico. {inquadratura}, {angolo}, posa editoriale." I 4 prompt_on_model DEVONO essere diversi tra loro. Output: SOLO il JSON."""

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash")
IMAGE_MODEL = "google/gemini-2.5-flash-image"
IMAGES_DIR = os.path.join(tempfile.gettempdir(), "studio_images")


def _openrouter_client():
    import openai
    return openai.OpenAI(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1",
        default_headers={"HTTP-Referer": "https://etienne-studio.vercel.app", "X-Title": "ETIENNE Studio"})


def _analyze_product(photo_paths, label):
    """OpenRouter vision: analisi + copy."""
    client = _openrouter_client()
    content = []
    for p in photo_paths:
        with open(p, "rb") as f:
            b64 = base64.standard_b64encode(f.read()).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    content.append({"type": "text", "text": f"Etichetta: {label}\n\n{PROMPT_CERVELLO}"})
    resp = client.chat.completions.create(model=OPENROUTER_MODEL, max_tokens=4096, messages=[{"role": "user", "content": content}])
    raw = resp.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


def _generate_image(prompt, ref_b64, label="immagine"):
    """Genera una immagine via OpenRouter (Gemini Flash Image) con riferimento."""
    client = _openrouter_client()
    content = [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{ref_b64}"}},
        {"type": "text", "text": f"Genera una foto basata sull'immagine di riferimento. {prompt}"},
    ]
    resp = client.chat.completions.create(
        model=IMAGE_MODEL,
        messages=[{"role": "user", "content": content}],
        modalities=["image", "text"],
        max_tokens=4096,
    )
    # Extract generated images from response
    images_data = getattr(resp.choices[0].message, 'images', None)
    if images_data and len(images_data) > 0:
        img = images_data[0]
        if isinstance(img, dict) and 'image_url' in img:
            url = img['image_url'].get('url', '')
            if url.startswith('data:'):
                # Base64 data URL -> save to file
                header, b64data = url.split(',', 1)
                ext = 'png' if 'png' in header else 'jpg'
                os.makedirs(IMAGES_DIR, exist_ok=True)
                filename = f"{uuid.uuid4().hex}.{ext}"
                filepath = os.path.join(IMAGES_DIR, filename)
                with open(filepath, 'wb') as f:
                    f.write(base64.standard_b64decode(b64data))
                return f"/studio/images/{filename}"
            return url
    return None


def _generate_images(ref_path, analysis):
    """1 still-life + 4 on-model via OpenRouter image gen."""
    with open(ref_path, "rb") as f:
        ref_b64 = base64.standard_b64encode(f.read()).decode()
    results = []

    still = analysis.get("prompt_still_life", "")
    if still:
        try:
            url = _generate_image(still, ref_b64, "still_life")
            results.append({"tipo": "still_life", "url": url})
        except Exception as e:
            results.append({"tipo": "still_life", "url": None, "error": str(e)})

    for idx, prompt in enumerate(analysis.get("prompt_on_model", [])[:4]):
        if not prompt: continue
        try:
            url = _generate_image(prompt, ref_b64, f"on_model_{idx+1}")
            results.append({"tipo": f"on_model_{idx+1}", "url": url})
        except Exception as e:
            results.append({"tipo": f"on_model_{idx+1}", "url": None, "error": str(e)})
    return results


def process_item(photo_paths, label):
    """Pipeline completa: analisi + copy + immagini."""
    analysis = _analyze_product(photo_paths, label)
    immagini = _generate_images(photo_paths[0], analysis) if photo_paths else []
    return {"analisi": analysis.get("analisi", {}), "copy_it": analysis.get("copy_it", {}), "copy_en": analysis.get("copy_en", {}), "immagini": immagini}
