(function () {
  "use strict";

  const S = window.Schulz;
  const { esc, num, data, dataHora } = S;
  const $ = (sel) => document.querySelector(sel);
  const resultado = $("#resultado");
  let fluxo = null;
  let leituraAtiva = false;

  function codigoDe(texto) {
    const t = String(texto || "").trim();
    try {
      const url = new URL(t);
      return url.searchParams.get("c") || "";
    } catch (e) {
      return t;
    }
  }

  async function validar(codigo) {
    const c = codigoDe(codigo);
    if (!c) return;
    resultado.innerHTML = '<div class="carregando">Consultando certificado…<div class="progresso"><span></span></div></div>';
    try {
      const v = await S.api("GET", `/api/validacao/${encodeURIComponent(c)}`, undefined, { publico: true });
      mostrar(v, c);
      if (history.replaceState) history.replaceState(null, "", `validar.html?c=${encodeURIComponent(c)}`);
    } catch (e) {
      if (e.status === 404) {
        resultado.innerHTML = `<div class="selo inexistente"><div class="grande">CERTIFICADO NÃO ENCONTRADO</div>
          <div class="menor">O código <strong>${esc(c)}</strong> não corresponde a nenhum certificado emitido. Não utilize o instrumento e procure o laboratório.</div></div>`;
      } else {
        resultado.innerHTML = S.blocoErro(e);
      }
    }
    resultado.scrollIntoView({ behavior: "smooth" });
  }

  function mostrar(v, codigo) {
    const valido = v.situacao === "VALIDO";
    const aprovado = v.resultado_geral === "APROVADO";
    let selo;
    if (!valido) {
      selo = `<div class="selo anulado"><div class="grande">CERTIFICADO ANULADO</div>
        <div class="menor">Nº ${esc(v.nr_certificado)} anulado em ${dataHora(v.anulado_em)}. Motivo: ${esc(v.motivo_anulacao || "-")}. Não utilize este certificado.</div></div>`;
    } else if (aprovado) {
      selo = `<div class="selo valido aprovado"><div class="grande">✓ CERTIFICADO VÁLIDO · APROVADO</div>
        <div class="menor">Instrumento ${esc(v.instrumento.codigo)} com todas as ${v.total_caracteristicas} características dentro da tolerância.</div></div>`;
    } else {
      selo = `<div class="selo valido reprovado"><div class="grande">✕ CERTIFICADO VÁLIDO · REPROVADO</div>
        <div class="menor">${v.reprovadas.length} característica(s) fora da tolerância. Verifique com o laboratório antes de usar o instrumento.</div></div>`;
    }
    resultado.innerHTML = `${selo}
      <dl class="meta">
        <div><dt>Certificado</dt><dd>${esc(v.nr_certificado)}</dd></div>
        <div><dt>Emissão</dt><dd>${dataHora(v.data_emissao)}</dd></div>
        <div><dt>Instrumento</dt><dd>${esc(v.instrumento.codigo)} · ${esc(v.instrumento.denominacao)}</dd></div>
        <div><dt>Solicitante</dt><dd>${esc(v.solicitante || "-")}</dd></div>
        <div><dt>Data da calibração</dt><dd>${data(v.data_calibracao)}</dd></div>
        <div><dt>Incerteza expandida</dt><dd>U = ${num(v.incerteza_expandida_mm, 4)} mm · k = ${num(v.fator_k, 2)}</dd></div>
        <div><dt>Padrão</dt><dd>${esc(v.padrao.codigo || "-")} · ${esc(v.padrao.descricao || "")}</dd></div>
        <div><dt>Signatário</dt><dd>${esc(v.signatario || "-")}</dd></div>
      </dl>
      <section class="card" style="margin-bottom:18px">
        <h2 style="margin-top:0">Características (${v.total_caracteristicas})</h2>
        <ul class="lista-car">${v.caracteristicas.map((c) => `<li><span>${esc(c.parametro)}</span>${c.resultado === "APROVADO" ? '<span class="badge ok">APROVADO</span>' : '<span class="badge bad">REPROVADO</span>'}</li>`).join("")}</ul>
      </section>
      <section class="card" style="margin-bottom:18px">
        <h2 style="margin-top:0">Conferir a integridade do PDF</h2>
        <p class="nota">Selecione o arquivo PDF recebido. O navegador calcula o SHA-256 localmente (o arquivo não é enviado) e compara com o hash registrado na emissão.</p>
        <p class="hash">Hash registrado: ${esc(v.sha256)}</p>
        <input type="file" id="pdf" accept="application/pdf,.pdf">
        <div id="hash-resultado" style="margin-top:10px"></div>
      </section>
      <p class="nota">Consulta realizada em ${dataHora(v.consultado_em)} · código ${esc(codigo)}</p>`;
    $("#pdf").addEventListener("change", async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      const alvo = $("#hash-resultado");
      const hash = await S.sha256Hex(new Uint8Array(await f.arrayBuffer()));
      if (!hash) { alvo.innerHTML = '<div class="alerta aviso">Este navegador não permite calcular o hash nesta página.</div>'; return; }
      alvo.innerHTML = hash === v.sha256
        ? `<div class="alerta" style="background:var(--ok-bg);border-color:var(--ok-bd);color:var(--ok-tx)"><strong>Arquivo íntegro</strong>O PDF é idêntico ao emitido pelo sistema.</div>`
        : `<div class="alerta erro"><strong>Arquivo diferente do original</strong>O hash calculado (${esc(hash)}) não confere. O PDF pode ter sido alterado.</div>`;
    });
  }

  async function abrirCamera() {
    if (!("BarcodeDetector" in window) || !navigator.mediaDevices) {
      S.mostrarAviso("Leitura pela câmera indisponível neste navegador. Use a câmera do celular ou digite o código.");
      return;
    }
    try {
      const detector = new window.BarcodeDetector({ formats: ["qr_code"] });
      fluxo = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
      const video = $("#camera");
      video.srcObject = fluxo;
      video.hidden = false;
      await video.play();
      $("#abrir-camera").hidden = true;
      $("#parar-camera").hidden = false;
      leituraAtiva = true;
      const ler = async () => {
        if (!leituraAtiva) return;
        try {
          const codigos = await detector.detect(video);
          if (codigos.length) { pararCamera(); validar(codigos[0].rawValue); return; }
        } catch (e) { }
        requestAnimationFrame(ler);
      };
      ler();
    } catch (e) {
      S.mostrarAviso("Não foi possível acessar a câmera. Verifique a permissão do navegador.");
      pararCamera();
    }
  }

  function pararCamera() {
    leituraAtiva = false;
    if (fluxo) fluxo.getTracks().forEach((t) => t.stop());
    fluxo = null;
    $("#camera").hidden = true;
    $("#abrir-camera").hidden = false;
    $("#parar-camera").hidden = true;
  }

  $("#abrir-camera").addEventListener("click", abrirCamera);
  $("#parar-camera").addEventListener("click", pararCamera);
  $("#form-codigo").addEventListener("submit", (e) => { e.preventDefault(); validar($("#codigo").value); });

  const inicial = new URLSearchParams(location.search).get("c");
  if (inicial) { $("#codigo").value = inicial; validar(inicial); }
})();
