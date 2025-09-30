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
    os.path.join(BASE_DIR, "certs", "hom_cert_ciarama.crt"),
    os.path.join(BASE_DIR, "certs", "hom_key_ciarama.key")
)

@app.route('/test-cert')
def test_cert():
    try:
        with open(CERT[0], 'r') as f:
            cert_content = f.read(100)  # lê os primeiros 100 caracteres
        return jsonify({"message": "Certificado lido com sucesso", "preview": cert_content})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ===============================
# 📝 Mapeamento de códigos de score (resumido)
# ===============================
REASONS_SCORE_PF = {
    "R01": "Apresenta atraso no pagamento",
    "R29": "Pouco volume de pagamentos em dia"
}
REASONS_SCORE_PJ = {
    "R00": "Empresa com Score baixo risco",
    "R29": "Pouco volume de pagamentos em dia"
}

# ===============================
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
# Rotas da API
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
# ===========================fim consulta score================

# ===========================traduções==========================

def traduzir_probabilidade(valor):
    if not valor:
        return ""
    # Procura dois números na string
    match = re.search(r'BETWEEN_(\d+)_AND_(\d+)_PERCENT', valor)
    if match:
        inicio, fim = match.groups()
        return f"Entre {inicio}% e {fim}%"
    return valor  # caso não bata o padrão

def traduzir_faixa_credito(valor):
    if not valor:
        return ""
    # Procura dois números na string
    match = re.search(r'FROM_(\d+)_TO_(\d+)', valor)
    if match:
        inicio, fim = match.groups()
        # Formata com R$ e separador de milhar
        inicio_fmt = f"R$ {int(inicio):,}".replace(",", ".")
        fim_fmt = f"R$ {int(fim):,}".replace(",", ".")
        return f"{inicio_fmt} até {fim_fmt}"
    return valor

def traduzir_risco_credito(valor):
    mapa = {
        "VERY_LOW": "Muito baixo",
        "LOW": "Baixo",
        "MEDIUM": "Médio",
        "HIGH": "Alto",
        "VERY_HIGH": "Muito alto",
        "NOT_INFORMED": "Não informado"
    }
    return mapa.get(valor, valor)  # se não encontrar, retorna o original

