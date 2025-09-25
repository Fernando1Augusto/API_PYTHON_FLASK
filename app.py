from flask import Flask, request, jsonify, abort
from flask_cors import CORS
import requests
import uuid
from validate_docbr import CPF, CNPJ
import re
from flask import Response
import json
import logging

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
 