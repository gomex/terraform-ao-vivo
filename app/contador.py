#!/usr/bin/env python3
"""Contador de máquinas: mostra, ao vivo, as instâncias do Auto Scaling Group
em que esta máquina está rodando e todo o caminho da requisição até ela
(Route53 -> ACM -> ALB -> Target Group -> ASG -> EC2).
Só usa a biblioteca padrão + boto3."""

import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3

PORTA = int(os.environ.get("PORT", "80"))
VERSAO = os.environ.get("APP_VERSION", "dev")
REPOSITORIO = os.environ.get("REPOSITORIO", "")  # vira QR Code na página para a plateia mandar PR
CACHE_GRUPO = 3  # máquinas mudam rápido; evita estourar a API com a plateia dando refresh
CACHE_INFRA = 30  # Route53, ACM, ALB e VPC quase não mudam (e a API do Route53 é limitada)
IMDS = "http://169.254.169.254/latest"


def metadado(caminho):
    """Lê um metadado da instância usando IMDSv2."""
    pedido_token = urllib.request.Request(
        f"{IMDS}/api/token",
        method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "300"},
    )
    token = urllib.request.urlopen(pedido_token, timeout=2).read().decode()
    pedido = urllib.request.Request(
        f"{IMDS}/meta-data/{caminho}",
        headers={"X-aws-ec2-metadata-token": token},
    )
    return urllib.request.urlopen(pedido, timeout=2).read().decode()


def seguro(funcao):
    """Roda uma consulta; se der erro, devolve o erro para aparecer só naquele cartão."""
    try:
        return funcao()
    except Exception as erro:
        return {"erro": f"{type(erro).__name__}: {erro}"}


def iso(data):
    return data.isoformat() if data else None