fraudes_map = {
    "1": "2 ou mais CPFs possuem o mesmo nome",
    "2": "Data de nascimento informada na consulta é diferente da data de nascimento cadastrada na base Quod",
    "3": "Nos últimos 6 meses, mais de 4 pessoas possuem o mesmo endereço informado",
    "4": "No último ano, mais de 2 pessoas possuem o mesmo endereço informado na consulta",
    "5": "Nos últimos 6 meses, mais de 4 pessoas diferentes com status óbito possuem o mesmo endereço informado na consulta",
    "6": "No último ano, mais de 2 pessoas com status óbito possuem o mesmo endereço informado na consulta",
    "7": "O endereço informado na consulta é diferente do endereço cadastrado na base Quod",
    "8": "Nos últimos 6 meses, mais de 4 pessoas possuem o mesmo telefone informado na consulta",
    "9": "No último ano, mais de 2 pessoas possuem o mesmo telefone informado na consulta",
    "10": "Nos últimos 6 meses, mais de 4 pessoas com status de óbito possuem o mesmo número de telefone informado na consulta",
    "11": "No último ano, mais de 2 pessoas com status de óbito possuem o número de telefone informado na consulta",
    "12": "O número de telefone informado na consulta é diferente do número de telefone deste titular na base Quod",
    "13": "Pelo menos 8 instituições reportaram o nome completo do titular, na última semana",
    "14": "Pelo menos 8 instituições reportaram o nome completo do titular, no último mês",
    "15": "Pelo menos 8 instituições reportaram o nome completo do titular, nos últimos 6 meses",
    "16": "No último ano, nenhuma fonte reportou o nome completo deste titular",
    "17": "No último ano, até 1 instituição reportou o nome completo do titular igual ao cadastro na base Quod",
    "18": "O titular não possui CEP cadastrado na base Quod",
    "19": "Nos últimos 15 dias, nenhuma fonte reportou o mesmo CEP cadastrado na base Quod para este titular",
    "20": "Nos últimos 15 dias, nenhuma fonte reportou a data de nascimento deste titular",
    "21": "Nos últimos 7 meses, nenhuma fonte reportou a data de nascimento deste titular",
    "22": "Seis ou mais instituições enviaram a informação CEP do titular nos últimos 6 meses",
    "23": "Seis ou mais instituições enviaram a informação Cidade do titular nos últimos 7 dias",
    "24": "O CPF do titular não possui status ativo na Receita",
    "25": "Nos últimos 7 dias algumas fontes reportaram o telefone diferente do cadastrado na base Quod para o titular",
    "26": "Nos últimos 7 dias, nenhuma fonte reportou nome completo do titular diferente da base Quod",
    "27": "O titular solicitou descadastramento da base de dados de histórico de crédito",
    "28": "O titular está em processo de comunicação ou ainda não foi comunicado sobre a abertura do Cadastro Positivo",
    "29": "O titular possui relacionamento com 6 ou mais credores distintos",
    "30": "O titular possui relacionamento com 6 ou mais credores distintos nos últimos 5 meses",
    "31": "O titular efetuou o pagamento de parcelas em atraso e o valor pago foi pelo menos o dobro do valor devido, considerando todos os produtos contratados nos últimos 7 meses",
    "32": "O titular possui 10 ou mais contratos ativos nos últimos 30 dias",
    "33": "O titular contratou um novo produto de crédito nos últimos 4 meses",
    "34": "Nos últimos 5 anos, a soma de todas as contratações realizadas pelo titular é de R$150.000,00 ou mais",
    "35": "Nos últimos 8 meses, o titular pagou em dia, parcelas ou faturas de R$3.000,00 ou mais, considerando a soma de todos os produtos contratados",
    "36": "O CPF não possui data de nascimento cadastrada na base Quod",
    "37": "-",
    "38": "Os contratos de produtos de crédito do titular possuem, em média, menos de 4 anos",
    "39": "O titular possui pelo menos uma negativação ativa por 45 dias ou mais",
    "40": "O titular possui 13 ou mais parcelas contratadas de produtos de crédito de maior risco, no último ano",
    "41": "O titular possui 24 ou mais parcelas de produto de crédito de maior risco contratadas nos últimos 6 meses",
    "42": "O titular não possui contratos ativos de produtos de crédito com mais de 3 anos",
    "43": "O titular possui 3 parcelas/faturas ou mais com pagamento parcial, o que pode indicar atraso nos pagamentos",
    "44": "O titular possui pelo menos 15 dias de atraso no pagamento dos produtos do tipo parcelados, para os contratos realizados nos últimos 4 meses",
    "45": "O titular possui contratos de pelo menos 6 produtos nos últimos 5 meses",
    "46": "O titular possui pelo menos 2 parcelas vencidas de produtos de crédito de maior risco nos últimos 6 meses",
    "47": "O titular contratou mais de 6 produtos do tipo Parcelados nos últimos 5 meses",
    "48": "O titular contratou mais de 7 produtos do tipo Parcelados nos últimos 7 meses",
    "49": "O titular não possui parcelas e faturas pagas de produtos contratados nos últimos 8 meses",
    "50": "O titular foi consultado por pelo menos 3 credores diferentes no último ano",
    "51": "O titular foi consultado mais de 5 vezes nos últimos 6 meses",
    "52": "O titular foi consultado mais de 5 vezes no último ano"
}


def traduzir_alertas_fraude(codigos):
    resultado_traduzido = []
    for codigo in codigos:
        descricao = fraudes_map.get(str(codigo), "Código desconhecido")
        resultado_traduzido.append(f"{codigo}: {descricao}")
    return resultado_traduzido

def traduzir_situacao_governo(status):
    mapeamento = {
        "ACTIVE": "Ativo",
        "INACTIVE": "Inativo",
        "SUSPENDED": "Suspenso",
        "CANCELLED": "Cancelado",
        "NULL": "Não informado"
    }
    return mapeamento.get(status, status)  # se não encontrar, devolve o original

