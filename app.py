from flask import Flask, request, jsonify, abort
from flask_cors import CORS
import requests
import uuid
from validate_docbr import CPF, CNPJ
import re
from flask import Response
import json
import logging
import os


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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CERT = (
    os.path.join(BASE_DIR, "certs", "hom_cert_ciamara.crt"),
    os.path.join(BASE_DIR, "certs", "hom_key_ciamara.key")
)

#===============================
# 1️⃣ Gerar Token de Acesso
# ===============================
def gerar_token():
    url_token = "https://sts.rdhi.com.br/api/oauth/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": "89b42b5c-be89-402a-8b8f-479004e61f77",
        "client_secret": "1c4911fa-042f-44a1-9d52-144d6ebb7c38",
        "scope": "iaas-riskradar.read"
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    response = requests.post(url_token, data=payload, headers=headers, cert=CERT)

    if response.status_code == 200:
        return response.json()["access_token"]
    return None

# ===============================
# Validar CPF ou CNPJ
# ===============================
def validar_documento(doc):
    doc = ''.join(filter(str.isdigit, doc))
    if len(doc) == 11:
        return CPF().validate(doc), doc, "PF"
    elif len(doc) == 14:
        return CNPJ().validate(doc), doc, "PJ"
    return False, doc, None

# ===============================
# Função genérica de consulta
# ===============================
def consulta_api(token, endpoint, subject_document):
    url = f"https://riskradar-iaas.api.hom.itau.com/risk-radar/v1/{endpoint}"
    correlation_id = str(uuid.uuid4())
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "x-itau-correlationID": correlation_id
    }
    payload = {"requesterDocument": "12902385000161", "subjectDocument": subject_document}
    response = requests.post(url, headers=headers, json=payload, cert=CERT)

    if response.status_code == 200:
        return response.json()
    else:
        return {"success": False, "error": response.status_code, "message": response.text}

# ===============================
# Rotas da API - Radar de Risco Itaú
# ===============================
@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "message": "API Itaú Consulta de Crédito ativa"}), 200

RISK_LEVELS = {
    "VERY_LOW": "Muito Baixo",
    "LOW": "Baixo",
    "MEDIUM": "Médio",
    "HIGH": "Alto",
    "VERY_HIGH": "Muito Alto",
    "UNDETERMINED": "Indeterminado"
}

@app.route("/consulta/score", methods=["POST"])
def consulta_score():
    body = request.json
    documento = body.get("documento")

    valido, doc, tipo = validar_documento(documento)
    if not valido:
        return jsonify({"error": "Documento inválido"}), 400

    token = gerar_token()
    if not token:
        return jsonify({"error": "Falha ao gerar token"}), 500

    data = consulta_api(token, "credit-scores", doc).get("data", [])
    if isinstance(data, dict):
        data = [data]

    for item in data:
        if isinstance(item, dict):
            # 🔹 traduz apenas o campo riskLevel
            risk = item.get("riskLevel")
            if risk:
                item["riskLevel"] = RISK_LEVELS.get(risk, risk)

            # 🔹 mantém sua lógica original dos motivos
            item["reasons_description"] = [
                REASONS_SCORE_PF.get(r.get("code"), r) if tipo == "PF" else REASONS_SCORE_PJ.get(r.get("code"), r)
                for r in item.get("reasons", [])
            ]

    return jsonify(data)





if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
 