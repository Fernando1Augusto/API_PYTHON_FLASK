from flask import Flask, request, jsonify, abort
from flask_cors import CORS


app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({"message": "API rodando com Flask no Docker!"})

@app.route('/health')
def health():
    return jsonify({"status": "ok"})

# ===============================
# 🔑 Certificado único (global)
# ===============================
CERT = (
    "./certs/hom_cert_ciamara.crt",
    "./certs/hom_key_ciamara.key"
)







if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
 