class Contador:
    def __init__(self):
        self.instancia = metadado("instance-id")
        self.az = metadado("placement/availability-zone")
        regiao = metadado("placement/region")
        self.autoscaling = boto3.client("autoscaling", region_name=regiao)
        self.ec2 = boto3.client("ec2", region_name=regiao)
        self.elb = boto3.client("elbv2", region_name=regiao)
        self.acm = boto3.client("acm", region_name=regiao)
        self.route53 = boto3.client("route53", region_name=regiao)  # serviço global
        self.nome_asg = None
        self.grupo = {}  # resposta crua do ASG, usada para descobrir o resto da infra
        self.target_groups = []
        self.trava = threading.Lock()
        self.cache = {}

    def _cacheado(self, chave, validade, funcao):
        valor, quando = self.cache.get(chave, (None, 0.0))
        if valor is None or time.monotonic() - quando > validade:
            valor = seguro(funcao)
            self.cache[chave] = (valor, time.monotonic())
        return valor

    def status(self):
        with self.trava:
            grupo = self._cacheado("grupo", CACHE_GRUPO, self._consultar_grupo)
            infra = {}
            if "erro" not in grupo:
                infra = self._cacheado("infra", CACHE_INFRA, self._consultar_infra)
        return {**grupo, "infra": infra, "servido_por": self.instancia, "az": self.az, "versao": VERSAO,
                "repositorio": REPOSITORIO}

    # ------------------------------------------------------------------ ASG + EC2

    def _consultar_grupo(self):
        if not self.nome_asg:
            encontradas = self.autoscaling.describe_auto_scaling_instances(
                InstanceIds=[self.instancia]
            )["AutoScalingInstances"]
            if not encontradas:
                return {"erro": "esta máquina ainda não entrou no Auto Scaling Group"}
            self.nome_asg = encontradas[0]["AutoScalingGroupName"]

        grupo = self.autoscaling.describe_auto_scaling_groups(
            AutoScalingGroupNames=[self.nome_asg]
        )["AutoScalingGroups"][0]
        self.grupo = grupo
        alvos = [t["Identifier"] for t in grupo.get("TrafficSources", []) if t.get("Type") == "elbv2"]
        self.target_groups = list(dict.fromkeys(alvos + grupo.get("TargetGroupARNs", [])))

        detalhes = {}
        ids = [i["InstanceId"] for i in grupo["Instances"]]
        if ids:
            for reserva in self.ec2.describe_instances(InstanceIds=ids)["Reservations"]:
                for instancia in reserva["Instances"]:
                    detalhes[instancia["InstanceId"]] = instancia

        saude_no_lb = {}
        if self.target_groups:
            for alvo in self.elb.describe_target_health(
                TargetGroupArn=self.target_groups[0]
            )["TargetHealthDescriptions"]:
                saude_no_lb[alvo["Target"]["Id"]] = alvo["TargetHealth"]["State"]

        maquinas = []
        for i in grupo["Instances"]:
            d = detalhes.get(i["InstanceId"], {})
            maquinas.append({
                "id": i["InstanceId"],
                "az": i["AvailabilityZone"],
                "estado": i["LifecycleState"],
                "saude": i["HealthStatus"],
                "saude_lb": saude_no_lb.get(i["InstanceId"], "-"),
                "tipo": i.get("InstanceType"),
                "ip": d.get("PrivateIpAddress"),
                "ami": d.get("ImageId"),
                "lancada_em": iso(d.get("LaunchTime")),
            })
        maquinas.sort(key=lambda m: m["lancada_em"] or "")

        template = grupo.get("LaunchTemplate", {})
        return {
            "asg": {
                "nome": self.nome_asg,
                "desejado": grupo["DesiredCapacity"],
                "minimo": grupo["MinSize"],
                "maximo": grupo["MaxSize"],
                "health_check": grupo["HealthCheckType"],
                "zonas": grupo["AvailabilityZones"],
                "launch_template": f'{template.get("LaunchTemplateName", "-")} (v{template.get("Version", "?")})',
                "atualizacao": seguro(self._atualizacao),
            },
            "em_servico": sum(m["estado"] == "InService" for m in maquinas),
            "maquinas": maquinas,
        }

    def _atualizacao(self):
        """Último instance refresh (troca de AMI) do grupo."""
        refreshes = self.autoscaling.describe_instance_refreshes(
            AutoScalingGroupName=self.nome_asg, MaxRecords=1
        )["InstanceRefreshes"]
        if not refreshes:
            return None
        r = refreshes[0]
        return {
            "status": r["Status"],
            "progresso": r.get("PercentageComplete", 0),
            "inicio": iso(r.get("StartTime")),
            "fim": iso(r.get("EndTime")),
        }

    # ------------------------------------------------ Route53, ACM, ALB, TG, VPC

    def _consultar_infra(self):
        infra = {"vpc": seguro(self._vpc), "route53": seguro(self._route53)}
        if not self.target_groups:
            return infra

        tg = self.elb.describe_target_groups(TargetGroupArns=[self.target_groups[0]])["TargetGroups"][0]
        infra["target_group"] = {
            "nome": tg["TargetGroupName"],
            "protocolo": f'{tg["Protocol"]}:{tg["Port"]}',
            "health_check": f'{tg.get("HealthCheckPath", "-")} a cada {tg["HealthCheckIntervalSeconds"]}s',
        }
        if not tg["LoadBalancerArns"]:
            return infra

        lb_arn = tg["LoadBalancerArns"][0]
        infra["alb"] = seguro(lambda: self._alb(lb_arn))
        certificado = infra["alb"].get("certificado") if "erro" not in infra["alb"] else None
        if certificado:
            infra["acm"] = seguro(lambda: self._acm(certificado))
        return infra

    def _alb(self, arn):
        lb = self.elb.describe_load_balancers(LoadBalancerArns=[arn])["LoadBalancers"][0]
        listeners, certificado = [], None
        for listener in sorted(self.elb.describe_listeners(LoadBalancerArn=arn)["Listeners"], key=lambda l: l["Port"]):
            acao = listener["DefaultActions"][0]
            if acao["Type"] == "redirect":
                destino = f'redireciona para {acao["RedirectConfig"].get("Protocol", "")}'
            else:
                destino = "encaminha para o target group"
            listeners.append(f'{listener["Protocol"]}:{listener["Port"]} → {destino}')
            if listener.get("Certificates"):
                certificado = listener["Certificates"][0]["CertificateArn"]
        return {
            "nome": lb["LoadBalancerName"],
            "dns": lb["DNSName"],
            "estado": lb["State"]["Code"],
            "tipo": f'{lb["Type"]} / {lb["Scheme"]}',
            "zonas": [z["ZoneName"] for z in lb["AvailabilityZones"]],
            "listeners": listeners,
            "certificado": certificado,
        }

    def _acm(self, arn):
        c = self.acm.describe_certificate(CertificateArn=arn)["Certificate"]
        validacao = c.get("DomainValidationOptions") or [{}]
        return {
            "dominio": c["DomainName"],
            "status": c["Status"],
            "emissor": c.get("Issuer", "-"),
            "validacao": validacao[0].get("ValidationMethod", "-"),
            "expira_em": iso(c.get("NotAfter")),
        }

    def _route53(self):
        tags = {t["Key"]: t["Value"] for t in self.grupo.get("Tags", [])}
        zona, registro = tags.get("Route53Zona"), tags.get("Route53Registro")
        if not zona or not registro:
            return {"erro": "tags Route53Zona/Route53Registro não encontradas no ASG"}
        nome_zona = self.route53.get_hosted_zone(Id=zona)["HostedZone"]["Name"].rstrip(".")
        encontrados = self.route53.list_resource_record_sets(
            HostedZoneId=zona, StartRecordName=registro, StartRecordType="A", MaxItems="1"
        )["ResourceRecordSets"]
        if not encontrados or encontrados[0]["Name"].rstrip(".") != registro:
            return {"erro": f"registro {registro} não encontrado na zona {nome_zona}"}
        r = encontrados[0]
        destino = r["AliasTarget"]["DNSName"].rstrip(".") if "AliasTarget" in r else ", ".join(
            v["Value"] for v in r.get("ResourceRecords", [])
        )
        return {
            "registro": registro,
            "zona": nome_zona,
            "tipo": r["Type"] + (" (alias)" if "AliasTarget" in r else ""),
            "destino": destino,
        }

    def _vpc(self):
        eu = self.ec2.describe_instances(InstanceIds=[self.instancia])["Reservations"][0]["Instances"][0]
        vpc = self.ec2.describe_vpcs(VpcIds=[eu["VpcId"]])["Vpcs"][0]
        subnets = self.ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [eu["VpcId"]]}])["Subnets"]
        nome = next((t["Value"] for t in vpc.get("Tags", []) if t["Key"] == "Name"), vpc["VpcId"])
        return {
            "nome": nome,
            "cidr": vpc["CidrBlock"],
            "subnets": sorted(f'{s["CidrBlock"]} ({s["AvailabilityZone"]})' for s in subnets),
        }


