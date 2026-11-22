"use strict";

(() => {
  const get = id => document.getElementById(id);

  function rotate(section, track) {
    const slides = Array.from(track.children);
    if (slides.length < 2) return;
    const controls = get("banner-controls");
    const pause = get("banner-pause");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let index = 0;
    let timer;
    let paused = reducedMotion.matches;

    function show(next) {
      slides[index].classList.remove("banner-active");
      index = (next + slides.length) % slides.length;
      slides[index].classList.add("banner-active");
    }

    function syncRotation() {
      clearInterval(timer);
      pause.textContent = paused ? "Resume rotation" : "Pause rotation";
      if (!paused) timer = setInterval(() => {
        if (!document.hidden && !section.matches(":hover")) show(index + 1);
      }, 6000);
    }

    slides[0].classList.add("banner-active");
    track.classList.add("banner-rotating");
    controls.hidden = false;
    get("banner-prev").addEventListener("click", () => show(index - 1));
    get("banner-next").addEventListener("click", () => show(index + 1));
    pause.addEventListener("click", () => {
      paused = !paused;
      syncRotation();
    });
    section.addEventListener("focusin", event => {
      if (event.target === pause) return;
      paused = true;
      syncRotation();
    });
    reducedMotion.addEventListener?.("change", event => {
      if (event.matches) {
        paused = true;
        syncRotation();
      }
    });
    syncRotation();
  }

  async function render(config, connected) {
    const section = get("portal-banners");
    const tiles = get("banner-tiles");
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
        const content = document.createElement("div");
        if (typeof banner.image === "string" && /^\/files\/[^\\?#]+$/.test(banner.image)) {
          const src = new URL(banner.image, config.origin);
          if (src.origin === config.origin && src.pathname.startsWith("/files/")) {
            const image = document.createElement("img");
            image.src = src.href;
            image.alt = typeof banner.alt === "string" ? banner.alt : "";
            image.referrerPolicy = "no-referrer";
            image.addEventListener("error", () => image.remove());
            tile.append(image);
          }
        }
        const heading = document.createElement("h3");
        heading.textContent = banner.title;
        content.append(heading);
        if (typeof banner.description === "string" && banner.description) {
          const description = document.createElement("p");
          description.textContent = banner.description;
          content.append(description);
        }
        if (connected && typeof banner.url === "string") {
          try {
            const target = new URL(banner.url);
            if (target.protocol === "https:" && !target.username && !target.password) {
              const link = document.createElement("a");
              link.href = target.href;
              link.rel = "noreferrer";
              link.textContent = `Visit ${banner.title}`;
              content.append(link);
            }
          } catch { /* An invalid destination does not prevent displaying the banner. */ }
        }
        tile.append(content);
        tiles.append(tile);
      }
      rotate(section, tiles);
      section.hidden = !tiles.children.length;
      return !section.hidden;
    } catch {
      return false;
    } finally {
      clearTimeout(timeout);
    }
  }

  function published(connected) {
    const settings = window.mikrotikPortalConfig;
    let origin;
    try {
      origin = new URL(settings?.billingOrigin);
    } catch {
      return false;
    }
    if (origin.protocol !== "https:" || (settings.billingOrigin !== origin.origin &&
        settings.billingOrigin !== origin.origin + "/") ||
        typeof settings.zoneId !== "string" || !settings.zoneId.trim()) return false;
    return render({ origin: origin.origin, zone: settings.zoneId.trim() }, connected);
  }

  async function connected(destination) {
    const next = get("continue-browsing");
    if (destination) {
      next.href = destination;
      next.hidden = false;
    }
    const shown = await published(true);
    if (!shown && destination) location.replace(destination);
  }
  window.HotspotBanners = { render, connected, preview: () => published(false) };
})();
