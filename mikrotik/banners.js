"use strict";

(() => {
  const get = id => document.getElementById(id);

  function rotate(section, track) {
    const slides = Array.from(track.children);
    if (!slides.length) return;
    const pagination = get("banner-dots");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let index = 0;
    let timer;
    let touch = null;
    let touching = false;
    let hovering = false;
    const dots = slides.map((slide, i) => {
      const dot = document.createElement("button");
      dot.type = "button";
      dot.className = "banner-dot";
      dot.setAttribute("aria-label", `Show featured item ${i + 1} of ${slides.length}: ${slide.querySelector("h3").textContent}`);
      dot.disabled = slides.length === 1;
      dot.addEventListener("click", () => {
        show(i);
        restartRotation();
      });
      pagination.append(dot);
      return dot;
    });

    function show(next) {
      next = (next + slides.length) % slides.length;
      if (next === index) return;
      slides[index].classList.remove("banner-active");
      dots[index].removeAttribute("aria-current");
      index = next;
      slides[index].classList.add("banner-active");
      dots[index].setAttribute("aria-current", "true");
    }

    function restartRotation() {
      clearInterval(timer);
      if (!reducedMotion.matches && !touching) timer = setInterval(() => {
        if (!document.hidden && !hovering && !section.querySelector(":focus-visible")) show(index + 1);
      }, 6000);
    }

    dots[0].setAttribute("aria-current", "true");
    pagination.hidden = false;
    if (slides.length === 1) return;
    slides[0].classList.add("banner-active");
    track.classList.add("banner-rotating");
    section.addEventListener("pointerenter", event => { hovering = event.pointerType === "mouse"; });
    section.addEventListener("pointerleave", () => { hovering = false; });
    track.addEventListener("touchstart", event => {
      touching = true;
      touch = event.touches.length === 1 ? event.touches[0] : null;
      restartRotation();
    }, { passive: true });
    track.addEventListener("touchend", event => {
      const end = Array.from(event.changedTouches).find(point => point.identifier === touch?.identifier);
      if (end && touch) {
        const dx = end.clientX - touch.clientX;
        const dy = end.clientY - touch.clientY;
        if (Math.abs(dx) >= 40 && Math.abs(dx) > Math.abs(dy)) {
          event.preventDefault(); // Do not also follow an advertiser link after swiping.
          show(index + (dx > 0 ? 1 : -1));
        }
      }
      touch = null;
      touching = event.touches.length > 0;
      restartRotation();
    }, { passive: false });
    track.addEventListener("touchcancel", event => {
      touch = null;
      touching = event.touches.length > 0;
      restartRotation();
    });
    reducedMotion.addEventListener?.("change", restartRotation);
    restartRotation();
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
        const displayPrice = typeof banner.display_price === "string" ? banner.display_price : "";
        let destination;
        if (connected && typeof banner.url === "string") {
          try {
            const target = new URL(banner.url);
            if (target.protocol === "https:" && !target.username && !target.password) destination = target.href;
          } catch { /* An invalid destination does not prevent displaying the banner. */ }
        }
        const tile = document.createElement(destination ? "a" : "article");
        tile.className = "tile banner";
        if (destination) {
          tile.href = destination;
          tile.rel = "noreferrer";
          tile.setAttribute("aria-label", `View details: ${banner.title}${displayPrice ? ` · ${displayPrice}` : ""}`);
        }
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
        if (displayPrice) {
          const price = document.createElement("p");
          price.className = "banner-price";
          price.textContent = displayPrice;
          content.append(price);
        }
        if (typeof banner.description === "string" && banner.description) {
          const description = document.createElement("p");
          description.textContent = banner.description;
          content.append(description);
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

  function billingOrigin(value, protocol) {
    try {
      const url = new URL(value);
      if (url.protocol === protocol && (value === url.origin || value === url.origin + "/")) return url.origin;
    } catch { /* Banners are optional. */ }
    return null;
  }

  function published(connected) {
    const settings = window.mikrotikPortalConfig;
    const zone = typeof settings?.zoneId === "string" ? settings.zoneId.trim() : "";
    const checkoutOrigin = billingOrigin(settings?.billingOrigin, "https:");
    const origin = checkoutOrigin || billingOrigin(settings?.billingOriginLocal, "http:");
    if (!origin || !zone) return false;
    return render({ origin, zone }, connected && Boolean(checkoutOrigin));
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