PAGINA = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Terraform ao vivo</title>
<style>
  :root { --fundo:#0b1020; --cartao:#151b2f; --borda:#232b47; --texto:#e8ecf8; --fraco:#8a93b2;
          --roxo:#7b42bc; --verde:#5be37d; --amarelo:#ffd166; --vermelho:#ff6b6b; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         background: var(--fundo); color: var(--texto); padding: 24px 16px 48px; }
  main { max-width: 1200px; margin: 0 auto; }
  header.topo { display:flex; justify-content:space-between; gap:24px; align-items:flex-start; }
  .titulo { color: var(--fraco); text-transform: uppercase; letter-spacing:.12em; font-size:14px; margin:0; }
  .numero { font-size: clamp(96px, 22vw, 200px); font-weight: 800; line-height: 1; margin: 8px 0 0;
            background: linear-gradient(135deg, #a974ff, var(--roxo)); -webkit-background-clip:text;
            background-clip:text; color: transparent; transition: transform .3s; }
  .numero.pulsa { transform: scale(1.12); }
  .legenda { font-size: 22px; margin: 4px 0 0; }
  [hidden] { display: none !important; }
  .qrs { display: none; gap: 18px; }
  @media (min-width: 800px) { .qrs { display: flex; } }
  .qr { margin: 0; text-align: center; }
  .qr > div { background: #fff; padding: 8px; border-radius: 12px; }
  .qr svg { display: block; width: 150px; height: 150px; }
  .qr figcaption { color: var(--fraco); font-size: 13px; margin-top: 8px; text-transform: uppercase; letter-spacing: .08em; }
  .contribua { display: block; margin-top: 12px; padding: 12px 18px; border-radius: 12px; border: 1px dashed var(--roxo);
               color: var(--texto); text-decoration: none; font-size: 16px; overflow-wrap: anywhere; }
  .contribua b { color: #a974ff; }
  h2 { font-size: 14px; text-transform: uppercase; letter-spacing: .12em; color: var(--fraco); margin: 36px 0 14px; }
  .servido { margin: 24px 0 0; padding: 14px 18px; border-radius: 12px; background: var(--cartao);
             border-left: 6px solid var(--cor, var(--roxo)); font-size: 18px; }
  #erro { color: var(--vermelho); }

  /* caminho da requisição */
  .fluxo { display:grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 30px; }
  .no { position: relative; background: var(--cartao); border: 1px solid var(--borda); border-radius: 14px;
        padding: 14px; min-width: 0; }
  .no:not(:last-child)::after { content: "→"; position: absolute; right: -24px; top: 50%;
        transform: translateY(-50%); color: var(--fraco); font-size: 20px; }
  .no header { display:flex; justify-content:space-between; align-items:center; }
  .no .servico { font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: #a974ff; }
  .no h3 { margin: 6px 0 8px; font-size: 15px; font-family: ui-monospace, monospace; overflow-wrap:anywhere; }
  .no .erro { color: var(--vermelho); font-size: 12px; overflow-wrap:anywhere; }
  .vpc { margin-top: 14px; padding: 12px 16px; border: 1px dashed var(--borda); border-radius: 14px;
         color: var(--fraco); font-size: 13px; }
  .vpc b { color: var(--texto); }
  @media (max-width: 1000px) {
    .fluxo { grid-template-columns: 1fr; gap: 26px; }
    .no:not(:last-child)::after { content: "↓"; right: auto; left: 50%; top: auto; bottom: -24px; transform: translateX(-50%); }
  }
  .barra { height: 6px; background: var(--borda); border-radius: 99px; overflow: hidden; margin-top: 8px; }
  .barra i { display:block; height: 100%; background: var(--amarelo); transition: width .5s; }

  dl { margin: 0; display:grid; grid-template-columns:auto 1fr; gap:3px 10px; font-size:12.5px; }
  dt { color: var(--fraco); }
  dd { margin:0; font-family: ui-monospace, monospace; overflow-wrap:anywhere; }
  .ponto { width: 10px; height: 10px; border-radius: 50%; display:inline-block; background: var(--fraco); }
  .ponto.verde { background: var(--verde); box-shadow: 0 0 8px var(--verde); }
  .ponto.amarelo { background: var(--amarelo); box-shadow: 0 0 8px var(--amarelo); }
  .ponto.vermelho { background: var(--vermelho); box-shadow: 0 0 8px var(--vermelho); }
  .verde { color: var(--verde); } .amarelo { color: var(--amarelo); } .vermelho { color: var(--vermelho); }

  /* máquinas */
  .grade { display:grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 14px; }
  .maquina { background: var(--cartao); border-radius: 14px; padding: 16px; border-top: 6px solid var(--cor);
             position: relative; }
  .maquina.nova { animation: entra .5s ease-out; }
  .maquina.eu { outline: 3px solid var(--cor); }
  .maquina.saindo { opacity: .4; }
  .maquina .id { font-family: ui-monospace, monospace; font-size: 15px; font-weight: 700; margin-bottom: 10px; }
  .selo { position:absolute; top:10px; right:10px; font-size:11px; padding:2px 8px; border-radius:999px;
          background: var(--cor); color:#0b1020; font-weight:700; }
  #eventos { margin-top: 28px; color: var(--fraco); font-family: ui-monospace, monospace; font-size: 13px;
             list-style: none; padding: 0; }
  @keyframes entra { from { transform: scale(.8); opacity: 0; } to { transform: none; opacity: 1; } }
</style>
</head>
<body>
<main>
  <header class="topo">
    <div>
      <p class="titulo">Terraform ao vivo</p>
      <p class="numero" id="numero">&middot;</p>
      <p class="legenda" id="legenda">carregando&hellip;</p>
      <p id="erro"></p>
    </div>
    <div class="qrs">
      <figure class="qr"><div id="qr-site"></div><figcaption>abra no celular</figcaption></figure>
      <figure class="qr" id="qr-repo-caixa" hidden><div id="qr-repo"></div><figcaption>mande um PR</figcaption></figure>
    </div>
  </header>
  <div class="servido" id="servido"></div>
  <a class="contribua" id="contribua" target="_blank" rel="noopener" hidden>
    Quer mudar esta página ou o número de máquinas? Mande um PR &rarr; <b></b>
  </a>

  <h2>O caminho da sua requisição</h2>
  <section class="fluxo" id="fluxo"></section>
  <div class="vpc" id="vpc"></div>

  <h2>Máquinas no Auto Scaling Group</h2>
  <section class="grade" id="grade"></section>
  <ul id="eventos"></ul>
</main>
<script src="https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.js"></script>
<script>
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? "-").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const cor = (id) => { let h = 0; for (const c of id) h = (h * 31 + c.charCodeAt(0)) % 360; return `hsl(${h} 75% 62%)`; };
  const tempo = (iso) => {
    if (!iso) return "-";
    const s = Math.max(0, Math.round((Date.now() - new Date(iso)) / 1000));
    return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}min ${s % 60}s` : `${Math.floor(s / 3600)}h ${Math.floor(s % 3600 / 60)}min`;
  };
  const corEstado = { InService: "verde", healthy: "verde", Pending: "amarelo", "Pending:Wait": "amarelo", initial: "amarelo",
                      draining: "amarelo", Terminating: "vermelho", "Terminating:Wait": "vermelho", unhealthy: "vermelho" };
  const lista = (linhas) => `<dl>${linhas.filter(Boolean).map(([k, v, classe]) =>
    `<dt>${k}</dt><dd class="${classe || ""}">${esc(v)}</dd>`).join("")}</dl>`;
  const no = (servico, titulo, status, dados, extra = "") => `
    <article class="no">
      <header><span class="servico">${servico}</span><span class="ponto ${status}"></span></header>
      <h3>${esc(titulo)}</h3>
      ${dados && dados.erro ? `<div class="erro">${esc(dados.erro)}</div>` : ""}
      ${extra}
    </article>`;

  let anteriores = null, ultimoNumero = null;

  function evento(texto) {
    const li = document.createElement("li");
    li.textContent = `${new Date().toLocaleTimeString()}  ${texto}`;
    $("eventos").prepend(li);
    while ($("eventos").children.length > 12) $("eventos").lastChild.remove();
  }

  function renderFluxo(d) {
    const infra = d.infra || {}, asg = d.asg || {};
    const r53 = infra.route53 || {}, acm = infra.acm || {}, alb = infra.alb || {}, tg = infra.target_group || {};
    const saudaveis = d.maquinas.filter((m) => m.saude_lb === "healthy").length;
    const dias = acm.expira_em ? Math.round((new Date(acm.expira_em) - Date.now()) / 86400000) : null;
    const at = asg.atualizacao && !asg.atualizacao.erro ? asg.atualizacao : null;
    const trocando = at && ["Pending", "InProgress"].includes(at.status);

    $("fluxo").innerHTML = [
      no("Route53", r53.registro || "DNS", r53.erro ? "vermelho" : r53.registro ? "verde" : "",
        r53, r53.erro ? "" : lista([["zona", r53.zona], ["tipo", r53.tipo], ["aponta p/", r53.destino]])),

      no("ACM", acm.dominio || "certificado", acm.erro ? "vermelho" : acm.status === "ISSUED" ? "verde" : acm.status ? "amarelo" : "",
        acm, acm.erro ? "" : lista([["status", acm.status, acm.status === "ISSUED" ? "verde" : "amarelo"],
          ["emissor", acm.emissor], ["validação", acm.validacao], ["expira em", dias !== null ? `${dias} dias` : "-"]])),

      no("Load Balancer", alb.nome || "ALB", alb.erro ? "vermelho" : alb.estado === "active" ? "verde" : alb.estado ? "amarelo" : "",
        alb, alb.erro ? "" : lista([["estado", alb.estado, alb.estado === "active" ? "verde" : "amarelo"], ["tipo", alb.tipo],
          ["zonas", (alb.zonas || []).join(", ")], ...(alb.listeners || []).map((l) => ["listener", l])])),

      no("Target Group", tg.nome || "target group", !tg.nome ? "" : saudaveis === d.maquinas.length && saudaveis ? "verde" : "amarelo",
        tg, tg.erro ? "" : lista([["protocolo", tg.protocolo], ["health check", tg.health_check],
          ["saudáveis", `${saudaveis} de ${d.maquinas.length}`, saudaveis === d.maquinas.length ? "verde" : "amarelo"]])),

      no("Auto Scaling Group", asg.nome, d.em_servico === asg.desejado && !trocando ? "verde" : "amarelo", asg,
        lista([["desejado", asg.desejado], ["mín / máx", `${asg.minimo} / ${asg.maximo}`], ["em serviço", d.em_servico],
          ["health check", asg.health_check], ["template", asg.launch_template],
          at && ["troca de AMI", `${at.status} ${at.progresso ?? 0}%`, trocando ? "amarelo" : at.status === "Successful" ? "verde" : ""]])
        + (trocando ? `<div class="barra"><i style="width:${at.progresso || 0}%"></i></div>` : "")),
    ].join("");

    const vpc = infra.vpc || {};
    $("vpc").innerHTML = vpc.erro ? `VPC: <span class="vermelho">${esc(vpc.erro)}</span>`
      : vpc.cidr ? `Tudo isso dentro da <b>VPC ${esc(vpc.nome)}</b> (${esc(vpc.cidr)}) &middot; subnets: ${(vpc.subnets || []).map(esc).join(", ")}` : "";
  }

  function render(d) {
    mostrarRepositorio(d.repositorio);
    $("erro").textContent = d.erro || "";
    $("servido").style.setProperty("--cor", cor(d.servido_por));
    $("servido").innerHTML = `Esta resposta veio da máquina <b>${esc(d.servido_por)}</b> (${esc(d.az)}) &middot; app <b>${esc(d.versao)}</b>`;
    if (!d.maquinas) return;

    if (ultimoNumero !== null && ultimoNumero !== d.em_servico) {
      $("numero").classList.add("pulsa");
      setTimeout(() => $("numero").classList.remove("pulsa"), 300);
    }
    ultimoNumero = d.em_servico;
    $("numero").textContent = d.em_servico;
    $("legenda").textContent = d.em_servico === 1 ? "máquina rodando" : "máquinas rodando";

    const atuais = new Set(d.maquinas.map((m) => m.id));
    const nova = (id) => !anteriores || !anteriores.has(id); // só anima quem acabou de chegar
    if (anteriores) {
      for (const id of atuais) if (!anteriores.has(id)) evento(`+ ${id} entrou no grupo`);
      for (const id of anteriores) if (!atuais.has(id)) evento(`- ${id} saiu do grupo`);
    }

    renderFluxo(d);

    $("grade").innerHTML = d.maquinas.map((m) => `
      <article class="maquina ${nova(m.id) ? "nova" : ""} ${m.id === d.servido_por ? "eu" : ""} ${m.estado.startsWith("Terminating") ? "saindo" : ""}"
               style="--cor:${cor(m.id)}">
        ${m.id === d.servido_por ? '<span class="selo">respondeu agora</span>' : ""}
        <div class="id">${esc(m.id)}</div>
        ${lista([["no ASG", m.estado, corEstado[m.estado]], ["no ALB", m.saude_lb, corEstado[m.saude_lb]],
                 ["zona", m.az], ["ip", m.ip], ["ami", m.ami], ["no ar há", tempo(m.lancada_em)]])}
      </article>`).join("");
    anteriores = atuais;
  }

  async function atualizar() {
    try {
      const r = await fetch("/api/status", { cache: "no-store" });
      render(await r.json());
    } catch (e) {
      $("erro").textContent = "sem resposta do load balancer…";
    }
  }

  function desenharQr(id, texto) {
    try {
      const qr = qrcode(0, "M");
      qr.addData(texto);
      qr.make();
      $(id).innerHTML = qr.createSvgTag({ cellSize: 4, margin: 0 });
    } catch (e) {}
  }

  let repositorioMostrado = false;
  function mostrarRepositorio(url) {
    if (!url || repositorioMostrado) return;
    repositorioMostrado = true;
    desenharQr("qr-repo", url);
    $("qr-repo-caixa").hidden = false;
    $("contribua").href = url;
    $("contribua").querySelector("b").textContent = url.replace(/^https?:[/][/]/, "");
    $("contribua").hidden = false;
  }

  desenharQr("qr-site", location.href);
  atualizar();
  setInterval(atualizar, 2000);
</script>
</body>
</html>
""".encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/saude":
            self._responder(200, "text/plain", b"ok")
        elif self.path.startswith("/api/status"):
            self._responder(200, "application/json", json.dumps(contador.status()).encode())
        elif self.path in ("/", "/index.html"):
            self._responder(200, "text/html; charset=utf-8", PAGINA)
        else:
            self._responder(404, "text/plain", b"nada aqui")

    def _responder(self, codigo, tipo, corpo):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *args):
        pass  # o health check do ALB lotaria o log


if __name__ == "__main__":
    contador = Contador()
    print(f"contador {VERSAO} rodando em {contador.instancia} na porta {PORTA}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORTA), Handler).serve_forever()
