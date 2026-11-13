/**
 * Suivi de Colis Card — Lovelace card for package tracking.
 *
 * Auto-discovers the Suivi de Colis sensors (attribute tracking_number) and
 * shows them the Home Assistant way: summary badges, packages grouped by
 * urgency, history on click. Uses the theme variables, so it follows light /
 * dark themes and custom themes.
 *
 * Config (all optional):
 *   type: custom:suivi-colis-card
 *   title: Colis
 *   show_delivered: true
 */

const CARD_VERSION = "1.2.0";

// status -> label, icon, theme colour variable (+ fallback), group
const STATUS = {
  out_for_delivery: { label: "En livraison", icon: "mdi:truck-fast", color: "var(--orange-color, #ff9800)", group: "route" },
  available_for_pickup: { label: "À retirer", icon: "mdi:store-marker", color: "var(--purple-color, #9c27b0)", group: "action" },
  delivery_failure: { label: "Échec de livraison", icon: "mdi:package-variant-remove", color: "var(--red-color, #f44336)", group: "action" },
  exception: { label: "Problème", icon: "mdi:alert-circle-outline", color: "var(--red-color, #f44336)", group: "action" },
  not_found: { label: "Introuvable", icon: "mdi:help-circle-outline", color: "var(--grey-color, #9e9e9e)", group: "action" },
  expired: { label: "Suivi expiré", icon: "mdi:clock-alert-outline", color: "var(--brown-color, #795548)", group: "action" },
  in_transit: { label: "En transit", icon: "mdi:truck-delivery", color: "var(--blue-color, #2196f3)", group: "route" },
  info_received: { label: "Expédition annoncée", icon: "mdi:package-variant-closed", color: "var(--blue-grey-color, #607d8b)", group: "route" },
  unknown: { label: "Inconnu", icon: "mdi:package-variant", color: "var(--grey-color, #9e9e9e)", group: "route" },
  delivered: { label: "Livré", icon: "mdi:package-variant-closed-check", color: "var(--green-color, #4caf50)", group: "delivered" },
};
const WAITING = { label: "En attente", icon: "mdi:timer-sand", color: "var(--grey-color, #9e9e9e)", group: "route" };

const GROUPS = [
  { key: "action", title: "Action requise" },
  { key: "route", title: "En route" },
  { key: "delivered", title: "Livrés" },
];

// Order inside a group (most urgent first)
const ORDER = ["delivery_failure", "exception", "available_for_pickup", "not_found", "expired",
  "out_for_delivery", "in_transit", "info_received", "unknown", "delivered"];

// 17track sub-statuses worth showing (the "_Other" ones add nothing)
const SUB_STATUS_LABELS = {
  NotFound_InvalidCode: "Numéro invalide",
  InTransit_PickedUp: "Pris en charge",
  InTransit_Departure: "Départ",
  InTransit_Arrival: "Arrivé",
  InTransit_CustomsProcessing: "En douane",
  InTransit_CustomsReleased: "Dédouané",
  InTransit_CustomsRequiringInformation: "Douane : informations requises",
  DeliveryFailure_NoBody: "Destinataire absent",
  DeliveryFailure_Security: "Problème de sécurité",
  DeliveryFailure_Rejected: "Refusé par le destinataire",
  DeliveryFailure_InvalidAddress: "Adresse invalide",
  Exception_Returning: "Retour à l'expéditeur",
  Exception_Returned: "Retourné à l'expéditeur",
  Exception_NoBody: "Destinataire absent",
  Exception_Security: "Problème de sécurité",
  Exception_Damage: "Endommagé",
  Exception_Rejected: "Refusé",
  Exception_Delayed: "Retardé",
  Exception_Lost: "Perdu",
  Exception_Destroyed: "Détruit",
  Exception_Cancel: "Annulé",
};

const CARRIERS = {
  colissimo: "Colissimo",
  laposte: "La Poste",
  chronopost: "Chronopost",
  colisprive: "Colis Privé",
  mondialrelay: "Mondial Relay",
  relaiscolis: "Relais Colis",
  dpd: "DPD",
  gls: "GLS",
  ups: "UPS",
  fedex: "FedEx",
  tnt: "TNT",
  dhl: "DHL",
  amazon: "Amazon",
  vintedgo: "Vinted Go",
  cainiao: "Cainiao",
};

