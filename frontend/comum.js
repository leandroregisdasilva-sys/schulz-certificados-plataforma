(function () {
  "use strict";

  const CFG = Object.assign(
    { regiao: "sa-east-1", userPoolId: "", clientId: "", modo: "cloudfront", api: {}, cognitoEndpoint: "" },
    window.SCHULZ_CONFIG || {}
  );
  CFG.api = Object.assign({ padrao: "", uploads: "", emissao: "", cadastros: "" }, CFG.api || {});

  const memoria = {};
  const armazenamento = {
    ler(chave) {
      try { return window.sessionStorage.getItem(chave); } catch (e) { return memoria[chave] || null; }
    },
    gravar(chave, valor) {
      memoria[chave] = valor;
      try { window.sessionStorage.setItem(chave, valor); } catch (e) { }
    },
    apagar(chave) {
      delete memoria[chave];
      try { window.sessionStorage.removeItem(chave); } catch (e) { }
    }
  };

  function esc(valor) {
    return String(valor === null || valor === undefined ? "" : valor)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function num(valor, casas) {
    if (valor === null || valor === undefined || valor === "" || Number.isNaN(Number(valor))) return "-";
    const c = casas === undefined ? 4 : casas;
    return new Intl.NumberFormat("pt-BR", { minimumFractionDigits: c, maximumFractionDigits: c }).format(Number(valor));
  }

  function data(iso) {
    if (!iso) return "-";
    const partes = String(iso).slice(0, 10).split("-");
    return partes.length === 3 ? `${partes[2]}/${partes[1]}/${partes[0]}` : String(iso);
  }

  function dataHora(iso) {
    if (!iso) return "-";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo", dateStyle: "short", timeStyle: "short" });
  }

  async function sha256Hex(conteudo) {
    if (!window.crypto || !window.crypto.subtle) return null;
    const bytes = typeof conteudo === "string" ? new TextEncoder().encode(conteudo) : conteudo;
    const resumo = await window.crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(resumo)).map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  function baseDaRota(caminho) {
    if (caminho.startsWith("/api/uploads")) return CFG.api.uploads;
    if (caminho.startsWith("/api/emissao")) return CFG.api.emissao;
    if (caminho.startsWith("/api/cadastros")) return CFG.api.cadastros;
    return CFG.api.padrao;
  }

  class ErroApi extends Error {
    constructor(status, corpo) {
      const erro = (corpo && corpo.erro) || {};
      super(erro.mensagem || `Falha na comunicação com o servidor (HTTP ${status}).`);
      this.status = status;
      this.codigo = erro.codigo || "ERRO_HTTP";
      this.detalhes = erro.detalhes || [];
    }
  }

  const sessao = {
    tokens() {
      const bruto = armazenamento.ler("schulz.sessao");
      if (!bruto) return null;
      try { return JSON.parse(bruto); } catch (e) { return null; }
    },
    salvar(resultado, usuario) {
      const atual = this.tokens() || {};
      const dados = {
        idToken: resultado.IdToken,
        accessToken: resultado.AccessToken,
        refreshToken: resultado.RefreshToken || atual.refreshToken,
        usuario: usuario || atual.usuario,
        expiraEm: Date.now() + (resultado.ExpiresIn || 3600) * 1000
      };
      armazenamento.gravar("schulz.sessao", JSON.stringify(dados));
      return dados;
    },
    limpar() { armazenamento.apagar("schulz.sessao"); },
    reivindicacoes() {
      const t = this.tokens();
      if (!t || !t.idToken) return null;
      try {
        const carga = t.idToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
        const json = decodeURIComponent(atob(carga).split("").map((c) => "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2)).join(""));
        return JSON.parse(json);
      } catch (e) { return null; }
    }
  };

  async function cognito(alvo, corpo) {
    const endpoint = CFG.cognitoEndpoint || `https://cognito-idp.${CFG.regiao}.amazonaws.com/`;
    const r = await fetch(endpoint, {
      method: "POST",
      headers: { "content-type": "application/x-amz-json-1.1", "x-amz-target": `AWSCognitoIdentityProviderService.${alvo}` },
      body: JSON.stringify(corpo)
    });
    const texto = await r.text();
    let json = {};
    try { json = texto ? JSON.parse(texto) : {}; } catch (e) { json = {}; }
    if (!r.ok) {
      const tipo = String(json.__type || json.code || "Erro").split("#").pop();
      const mensagens = {
        NotAuthorizedException: "E-mail ou senha incorretos.",
        UserNotFoundException: "E-mail ou senha incorretos.",
        CodeMismatchException: "Código do autenticador inválido. Confira o horário do celular e tente de novo.",
        EnableSoftwareTokenMFAException: "Código do autenticador inválido. Tente o próximo código.",
        InvalidPasswordException: "A nova senha não atende à política: mínimo de 10 caracteres com maiúscula, minúscula, número e símbolo.",
        ExpiredCodeException: "O código expirou. Gere um novo no aplicativo autenticador.",
        TooManyRequestsException: "Muitas tentativas. Aguarde um minuto.",
        LimitExceededException: "Muitas tentativas. Aguarde alguns minutos.",
        PasswordResetRequiredException: "É necessário redefinir a senha. Fale com o administrador."
      };
      const erro = new Error(mensagens[tipo] || json.message || "Falha na autenticação.");
      erro.tipo = tipo;
      throw erro;
    }
    return json;
  }

  async function renovar() {
    const t = sessao.tokens();
    if (!t || !t.refreshToken) return false;
    try {
      const r = await cognito("InitiateAuth", {
        AuthFlow: "REFRESH_TOKEN_AUTH",
        ClientId: CFG.clientId,
        AuthParameters: { REFRESH_TOKEN: t.refreshToken }
      });
      if (r.AuthenticationResult) {
        sessao.salvar(r.AuthenticationResult);
        return true;
      }
    } catch (e) { }
    return false;
  }

  async function api(metodo, caminho, corpo, opcoes) {
    const config = Object.assign({ publico: false, tentativa: 0 }, opcoes || {});
    const cabecalhos = {};
    let texto;
    if (corpo !== undefined) {
      texto = JSON.stringify(corpo);
      cabecalhos["content-type"] = "application/json";
      const hash = await sha256Hex(texto);
      if (hash) cabecalhos["x-amz-content-sha256"] = hash;
    }
    if (!config.publico) {
      const t = sessao.tokens();
      if (t && t.idToken) cabecalhos["x-token-acesso"] = t.idToken;
    }
    let r;
    try {
      r = await fetch(baseDaRota(caminho) + caminho, { method: metodo, headers: cabecalhos, body: texto });
    } catch (e) {
      throw new ErroApi(0, { erro: { mensagem: "Sem conexão com o servidor. Verifique a internet e tente novamente." } });
    }
    let json = null;
    const bruto = await r.text();
    try { json = bruto ? JSON.parse(bruto) : null; } catch (e) { json = null; }
    if (r.status === 401 && !config.publico && config.tentativa === 0 && await renovar()) {
      return api(metodo, caminho, corpo, Object.assign({}, config, { tentativa: 1 }));
    }
    if (!r.ok) {
      const erro = new ErroApi(r.status, json);
      if (r.status === 401 && !config.publico) {
        sessao.limpar();
        window.dispatchEvent(new CustomEvent("schulz:sessao-expirada"));
      }
      throw erro;
    }
    return json;
  }

  function mostrarAviso(mensagem) {
    const antigo = document.querySelector(".toast");
    if (antigo) antigo.remove();
    const el = document.createElement("div");
    el.className = "toast";
    el.setAttribute("role", "status");
    el.textContent = mensagem;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 4200);
  }

  function blocoErro(erro) {
    const detalhes = (erro.detalhes || []).map((d) => `<li>${esc(d)}</li>`).join("");
    return `<div class="alerta erro" role="alert"><strong>${esc(erro.message || "Erro inesperado.")}</strong>${detalhes ? `<ul>${detalhes}</ul>` : ""}</div>`;
  }

  function desenharQr(elemento, texto, lado) {
    elemento.innerHTML = "";
    if (window.QRCode) {
      new window.QRCode(elemento, { text: texto, width: lado || 160, height: lado || 160, correctLevel: window.QRCode.CorrectLevel.M });
    } else {
      elemento.innerHTML = `<a href="${esc(texto)}">${esc(texto)}</a>`;
    }
  }

  window.Schulz = { CFG, esc, num, data, dataHora, sha256Hex, api, cognito, sessao, ErroApi, mostrarAviso, blocoErro, desenharQr };
})();
