"use strict";
// Planta 2D. Sem dependências externas; todo texto vindo da API entra por textContent (sem innerHTML).

const ESTADOS = {
  divergencia_confirmada: { rotulo: "Divergência confirmada", cor: "#9b2c2c", icone: "!", texto: "#fff" },
  possivel_divergencia:   { rotulo: "Possível divergência",  cor: "#dd6b20", icone: "!", texto: "#fff" },
  inconclusivo:           { rotulo: "Inconclusivo (verificar)", cor: "#718096", icone: "?", texto: "#fff", hachura: true },
  sem_observacao:         { rotulo: "Sem foto recente",      cor: "#e2e8f0", icone: "–", texto: "#4a5568", tracejado: true },
  vazio_coerente:         { rotulo: "Vazio, sem estoque no sistema", cor: "#a0aec0", icone: "○", texto: "#1d2430" },
  parcial:                { rotulo: "Parcial",               cor: "#d69e2e", icone: "◐", texto: "#1d2430" },
  ok:                     { rotulo: "Abastecida",            cor: "#2f855a", icone: "✓", texto: "#fff" },
};
const CLASSES = { vazio: "vazia", parcial: "parcial", ocupado: "ocupada", indeterminado: "indeterminada" };
const SITUACOES = {
  alinhada: "foto alinhada", marcadores_insuficientes: "marcadores insuficientes", alinhamento_impreciso: "alinhamento impreciso",
  resolucao_insuficiente: "foto distante demais", exposicao_inadequada: "luz inadequada", desfocada: "foto tremida",
  sem_referencia: "sem foto de referência",
};
const SVG = "http://www.w3.org/2000/svg";
const $ = (id) => document.getElementById(id);
let selecionada = null;

function el(tag, attrs = {}, texto) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (texto !== undefined) e.textContent = texto;
  return e;
}
function svg(tag, attrs = {}, texto) {
  const e = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (texto !== undefined) e.textContent = texto;
  return e;
}
const hora = (iso) => iso ? new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "—";

async function api(caminho, opcoes = {}) {
  const r = await fetch(caminho, { credentials: "same-origin", headers: { "Content-Type": "application/json" }, ...opcoes });
  if (r.status === 401) { mostrarLogin(); throw new Error("sessão"); }
  return r;
}

function mostrarLogin() {
  $("tela-login").hidden = false; $("tela-planta").hidden = true; $("sair").hidden = true; fecharPainel();
}

