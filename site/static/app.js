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
function card(c) {
  const reg = c.regular && c.regular > c.best_price ? `<span class="regular">${money(c.regular)}</span>` : "";
  const pct = c.discount_pct > 0 ? `<span class="pct">-${c.discount_pct}%</span>` : "";
  const tags = (c.historic_low ? `<span class="tag low">mínimo histórico</span>` : "") +
               (c.stale_sources.length ? `<span class="tag stale" title="${esc(c.stale_sources.join(", "))}">datos desactualizados</span>` : "") +
               (c.store_count === 1 ? `<span class="tag single">solo en ${esc(c.best_store)}</span>` : "");
  return `<a class="card" href="game.html?id=${c.id}">
    <div class="thumb" data-title="${esc(c.title)}"><img loading="lazy" src="${esc(c.image)}" alt=""></div>
    <div class="body">
      <h3>${esc(c.title)}</h3>
      <div><span class="price">${money(c.best_price)}</span>${reg}${pct}</div>
      <div class="meta">${esc(c.best_store)}${c.store_count > 1 ? ` (de ${c.store_count} tiendas)` : ""} · ${esc(c.platforms.join(", "))}</div>
      <div class="meta">Calificación: ${c.rating ?? "—"}</div>
      <div>${tags}</div>
    </div></a>`;
}

// Imagen que no carga (la tienda la movió o la borró): se oculta y el recuadro muestra el título.
document.addEventListener("error", e => {
  if (e.target instanceof HTMLImageElement) e.target.classList.add("broken");
}, true);
