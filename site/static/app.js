// Utilidades compartidas por las páginas del sitio.
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function money(v, cur = "USD") {
  return v == null ? "—" : new Intl.NumberFormat("es-CR", { style: "currency", currency: cur }).format(v);
}
function fmtDate(iso) {
  return iso ? new Date(iso).toLocaleString("es-CR", { dateStyle: "medium", timeStyle: "short" }) : "—";
}
const SOURCE_NAMES = { metacritic: "Metacritic", steam_users: "Usuarios de Steam" };

// Calificación de 0 a 100 como estrellas (puntaje / 20) más el número.
function stars(score, source, withNumber = true) {
  if (score == null) return `<span class="meta">Sin calificación</span>`;
  const label = `${score} de 100${source ? " · " + (SOURCE_NAMES[source] || source) : ""}`;
  const number = withNumber ? `<span class="score">${Math.round(score)}</span>` : "";
  return `<span class="rating" title="${esc(label)}"><span class="stars" role="img" aria-label="${esc(label)}" style="--pct:${Math.max(0, Math.min(100, score))}%"></span>` +
         number + "</span>";
}

function priceBox(price, regular, pct) {
  const old = regular && regular > price ? `<span class="regular">${money(regular)}</span>` : "";
  const badge = pct > 0 ? `<span class="pct">-${pct}%</span>` : "";
  return `<span class="pricebox${badge ? "" : " plain"}">${badge}<span class="prices">${old}<span class="price">${money(price)}</span></span></span>`;
}

function card(c) {
  const tags = (c.historic_low ? `<span class="tag low">mínimo histórico</span>` : "") +
               (c.stale_sources.length ? `<span class="tag stale" title="${esc(c.stale_sources.join(", "))}">datos desactualizados</span>` : "") +
               (c.store_count === 1 ? `<span class="tag single">solo en ${esc(c.best_store)}</span>` : "");
  const where = c.store_count > 1 ? `Mejor precio en ${esc(c.best_store)} · ${c.store_count} tiendas` : esc(c.best_store);
  return `<a class="card" href="game.html?id=${c.id}">
    <div class="thumb" data-title="${esc(c.title)}"><img loading="lazy" src="${esc(c.image)}" alt=""></div>
    <div class="body">
      <h3>${esc(c.title)}</h3>
      <div class="chips">${c.platforms.map(p => `<span class="chip">${esc(p)}</span>`).join("")}</div>
      ${tags ? `<div class="tags">${tags}</div>` : ""}
      <div class="foot">${stars(c.rating, c.rating_source)}${priceBox(c.best_price, c.regular, c.discount_pct)}</div>
      <div class="meta">${where}</div>
    </div></a>`;
}

// Imagen que no carga (la tienda la movió o la borró): se oculta y el recuadro muestra el título.
document.addEventListener("error", e => {
  if (e.target instanceof HTMLImageElement) e.target.classList.add("broken");
}, true);