function hachura(defs) {
  const p = svg("pattern", { id: "hachura", width: 16, height: 16, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" });
  p.append(svg("rect", { width: 16, height: 16, fill: ESTADOS.inconclusivo.cor }), svg("line", { x1: 0, y1: 0, x2: 0, y2: 16, stroke: "#fff", "stroke-width": 5, "stroke-opacity": 0.45 }));
  defs.append(p);
}

function legenda() {
  const ul = $("legenda"); ul.replaceChildren();
  for (const e of Object.values(ESTADOS)) {
    const s = svg("svg", { width: 18, height: 18, viewBox: "0 0 18 18", "aria-hidden": "true" });
    s.append(svg("rect", { x: 1, y: 1, width: 16, height: 16, rx: 3, fill: e.cor, stroke: "#4a5568", "stroke-dasharray": e.tracejado ? "3 2" : "none" }));
    const li = el("li"); li.append(s, document.createTextNode(e.rotulo)); ul.append(li);
  }
}

function desenharFace(face) {
  const caixa = el("article", { class: "face" });
  caixa.append(el("h2", {}, `Gôndola ${face.codigo}`));
  const f = face.ultima_foto;
  caixa.append(el("p", { class: "discreto" }, f ? `Última foto: ${hora(f.capturado_em)} · ${SITUACOES[f.situacao] || f.situacao}` : "Ainda sem foto processada"));
  const s = svg("svg", { viewBox: `0 0 ${face.largura_mm} ${face.altura_mm}`, role: "group", "aria-label": `Planta da gôndola ${face.codigo}` });
  const defs = svg("defs"); hachura(defs); s.append(defs);
  s.append(svg("rect", { x: 0, y: 0, width: face.largura_mm, height: face.altura_mm, fill: "#edf2f7", stroke: "#a0aec0", "stroke-width": 3, rx: 8 }));
  for (const p of face.posicoes) {
    const e = ESTADOS[p.estado];
    const g = svg("g", { class: "posicao", tabindex: 0, role: "button", "data-id": p.id,
                          "aria-label": `${p.codigo}: ${p.produto ? p.produto.descricao : "sem produto"} — ${e.rotulo}` });
    g.append(svg("rect", { class: "caixa", x: p.x_mm, y: p.y_mm, width: p.largura_mm, height: p.altura_mm, rx: 6,
                           fill: e.hachura ? "url(#hachura)" : e.cor, stroke: "#2d3748", "stroke-width": 2,
                           "stroke-dasharray": e.tracejado ? "10 6" : "none" }));
    g.append(svg("text", { class: "icone", x: p.x_mm + p.largura_mm / 2, y: p.y_mm + p.altura_mm / 2, "text-anchor": "middle", "dominant-baseline": "middle", fill: e.texto }, e.icone));
    g.append(svg("text", { class: "rotulo", x: p.x_mm + 8, y: p.y_mm + 26, fill: e.texto }, p.codigo.split("/").pop()));
    if (p.produto) g.append(svg("text", { class: "rotulo", x: p.x_mm + 8, y: p.y_mm + p.altura_mm - 12, fill: e.texto }, p.produto.descricao.slice(0, 22)));
    g.append(svg("title", {}, `${p.codigo} — ${e.rotulo}`));
    const abrir = () => abrirPainel(face, p, g);
    g.addEventListener("click", abrir);
    g.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); abrir(); } });
    s.append(g);
  }
  caixa.append(s);
  return caixa;
}

function linha(dl, rotulo, valor) { dl.append(el("dt", {}, rotulo), el("dd", {}, valor)); }

function abrirPainel(face, p, g) {
  document.querySelectorAll(".posicao.selecionada").forEach((x) => x.classList.remove("selecionada"));
  g.classList.add("selecionada"); selecionada = p.id;
  const e = ESTADOS[p.estado], c = $("painel-conteudo"); c.replaceChildren();
  c.append(el("h3", {}, p.codigo));
  const selo = el("span", { class: "selo" }, e.rotulo); selo.setAttribute("data-estado", p.estado); c.append(selo);
  const dl = el("dl");
  linha(dl, "Produto esperado", p.produto ? p.produto.descricao : "—");
  linha(dl, "Na foto", p.observacao ? `${CLASSES[p.observacao.classe]} (confiança ${Math.round(p.observacao.confianca * 100)}%)` : "sem observação");
  linha(dl, "Foto de", p.observacao ? hora(p.observacao.capturado_em) : "—");
  linha(dl, "No sistema", p.estoque ? `${p.estoque.quantidade} un. em ${new Date(p.estoque.data_referencia + "T12:00").toLocaleDateString("pt-BR")}` : "sem estoque importado");
  linha(dl, "Alerta", p.alerta ? `${{ D1: "vazio com estoque no sistema", D2: "produto sem estoque no sistema", V: "verificar" }[p.alerta.tipo]} · ${p.alerta.status.replace("_", " ")}` : "nenhum");
  c.append(dl);
  if (p.alerta && ["aberto", "em_verificacao", "confirmado"].includes(p.alerta.status)) c.append(acoes(p.alerta));
  if (face.ultima_foto && face.ultima_foto.captura_id) {
    const img = el("img", { alt: `Última foto da gôndola ${face.codigo}`, src: `/capturas/${face.ultima_foto.captura_id}/imagem` });
    c.append(img);
  }
  $("painel").hidden = false; document.querySelector("main").classList.add("com-painel");
}

const CAUSAS = { reposicao_pendente: "Reposição pendente", estoque_fantasma: "Estoque fantasma", fora_de_posicao: "Produto fora de posição",
                 planograma_desatualizado: "Planograma desatualizado", outra: "Outra" };