def traduzir_genero(genero):
    mapeamento = {
        "MALE": "Masculino",
        "FEMALE": "Feminino",
        "NOT_INFORMED": "Não informado",
        "OTHER": "Outro"
    }
    return mapeamento.get(genero, genero)  # devolve original se não achar

def formatar_moeda(valor):
    if valor is None:
        return None
    try:
        return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return None


def traduzir_despesa_estimada(valor):
    if not valor:
        return "Não informado"

    valor = valor.upper()

    if "UP_TO_" in valor:
        limite = valor.replace("UP_TO_", "")
        return f"Até R$ {limite.replace('_', '.')}"

    if "BETWEEN_" in valor:
        partes = valor.replace("BETWEEN_", "").split("_AND_")
        if len(partes) == 2:
            return f"De R$ {partes[0]} a R$ {partes[1]}"

    if "ABOVE_" in valor:
        limite = valor.replace("ABOVE_", "")
        return f"Acima de R$ {limite}"
    
    if "FROM_" in valor:
        partes = valor.replace("FROM_", "").split("_TO_")
        if len(partes) == 2:
            return f"De R$ {partes[0]} a R$ {partes[1]}"
        

    if valor == "NOT_INFORMED":
        return "Não informado"

    return valor

def traduzir_nivel_risco(valor):
    if not valor:
        return "Não informado"

    mapa = {
        "LOW": "Baixo",
        "MEDIUM": "Médio",
        "HIGH": "Alto",
        "VERY_HIGH": "Muito Alto",
        "NOT_INFORMED": "Não informado"
    }

    return mapa.get(valor.upper(), valor)


# ========================fim traduções ========================

