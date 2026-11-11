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
               (c.stale_sources.length ? `<span class="tag stale" title="${esc(c.stale_sources.join(", "))}">datos desactualizados</span>` : "");
  return `<a class="card" href="game.html?id=${c.id}">
    <img loading="lazy" src="${esc(c.image)}" alt="">
    <div class="body">
      <h3>${esc(c.title)}</h3>
      <div><span class="price">${money(c.best_price)}</span>${reg}${pct}</div>
      <div class="meta">${esc(c.best_store)} · ${esc(c.platforms.join(", "))}</div>
      <div class="meta">Calificación: ${c.rating ?? "—"}</div>
      <div>${tags}</div>
    </div></a>`;
}
