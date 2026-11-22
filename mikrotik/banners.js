"use strict";

(() => {
  async function render(config, connected) {
    const section = document.getElementById("portal-banners");
    const tiles = document.getElementById("banner-tiles");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 2500);
    try {
      const response = await fetch(config.origin +
        "/api/method/network_billing_system.api.captive_portal.get_mikrotik_banners", {
        method: "POST", body: new URLSearchParams({ zone_id: config.zone }),
        signal: controller.signal, credentials: "omit", redirect: "error", referrerPolicy: "no-referrer",
      });
      if (!response.ok) return false;
      const { message } = await response.json();
      if (!Array.isArray(message)) return false;
      for (const banner of message.slice(0, 6)) {
        if (!banner || typeof banner.title !== "string") continue;
        const tile = document.createElement("article");
        tile.className = "tile banner";
        if (typeof banner.image === "string" && /^\/files\/[^\\?#]+$/.test(banner.image)) {
          const src = new URL(banner.image, config.origin);
          if (src.origin === config.origin && src.pathname.startsWith("/files/")) {
            const image = document.createElement("img");
            image.src = src.href;
            image.alt = typeof banner.alt === "string" ? banner.alt : "";
            image.loading = "lazy";
            image.referrerPolicy = "no-referrer";
            image.addEventListener("error", () => image.remove());
            tile.append(image);
          }
        }
        const heading = document.createElement("h3");
        heading.textContent = banner.title;
        tile.append(heading);
        if (typeof banner.description === "string" && banner.description) {
          const description = document.createElement("p");
          description.textContent = banner.description;
          tile.append(description);
        }
        if (connected && typeof banner.url === "string") {
          try {
            const target = new URL(banner.url);
            if (target.protocol === "https:" && !target.username && !target.password) {
              const link = document.createElement("a");
              link.href = target.href;
              link.rel = "noreferrer";
              link.textContent = `Visit ${banner.title}`;
              tile.append(link);
            }
          } catch { /* An invalid destination does not prevent displaying the banner. */ }
        }
        tiles.append(tile);
      }
      section.hidden = !tiles.children.length;
      return !section.hidden;
    } catch {
      return false;
    } finally {
      clearTimeout(timeout);
    }
  }

  async function connected(destination) {
    const next = document.getElementById("continue-browsing");
    if (destination) {
      next.href = destination;
      next.hidden = false;
    }
    const settings = window.mikrotikPortalConfig;
    let shown = false;
    try {
      const origin = new URL(settings?.billingOrigin);
      if (origin.protocol === "https:" && (settings.billingOrigin === origin.origin ||
          settings.billingOrigin === origin.origin + "/") && settings.zoneId?.trim()) {
        shown = await render({ origin: origin.origin, zone: settings.zoneId.trim() }, true);
      }
    } catch { /* An unconfigured portal keeps the normal RouterOS destination. */ }
    if (!shown && destination) location.replace(destination);
  }
  window.HotspotBanners = { render, connected };
})();