def filtrar_e_renomear_json(data):
    resultado = {}

    # =======================
    # DADOS PESSOAIS
    # =======================
    person = data.get("data", {}).get("personData", {})
    resultado["nome"] = person.get("name")
    resultado["nome_mae"] = person.get("motherName")                # PF
    resultado["tipo_pessoa"] = person.get("personType")             # PF ou PJ
    resultado["idade"] = person.get("age")                          # PF
    resultado["email"] = person.get("email")
    resultado["telefone"] = person.get("currentPhoneNumber")
    resultado["genero"] = traduzir_genero(person.get("gender"))     # PF
    resultado["situacao_governo"] = traduzir_situacao_governo(person.get("governmentStatus"))
    resultado["data_nascimento"] = person.get("foundationBirthDate")  # nascimento PF, fundação PJ

    # Histórico de e-mails antigos (PF)
    email_history = person.get("emailHistory", [])
    resultado["historico_emails"] = [
        {
            "email": e.get("email"),
            "ultima_data_contato": e.get("lastContactDate")
        }
        for e in email_history
    ]

    # Telefones antigos (PF e PJ)
    phone_history = person.get("phoneNumbersHistory", [])
    resultado["historico_telefones"] = [
        {
            "telefone": p.get("phoneNumber"),
            "ultima_data_contato": p.get("lastContactDate")
        }
        for p in phone_history
    ]

    # Endereço atual
    endereco_atual = person.get("currentStreetAddress", {})
    resultado["endereco_atual"] = {
        "rua": endereco_atual.get("streetName"),
        "numero": endereco_atual.get("streetNumber"),
        "complemento": endereco_atual.get("complement"),
        "bairro": endereco_atual.get("neighbour"),
        "cidade": endereco_atual.get("city"),
        "estado": endereco_atual.get("state"),
        "cep": endereco_atual.get("zipCode")
    }

    # Histórico de endereços
    enderecos_antigos = person.get("streetAddressesHistory", [])
    resultado["historico_enderecos"] = [
        {
            "rua": e.get("streetName"),
            "numero": e.get("streetNumber"),
            "complemento": e.get("complement"),
            "bairro": e.get("neighbour"),
            "cidade": e.get("city"),
            "estado": e.get("state"),
            "cep": e.get("zipCode")
        }
        for e in enderecos_antigos
    ]

    # =======================
    # DADOS PRESUMIDOS
    # =======================
    presumed = data.get("data", {}).get("presumedData", {})
    resultado["renda_presumida"] = formatar_moeda(presumed.get("presumedIncome"))  # PF
    resultado["probabilidade_pagamento"] = traduzir_probabilidade(
        presumed.get("paymentProbability")  # PF
    )
    resultado["despesa_estimada"] = traduzir_despesa_estimada(
    presumed.get("estimatedExpense")
    )# PF

    # =======================
    # SCORE DE CRÉDITO
    # =======================
    score = data.get("data", {}).get("scoreDetails", {})
    resultado["pontuacao"] = score.get("score")
    resultado["nivel_risco"] = traduzir_nivel_risco(score.get("riskLevel"))


    # =======================
    # ANÁLISE DE CRÉDITO
    # =======================
    credit = data.get("data", {}).get("creditLimit", {})
    resultado["faixa_limite_credito"] = traduzir_faixa_credito(credit.get("limitRange"))  # PF
    resultado["descricao_risco_credito"] = traduzir_risco_credito(credit.get("riskDescription"))  # PF
    resultado["classificacao_risco_credito"] = credit.get("riskRanking")  # PF

    # =======================
    # PREVENÇÃO DE FRAUDES
    # =======================
    fraud = data.get("data", {}).get("fraudPrevention", {})
    codigos = [a.get("code") for a in fraud.get("alerts", [])]  # PF
    resultado["alertas_risco_fraude"] = traduzir_alertas_fraude(codigos)
    resultado["score_fraude"] = fraud.get("score")  # PF

    # =======================
    # ANÁLISE DE PONTUALIDADE
    # =======================
    pont = data.get("data", {}).get("businessAnalytics", {})
    pagamento = pont.get("paymentPunctuality", [])  # PF
    resultado["percentual_pagamento"] = [a.get("percentage") for a in pagamento]
    resultado["classificacao_pagamento"] = [traduzir_nivel_risco(a.get("classification")) for a in pagamento]


    # =======================
    # PENDÊNCIAS FINANCEIRAS
    # =======================
    pend = data.get("data", {}).get("financialPendencies", {})
    resultado["pendencias_quantidade"] = pend.get("quantity")
    resultado["pendencias_valor_total"] = formatar_moeda(pend.get("totalValue"))
    delinquencias = pend.get("reportedDelinquencies", [])
    resultado["pendencias_detalhes"] = [
        {
            "data_disponibilidade": d.get("availabilityDate"),
            "data_ocorrencia": d.get("occurrenceDate"),
            "natureza_operacao": d.get("operationNature"),
            "empresa_credora_documento": d.get("creditorCompanyDocument"),
            "empresa_credora_nome": d.get("creditorCompanyName"),
            "localizacao": d.get("location"),
            "tipo_participante": d.get("participantType"),
            "valor": d.get("value"),
        }
        for d in delinquencias
    ]

    # =======================
    # REGISTROS DE CONSULTA
    # =======================
    passage = data.get("data", {}).get("passageRecord", {})
    resultado["consultas_12_meses"] = passage.get("quantityOfQueriesLastTwelveMonths")

    consultas_agrupadas = passage.get("groupedQueries", [])
    resultado["consultas_agrupadas"] = [
        {
            "periodo": g.get("period"),
            "quantidade": g.get("quantity"),
        }
        for g in consultas_agrupadas
    ]

    consultas_recentes = passage.get("recentDetails", [])
    resultado["consultas_recentes"] = [
        {
            "data": r.get("date"),
            "quantidade": r.get("quantity"),
            "segmento": r.get("segment"),
            "empresa_nome": r.get("companyName"),
            "empresa_numero": r.get("companyNumber"),
        }
        for r in consultas_recentes
    ]

    return resultado


# =============================== filtrar e renomear pj ===============================

