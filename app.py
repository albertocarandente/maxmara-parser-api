"""Microservizio parser MaxMara — Flask API chiamata da Vercel."""
from flask import Flask, request, jsonify
from maxmara_order_parser import parse_order
import tempfile, os

app = Flask(__name__)

@app.route('/health')
def health():
    return jsonify({"status": "ok"})

@app.route('/parse', methods=['POST'])
def parse():
    if 'file' not in request.files:
        return jsonify({"error": "Nessun file ricevuto"}), 400
    file = request.files['file']
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
    try:
        file.save(tmp.name)
        tmp.close()
        result = parse_order(tmp.name)
        return jsonify(result)
    finally:
        os.unlink(tmp.name)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