const MOTIVOS = { produto_presente: "Produto estava presente", foto_inadequada: "Foto inadequada", planograma_errado: "Planograma errado", outro: "Outro" };

function seletor(nome, opcoes) {
  const s = el("select", { name: nome, "aria-label": nome });
  for (const [v, t] of Object.entries(opcoes)) s.append(el("option", { value: v }, t));
  return s;
}

function acoes(alerta) {
  const div = el("div", { class: "acoes" });
  const botoes = {
    aberto: [["em_verificacao", "Em verificação"], ["confirmado", "Confirmar"], ["rejeitado", "Rejeitar"]],
    em_verificacao: [["confirmado", "Confirmar"], ["rejeitado", "Rejeitar"]],
    confirmado: [["resolvido", "Marcar resolvido"]],
  }[alerta.status] || [];
  const causa = seletor("causa", CAUSAS), motivo = seletor("motivo", MOTIVOS);
  if (botoes.some(([p]) => p === "confirmado")) div.append(el("label", {}, "Causa ao confirmar"), causa);
  if (botoes.some(([p]) => p === "rejeitado")) div.append(el("label", {}, "Motivo ao rejeitar"), motivo);
  for (const [para, rotulo] of botoes) {
    const b = el("button", { class: para === "rejeitado" ? "secundario" : "", "data-acao": para }, rotulo);
    b.addEventListener("click", () => transicionar(alerta, para, div, causa.value, motivo.value));
    div.append(b);
  }
  div.append(el("p", { class: "erro", role: "alert" }));
  return div;
}

async function transicionar(alerta, para, div, causa, motivo) {
  const corpo = { versao: alerta.versao, para };
  if (para === "confirmado") corpo.causa = causa;
  if (para === "rejeitado") corpo.motivo = motivo;
  const r = await api(`/alertas/${alerta.id}/transicoes`, { method: "POST", body: JSON.stringify(corpo) });
  if (r.ok) { await carregar(); return; }
  div.querySelector(".erro").textContent = { 403: "Seu papel não permite esta ação.", 409: "O alerta mudou; atualize a planta.", 422: "Dados inválidos." }[r.status] || "Não foi possível registrar.";
}

function fecharPainel() { $("painel").hidden = true; document.querySelector("main").classList.remove("com-painel"); selecionada = null; }

async function carregar() {
  const r = await api("/planta");
  if (!r.ok) return;
  const { faces, sintetico } = await r.json();
  $("aviso-sintetico").hidden = !sintetico;
  $("tela-login").hidden = true; $("tela-planta").hidden = false; $("sair").hidden = false;
  const alvo = $("faces"); alvo.replaceChildren();
  if (!faces.length) alvo.append(el("p", { class: "discreto" }, "Nenhuma gôndola cadastrada."));
  for (const f of faces) alvo.append(desenharFace(f));
  if (selecionada !== null) {
    const g = document.querySelector(`.posicao[data-id="${selecionada}"]`);
    const face = faces.find((f) => f.posicoes.some((p) => p.id === selecionada));
    if (g && face) abrirPainel(face, face.posicoes.find((p) => p.id === selecionada), g);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  legenda();
  for (const [k, e] of Object.entries(ESTADOS)) {   // cores dos selos sem estilo inline
    const regra = `.selo[data-estado="${k}"] { background: ${e.cor}; color: ${e.texto}; }`;
    document.styleSheets[0].insertRule(regra, document.styleSheets[0].cssRules.length);
  }
  $("form-login").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const d = new FormData(ev.target);
    const r = await fetch("/sessao", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
                                       body: JSON.stringify({ login: d.get("login"), senha: d.get("senha") }) });
    if (r.ok) { $("erro-login").textContent = ""; ev.target.reset(); await carregar(); }
    else $("erro-login").textContent = "Login ou senha inválidos.";
  });
  $("sair").addEventListener("click", async () => { await fetch("/sessao", { method: "DELETE", credentials: "same-origin" }); mostrarLogin(); });
  $("atualizar").addEventListener("click", carregar);
  $("fechar-painel").addEventListener("click", fecharPainel);
  carregar().catch(() => mostrarLogin());
});
