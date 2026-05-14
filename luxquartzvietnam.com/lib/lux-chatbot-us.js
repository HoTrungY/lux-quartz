(function () {
  const cfg = window.LUX_CHATBOT_US_CONFIG || {};
  const apiBase = (cfg.apiBase || "").replace(/\/$/, "");
  const detectedMarket = window.location.pathname.includes("/en/")
    ? "US"
    : cfg.market || "US";
  const market = detectedMarket;
  const endpoint = `${apiBase}/api/chat`;

  const root = document.createElement("div");
  root.id = "lux-chatbot-us-root";
  root.style.cssText =
    "position:fixed;right:20px;bottom:80px;z-index:2147483640;display:block;pointer-events:none;font-family:Montserrat,Roboto,Arial,sans-serif;";
  root.style.width = "fit-content";

  const panel = document.createElement("div");
  panel.className = "lux-chatbot-panel";
  panel.innerHTML = `
    <div class="lux-chatbot-header">
      <span>Lux Quartz Assistant (US)</span>
      <div class="lux-chatbot-header-actions">
        <button type="button" data-minimize aria-label="Minimize chat">−</button>
        <button type="button" data-close aria-label="Close chat">×</button>
      </div>
    </div>
    <div class="lux-chatbot-messages" id="lux-chatbot-messages"></div>
    <div class="lux-chatbot-suggestions">
      <button type="button" data-suggest="Quote LQ 504 2cm for 120 sf">Quote by code</button>
      <button type="button" data-suggest="Show white quartz 3cm">Find white quartz</button>
      <button type="button" data-suggest="Send real slab photo for LQ 917">Get slab photos</button>
    </div>
    <div class="lux-chatbot-input">
      <textarea id="lux-chatbot-input" placeholder="Type your request..."></textarea>
      <button type="button" id="lux-chatbot-send">Send</button>
    </div>
  `;

  const toggle = document.createElement("button");
  toggle.className = "lux-chatbot-toggle";
  toggle.setAttribute("type", "button");
  toggle.setAttribute("aria-label", "Open Lux chatbot");
  toggle.style.cssText = "display:flex;justify-content:flex-end;";
  toggle.innerHTML = `
    <div class="lux-chatbot-toggle-pill border shadow p-2">
      <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path stroke="none" d="M0 0h24v24H0z" fill="none"></path>
        <path d="M4 12a8 8 0 0 1 16 0"></path>
        <path d="M5 13v3a2 2 0 0 0 2 2h1v-5h-1a2 2 0 0 0 -2 2"></path>
        <path d="M19 13v3a2 2 0 0 1 -2 2h-1v-5h1a2 2 0 0 1 2 2"></path>
        <path d="M9.5 10.5h.01"></path>
        <path d="M14.5 10.5h.01"></path>
        <path d="M9 14c.8 .7 1.8 1 3 1s2.2 -.3 3 -1"></path>
      </svg>
      <span class="lux-chatbot-toggle-label">CHAT WITH AI</span>
    </div>
  `;

  root.appendChild(panel);
  root.appendChild(toggle);
  document.body.appendChild(root);

  const typingStyle = document.createElement("style");
  typingStyle.textContent = `
    #lux-chatbot-us-root .lux-typing-indicator { display: inline-flex; align-items: center; gap: 5px; min-width: 34px; min-height: 18px; }
    #lux-chatbot-us-root .lux-typing-indicator span { width: 7px; height: 7px; border-radius: 999px; background: #6b7280; animation: lux-us-typing-bounce 0.9s infinite ease-in-out; }
    #lux-chatbot-us-root .lux-typing-indicator span:nth-child(2) { animation-delay: 0.15s; }
    #lux-chatbot-us-root .lux-typing-indicator span:nth-child(3) { animation-delay: 0.3s; }
    @keyframes lux-us-typing-bounce { 0%, 80%, 100% { transform: translateY(0); opacity: 0.45; } 40% { transform: translateY(-6px); opacity: 1; } }
  `;
  document.head.appendChild(typingStyle);

  const messagesEl = panel.querySelector("#lux-chatbot-messages");
  const inputEl = panel.querySelector("#lux-chatbot-input");
  const sendBtn = panel.querySelector("#lux-chatbot-send");
  const closeBtn = panel.querySelector("[data-close]");
  const minimizeBtn = panel.querySelector("[data-minimize]");

  function syncToggleOffset() {
    root.style.right = window.innerWidth < 768 ? "10px" : "20px";
    root.style.bottom = window.innerWidth < 768 ? "10px" : "80px";
    root.style.display = "block";
  }

  syncToggleOffset();
  window.addEventListener("resize", syncToggleOffset);
  window.addEventListener("scroll", syncToggleOffset, { passive: true });
  window.addEventListener("load", syncToggleOffset);

  const STORAGE_KEY = "lux-chatbot-us-history-v2";
  const PANEL_STATE_KEY = "lux-chatbot-us-panel-state";
  const IDLE_TIMEOUT_MS = 2 * 60 * 1000;
  const IDLE_REMINDER_TEXT =
    "I am still online to support you. If you need real slab photos, a discounted quote, or delivery details, please send me a message anytime.";
  const MIN_REPLY_DELAY_MS = 2000;
  const MAX_REPLY_DELAY_MS = 4000;

  let idleTimer = null;
  let idleReminderSent = false;

  const storage = (() => {
    try {
      const probeKey = "__lux_chatbot_storage_probe__";
      window.sessionStorage.setItem(probeKey, "1");
      window.sessionStorage.removeItem(probeKey);
      return window.sessionStorage;
    } catch {
      return null;
    }
  })();

  function readStorage(key) {
    if (!storage) return null;
    try {
      return storage.getItem(key);
    } catch {
      return null;
    }
  }

  function writeStorage(key, value) {
    if (!storage) return;
    try {
      storage.setItem(key, value);
    } catch {
      // ignore write errors
    }
  }

  function clearIdleTimer() {
    if (idleTimer) {
      clearTimeout(idleTimer);
      idleTimer = null;
    }
  }

  function scheduleIdleReminder() {
    clearIdleTimer();
    if (idleReminderSent) return;
    if (!panel.classList.contains("open")) return;

    idleTimer = setTimeout(() => {
      if (idleReminderSent) return;
      if (!panel.classList.contains("open")) return;
      pushMessage("bot", IDLE_REMINDER_TEXT);
      idleReminderSent = true;
    }, IDLE_TIMEOUT_MS);
  }

  function resetIdleReminder() {
    idleReminderSent = false;
    scheduleIdleReminder();
  }

  function getReplyDelay() {
    return (
      MIN_REPLY_DELAY_MS +
      Math.floor(Math.random() * (MAX_REPLY_DELAY_MS - MIN_REPLY_DELAY_MS + 1))
    );
  }

  function wait(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function renderTypingIndicator() {
    return '<span class="lux-typing-indicator" aria-label="Typing"><span></span><span></span><span></span></span>';
  }

  async function waitForMinimumDelay(startedAt, delayMs) {
    const remaining = delayMs - (Date.now() - startedAt);
    if (remaining > 0) {
      await wait(remaining);
    }
  }

  const sessionId = (() => {
    const key = "lux-chatbot-us-session-id";
    const existing = readStorage(key);
    if (existing) return existing;
    const generated = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    writeStorage(key, generated);
    return generated;
  })();

  function escapeHtml(value) {
    return (value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function normalizeUrl(rawUrl) {
    const trimmed = (rawUrl || "").trim();
    if (!trimmed) return null;
    if (/^https?:\/\//i.test(trimmed)) return trimmed;
    return null;
  }

  function renderBotMessage(text) {
    const placeholderMap = {};
    let placeholderIndex = 0;

    const putPlaceholder = (html) => {
      const key = `__LUX_MD_TOKEN_${placeholderIndex++}__`;
      placeholderMap[key] = html;
      return key;
    };

    let safe = escapeHtml(text || "");

    safe = safe.replace(
      /\[!\[([^\]]*)\]\((https?:\/\/[^)\s]+)\)\]\((https?:\/\/[^)\s]+)\)/gi,
      (_, alt, imageUrl, productUrl) => {
        const productHref = normalizeUrl(productUrl);
        if (!productHref) return "";
        return putPlaceholder(
          `<a href="${productHref}" target="_blank" rel="noopener noreferrer" style="color: #007bff; text-decoration: underline;">View product details here</a>`,
        );
      },
    );

    safe = safe.replace(/!\[([^\]]*)\]\((https?:\/\/[^)\s]+)\)/gi, "");

    safe = safe.replace(
      /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/gi,
      (_, label, url) => {
        const href = normalizeUrl(url);
        if (!href) return _;
        const labelSafe = escapeHtml(label || href);
        return putPlaceholder(
          `<a href="${href}" target="_blank" rel="noopener noreferrer" style="color: #007bff; text-decoration: underline;">${labelSafe}</a>`,
        );
      },
    );

    safe = safe.replace(
      /(^|\s)(https?:\/\/[^\s<]+)/gi,
      (match, prefix, url) => {
        const href = normalizeUrl(url);
        if (!href) return match;
        if (/__LUX_MD_TOKEN_\d+__/.test(url)) return match;
        const link = `<a href="${href}" target="_blank" rel="noopener noreferrer" style="color: #007bff; text-decoration: underline;">${href}</a>`;
        return `${prefix}${putPlaceholder(link)}`;
      },
    );

    safe = safe.replace(/\n/g, "<br>");

    Object.keys(placeholderMap).forEach((key) => {
      safe = safe.replaceAll(key, placeholderMap[key]);
    });

    return safe;
  }

  function getHistory() {
    try {
      const raw = readStorage(STORAGE_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return parsed.filter(
        (item) =>
          item &&
          (item.role === "user" || item.role === "bot") &&
          typeof item.text === "string",
      );
    } catch {
      return [];
    }
  }

  function saveChatHistory() {
    const items = Array.from(
      messagesEl.querySelectorAll(".lux-chatbot-msg"),
    ).map((node) => ({
      role: node.classList.contains("user") ? "user" : "bot",
      text: node.dataset.rawText || node.textContent || "",
    }));
    writeStorage(STORAGE_KEY, JSON.stringify(items));
  }

  function pushMessage(role, text, options = {}) {
    const bubble = document.createElement("div");
    bubble.className = `lux-chatbot-msg ${role}${options.typing ? " typing" : ""}`;
    bubble.dataset.rawText = options.typing ? "" : text || "";

    if (options.typing) {
      bubble.innerHTML = renderTypingIndicator();
    } else if (role === "bot") {
      bubble.innerHTML = renderBotMessage(text);
    } else {
      bubble.textContent = text;
    }

    messagesEl.appendChild(bubble);
    messagesEl.scrollTop = messagesEl.scrollHeight;

    if (!options.skipSave) {
      saveChatHistory();
    }

    return bubble;
  }

  function loadChatHistory() {
    const history = getHistory();
    history.forEach((item) => {
      pushMessage(item.role, item.text, { skipSave: true });
    });
    if (history.length) {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }
  }

  async function sendMessage(text) {
    const value = (text || "").trim();
    if (!value) return;

    clearIdleTimer();
    pushMessage("user", value);
    const startedAt = Date.now();
    const replyDelayMs = getReplyDelay();
    const loadingNode = pushMessage("bot", "", {
      typing: true,
      skipSave: true,
    });

    try {
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sessionId,
          market,
          message: value,
          page: {
            url: window.location.href,
            title: document.title,
          },
        }),
      });

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

      const data = await res.json();
      const replyText = data.reply || "No response.";
      await waitForMinimumDelay(startedAt, replyDelayMs);
      loadingNode.classList.remove("typing");
      loadingNode.dataset.rawText = replyText;
      loadingNode.innerHTML = renderBotMessage(replyText);
      saveChatHistory();
      resetIdleReminder();
    } catch (err) {
      const fallback =
        "The system is busy for a moment. Please try again in a few minutes and I will support you right away.";
      await waitForMinimumDelay(startedAt, replyDelayMs);
      loadingNode.classList.remove("typing");
      loadingNode.dataset.rawText = fallback;
      loadingNode.innerHTML = renderBotMessage(fallback);
      saveChatHistory();
      resetIdleReminder();
      console.error("[lux-chatbot-us]", err);
    }
  }

  function setPanelState(state) {
    writeStorage(PANEL_STATE_KEY, state);
  }

  function setContactIconsHidden(hidden) {
    if (hidden) {
      document.body.classList.add("lux-chatbot-open");
      root.classList.add("chat-open");
      return;
    }
    document.body.classList.remove("lux-chatbot-open");
    root.classList.remove("chat-open");
  }

  function openPanel() {
    root.classList.remove("lux-chatbot-minimized");
    panel.classList.add("open");
    toggle.style.display = "none";
    setContactIconsHidden(true);
    setPanelState("open");
    if (!messagesEl.children.length) {
      pushMessage(
        "bot",
        "Hi there! I'm LuxBot, your personal assistant at Lux Quartz Vietnam Co., Ltd.\nTo get you a quick quote, please share:\n- Stone code or product line you're interested in\n- Project area (SF or sqm)\nI'm here to help anytime!",
      );
    }
    resetIdleReminder();
  }

  function minimizePanel() {
    panel.classList.remove("open");
    root.classList.add("lux-chatbot-minimized");
    toggle.style.display = "flex";
    setContactIconsHidden(false);
    setPanelState("minimized");
    clearIdleTimer();
  }

  function closePanel() {
    panel.classList.remove("open");
    root.classList.remove("lux-chatbot-minimized");
    toggle.style.display = "flex";
    setContactIconsHidden(false);
    setPanelState("closed");
    clearIdleTimer();
  }

  toggle.addEventListener("click", openPanel);
  minimizeBtn.addEventListener("click", minimizePanel);
  closeBtn.addEventListener("click", closePanel);

  sendBtn.addEventListener("click", () => {
    const text = inputEl.value;
    inputEl.value = "";
    sendMessage(text);
  });

  inputEl.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      const text = inputEl.value;
      inputEl.value = "";
      sendMessage(text);
    }
  });

  panel.querySelectorAll("[data-suggest]").forEach((btn) => {
    btn.addEventListener("click", () =>
      sendMessage(btn.getAttribute("data-suggest") || ""),
    );
  });

  loadChatHistory();

  const savedPanelState = readStorage(PANEL_STATE_KEY);
  if (savedPanelState === "open") {
    openPanel();
  } else {
    toggle.style.display = "flex";
    setContactIconsHidden(false);
    if (savedPanelState === "minimized") {
      root.classList.add("lux-chatbot-minimized");
    }
  }

  setTimeout(syncToggleOffset, 0);
  setTimeout(syncToggleOffset, 300);
})();