def filtrar_e_renomear_json_pj(data):
    resultado = {}

    # ========================
    # DADOS DE PESSOA JURÍDICA
    # ========================
    person = data.get("data", {}).get("personData", {})
    resultado["razao_social"] = person.get("name")
    resultado["nome_fantasia"] = person.get("fantasyName")
    resultado["tipo_pessoa"] = person.get("personType")
    resultado["situacao_governo"] = traduzir_situacao_governo(person.get("governmentStatus"))
    resultado["natureza_juridica"] = person.get("legalNature")
    resultado["quantidade_filiais"] = person.get("branchCount")
    resultado["data_fundacao"] = person.get("foundationBirthDate")
    resultado["email"] = person.get("email")
    resultado["telefone"] = person.get("currentPhoneNumber")

    # Histórico de telefones
    phone_history = person.get("phoneNumbersHistory", [])
    resultado["historico_telefones"] = [
        {
            "telefone": p.get("phoneNumber"),
            "ultima_data_contato": p.get("lastContactDate")
        } for p in phone_history
    ]

    # Endereço atual
    endereco_atual = person.get("currentStreetAddress", {})
    resultado["endereco_atual"] = {
        "rua": endereco_atual.get("streetName"),
        "numero": endereco_atual.get("streetNumber"),
        "complemento": endereco_atual.get("complement"),
        "bairro": endereco_atual.get("neighbour"),
        "cidade": endereco_atual.get("city"),
        "estado": endereco_atual.get("state"),
        "cep": endereco_atual.get("zipCode")
    }

    # Histórico de endereços
    enderecos_antigos = person.get("streetAddressesHistory", [])
    resultado["historico_enderecos"] = [
        {
            "rua": e.get("streetName"),
            "numero": e.get("streetNumber"),
            "complemento": e.get("complement"),
            "bairro": e.get("neighbour"),
            "cidade": e.get("city"),
            "estado": e.get("state"),
            "cep": e.get("zipCode")
        } for e in enderecos_antigos
    ]

    # Relações com outras empresas
    company_relations = person.get("companyRelations", [])
    resultado["relacoes_empresas"] = [
        {
            "documento": c.get("companyDocument"),
            "nome": c.get("legalName"),
            "situacao_governo": c.get("governmentStatus"),
            "percentual_participacao": c.get("participationPercentage"),
            "data_inclusao_relacao": c.get("relationInclusionDate"),
            "ultima_atualizacao": c.get("lastUpdate")
        } for c in company_relations
    ]
    resultado["quantidade_relacoes_empresas"] = person.get("companyRelationsQuantity")

    # Conselho de acionistas
    board = person.get("shareholderBoard", {})
    resultado["total_acionistas"] = board.get("totalShareholders")
    resultado["capital_social_total"] = board.get("shareCapital")
    resultado["total_representantes"] = board.get("totalRepresentatives")

    admin_shares = board.get("administrativeShareholders", [])
    resultado["acionistas_administrativos"] = [
        {
            "documento": s.get("document"),
            "nome": s.get("name"),
            "status_receita": s.get("federalRevenueStatus"),
            "registros_negativos": s.get("hasNegativeRecords"),
            "valor_participacao": s.get("participationValue"),
            "percentual_participacao": s.get("participationPercentage"),
            "papel": s.get("shareholderRole"),
            "poderes_assinatura": s.get("shareholderSigns"),
            "ultima_atualizacao": s.get("lastUpdateDate")
        } for s in admin_shares
    ]

    # Indicador de atividade
    activity = data.get("data", {}).get("activityIndicator", {})
    resultado["pontuacao_atividade"] = activity.get("activityScore")
    resultado["nivel_atividade"] = activity.get("activityLevel")

    # ========================
    # DADOS PRESUMIDOS
    # ========================
    presumed = data.get("data", {}).get("presumedData", {})
    resultado["renda_presumida"] = formatar_moeda(presumed.get("presumedIncome"))
    resultado["despesa_estimada"] = presumed.get("estimatedExpense")
    resultado["faturamento_anual"] = presumed.get("annualRevenue")
    resultado["porte_empresa"] = presumed.get("companySize")

    # ========================
    # SCORE DE CRÉDITO
    # ========================
    score = data.get("data", {}).get("scoreDetails", {})
    resultado["pontuacao"] = score.get("score")
    resultado["nivel_risco"] = traduzir_nivel_risco(score.get("riskLevel"))
    resultado["razoes_score"] = [r.get("code") for r in score.get("reasons", [])]

    # ========================
    # PROPOSTA DE NEGÓCIO
    # ========================
    proposal = data.get("data", {}).get("businessProposal", {})
    resultado["recomendacao_negocio"] = proposal.get("recommendation")
    resultado["razoes_recomendacao"] = proposal.get("reasons", [])

    # ========================
    # ANÁLISE DE DÍVIDAS
    # ========================
    debts = data.get("data", {}).get("businessAnalytics", {}).get("debt", [])
    resultado["dividas"] = [
        {
            "indicador": d.get("indicator"),
            "valor": d.get("value"),
            "risco": d.get("risk"),
            "legenda": d.get("legend"),
            "conceito": d.get("concept")
        } for d in debts
    ]

    # ========================
    # PENDÊNCIAS FINANCEIRAS
    # ========================
    pend = data.get("data", {}).get("financialPendencies", {})
    resultado["pendencias_quantidade"] = pend.get("quantity")
    resultado["pendencias_valor_total"] = formatar_moeda(pend.get("totalValue"))

    delinquencias = pend.get("reportedDelinquencies", [])
    resultado["pendencias_detalhes"] = [
        {
            "data_disponibilidade": d.get("availabilityDate"),
            "data_ocorrencia": d.get("occurrenceDate"),
            "natureza_operacao": d.get("operationNature"),
            "empresa_credora_documento": d.get("creditorCompanyDocument"),
            "empresa_credora_nome": d.get("creditorCompanyName"),
            "localizacao": d.get("location"),
            "tipo_participante": d.get("participantType"),
            "valor": d.get("value")
        } for d in delinquencias
    ]

    # ========================
    # REGISTROS DE CONSULTA
    # ========================
    passage = data.get("data", {}).get("passageRecord", {})
    resultado["consultas_12_meses"] = passage.get("quantityOfQueriesLastTwelveMonths")
    resultado["consultas_agrupadas"] = [
        {
            "periodo": g.get("period"),
            "quantidade": g.get("quantity")
        } for g in passage.get("groupedQueries", [])
    ]
    resultado["consultas_recentes"] = [
        {
            "data": r.get("date"),
            "quantidade": r.get("quantity"),
            "segmento": r.get("segment"),
            "empresa_nome": r.get("companyName"),
            "empresa_numero": r.get("companyNumber")
        } for r in passage.get("recentDetails", [])
    ]

    # ========================
    # LIMITE DE CRÉDITO
    # ========================
    credit = data.get("data", {}).get("creditLimit", {})
    resultado["data_referencia_credito"] = credit.get("referenceDate")
    resultado["credito_risco"] = credit.get("risky", {})
    resultado["credito_moderado"] = credit.get("moderate", {})
    resultado["credito_conservador"] = credit.get("conservative", {})


    # ========================
    # ANÁLISE DE PONTUALIDADE
    # ========================
    pont = data.get("data", {}).get("businessAnalytics", {})
    pagamento = pont.get("paymentPunctuality", [])
    resultado["percentual_pagamento"] = [a.get("percentage") for a in pagamento]
    resultado["classificacao_pagamento"] = [traduzir_nivel_risco(a.get("classification")) for a in pagamento]

    return resultado

# =============================== fim filtrar e renomear pj ===============================

@app.route("/consulta/completa", methods=["POST"])
def consulta_completa():
    body = request.json
    documento = body.get("documento")

    valido, doc, tipo = validar_documento(documento)
    if not valido:
        return jsonify({"error": "Documento inválido"}), 400

    token = gerar_token()
    if not token:
        return jsonify({"error": "Falha ao gerar token"}), 500

    # Consulta API
    data = consulta_api(token, "credit-scores-reports", doc)

    # Escolhe a função certa
    if tipo == "PF":
        data_custom = filtrar_e_renomear_json(data)
    elif tipo == "PJ":
        data_custom = filtrar_e_renomear_json_pj(data)
    else:
        return jsonify({"error": "Tipo de pessoa não identificado"}), 400

    #return jsonify(data_custom)
    return Response( json.dumps(data_custom, ensure_ascii=False, indent=2), mimetype="application/json" )


# ===============================
# Iniciar API
# ===============================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)