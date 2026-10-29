(function () {
  "use strict";

  const S = window.Schulz;
  const { esc, num, data, dataHora, api } = S;
  const $ = (sel, raiz) => (raiz || document).querySelector(sel);
  const $$ = (sel, raiz) => Array.from((raiz || document).querySelectorAll(sel));
  const conteudo = $("#conteudo");

  const ROTULOS_GRUPO = { tecnico: "Técnico", signatario: "Signatário", auditor: "Auditor", admin: "Admin" };
  const ROTULOS_STATUS = {
    PENDENTE_ASSINATURA: "Pendente de assinatura", EMITIDA: "Emitida", ANULADA: "Anulada", DEVOLVIDA: "Devolvida",
    RECEBIDA: "Recebida", PROCESSANDO: "Processando", ERRO_ARQUIVO: "Erro no arquivo", ERRO_VALIDACAO: "Erro de validação"
  };
  const MENU = [
    { rota: "#/", rotulo: "Início", grupos: null },
    { rota: "#/nova", rotulo: "Nova calibração", grupos: ["tecnico", "admin"] },
    { rota: "#/pendentes", rotulo: "Pendentes", grupos: ["signatario", "admin"] },
    { rota: "#/calibracoes", rotulo: "Certificados", grupos: null },
    { rota: "#/cadastros", rotulo: "Cadastros", grupos: null },
    { rota: "#/auditoria", rotulo: "Auditoria", grupos: ["signatario", "auditor", "admin"] }
  ];

  let usuario = null;
  let desafio = null;
  let geracao = 0;

  function grupos() { return (usuario && usuario.grupos) || []; }
  function tem(...lista) { return lista.some((g) => grupos().includes(g)); }
  function status(s) { return `<span class="status ${esc(s)}">${esc(ROTULOS_STATUS[s] || s)}</span>`; }
  function resultado(r) { return r === "APROVADO" ? '<span class="badge ok">APROVADO</span>' : r === "REPROVADO" ? '<span class="badge bad">REPROVADO</span>' : "-"; }
  function carregando(texto) { conteudo.innerHTML = `<div class="carregando">${esc(texto || "Carregando…")}<div class="progresso"><span></span></div></div>`; }
  function falha(erro) { conteudo.innerHTML = S.blocoErro(erro); }

  function usuarioDoToken() {
    const r = S.sessao.reivindicacoes();
    if (!r) return null;
    const lista = r["cognito:groups"] || [];
    return { nome: r.name || r.email, email: r.email, sub: r.sub, grupos: Array.isArray(lista) ? lista : [lista] };
  }

  function mostrarLogin(mensagem) {
    $("#app").hidden = true;
    $("#tela-login").hidden = false;
    trocarFormulario("form-senha");
    $("#login-msg").innerHTML = mensagem ? `<div class="alerta aviso">${esc(mensagem)}</div>` : "";
    setTimeout(() => $("#login-email").focus(), 30);
  }

  function trocarFormulario(id) {
    ["form-senha", "form-nova-senha", "form-mfa-config", "form-mfa"].forEach((f) => { $("#" + f).hidden = f !== id; });
  }

  function erroLogin(erro) {
    $("#login-msg").innerHTML = `<div class="alerta erro" role="alert">${esc(erro.message)}</div>`;
  }

  async function tratarResposta(r) {
    if (r.AuthenticationResult) {
      S.sessao.salvar(r.AuthenticationResult, desafio && desafio.email);
      desafio = null;
      iniciarApp();
      return;
    }
    desafio = Object.assign({}, desafio, { nome: r.ChallengeName, sessao: r.Session, parametros: r.ChallengeParameters || {} });
    const usuarioId = desafio.parametros.USER_ID_FOR_SRP || desafio.parametros.USERNAME;
    if (usuarioId) desafio.username = usuarioId;
    $("#login-msg").innerHTML = "";
    if (r.ChallengeName === "NEW_PASSWORD_REQUIRED") {
      trocarFormulario("form-nova-senha");
      $("#nova-senha").focus();
    } else if (r.ChallengeName === "MFA_SETUP") {
      const assoc = await S.cognito("AssociateSoftwareToken", { Session: r.Session });
      desafio.sessao = assoc.Session;
      const rotulo = encodeURIComponent(`Schulz Certificados:${desafio.email}`);
      const uri = `otpauth://totp/${rotulo}?secret=${assoc.SecretCode}&issuer=${encodeURIComponent("Schulz Certificados")}`;
      S.desenharQr($("#mfa-qr"), uri, 180);
      $("#mfa-segredo").textContent = assoc.SecretCode;
      trocarFormulario("form-mfa-config");
      $("#mfa-codigo-config").focus();
    } else if (r.ChallengeName === "SOFTWARE_TOKEN_MFA") {
      trocarFormulario("form-mfa");
      $("#mfa-codigo").value = "";
      $("#mfa-codigo").focus();
    } else {
      erroLogin(new Error(`Etapa de login não suportada: ${r.ChallengeName}`));
    }
  }

  function comBotao(form, acao) {
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const botao = $("button[type=submit]", form);
      botao.disabled = true;
      try { await acao(); } catch (e) { erroLogin(e); } finally { botao.disabled = false; }
    });
  }

  comBotao($("#form-senha"), async () => {
    const email = $("#login-email").value.trim().toLowerCase();
    desafio = { email, username: email };
    const r = await S.cognito("InitiateAuth", {
      AuthFlow: "USER_PASSWORD_AUTH", ClientId: S.CFG.clientId,
      AuthParameters: { USERNAME: email, PASSWORD: $("#login-senha").value }
    });
    $("#login-senha").value = "";
    await tratarResposta(r);
  });

  comBotao($("#form-nova-senha"), async () => {
    const a = $("#nova-senha").value, b = $("#nova-senha2").value;
    if (a !== b) throw new Error("As senhas não conferem.");
    const r = await S.cognito("RespondToAuthChallenge", {
      ChallengeName: "NEW_PASSWORD_REQUIRED", ClientId: S.CFG.clientId, Session: desafio.sessao,
      ChallengeResponses: { USERNAME: desafio.username, NEW_PASSWORD: a }
    });
    $("#nova-senha").value = ""; $("#nova-senha2").value = "";
    await tratarResposta(r);
  });

  comBotao($("#form-mfa-config"), async () => {
    const v = await S.cognito("VerifySoftwareToken", {
      Session: desafio.sessao, UserCode: $("#mfa-codigo-config").value.trim(), FriendlyDeviceName: "Autenticador"
    });
    if (v.Status !== "SUCCESS") throw new Error("Código não confirmado. Tente novamente.");
    const r = await S.cognito("RespondToAuthChallenge", {
      ChallengeName: "MFA_SETUP", ClientId: S.CFG.clientId, Session: v.Session,
      ChallengeResponses: { USERNAME: desafio.username }
    });
    await tratarResposta(r);
  });

  comBotao($("#form-mfa"), async () => {
    const r = await S.cognito("RespondToAuthChallenge", {
      ChallengeName: "SOFTWARE_TOKEN_MFA", ClientId: S.CFG.clientId, Session: desafio.sessao,
      ChallengeResponses: { USERNAME: desafio.username, SOFTWARE_TOKEN_MFA_CODE: $("#mfa-codigo").value.trim() }
    });
    await tratarResposta(r);
  });

  $("#sair").addEventListener("click", () => {
    S.sessao.limpar();
    usuario = null;
    location.hash = "#/";
    mostrarLogin("Você saiu do sistema.");
  });

  window.addEventListener("schulz:sessao-expirada", () => mostrarLogin("Sua sessão expirou. Entre novamente."));

  function iniciarApp() {
    usuario = usuarioDoToken();
    if (!usuario) { mostrarLogin(); return; }
    $("#tela-login").hidden = true;
    $("#app").hidden = false;
    $("#usuario-nome").textContent = usuario.nome;
    $("#usuario-grupos").innerHTML = grupos().map((g) => `<span class="chip">${esc(ROTULOS_GRUPO[g] || g)}</span>`).join("");
    $("#menu").innerHTML = MENU.filter((m) => !m.grupos || tem(...m.grupos))
      .map((m) => `<a href="${m.rota}" data-rota="${m.rota}">${esc(m.rotulo)}</a>`).join("");
    rotear();
  }

  function marcarMenu(rota) {
    $$("#menu a").forEach((a) => {
      const ativo = a.dataset.rota === rota || (a.dataset.rota !== "#/" && rota.startsWith(a.dataset.rota));
      if (ativo) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  }

  function lerHash() {
    const [caminho, consulta] = (location.hash || "#/").split("?");
    return { caminho, params: new URLSearchParams(consulta || "") };
  }

  async function rotear() {
    if (!usuario) return;
    const { caminho, params } = lerHash();
    geracao += 1;
    marcarMenu(caminho);
    window.scrollTo(0, 0);
    try {
      let m;
      if (caminho === "#/" || caminho === "") await telaInicio();
      else if (caminho === "#/nova") await telaNova();
      else if (caminho === "#/pendentes") await telaLista({ pendentes: true });
      else if (caminho === "#/calibracoes") await telaLista({ params });
      else if ((m = caminho.match(/^#\/calibracoes\/([a-f0-9]{32})$/))) await telaDetalhe(m[1]);
      else if (caminho === "#/cadastros") await telaCadastros(params);
      else if (caminho === "#/auditoria") await telaAuditoria(params);
      else conteudo.innerHTML = '<div class="vazio">Página não encontrada. <a href="#/">Voltar ao início</a></div>';
    } catch (e) {
      if (e.status !== 401) falha(e);
    }
    conteudo.focus({ preventScroll: true });
  }

  window.addEventListener("hashchange", rotear);

  async function telaInicio() {
    const g = geracao;
    carregando();
    const p = await api("GET", "/api/painel");
    if (g !== geracao) return;
    const alertas = (p.alertas_padroes || []).map((a) => {
      const texto = a.situacao === "VENCIDO"
        ? `O padrão ${a.codigo_exibicao} está com calibração vencida desde ${data(a.data_proxima_cal)}. Emissões que usem este padrão ficam bloqueadas (RN02).`
        : `O padrão ${a.codigo_exibicao} vence em ${data(a.data_proxima_cal)} (alerta de 30 dias – RF11).`;
      return `<div class="alerta ${a.situacao === "VENCENDO" ? "aviso" : "erro"}"><strong>${esc(a.descricao)} ${esc(a.codigo_exibicao)}</strong>${esc(texto)}
        ${tem("admin", "signatario") ? ` <a href="#/cadastros?aba=padroes">Atualizar cadastro</a>` : ""}</div>`;
    }).join("");
    const cartoes = [];
    if (tem("tecnico", "admin")) cartoes.push(["#/nova", "Nova calibração", "Envie a planilha exportada pelo Calypso. O sistema lê, valida e calcula tudo sozinho."]);
    if (tem("signatario", "admin")) cartoes.push(["#/pendentes", `Pendentes de assinatura${p.pendentes_assinatura !== null ? ` (${p.pendentes_assinatura})` : ""}`, "Revise os resultados e emita o certificado com QR Code."]);
    cartoes.push(["#/calibracoes", "Certificados", "Consulte e baixe certificados por número, instrumento, setor ou data (RF10)."]);
    cartoes.push(["#/cadastros", "Cadastros", "Instrumentos, padrões (MMC) e procedimentos (RF08)."]);
    if (tem("signatario", "auditor", "admin")) cartoes.push(["#/auditoria", "Trilha de auditoria", "Quem fez o quê e quando (RNF03)."]);
    conteudo.innerHTML = `
      <h1>Olá, ${esc(usuario.nome)}</h1>
      <p class="sub">Perfil: ${grupos().map((g) => esc(ROTULOS_GRUPO[g] || g)).join(", ") || "sem grupo"}</p>
      ${grupos().length === 0 ? '<div class="alerta aviso"><strong>Usuário sem perfil</strong>Peça ao administrador para incluir você em um grupo (tecnico, signatario, auditor ou admin).</div>' : ""}
      ${p.cadastros_vazios && tem("admin") ? `<div class="alerta aviso"><strong>Cadastros vazios</strong>Carregue os cadastros iniciais (padrão MMC 500.011, procedimento ITL-0026 e instrumento 703.1208).
        <div class="acoes"><button class="btn mini" id="btn-semente" type="button">Carregar cadastros iniciais</button></div></div>` : ""}
      ${alertas}
      <div class="painel-grid">${cartoes.map(([h, t, d]) => `<a class="card atalho" href="${h}"><div class="t">${esc(t)}</div><div class="d">${esc(d)}</div></a>`).join("")}</div>
      <p class="nota" style="margin-top:22px">Operadores de chão de fábrica validam certificados sem login, lendo o QR Code impresso: <a href="validar.html" target="_blank" rel="noopener">página de validação</a>.</p>`;
    const semente = $("#btn-semente");
    if (semente) semente.addEventListener("click", async () => {
      semente.disabled = true;
      try { await api("POST", "/api/cadastros/semente", {}); S.mostrarAviso("Cadastros iniciais carregados."); await telaInicio(); }
      catch (e) { semente.disabled = false; S.mostrarAviso(e.message); }
    });
  }

  async function telaNova() {
    conteudo.innerHTML = `
      <h1>Nova calibração</h1>
      <p class="sub">Envie o relatório do Calypso (aba Report) ou a planilha de registro. Somente .xlsx até 5 MB.</p>
      <div id="nova-msg"></div>
      <div class="upload">
        <label class="drop card" id="drop">
          <input type="file" id="arquivo" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet">
          <span class="t">Clique para escolher a planilha</span>
          <span class="nota">ou arraste o arquivo para cá</span>
          <span class="nome" id="nome-arquivo"></span>
        </label>
        <div class="card">
          <strong>O que acontece depois do envio</strong>
          <ul class="simples">
            <li>O arquivo vai direto para o Amazon S3 por uma URL temporária (5 min).</li>
            <li>O evento do S3 aciona a função <code>processar-planilha</code>.</li>
            <li>Os dois blocos do Calypso viram Medida 1 e Medida 2 (ETL-05).</li>
            <li>Média, desvio padrão, erro, incerteza e resultado são calculados.</li>
            <li>A calibração fica pendente de assinatura do signatário.</li>
          </ul>
          <div class="acoes"><button class="btn" id="enviar" type="button" disabled>Enviar e processar</button></div>
        </div>
      </div>`;
    const input = $("#arquivo"), drop = $("#drop"), botao = $("#enviar");
    const escolher = () => {
      const f = input.files[0];
      $("#nome-arquivo").textContent = f ? `${f.name} · ${(f.size / 1024).toFixed(1)} KB` : "";
      botao.disabled = !f;
    };
    input.addEventListener("change", escolher);
    ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("sobre"); }));
    ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("sobre"); }));
    drop.addEventListener("drop", (e) => { if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; escolher(); } });
    botao.addEventListener("click", async () => {
      const f = input.files[0];
      if (!f) return;
      const msg = $("#nova-msg");
      if (!/\.xlsx$/i.test(f.name)) { msg.innerHTML = S.blocoErro(new Error("Envie um arquivo .xlsx (arquivos .xlsm com macros são recusados).")); return; }
      if (f.size > 5 * 1024 * 1024) { msg.innerHTML = S.blocoErro(new Error("O arquivo excede 5 MB.")); return; }
      botao.disabled = true;
      msg.innerHTML = `<div class="alerta aviso"><strong>Enviando ${esc(f.name)}…</strong><div class="progresso"><span></span></div></div>`;
      try {
        const up = await api("POST", "/api/uploads", { nome_arquivo: f.name, tamanho: f.size });
        const form = new FormData();
        Object.entries(up.campos).forEach(([k, v]) => form.append(k, v));
        form.append("file", f);
        const r = await fetch(up.url, { method: "POST", body: form });
        if (!r.ok) throw new Error(`O armazenamento recusou o arquivo (HTTP ${r.status}).`);
        msg.innerHTML = `<div class="alerta aviso"><strong>Arquivo recebido. Processando na nuvem…</strong><div class="progresso"><span></span></div></div>`;
        const fim = Date.now() + 90000;
        while (Date.now() < fim) {
          await new Promise((ok) => setTimeout(ok, 1500));
          const imp = await api("GET", `/api/importacoes/${up.id}`);
          if (imp.status === "PENDENTE_ASSINATURA") { location.hash = `#/calibracoes/${imp.id_calibracao}`; return; }
          if (imp.status === "ERRO_ARQUIVO" || imp.status === "ERRO_VALIDACAO") {
            const e = new Error((imp.erro && imp.erro.mensagem) || "A planilha não pôde ser processada.");
            e.detalhes = (imp.erro && imp.erro.detalhes) || [];
            msg.innerHTML = S.blocoErro(e);
            botao.disabled = false;
            return;
          }
        }
        throw new Error("O processamento demorou mais que o esperado. Confira a lista de certificados em instantes.");
      } catch (e) {
        msg.innerHTML = S.blocoErro(e);
        botao.disabled = false;
      }
    });
  }

  function tabelaCalibracoes(itens) {
    if (!itens.length) return '<div class="card vazio">Nenhuma calibração encontrada.</div>';
    return `<div class="tabela-wrap lista"><table><thead><tr><th>Certificado</th><th>Instrumento</th><th>Solicitante</th><th>Data</th><th>Resultado</th><th>Situação</th></tr></thead><tbody>
      ${itens.map((c) => `<tr data-id="${esc(c.id_calibracao)}" tabindex="0">
        <td>${c.nr_certificado ? `<strong>${esc(c.nr_certificado)}</strong>` : `<span class="nota">${esc(c.nome_arquivo || "-")}</span>`}</td>
        <td>${esc(c.codigo_instrumento || "-")}<br><span class="nota">${esc(c.denominacao || "")}</span></td>
        <td>${esc(c.solicitante || c.cliente || "-")}</td>
        <td>${data(c.data_emissao || c.data_calibracao || c.criado_em)}</td>
        <td>${resultado(c.resultado_geral)}${c.qtd_reprovadas ? ` <span class="nota">${c.qtd_reprovadas} fora</span>` : ""}</td>
        <td>${status(c.status)}${c.qtd_pendencias ? ` <span class="nota">${c.qtd_pendencias} pendência(s)</span>` : ""}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function ligarLinhas() {
    $$(".lista tbody tr").forEach((tr) => {
      const abrir = () => { location.hash = `#/calibracoes/${tr.dataset.id}`; };
      tr.addEventListener("click", abrir);
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") abrir(); });
    });
  }

  async function telaLista(opcoes) {
    const g = geracao;
    carregando();
    if (opcoes.pendentes) {
      const r = await api("GET", "/api/pendentes");
      if (g !== geracao) return;
      conteudo.innerHTML = `<h1>Pendentes de assinatura</h1><p class="sub">Calibrações processadas aguardando revisão do signatário (UC01, passo 8).</p>${tabelaCalibracoes(r.itens)}`;
      ligarLinhas();
      return;
    }
    const p = opcoes.params;
    const consulta = new URLSearchParams();
    ["nr", "instrumento", "setor", "status", "de", "ate"].forEach((k) => { if (p.get(k)) consulta.set(k, p.get(k)); });
    const r = await api("GET", `/api/calibracoes${consulta.toString() ? "?" + consulta : ""}`);
    if (g !== geracao) return;
    conteudo.innerHTML = `<h1>Certificados e calibrações</h1><p class="sub">Busca por número, instrumento, setor ou período (RF10).</p>
      <form class="filtros card" id="filtros">
        <div class="campo"><label for="f-nr">Nº certificado</label><input id="f-nr" name="nr" placeholder="00001/2026" value="${esc(p.get("nr") || "")}"></div>
        <div class="campo"><label for="f-inst">Instrumento</label><input id="f-inst" name="instrumento" placeholder="703.1208" value="${esc(p.get("instrumento") || "")}"></div>
        <div class="campo"><label for="f-setor">Setor</label><input id="f-setor" name="setor" placeholder="Usinagem I" value="${esc(p.get("setor") || "")}"></div>
        <div class="campo"><label for="f-status">Situação</label><select id="f-status" name="status">
          ${["", "PENDENTE_ASSINATURA", "EMITIDA", "ANULADA", "DEVOLVIDA"].map((s) => `<option value="${s}" ${p.get("status") === s ? "selected" : ""}>${s ? esc(ROTULOS_STATUS[s]) : "Todas"}</option>`).join("")}</select></div>
        <div class="campo"><label for="f-de">De</label><input id="f-de" name="de" type="date" value="${esc(p.get("de") || "")}"></div>
        <div class="campo"><label for="f-ate">Até</label><input id="f-ate" name="ate" type="date" value="${esc(p.get("ate") || "")}"></div>
        <button class="btn" type="submit">Buscar</button><a class="btn sec" href="#/calibracoes">Limpar</a>
      </form>
      <p class="nota">${r.total} resultado(s)</p>${tabelaCalibracoes(r.itens)}`;
    $("#filtros").addEventListener("submit", (e) => {
      e.preventDefault();
      const q = new URLSearchParams();
      new FormData(e.target).forEach((v, k) => { if (String(v).trim()) q.set(k, String(v).trim()); });
      location.hash = `#/calibracoes${q.toString() ? "?" + q : ""}`;
    });
    ligarLinhas();
  }

  function exibirPadrao(codigo) { return /^\d{6}$/.test(codigo || "") ? `${codigo.slice(0, 3)}.${codigo.slice(3)}` : codigo; }

  function campo(rotulo, valor) { return `<div><dt>${esc(rotulo)}</dt><dd>${valor}</dd></div>`; }

  async function telaDetalhe(id) {
    const g = geracao;
    carregando();
    const c = await api("GET", `/api/calibracoes/${id}`);
    if (g !== geracao) return;
    const efetivos = c.campos_efetivos || {};
    const inst = c.instrumento_certificado || {};
    const pend = c.pendencias || [];
    const reprovado = c.resultado_geral === "REPROVADO";
    const inc = c.incerteza;
    let html = `<p><a href="#/calibracoes">← Certificados</a></p>
      <h1>${c.nr_certificado ? `Certificado nº ${esc(c.nr_certificado)}` : "Prévia da calibração"}</h1>
      <p class="sub">${status(c.status)} · arquivo ${esc(c.nome_arquivo || "-")} · enviado por ${esc(c.nome_usuario_envio || "-")} em ${dataHora(c.criado_em)}</p>
      <div class="parecer ${reprovado ? "reprovado" : "aprovado"}" role="status">
        <div class="icone">${reprovado ? "✕" : "✓"}</div>
        <div><div class="tit">RESULTADO GERAL: ${esc(c.resultado_geral)}</div>
        <div class="det">${c.total_caracteristicas} característica(s) avaliada(s)${reprovado ? ` · <strong>${(c.reprovadas || []).length} fora da tolerância:</strong> ${esc((c.reprovadas || []).join(", "))}` : " · todas dentro da tolerância"}</div></div>
      </div>`;
    if (c.status === "ANULADA") html += `<div class="alerta erro"><strong>Certificado anulado</strong>${esc(c.motivo_anulacao || "")} · por ${esc(c.anulado_por || "-")} em ${dataHora(c.anulado_em)}. O número ${esc(c.nr_certificado)} não será reutilizado (RN01).</div>`;
    if (c.status === "DEVOLVIDA") html += `<div class="alerta aviso"><strong>Devolvida para correção</strong>${esc(c.motivo_devolucao || "")} · por ${esc(c.devolvido_por || "-")}</div>`;
    if (c.status === "PENDENTE_ASSINATURA" && pend.length) {
      html += `<div class="alerta erro" role="alert"><strong>Emissão bloqueada até resolver ${pend.length} pendência(s) (RF09)</strong><ul>
        ${pend.map((p) => `<li>${esc(p.mensagem)} <span class="nota">[${esc(p.regra)}]</span>${acaoPendencia(p, c, efetivos)}</li>`).join("")}</ul></div>`;
    }
    if ((c.avisos || []).length) html += `<details class="card" style="margin-bottom:18px"><summary>Avisos da leitura (${c.avisos.length})</summary><ul class="simples">${c.avisos.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></details>`;
    html += `<dl class="meta">
      ${campo("Instrumento", `${esc(c.codigo_instrumento || "-")} · ${esc(inst.denominacao || efetivos.denominacao || c.denominacao || "-")}`)}
      ${campo("Solicitante", esc(c.solicitante || efetivos.cliente || c.cliente || "-"))}
      ${campo("Data da calibração", data(c.data_calibracao))}
      ${campo("Técnico executante", esc(c.tecnico || "-"))}
      ${campo("Padrão (MMC)", esc((c.padrao_utilizado && c.padrao_utilizado.codigo_exibicao) || exibirPadrao(c.codigo_padrao) || "não informado"))}
      ${campo("Incerteza expandida", inc ? `U = ${num(inc.incerteza_expandida_mm, 4)} mm · k = ${num(inc.fator_k, 2)}` : "calculada na emissão")}
    </dl>`;
    if (c.status === "EMITIDA" || c.status === "ANULADA") {
      html += `<div class="card cert" style="margin-bottom:18px"><div>
        <h2 style="margin-top:0">Certificado emitido</h2>
        <dl class="meta" style="margin-bottom:10px">
          ${campo("Número", esc(c.nr_certificado))}${campo("Emissão", dataHora(c.data_emissao))}
          ${campo("Signatário", esc(c.nome_signatario))}${campo("Procedimento", esc(c.procedimento ? `${c.procedimento.codigo} rev. ${c.procedimento.revisao}` : "-"))}
        </dl>
        <p class="hash">SHA-256 do PDF: ${esc(c.sha256)}</p>
        <div class="acoes"><button class="btn" id="baixar" type="button">Baixar PDF</button>
          <a class="btn sec" href="${esc(c.url_validacao)}" target="_blank" rel="noopener">Abrir validação pública</a></div>
        ${c.status === "EMITIDA" && tem("signatario") ? `<details style="margin-top:14px"><summary>Anular certificado</summary>
          <div class="campo"><label for="motivo-anular">Motivo da anulação</label><textarea id="motivo-anular" rows="2" maxlength="400"></textarea></div>
          <button class="btn perigo mini" id="anular" type="button">Confirmar anulação</button></details>` : ""}
      </div><div class="qr" id="qr-cert" aria-label="QR Code de validação"></div></div>`;
    }
    if (c.status === "PENDENTE_ASSINATURA" && tem("signatario")) html += `<div class="card" id="emissao" style="margin-bottom:18px"><div class="carregando">Carregando padrões…</div></div>`;
    html += `<div class="barra"><h2>Resultados (mm)</h2></div>
      <div class="tabela-wrap"><table><thead><tr><th>#</th><th>Parâmetro avaliado</th><th>Nominal</th><th>Tol. +</th><th>Tol. -</th><th>Medida 1</th><th>Medida 2</th><th>Média</th><th>Desvio padrão</th><th>Erro</th><th>Resultado</th></tr></thead><tbody>
      ${(c.resultados || []).map((r) => `<tr class="${r.resultado === "REPROVADO" ? "fora" : ""}"><td>${r.sequencia}</td><td class="ponto">${esc(r.parametro_exibicao)}</td>
        <td>${num(r.valor_nominal)}</td><td>${num(r.tolerancia_pos)}</td><td>${num(r.tolerancia_neg)}</td><td>${num(r.medida_1)}</td><td>${num(r.medida_2)}</td>
        <td><strong>${num(r.media)}</strong></td><td>${num(r.desvio_padrao, 6)}</td><td>${num(r.erro)}</td><td>${resultado(r.resultado)}</td></tr>`).join("")}
      </tbody></table></div>`;
    if (inc) {
      html += `<details class="card componentes" style="margin-top:18px"><summary>Memória de cálculo da incerteza (EA-4/02)</summary>
        <p>u<sub>c</sub> = ${num(inc.incerteza_combinada_um, 4)} µm · v<sub>eff</sub> = ${num(Math.min(inc.veff, 99999), 1)} · k = ${num(inc.fator_k, 2)} · U = ${num(inc.incerteza_expandida_um, 4)} µm</p>
        <div class="tabela-wrap"><table><thead><tr><th>#</th><th>Componente</th><th>Valor (µm)</th><th>Divisor</th><th>Distribuição</th><th>ci</th><th>ui (µm)</th><th>vi</th></tr></thead><tbody>
        ${inc.componentes.map((k, i) => `<tr><td>${i + 1}</td><td class="ponto">${esc(k.descricao)}</td><td>${num(k.valor_um, 6)}</td><td>${num(k.divisor, 4)}</td><td>${esc(k.distribuicao)}</td><td>${num(k.ci, 4)}</td><td>${num(k.ui_um, 6)}</td><td>${num(k.vi, 0)}</td></tr>`).join("")}
        </tbody></table></div></details>`;
    }
    conteudo.innerHTML = html;
    if (c.url_validacao && $("#qr-cert")) S.desenharQr($("#qr-cert"), c.url_validacao, 170);
    const baixar = $("#baixar");
    if (baixar) baixar.addEventListener("click", async () => {
      baixar.disabled = true;
      try { const r = await api("GET", `/api/calibracoes/${id}/certificado`); window.open(r.url, "_blank", "noopener"); }
      catch (e) { S.mostrarAviso(e.message); } finally { baixar.disabled = false; }
    });
    const anular = $("#anular");
    if (anular) anular.addEventListener("click", async () => {
      const motivo = $("#motivo-anular").value.trim();
      if (!motivo) { S.mostrarAviso("Informe o motivo da anulação."); return; }
      anular.disabled = true;
      try { await api("POST", `/api/emissao/${id}/anular`, { motivo }); S.mostrarAviso("Certificado anulado."); await telaDetalhe(id); }
      catch (e) { anular.disabled = false; S.mostrarAviso(e.message); }
    });
    $$("[data-ir]").forEach((b) => b.addEventListener("click", () => { location.hash = b.dataset.ir; }));
    if ($("#emissao")) await montarEmissao(c, id);
  }

  function acaoPendencia(p, c, efetivos) {
    if (p.codigo === "INSTRUMENTO_NAO_CADASTRADO" && tem("admin")) {
      const q = new URLSearchParams({ aba: "instrumentos", novo: c.codigo_instrumento || "", denominacao: efetivos.denominacao || c.denominacao || "", setor: efetivos.cliente || c.cliente || "", fabricante: c.fabricante || "" });
      return ` <button class="btn mini sec" type="button" data-ir="#/cadastros?${esc(q.toString())}">Cadastrar instrumento</button>`;
    }
    if ((p.codigo === "PADRAO_VENCIDO" || p.codigo === "PADRAO_NAO_CADASTRADO") && tem("admin", "signatario")) {
      return ` <button class="btn mini sec" type="button" data-ir="#/cadastros?aba=padroes">Atualizar padrão</button>`;
    }
    if (p.codigo === "PADRAO_NAO_INFORMADO") return ' <span class="nota">Selecione o padrão no quadro de emissão abaixo.</span>';
    return "";
  }

  async function montarEmissao(c, id) {
    const caixa = $("#emissao");
    let padroes = [];
    try { padroes = (await api("GET", "/api/cadastros/padroes")).itens; } catch (e) { caixa.innerHTML = S.blocoErro(e); return; }
    const reprovado = c.resultado_geral === "REPROVADO";
    caixa.innerHTML = `<h2 style="margin-top:0">Emitir certificado</h2>
      <p class="nota">Ao emitir, o sistema reserva o número sequencial NNNNN/AAAA (RN01), gera o PDF com QR Code, calcula o SHA-256 e grava no bucket de certificados (versionado e com Object Lock).</p>
      <div id="emissao-msg"></div>
      <div class="grade">
        <div class="campo"><label for="padrao">Padrão utilizado (MMC)</label><select id="padrao">
          <option value="">Selecione…</option>
          ${padroes.map((p) => `<option value="${esc(p.codigo)}" ${p.codigo === c.codigo_padrao ? "selected" : ""}>${esc(p.codigo_exibicao)} · ${esc(p.descricao)} · ${esc(p.situacao)}</option>`).join("")}
        </select><span class="ajuda">Precisa estar com calibração válida na data da calibração (RN02).</span></div>
        <div class="campo"><label for="observacao">Observação adicional (opcional)</label><textarea id="observacao" rows="2" maxlength="400"></textarea></div>
      </div>
      ${reprovado ? `<label class="check"><input type="checkbox" id="ciente"><span><strong>Estou ciente</strong> de que ${(c.reprovadas || []).length} característica(s) estão REPROVADAS (${esc((c.reprovadas || []).join(", "))}) e confirmo a emissão do certificado com esse resultado (RF12/RN07).</span></label>` : ""}
      <div class="acoes"><button class="btn" id="emitir" type="button">Assinar e emitir certificado</button></div>
      <details style="margin-top:14px"><summary>Devolver para correção</summary>
        <div class="campo"><label for="motivo-devolver">Motivo</label><textarea id="motivo-devolver" rows="2" maxlength="400"></textarea></div>
        <button class="btn sec mini" id="devolver" type="button">Devolver ao técnico</button></details>`;
    $("#emitir").addEventListener("click", async () => {
      const botao = $("#emitir");
      const padrao = $("#padrao").value;
      if (!padrao) { $("#emissao-msg").innerHTML = S.blocoErro(new Error("Selecione o padrão utilizado.")); return; }
      if (reprovado && !$("#ciente").checked) { $("#emissao-msg").innerHTML = S.blocoErro(new Error("Confirme que está ciente das características reprovadas.")); return; }
      botao.disabled = true;
      $("#emissao-msg").innerHTML = '<div class="alerta aviso"><strong>Gerando o certificado…</strong><div class="progresso"><span></span></div></div>';
      try {
        const r = await api("POST", `/api/emissao/${id}`, { padrao, ciente_reprovacao: reprovado ? $("#ciente").checked : false, observacao: $("#observacao").value.trim() || null });
        S.mostrarAviso(`Certificado ${r.nr_certificado} emitido.`);
        await telaDetalhe(id);
      } catch (e) {
        $("#emissao-msg").innerHTML = S.blocoErro(e);
        botao.disabled = false;
      }
    });
    $("#devolver").addEventListener("click", async () => {
      const motivo = $("#motivo-devolver").value.trim();
      if (!motivo) { S.mostrarAviso("Informe o motivo da devolução."); return; }
      try { await api("POST", `/api/emissao/${id}/devolver`, { motivo }); S.mostrarAviso("Calibração devolvida."); await telaDetalhe(id); }
      catch (e) { S.mostrarAviso(e.message); }
    });
  }

  async function telaCadastros(params) {
    const g = geracao;
    carregando();
    const aba = params.get("aba") || "padroes";
    const abas = [["padroes", "Padrões (MMC)"], ["instrumentos", "Instrumentos"], ["procedimentos", "Procedimentos"]];
    conteudo.innerHTML = `<h1>Cadastros</h1><p class="sub">RF08 · instrumentos, padrões de medição e procedimentos.</p>
      <div class="abas" role="tablist">${abas.map(([k, r]) => `<button role="tab" type="button" data-aba="${k}" aria-selected="${k === aba}">${esc(r)}</button>`).join("")}</div>
      <div id="aba"></div>`;
    $$(".abas button").forEach((b) => b.addEventListener("click", () => { location.hash = `#/cadastros?aba=${b.dataset.aba}`; }));
    if (aba === "instrumentos") await abaInstrumentos(params, g);
    else if (aba === "procedimentos") await abaProcedimentos(g);
    else await abaPadroes(params, g);
  }

  async function abaPadroes(params, g) {
    const r = await api("GET", "/api/cadastros/padroes");
    if (g !== undefined && g !== geracao) return;
    const alvo = $("#aba");
    const pode = tem("admin", "signatario");
    alvo.innerHTML = `${r.itens.length ? `<div class="tabela-wrap lista"><table><thead><tr><th>Código</th><th>Descrição</th><th>Certificado do padrão</th><th>Última cal.</th><th>Próxima cal.</th><th>Situação</th><th></th></tr></thead><tbody>
      ${r.itens.map((p) => `<tr><td><strong>${esc(p.codigo_exibicao)}</strong></td><td>${esc(p.descricao)}<br><span class="nota">${esc(p.modelo || "")} · ${esc(p.fabricante)} · série ${esc(p.nr_serie)}</span></td>
        <td>${esc(p.cert_calibracao)}</td><td>${data(p.data_ultima_cal)}</td><td>${data(p.data_proxima_cal)}</td><td>${status(p.situacao)}</td>
        <td>${pode ? `<button class="btn mini sec" type="button" data-editar="${esc(p.codigo)}">Editar</button>` : ""}</td></tr>`).join("")}</tbody></table></div>`
      : '<div class="card vazio">Nenhum padrão cadastrado.</div>'}
      ${pode ? '<div class="acoes"><button class="btn" type="button" id="novo-padrao">Novo padrão</button></div>' : ""}
      <div id="form-padrao"></div>`;
    const abrir = (p) => formPadrao(p, r.componentes_sugeridos, r.temperatura_sugerida);
    $$("[data-editar]").forEach((b) => b.addEventListener("click", () => abrir(r.itens.find((p) => p.codigo === b.dataset.editar))));
    const novo = $("#novo-padrao");
    if (novo) novo.addEventListener("click", () => abrir(null));
  }

  function formPadrao(p, sugeridos, tempSugerida) {
    const alvo = $("#form-padrao");
    const comps = (p && p.componentes_incerteza) || sugeridos;
    const temp = Object.assign({}, tempSugerida, (p && p.temperatura) || {});
    const v = (k) => esc((p && p[k]) || "");
    alvo.innerHTML = `<form class="card" id="padrao-form" style="margin-top:18px">
      <h2 style="margin-top:0">${p ? `Editar padrão ${esc(p.codigo_exibicao)}` : "Novo padrão"}</h2><div id="padrao-msg"></div>
      <div class="grade">
        <div class="campo"><label>Código</label><input name="codigo" value="${v("codigo_exibicao")}" ${p ? "readonly" : ""} required placeholder="500.011"></div>
        <div class="campo"><label>Descrição</label><input name="descricao" value="${v("descricao")}" required></div>
        <div class="campo"><label>Modelo</label><input name="modelo" value="${v("modelo")}"></div>
        <div class="campo"><label>Fabricante</label><input name="fabricante" value="${v("fabricante")}" required></div>
        <div class="campo"><label>Nº de série</label><input name="nr_serie" value="${v("nr_serie")}" required></div>
        <div class="campo"><label>Nº do certificado de calibração do padrão</label><input name="cert_calibracao" value="${v("cert_calibracao")}" required></div>
        <div class="campo"><label>Última calibração</label><input name="data_ultima_cal" type="date" value="${v("data_ultima_cal")}" required></div>
        <div class="campo"><label>Próxima calibração</label><input name="data_proxima_cal" type="date" value="${v("data_proxima_cal")}" required></div>
      </div>
      <details class="componentes" open><summary>Parâmetros de incerteza (modelo da aba Incerteza – EA-4/02)</summary>
        <div class="tabela-wrap"><table><thead><tr><th>Componente</th><th>Valor (µm)</th><th>Divisor</th><th>Distribuição</th><th>ci</th><th>vi</th><th></th></tr></thead>
        <tbody id="comps">${comps.map(linhaComponente).join("")}</tbody></table></div>
        <div class="acoes"><button class="btn mini sec" type="button" id="add-comp">Adicionar componente</button></div>
        <div class="grade" style="margin-top:12px">
          <div class="campo"><label>Variação máx. de temperatura em relação a 20 °C</label><input name="variacao_temp_20" value="${esc(temp.variacao_temp_20)}"></div>
          <div class="campo"><label>Diferença de temperatura padrão × objeto (°C)</label><input name="dif_temp_padrao_objeto" value="${esc(temp.dif_temp_padrao_objeto)}"></div>
          <div class="campo"><label>Coef. de dilatação do objeto (10⁻⁶/°C)</label><input name="coef_dilatacao_objeto" value="${esc(temp.coef_dilatacao_objeto)}"></div>
        </div>
        <p class="nota">A dispersão das medidas (maior desvio padrão ÷ √2, v = 1) e os termos de temperatura são acrescentados automaticamente a cada calibração.</p>
      </details>
      <div class="acoes"><button class="btn" type="submit">Salvar</button><button class="btn sec" type="button" id="cancelar-padrao">Cancelar</button></div></form>`;
    $("#add-comp").addEventListener("click", () => $("#comps").insertAdjacentHTML("beforeend", linhaComponente({ descricao: "", valor_um: "", divisor: 2, distribuicao: "Normal", ci: 1, vi: 1000 })));
    $("#comps").addEventListener("click", (e) => { if (e.target.matches("[data-remover]")) e.target.closest("tr").remove(); });
    $("#cancelar-padrao").addEventListener("click", () => { alvo.innerHTML = ""; });
    $("#padrao-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      const corpo = {};
      ["codigo", "descricao", "modelo", "fabricante", "nr_serie", "cert_calibracao", "data_ultima_cal", "data_proxima_cal"].forEach((k) => { corpo[k] = String(f.get(k) || "").trim(); });
      corpo.temperatura = { variacao_temp_20: f.get("variacao_temp_20"), dif_temp_padrao_objeto: f.get("dif_temp_padrao_objeto"), coef_dilatacao_objeto: f.get("coef_dilatacao_objeto") };
      corpo.componentes_incerteza = $$("#comps tr").map((tr) => {
        const g = (n) => $(`[data-c="${n}"]`, tr).value.trim().replace(",", ".");
        return { descricao: $('[data-c="descricao"]', tr).value.trim(), valor_um: g("valor_um"), divisor: g("divisor"), distribuicao: $('[data-c="distribuicao"]', tr).value.trim(), ci: g("ci"), vi: g("vi") };
      });
      try {
        if (p) await api("PUT", `/api/cadastros/padroes/${encodeURIComponent(p.codigo)}`, corpo);
        else await api("POST", "/api/cadastros/padroes", corpo);
        S.mostrarAviso("Padrão salvo.");
        await telaCadastros(new URLSearchParams("aba=padroes"));
      } catch (erro) { $("#padrao-msg").innerHTML = S.blocoErro(erro); }
    });
    alvo.scrollIntoView({ behavior: "smooth" });
  }

  function linhaComponente(k) {
    const i = (n, valor) => `<td><input data-c="${n}" value="${esc(valor)}"></td>`;
    return `<tr>${i("descricao", k.descricao)}${i("valor_um", k.valor_um)}${i("divisor", typeof k.divisor === "number" ? Number(k.divisor.toFixed(6)) : k.divisor)}${i("distribuicao", k.distribuicao)}${i("ci", k.ci)}${i("vi", k.vi)}
      <td><button class="btn mini sec" type="button" data-remover>Remover</button></td></tr>`;
  }

  async function abaInstrumentos(params, g) {
    const r = await api("GET", "/api/cadastros/instrumentos");
    if (g !== undefined && g !== geracao) return;
    const alvo = $("#aba");
    const pode = tem("admin");
    alvo.innerHTML = `${r.itens.length ? `<div class="tabela-wrap lista"><table><thead><tr><th>Código</th><th>Denominação</th><th>Fabricante</th><th>Setor</th><th>Ativo</th><th></th></tr></thead><tbody>
      ${r.itens.map((i) => `<tr><td><strong>${esc(i.codigo)}</strong></td><td>${esc(i.denominacao)}</td><td>${esc(i.fabricante || "Não consta")}</td><td>${esc(i.setor)}</td><td>${i.ativo ? "Sim" : "Não"}</td>
        <td>${pode ? `<button class="btn mini sec" type="button" data-editar="${esc(i.codigo)}">Editar</button>` : ""}</td></tr>`).join("")}</tbody></table></div>` : '<div class="card vazio">Nenhum instrumento cadastrado.</div>'}
      ${pode ? '<div class="acoes"><button class="btn" type="button" id="novo-inst">Novo instrumento</button></div>' : ""}<div id="form-inst"></div>`;
    const abrir = (i, pre) => formInstrumento(i, pre);
    $$("[data-editar]").forEach((b) => b.addEventListener("click", () => abrir(r.itens.find((i) => i.codigo === b.dataset.editar))));
    const novo = $("#novo-inst");
    if (novo) novo.addEventListener("click", () => abrir(null));
    if (pode && params.get("novo")) abrir(null, { codigo: params.get("novo"), denominacao: params.get("denominacao"), setor: params.get("setor"), fabricante: params.get("fabricante") });
  }

  function formInstrumento(i, pre) {
    const alvo = $("#form-inst");
    const base = i || pre || {};
    const v = (k) => esc(base[k] || "");
    alvo.innerHTML = `<form class="card" id="inst-form" style="margin-top:18px"><h2 style="margin-top:0">${i ? `Editar ${esc(i.codigo)}` : "Novo instrumento"}</h2><div id="inst-msg"></div>
      <div class="grade">
        <div class="campo"><label>Código</label><input name="codigo" value="${v("codigo")}" ${i ? "readonly" : ""} required placeholder="703.1208"></div>
        <div class="campo"><label>Denominação</label><input name="denominacao" value="${v("denominacao")}" required></div>
        <div class="campo"><label>Fabricante</label><input name="fabricante" value="${base.fabricante === "Não consta" ? "" : v("fabricante")}" placeholder="vazio = Não consta"></div>
        <div class="campo"><label>Setor (cliente solicitante)</label><input name="setor" value="${v("setor")}" required></div>
        <div class="campo"><label>Faixa inicial (mm)</label><input name="faixa_inicial" value="${v("faixa_inicial")}"></div>
        <div class="campo"><label>Faixa final (mm)</label><input name="faixa_final" value="${v("faixa_final")}"></div>
        <div class="campo"><label>Ativo</label><select name="ativo"><option value="sim">Sim</option><option value="nao" ${i && i.ativo === false ? "selected" : ""}>Não</option></select></div>
      </div>
      <div class="acoes"><button class="btn" type="submit">Salvar</button><button class="btn sec" type="button" id="cancelar-inst">Cancelar</button></div></form>`;
    $("#cancelar-inst").addEventListener("click", () => { alvo.innerHTML = ""; });
    $("#inst-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      const corpo = {};
      ["codigo", "denominacao", "fabricante", "setor", "faixa_inicial", "faixa_final"].forEach((k) => { corpo[k] = String(f.get(k) || "").trim() || null; });
      corpo.ativo = f.get("ativo") !== "nao";
      try {
        if (i) await api("PUT", `/api/cadastros/instrumentos/${encodeURIComponent(i.codigo)}`, corpo);
        else await api("POST", "/api/cadastros/instrumentos", corpo);
        S.mostrarAviso("Instrumento salvo.");
        if (pre) history.back(); else await telaCadastros(new URLSearchParams("aba=instrumentos"));
      } catch (erro) { $("#inst-msg").innerHTML = S.blocoErro(erro); }
    });
    alvo.scrollIntoView({ behavior: "smooth" });
  }

  async function abaProcedimentos(g) {
    const r = await api("GET", "/api/cadastros/procedimentos");
    if (g !== undefined && g !== geracao) return;
    const alvo = $("#aba");
    const pode = tem("admin");
    alvo.innerHTML = `${r.itens.length ? `<div class="tabela-wrap lista"><table><thead><tr><th>Código</th><th>Revisão</th><th>Descrição</th><th>Tipo de instrumento (RN08)</th></tr></thead><tbody>
      ${r.itens.map((p) => `<tr><td><strong>${esc(p.codigo)}</strong></td><td>${esc(p.revisao)}</td><td>${esc(p.descricao)}</td><td>${esc(p.tipo_instrumento)}</td></tr>`).join("")}</tbody></table></div>` : '<div class="card vazio">Nenhum procedimento cadastrado.</div>'}
      ${pode ? `<form class="card" id="proc-form" style="margin-top:18px"><h2 style="margin-top:0">Novo procedimento</h2><div id="proc-msg"></div><div class="grade">
        <div class="campo"><label>Código</label><input name="codigo" required placeholder="ITL-0030"></div>
        <div class="campo"><label>Revisão</label><input name="revisao" type="number" min="0" required value="0"></div>
        <div class="campo"><label>Descrição</label><input name="descricao" required></div>
        <div class="campo"><label>Tipo de instrumento</label><input name="tipo_instrumento" required placeholder="Projetor de perfil"></div></div>
        <div class="acoes"><button class="btn" type="submit">Cadastrar</button></div></form>` : ""}`;
    const form = $("#proc-form");
    if (form) form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = new FormData(form);
      try {
        await api("POST", "/api/cadastros/procedimentos", { codigo: f.get("codigo").trim(), revisao: parseInt(f.get("revisao"), 10), descricao: f.get("descricao").trim(), tipo_instrumento: f.get("tipo_instrumento").trim() });
        S.mostrarAviso("Procedimento cadastrado.");
        await telaCadastros(new URLSearchParams("aba=procedimentos"));
      } catch (erro) { $("#proc-msg").innerHTML = S.blocoErro(erro); }
    });
  }

  async function telaAuditoria(params) {
    const g = geracao;
    carregando();
    const agora = new Date(Date.now() - 3 * 3600 * 1000).toISOString().slice(0, 7);
    const mes = params.get("mes") || agora;
    const r = await api("GET", `/api/auditoria?mes=${encodeURIComponent(mes)}`);
    if (g !== geracao) return;
    conteudo.innerHTML = `<h1>Trilha de auditoria</h1><p class="sub">Itens AUD# gravados no DynamoDB a cada ação de negócio (RNF03).</p>
      <form class="filtros card" id="f-aud"><div class="campo"><label for="mes">Mês</label><input id="mes" name="mes" type="month" value="${esc(mes)}"></div><button class="btn" type="submit">Ver</button></form>
      ${r.itens.length ? `<div class="tabela-wrap lista"><table><thead><tr><th>Quando</th><th>Usuário</th><th>Ação</th><th>Alvo</th><th>Resultado</th><th>Detalhes</th></tr></thead><tbody>
      ${r.itens.map((a) => `<tr><td>${dataHora(a.instante)}</td><td>${esc(a.usuario_nome)}</td><td><strong>${esc(a.acao)}</strong></td><td><span class="hash">${esc(a.alvo)}</span></td><td>${esc(a.resultado)}</td>
        <td><span class="hash">${esc(JSON.stringify(a.detalhes || {})).slice(0, 220)}</span></td></tr>`).join("")}</tbody></table></div>` : '<div class="card vazio">Nenhum registro neste mês.</div>'}`;
    $("#f-aud").addEventListener("submit", (e) => { e.preventDefault(); location.hash = `#/auditoria?mes=${$("#mes").value}`; });
  }

  if (usuarioDoToken()) iniciarApp(); else mostrarLogin();
})();
