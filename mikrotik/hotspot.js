"use strict";

(() => {
  // RouterOS HTTP-CHAP requires MD5, which Web Crypto does not provide.
  const shifts = [[7, 12, 17, 22], [5, 9, 14, 20], [4, 11, 16, 23], [6, 10, 15, 21]];
  const constants = Array.from({ length: 64 }, (_, i) => Math.floor(2 ** 32 * Math.abs(Math.sin(i + 1))));
  function md5(bytes) {
    const padded = new Uint8Array(Math.ceil((bytes.length + 9) / 64) * 64);
    const words = new DataView(padded.buffer);
    padded.set(bytes);
    padded[bytes.length] = 0x80;
    words.setUint32(padded.length - 8, bytes.length * 8, true);
    words.setUint32(padded.length - 4, Math.floor(bytes.length / 2 ** 29), true);
    const state = [0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476];
    for (let offset = 0; offset < padded.length; offset += 64) {
      let [a, b, c, d] = state;
      for (let i = 0; i < 64; i++) {
        const round = i >> 4;
        let f, g;
        if (round === 0) { f = (b & c) | (~b & d); g = i; }
        else if (round === 1) { f = (d & b) | (~d & c); g = (5 * i + 1) % 16; }
        else if (round === 2) { f = b ^ c ^ d; g = (3 * i + 5) % 16; }
        else { f = c ^ (b | ~d); g = (7 * i) % 16; }
        const sum = (a + f + constants[i] + words.getInt32(offset + 4 * g, true)) | 0;
        const shift = shifts[round][i % 4];
        [a, b, c, d] = [d, (b + ((sum << shift) | (sum >>> (32 - shift)))) | 0, b, c];
      }
      [a, b, c, d].forEach((word, i) => { state[i] = (state[i] + word) | 0; });
    }
    const digest = new DataView(new ArrayBuffer(16));
    state.forEach((word, i) => digest.setInt32(i * 4, word, true));
    return Array.from(new Uint8Array(digest.buffer), byte => byte.toString(16).padStart(2, "0")).join("");
  }

  function chapPassword(password, chap) {
    const { id, challenge } = chap;
    if (typeof id !== "string" || id.length !== 1 || typeof challenge !== "string" ||
        challenge.length !== 16 || /[^\x00-\xff]/.test(id + challenge)) {
      throw new Error("Invalid router challenge");
    }
    const secret = new TextEncoder().encode(password);
    const bytes = new Uint8Array(1 + secret.length + challenge.length);
    bytes[0] = id.charCodeAt(0);
    bytes.set(secret, 1);
    bytes.set(Array.from(challenge, ch => ch.charCodeAt(0)), 1 + secret.length);
    return md5(bytes);
  }

  function splitVoucher(voucher) {
    if (typeof voucher !== "string" || /[\x00-\x20\x7f]/.test(voucher)) return null;
    const dot = voucher.indexOf(".");
    return dot > 0 && dot < voucher.length - 1
      ? { username: voucher.slice(0, dot), password: voucher.slice(dot + 1) }
      : null;
  }

  function destination(escaped) {
    try {
      const url = new URL(decodeURIComponent(escaped));
      if (["http:", "https:"].includes(url.protocol) && !url.username && !url.password &&
          url.origin !== location.origin) return url.href;
    } catch { /* An absent or unresolved RouterOS variable is not a destination. */ }
    return "";
  }

  function submitLogin(credentials) {
    const get = id => document.getElementById(id);
    const form = get("router-login");
    const target = new URL(form.getAttribute("action"), location.href);
    if (target.origin !== location.origin || target.pathname !== "/login" ||
        target.username || target.password || target.search || target.hash) throw new Error("Invalid router login URL");
    const chap = window.hotspotChap;
    const password = chap ? chapPassword(credentials.password, chap)
      : chap === null && target.protocol === "https:" ? credentials.password : null;
    if (password === null) throw new Error("Router login requires CHAP or HTTPS");
    get("router-destination").value = destination(get("router-destination").dataset.original);
    get("router-user").value = credentials.username;
    get("router-password").value = password;
    try {
      form.submit();
    } finally {
      get("router-user").value = get("router-password").value = "";
    }
  }

  window.Hotspot = { chapPassword, splitVoucher, destination, submitLogin };
})();