// Carriers offered when adding a package ("" = automatic detection)
const ADD_CARRIERS = ["", "colissimo", "chronopost", "colisprive", "mondialrelay", "relaiscolis",
  "dpd", "gls", "ups", "fedex", "tnt", "dhl", "vintedgo", "cainiao"];

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const STYLE = `
  :host { display: block; }
  ha-card { overflow: hidden; }
  .header { display: flex; align-items: center; justify-content: space-between; padding: 12px 8px 4px 16px; }
  .title { font-size: var(--ha-card-header-font-size, 20px); font-weight: 400; color: var(--ha-card-header-color, var(--primary-text-color)); }
  .icon-btn { width: 40px; height: 40px; border: 0; border-radius: 50%; background: none; color: var(--secondary-text-color);
              display: inline-grid; place-items: center; cursor: pointer; padding: 0; }
  .icon-btn:hover, .icon-btn:focus-visible { background: var(--secondary-background-color, rgba(127,127,127,.12)); outline: none; }
  .icon-btn.small { width: 32px; height: 32px; --mdc-icon-size: 18px; }

  .badges { display: flex; flex-wrap: wrap; gap: 8px; padding: 4px 16px 8px; }
  .badge { display: inline-flex; align-items: center; gap: 6px; height: 32px; padding: 0 12px 0 5px; border-radius: 16px;
           border: 1px solid var(--divider-color); font-size: 13px; font-weight: 500; color: var(--primary-text-color);
           background: var(--card-background-color, var(--ha-card-background)); }
  .badge .dot { width: 22px; height: 22px; --mdc-icon-size: 14px; }

  .shape { position: relative; width: 40px; height: 40px; flex: none; border-radius: 50%; display: grid; place-items: center; --mdc-icon-size: 22px; }
  .shape::before, .dot::before, .chip::before { content: ""; position: absolute; inset: 0; border-radius: inherit; background: currentColor; opacity: .2; }
  .shape ha-icon, .dot ha-icon { position: relative; }
  .dot { position: relative; border-radius: 50%; display: grid; place-items: center; }

  .section { font-size: 12px; font-weight: 500; letter-spacing: .3px; color: var(--secondary-text-color); padding: 12px 16px 2px; }
  .row { display: flex; align-items: center; gap: 12px; padding: 8px 16px; cursor: pointer; }
  .row:hover, .row:focus-visible, .row.open { background: var(--secondary-background-color, rgba(127,127,127,.06)); outline: none; }
  .info { flex: 1; min-width: 0; }
  .primary { font-size: 14px; font-weight: 500; line-height: 20px; color: var(--primary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .secondary { font-size: 12px; line-height: 16px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .chip { position: relative; flex: none; font-size: 12px; font-weight: 500; line-height: 20px; padding: 0 8px; border-radius: 10px; white-space: nowrap; }
  .chevron { color: var(--secondary-text-color); --mdc-icon-size: 20px; flex: none; transition: transform .2s; }
  .row.open .chevron { transform: rotate(180deg); }

  .details { padding: 4px 16px 12px 68px; background: var(--secondary-background-color, rgba(127,127,127,.06)); }
  .meta { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; font-size: 12px; color: var(--secondary-text-color); margin-bottom: 10px; }
  .meta code { font-family: var(--code-font-family, monospace); font-size: 12px; color: var(--primary-text-color); }
  .meta .spacer { flex: 1; }
  .ev { position: relative; padding: 0 0 10px 18px; font-size: 13px; line-height: 18px; color: var(--secondary-text-color); }
  .ev::before { content: ""; position: absolute; left: 0; top: 5px; width: 8px; height: 8px; border-radius: 50%; background: var(--divider-color); }
  .ev::after { content: ""; position: absolute; left: 3.5px; top: 15px; bottom: 0; width: 1px; background: var(--divider-color); }
  .ev:last-child::after { display: none; }
  .ev.first { color: var(--primary-text-color); }
  .ev.first::before { background: var(--accent); }
  .ev small { display: block; font-size: 11px; line-height: 15px; color: var(--secondary-text-color); }
  .no-events { font-size: 13px; color: var(--secondary-text-color); padding-bottom: 6px; }

  .form { padding: 4px 16px 12px; display: grid; gap: 8px; }
  .form .line { display: flex; gap: 8px; flex-wrap: wrap; }
  .form input, .form select { flex: 1 1 140px; min-width: 0; height: 40px; padding: 0 12px; font: inherit; font-size: 14px;
         color: var(--primary-text-color); background: var(--input-fill-color, var(--secondary-background-color, transparent));
         border: 0; border-bottom: 1px solid var(--input-idle-line-color, var(--divider-color)); border-radius: 4px 4px 0 0; }
  .form input:focus, .form select:focus { outline: none; border-bottom: 2px solid var(--primary-color); }
  .form .actions { display: flex; justify-content: flex-end; gap: 8px; }
  .text-btn { font: inherit; font-size: 14px; font-weight: 500; color: var(--primary-color); background: none; border: 0;
              padding: 0 12px; height: 36px; border-radius: 18px; cursor: pointer; }
  .text-btn:hover, .text-btn:focus-visible { background: var(--secondary-background-color, rgba(127,127,127,.12)); outline: none; }
  .text-btn.danger { color: var(--error-color, #db4437); padding: 0 8px; height: 28px; font-size: 13px; }
  .text-btn.filled { background: var(--primary-color); color: var(--text-primary-color, #fff); }
  .error { font-size: 12px; color: var(--error-color, #db4437); }

  .footer { display: flex; justify-content: flex-end; padding: 4px 8px 8px; border-top: 1px solid var(--divider-color); margin-top: 8px; }
  .empty { padding: 8px 16px 20px; color: var(--secondary-text-color); font-size: 14px; display: flex; gap: 12px; align-items: center; }
  .empty ha-icon { --mdc-icon-size: 32px; opacity: .6; }
`;

class SuiviColisCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._open = null; // tracking number whose history is shown
    this._showForm = false;
    this._formError = "";
    this._signature = "";
  }

  setConfig(config) {
    this._config = { title: "Colis", show_delivered: true, ...(config || {}) };
    this._signature = "";
    if (this._hass) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    // Re-render only when the packages changed (keeps the form and focus intact)
    const sig = this._packagesSignature();
    if (sig !== this._signature) {
      this._signature = sig;
      this._render();
    }
  }

  getCardSize() {
    return 2 + this._getPackages().length;
  }

  getGridOptions() {
    return { columns: 6, min_columns: 4 };
  }

  static getConfigElement() {
    return document.createElement("suivi-colis-card-editor");
  }

  static getStubConfig() {
    return { title: "Colis" };
  }

  // ------------------------------------------------------------------ data

  _getPackages() {
    if (!this._hass) return [];
    const list = [];
    for (const [entityId, st] of Object.entries(this._hass.states)) {
      const a = st.attributes;
      if (!entityId.startsWith("sensor.") || !a.tracking_number || a.carrier === undefined || st.state === "unavailable") continue;
      list.push({
        entity_id: entityId,
        status: st.state,
        number: a.tracking_number,
        name: a.package_name || "",
        carrier: a.carrier || "unknown",
        info: a.info_text || "",
        location: a.location || "",
        events: a.events || [],
        sub_status: a.sub_status || "",
        last_updated: a.last_updated || "",
        stopped: !!a.tracking_stopped,
        delivered_at: a.delivered_at || "",
        eta_from: a.estimated_delivery_from || "",
        eta_to: a.estimated_delivery_to || "",
      });
    }
    return list;
  }

  _packagesSignature() {
    return JSON.stringify(this._getPackages().map((p) => [p.number, p.status, p.info, p.last_updated, p.name, p.carrier, p.stopped, p.events.length]));
  }

  _status(p) {
    if (p.status === "unknown" && !p.last_updated) return WAITING;
    return STATUS[p.status] || STATUS.unknown;
  }

  _group(p) {
    if (p.stopped && p.status !== "delivered") return "action";
    return this._status(p).group;
  }

  // ------------------------------------------------------------------ formatting

  _day(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return "";
    const today = new Date();
    const tomorrow = new Date(); tomorrow.setDate(today.getDate() + 1);
    const yesterday = new Date(); yesterday.setDate(today.getDate() - 1);
    if (d.toDateString() === today.toDateString()) return "aujourd'hui";
    if (d.toDateString() === tomorrow.toDateString()) return "demain";
    if (d.toDateString() === yesterday.toDateString()) return "hier";
    return d.toLocaleDateString("fr-FR", { weekday: "short", day: "2-digit", month: "2-digit" });
  }

  _time(iso) {
    const d = new Date(iso);
    return isNaN(d) ? "" : d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  }

  _cap(s) {
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : "";
  }

  _carrierName(c) {
    return CARRIERS[c] || (c && c !== "unknown" ? this._cap(c) : "");
  }

  // Short pill on the right of the row
  _chip(p, st) {
    if (p.stopped && p.status !== "delivered") return { text: "Suivi arrêté", color: "var(--brown-color, #795548)" };
    if (p.status === "delivered") return null;
    if (p.status === "out_for_delivery") return { text: "Aujourd'hui", color: st.color };
    if (st.group === "action") return { text: st.label, color: st.color };
    const from = p.eta_from ? this._day(p.eta_from) : "";
    const to = p.eta_to ? this._day(p.eta_to) : "";
    if (from && to && from !== to) return { text: `${this._cap(from)} – ${to}`, color: st.color };
    if (from || to) return { text: this._cap(from || to), color: st.color };
    return null;
  }

  _secondary(p, st) {
    const parts = [];
    const sub = SUB_STATUS_LABELS[p.sub_status];
    if (p.status === "delivered" && p.delivered_at) {
      parts.push(`Livré ${this._day(p.delivered_at)} à ${this._time(p.delivered_at)}`);
    } else if (sub) {
      parts.push(sub);
    } else if (p.info) {
      parts.push(p.info);
    } else {
      parts.push(st.label);
    }
    const carrier = this._carrierName(p.carrier);
    if (carrier) parts.push(carrier);
    if (p.location && p.status !== "delivered") parts.push(p.location);
    return parts.join(" · ");
  }

  // ------------------------------------------------------------------ rendering

  _render() {
    if (!this._hass) return;
    const all = this._getPackages();
    const visible = this._config.show_delivered ? all : all.filter((p) => p.status !== "delivered");
    const order = (p) => (p.stopped ? -1 : ORDER.indexOf(p.status));
    visible.sort((a, b) => order(a) - order(b) || a.number.localeCompare(b.number));

    const sections = GROUPS.map((g) => {
      const items = visible.filter((p) => this._group(p) === g.key);
      if (!items.length) return "";
      return `<div class="section">${g.title}</div>${items.map((p) => this._row(p)).join("")}`;
    }).join("");

    const hasDelivered = all.some((p) => p.status === "delivered");

    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        <div class="header">
          <span class="title">${esc(this._config.title)}</span>
          <button class="icon-btn" id="add" title="${this._showForm ? "Fermer" : "Ajouter un colis"}" aria-label="${this._showForm ? "Fermer" : "Ajouter un colis"}">
            <ha-icon icon="${this._showForm ? "mdi:close" : "mdi:plus"}"></ha-icon>
          </button>
        </div>
        ${this._showForm ? this._form() : ""}
        ${all.length ? this._badges(all) : ""}
        ${visible.length ? `<div role="list">${sections}</div>` : this._empty(all.length)}
        ${hasDelivered ? `<div class="footer"><button class="text-btn" id="archive">Archiver les livrés</button></div>` : `<div style="height:8px"></div>`}
      </ha-card>`;
    this._bind();
  }

  _badges(all) {
    const count = (fn) => all.filter(fn).length;
    const items = [
      { n: count((p) => p.status === "out_for_delivery"), text: "aujourd'hui", st: STATUS.out_for_delivery },
      { n: count((p) => p.status === "available_for_pickup"), text: "à retirer", st: STATUS.available_for_pickup },
      { n: count((p) => ["in_transit", "info_received", "unknown"].includes(p.status) && !p.stopped), text: "en route", st: STATUS.in_transit },
      { n: count((p) => ["delivery_failure", "exception", "not_found", "expired"].includes(p.status) || (p.stopped && p.status !== "delivered")), text: "problème", st: STATUS.exception },
    ].filter((b) => b.n > 0);
    if (!items.length) return "";
    return `<div class="badges">${items.map((b) => `
      <span class="badge"><span class="dot" style="color:${b.st.color}"><ha-icon icon="${b.st.icon}"></ha-icon></span>${b.n} ${b.text}${b.n > 1 && b.text === "problème" ? "s" : ""}</span>`).join("")}
    </div>`;
  }

  _row(p) {
    const st = this._status(p);
    const chip = this._chip(p, st);
    const open = this._open === p.number;
    const title = p.name || p.number;
    return `
      <div class="row${open ? " open" : ""}" role="button" tabindex="0" aria-expanded="${open}" data-number="${esc(p.number)}">
        <div class="shape" style="color:${st.color}"><ha-icon icon="${st.icon}"></ha-icon></div>
        <div class="info">
          <div class="primary">${esc(title)}</div>
          <div class="secondary">${esc(this._secondary(p, st))}</div>
        </div>
        ${chip ? `<span class="chip" style="color:${chip.color}">${esc(chip.text)}</span>` : ""}
        <ha-icon class="chevron" icon="mdi:chevron-down"></ha-icon>
      </div>
      ${open ? this._details(p, st) : ""}`;
  }

  _details(p, st) {
    const events = (p.events || []).slice(0, 15);
    const timeline = events.length
      ? events.map((e, i) => {
          const when = [this._cap(this._day(e.time)), this._time(e.time)].filter(Boolean).join(" ");
          const where = e.location ? ` · ${esc(e.location)}` : "";
          return `<div class="ev${i === 0 ? " first" : ""}" style="${i === 0 ? `--accent:${st.color}` : ""}">${esc(e.description)}<small>${esc(when)}${where}</small></div>`;
        }).join("")
      : `<div class="no-events">${p.last_updated ? "Aucun événement pour l'instant." : "17track récupère les informations du transporteur…"}</div>`;
    const updated = p.last_updated ? `Mis à jour ${this._day(p.last_updated)} à ${this._time(p.last_updated)}` : "";
    return `
      <div class="details">
        <div class="meta">
          <code>${esc(p.number)}</code>
          ${this._carrierName(p.carrier) ? `<span>${esc(this._carrierName(p.carrier))}</span>` : ""}
          ${updated ? `<span>${esc(updated)}</span>` : ""}
          <span class="spacer"></span>
          <button class="text-btn danger" data-remove="${esc(p.number)}">Supprimer</button>
        </div>
        ${timeline}
      </div>`;
  }

  _form() {
    const options = ADD_CARRIERS.map((c) => `<option value="${c}">${c ? CARRIERS[c] : "Transporteur : détection auto"}</option>`).join("");
    return `
      <div class="form">
        <div class="line">
          <input id="number" placeholder="Numéro de suivi" autocomplete="off" spellcheck="false" aria-label="Numéro de suivi">
          <input id="name" placeholder="Nom (optionnel)" autocomplete="off" aria-label="Nom du colis">
        </div>
        <div class="line"><select id="carrier" aria-label="Transporteur">${options}</select></div>
        ${this._formError ? `<div class="error">${esc(this._formError)}</div>` : ""}
        <div class="actions">
          <button class="text-btn" id="cancel">Annuler</button>
          <button class="text-btn filled" id="submit">Ajouter</button>
        </div>
      </div>`;
  }

  _empty(total) {
    const text = total ? "Aucun colis en cours." : "Aucun colis suivi. Ajoutez un numéro avec le bouton +.";
    return `<div class="empty"><ha-icon icon="mdi:package-variant"></ha-icon>${text}</div>`;
  }

  // ------------------------------------------------------------------ events

  _bind() {
    const root = this.shadowRoot;
    root.getElementById("add")?.addEventListener("click", () => {
      this._showForm = !this._showForm;
      this._formError = "";
      this._render();
      if (this._showForm) root.getElementById("number")?.focus();
    });
    root.getElementById("cancel")?.addEventListener("click", () => {
      this._showForm = false;
      this._render();
    });
    root.getElementById("submit")?.addEventListener("click", () => this._add());
    root.querySelectorAll(".form input").forEach((el) =>
      el.addEventListener("keydown", (e) => e.key === "Enter" && this._add())
    );
    root.getElementById("archive")?.addEventListener("click", () => this._call("archive_delivered", {}));
    root.querySelectorAll(".row").forEach((row) => {
      const toggle = () => {
        const n = row.dataset.number;
        this._open = this._open === n ? null : n;
        this._render();
      };
      row.addEventListener("click", toggle);
      row.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
      });
    });
    root.querySelectorAll("[data-remove]").forEach((btn) =>
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const number = btn.dataset.remove;
        if (!confirm(`Arrêter le suivi du colis ${number} ?`)) return;
        this._open = null;
        this._call("remove_package", { tracking_number: number });
      })
    );
  }

  async _add() {
    const root = this.shadowRoot;
    const number = (root.getElementById("number")?.value || "").trim();
    if (!number) {
      this._formError = "Saisissez un numéro de suivi.";
      this._render();
      root.getElementById("number")?.focus();
      return;
    }
    const data = { tracking_number: number };
    const name = (root.getElementById("name")?.value || "").trim();
    const carrier = root.getElementById("carrier")?.value || "";
    if (name) data.friendly_name = name;
    if (carrier) data.carrier = carrier;
    if (await this._call("add_package", data)) {
      this._showForm = false;
      this._formError = "";
      this._render();
    }
  }

  async _call(service, data) {
    try {
      await this._hass.callService("suivi_colis", service, data);
      return true;
    } catch (err) {
      this.dispatchEvent(new CustomEvent("hass-notification", {
        detail: { message: `Suivi de Colis : ${err.message || err}` }, bubbles: true, composed: true,
      }));
      return false;
    }
  }
}

// ------------------------------------------------------------------ editor

class SuiviColisCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { title: "Colis", show_delivered: true, ...(config || {}) };
    this._render();
  }

  set hass(hass) {}

  _render() {
    this.innerHTML = `
      <div style="display:grid;gap:12px;padding:8px 0">
        <label style="display:grid;gap:4px;font-size:14px">Titre
          <input id="title" value="${esc(this._config.title)}" style="height:36px;padding:0 8px;font:inherit">
        </label>
        <label style="display:flex;gap:8px;align-items:center;font-size:14px">
          <input id="delivered" type="checkbox" ${this._config.show_delivered ? "checked" : ""}> Afficher les colis livrés
        </label>
        <p style="margin:0;font-size:12px;color:var(--secondary-text-color)">Les colis sont détectés automatiquement.</p>
      </div>`;
    this.querySelector("#title").addEventListener("change", (e) => this._update({ title: e.target.value }));
    this.querySelector("#delivered").addEventListener("change", (e) => this._update({ show_delivered: e.target.checked }));
  }

  _update(change) {
    this._config = { ...this._config, ...change };
    this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this._config }, bubbles: true, composed: true }));
  }
}

customElements.define("suivi-colis-card", SuiviColisCard);
customElements.define("suivi-colis-card-editor", SuiviColisCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "suivi-colis-card",
  name: "Suivi de Colis",
  description: "Colis suivis via 17track, regroupés par urgence, avec historique",
  preview: true,
  documentationURL: "https://github.com/hello-lemon/hacs-suivi-colis",
});

console.info(
  `%c SUIVI-DE-COLIS %c v${CARD_VERSION} `,
  "background:#FDD835;color:#000;font-weight:bold;padding:2px 6px;border-radius:4px 0 0 4px",
  "background:#333;color:#fff;padding:2px 6px;border-radius:0 4px 4px 0"
);
