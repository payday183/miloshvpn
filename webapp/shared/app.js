const state = {
  appType: document.body.dataset.app || "user",
  sessionToken: null,
  me: null,
  plans: [],
  content: [],
  subscription: null,
  access: null,
  latestPayment: null,
  dashboard: null,
  adminPlans: [],
  adminContent: [],
  adminServers: [],
  adminPage: "dashboard",
  adminMenuOpen: false,
  yookassa: null,
  yookassaState: null,
};

function $(selector) {
  return document.querySelector(selector);
}

function getQueryParam(name) {
  return new URLSearchParams(window.location.search).get(name) || "";
}

function formatDate(value) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString("ru-RU");
}

function formatMoney(amount, currency = "RUB") {
  if (typeof amount !== "number") {
    return "—";
  }
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(amount);
}

function formatBytes(value) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "—";
  }
  if (value < 1024) {
    return `${value} Б`;
  }
  const units = ["КБ", "МБ", "ГБ", "ТБ"];
  let size = value / 1024;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(size >= 100 ? 0 : 1)} ${units[unitIndex]}`;
}

function translateRole(value) {
  switch (value) {
    case "admin":
      return "админ";
    case "moder":
      return "модератор";
    case "user":
      return "пользователь";
    default:
      return value || "—";
  }
}

function translateYookassaMode(value) {
  switch (String(value || "").toLowerCase()) {
    case "test":
      return "тест";
    case "live":
      return "боевой";
    default:
      return value || "—";
  }
}

function translateYookassaModeBadge(value) {
  const translated = translateYookassaMode(value);
  return translated === "—" ? "состояние" : translated.toUpperCase();
}

function translateServerStatus(value) {
  switch (String(value || "").toLowerCase()) {
    case "healthy":
      return "исправен";
    case "degraded":
      return "нестабилен";
    case "unknown":
      return "неизвестно";
    case "offline":
      return "офлайн";
    case "error":
      return "ошибка";
    default:
      return value || "—";
  }
}

function translateBooleanWord(value) {
  return value ? "да" : "нет";
}

function translatePeriodLabel(label, periodKey = "", months = 0) {
  const normalizedLabel = String(label || "").trim().toLowerCase();
  const normalizedKey = String(periodKey || "").trim().toLowerCase();
  const translatedByKey = {
    "1_month": "1 месяц",
    "3_month": "3 месяца",
    "6_month": "6 месяцев",
    "12_month": "12 месяцев",
  };
  const translatedByLabel = {
    "1 month": "1 месяц",
    "3 months": "3 месяца",
    "6 months": "6 месяцев",
    "12 months": "12 месяцев",
  };

  if (translatedByKey[normalizedKey]) {
    return translatedByKey[normalizedKey];
  }
  if (translatedByLabel[normalizedLabel]) {
    return translatedByLabel[normalizedLabel];
  }
  if (!label && Number(months) > 0) {
    return `${months} мес.`;
  }
  return label || "Период";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setStatus(text, type = "info") {
  const el = $("#status");
  if (!el) {
    return;
  }
  el.textContent = text;
  el.className = `notice ${type}`;
}

function getStorageKey() {
  return `miloshvpn:webapp:${state.appType}:session`;
}

function getResumeQueryFlag() {
  return getQueryParam("resume") === "1";
}

function getTelegramInitData() {
  return window.Telegram?.WebApp?.initData || "";
}

function getSessionTokenFromUrl() {
  return getQueryParam("session_token");
}

function getReturnUrl() {
  return `${window.location.origin}/webapp/user?resume=1`;
}

function clearSessionTokenFromUrl() {
  const url = new URL(window.location.href);
  if (!url.searchParams.has("session_token")) {
    return;
  }
  url.searchParams.delete("session_token");
  window.history.replaceState({}, "", url.toString());
}

function prepareTelegramWebApp() {
  const tg = window.Telegram?.WebApp;
  if (!tg) {
    return;
  }
  tg.ready();
  if (typeof tg.setHeaderColor === "function") {
    tg.setHeaderColor("#ffffff");
  }
  if (typeof tg.setBackgroundColor === "function") {
    tg.setBackgroundColor("#f7f6f1");
  }
  if (typeof tg.disableVerticalSwipes === "function") {
    tg.disableVerticalSwipes();
  }
  if (typeof tg.expand === "function") {
    tg.expand();
  }
}

async function api(path, options = {}, includeSession = true) {
  const headers = new Headers(options.headers || {});
  headers.set("Content-Type", "application/json");
  if (includeSession && state.sessionToken) {
    headers.set("Authorization", `Bearer ${state.sessionToken}`);
  }
  const response = await fetch(path, {
    ...options,
    headers,
  });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      if (payload?.detail) {
        detail = payload.detail;
      }
    } catch (error) {
      // ignore
    }
    throw new Error(detail);
  }
  return response.json();
}

async function bootstrapSession() {
  const initData = getTelegramInitData();
  const sessionTokenFromUrl = getSessionTokenFromUrl();

  if (initData) {
    const auth = await api(
      "/api/webapp/auth/telegram",
      {
        method: "POST",
        body: JSON.stringify({
          init_data: initData,
          scope: state.appType,
        }),
      },
      false,
    );
    state.sessionToken = auth.session.token;
    localStorage.setItem(getStorageKey(), state.sessionToken);
    return;
  }

  if (sessionTokenFromUrl) {
    state.sessionToken = sessionTokenFromUrl;
    localStorage.setItem(getStorageKey(), state.sessionToken);
    clearSessionTokenFromUrl();
    return;
  }

  if (getResumeQueryFlag()) {
    state.sessionToken = localStorage.getItem(getStorageKey());
    return;
  }

  localStorage.removeItem(getStorageKey());
  state.sessionToken = null;
}

function getActorLabel(user) {
  if (!user) {
    return "гость";
  }
  if (user.username) {
    return `@${user.username}`;
  }
  if (user.telegram_id) {
    return `ID ${user.telegram_id}`;
  }
  return "пользователь";
}

function getActorGreeting() {
  if (state.appType === "admin") {
    return state.dashboard?.actor || null;
  }
  return state.me?.user || null;
}

function renderAccessDenied() {
  const panelName = state.appType === "admin" ? "админ-панель" : "личный кабинет";
  $("#app").innerHTML = `
    <section class="hero">
      <div class="hero-layout">
        <div class="hero-copy">
          <span class="eyebrow">MiloshVPN Доступ</span>
          <h1>Эта панель открывается только из Telegram-бота.</h1>
          <p>
            Для безопасности ${panelName} привязывается к ID Telegram пользователя, который
            нажал кнопку "👤 Профиль" в боте. Обычное открытие ссылки не создает персональную сессию.
          </p>
          <div class="hero-metrics">
            <article class="metric-card">
              <strong>Нет доступа</strong>
              <span>Без Telegram initData сервер не понимает, чей кабинет нужно открыть.</span>
            </article>
            <article class="metric-card">
              <strong>Что делать</strong>
              <span>Откройте бота, нажмите /start и затем кнопку "👤 Профиль", чтобы получить вход.</span>
            </article>
            <article class="metric-card">
              <strong>Результат</strong>
              <span>После этого откроется именно ваш кабинет, а не общий экран.</span>
            </article>
          </div>
        </div>
        <aside class="hero-card">
          <span class="pill warn">Доступ закрыт</span>
          <h2 class="hero-card-title">Только через Telegram</h2>
          <p>
            Одна и та же ссылка может выдавать разные кабинеты, потому что личность определяется
            не по URL, а по подписанному Telegram-контексту текущего пользователя.
          </p>
          <div class="hero-card-grid">
            <div class="hero-point">
              <strong>По ID</strong>
              <span>Каждый кабинет привязывается к конкретному Telegram ID на сервере.</span>
            </div>
            <div class="hero-point">
              <strong>Без общего режима</strong>
              <span>Если Telegram-контекста нет, показывается только эта страница.</span>
            </div>
          </div>
        </aside>
      </div>
    </section>
  `;
}

function renderUserHero() {
  const actor = getActorGreeting();
  $("#app").innerHTML = `
    <section class="user-app">
      <header class="user-topbar">
        <div class="user-brand">
          <div class="user-brand-mark">M</div>
          <div class="user-brand-copy">
            <strong>MiloshVPN</strong>
            <span>Личный кабинет</span>
          </div>
        </div>
        <div class="user-profile-chip">${escapeHtml(getActorLabel(actor))}</div>
      </header>
      <div class="grid two user-main-grid">
        <section class="panel user-card stack" id="user-main-card"></section>
        <section class="panel user-card stack" id="user-instructions-card"></section>
      </div>
    </section>
  `;
}

function getPrimaryPlanPeriod() {
  for (const plan of state.plans) {
    const activePeriods = (plan.periods || []).filter((period) => period.is_active !== false);
    const period = activePeriods[0] || (plan.periods || [])[0];
    if (period) {
      return { plan, period };
    }
  }
  return null;
}

function getInstructionCards() {
  if (state.content.length) {
    return state.content.slice(0, 3).map((block, index) => ({
      index: index + 1,
      title: block.title || `Шаг ${index + 1}`,
      body: block.body,
    }));
  }

  return [
    {
      index: 1,
      title: "Скачайте приложение",
      body: "Откройте ваш VPN-клиент или установите подходящее приложение на устройство.",
    },
    {
      index: 2,
      title: "Вставьте ключ",
      body: "Скопируйте ключ из поля выше и вставьте его в приложение.",
    },
    {
      index: 3,
      title: "Подключитесь",
      body: "Сохраните конфиг и нажмите подключение, чтобы VPN начал работать.",
    },
  ];
}

function renderUserPanels() {
  const mainCard = $("#user-main-card");
  const instructionsCard = $("#user-instructions-card");
  if (!mainCard || !instructionsCard) {
    return;
  }

  const subscription = state.subscription?.subscription;
  const access = state.access?.vpn_access;
  const latestPayment = state.latestPayment?.payment;
  const primaryOffer = getPrimaryPlanPeriod();
  const connectionKey = access?.connection_key || access?.subscription_url || access?.access_url;
  const daysLeft = subscription?.days_left ?? 0;
  const renewButtonLabel = subscription ? "Продлить" : "Купить доступ";
  const renewButtonMeta = primaryOffer
    ? `${formatMoney(primaryOffer.period.price_amount, primaryOffer.period.currency)} · ${translatePeriodLabel(primaryOffer.period.label, primaryOffer.period.period_key, primaryOffer.period.period_months)}`
    : "Тариф недоступен";
  const instructionCards = getInstructionCards();

  mainCard.innerHTML = `
    <div class="section-title user-section-heading">
      <h2>${subscription ? "Ваш доступ" : "Доступ еще не активен"}</h2>
      <span class="pill ${subscription ? "" : "warn"}">${subscription ? "Активен" : "Ожидает оплату"}</span>
    </div>
    <div class="user-days-card">
      <span class="stat-label">Осталось дней</span>
      <span class="user-days-value">${daysLeft}</span>
      <span class="user-days-meta">
        ${subscription ? `До ${formatDate(subscription.ends_at)}` : "После оплаты здесь появится срок действия"}
      </span>
    </div>
    <div class="field">
      <label>Ключ подключения</label>
      <div class="code-box user-key-box">${connectionKey || "Ключ появится после активации подписки"}</div>
    </div>
    <button class="button ghost user-copy-button" id="copy-access" ${connectionKey ? "" : "disabled"}>
      Скопировать ключ
    </button>
    <div class="meta">
      Сервер: ${access?.server || "—"}<br />
      Протокол: ${access?.protocol || "—"}<br />
      ${access?.provisioning_error ? `Статус: ${access.provisioning_error}` : subscription ? "Подключение готово к использованию" : "Оформите подписку, чтобы получить доступ"}
    </div>
    ${
      latestPayment?.status === "pending"
        ? `
          <div class="meta">
            Есть незавершенный платеж на ${formatMoney(latestPayment.amount, latestPayment.currency)}.
          </div>
        `
        : ""
    }
    <button
      class="button user-renew-button buy-button"
      data-period-id="${primaryOffer?.period?.id || ""}"
      ${primaryOffer && state.sessionToken ? "" : "disabled"}
    >
      ${renewButtonLabel}${primaryOffer ? ` · ${renewButtonMeta}` : ""}
    </button>
    ${
      latestPayment?.confirmation_url && latestPayment.status === "pending"
        ? `<a class="button secondary user-renew-button" href="${latestPayment.confirmation_url}">Продолжить оплату</a>`
        : ""
    }
  `;

  instructionsCard.innerHTML = `
    <div class="section-title user-section-heading">
      <h2>Инструкция</h2>
      <span class="pill secondary">3 шага</span>
    </div>
    <div class="card-list user-instruction-list">
      ${instructionCards.map((item) => `
        <article class="content-card user-step-card">
          <span class="user-step-index">0${item.index}</span>
          <strong>${escapeHtml(item.title)}</strong>
          <p>${escapeHtml(item.body)}</p>
        </article>
      `).join("")}
    </div>
  `;

  const copyButton = $("#copy-access");
  if (copyButton && connectionKey) {
    copyButton.addEventListener("click", async () => {
      await navigator.clipboard.writeText(connectionKey);
      setStatus("Ключ скопирован в буфер обмена.", "success");
    });
  }

  document.querySelectorAll(".buy-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const periodId = Number(button.dataset.periodId);
      if (!periodId) {
        return;
      }
      button.disabled = true;
      setStatus("Создаю платеж в YooKassa...", "info");
      try {
        const response = await api("/api/webapp/payments", {
          method: "POST",
          body: JSON.stringify({
            plan_period_id: periodId,
            payment_method_type: "sbp",
            return_url: getReturnUrl(),
          }),
        });
        const payment = response.payment;
        state.latestPayment = { payment };
        if (payment.confirmation_url) {
          window.location.href = payment.confirmation_url;
          return;
        }
        setStatus(
          payment.error_message || "Платеж создан, но YooKassa не вернула confirmation_url.",
          "error",
        );
        renderUserPanels();
      } catch (error) {
        setStatus(error.message || "Не удалось создать платеж.", "error");
      } finally {
        button.disabled = false;
      }
    });
  });

  const refreshButton = $("#refresh-payment");
  if (refreshButton) {
    refreshButton.addEventListener("click", async () => {
      const paymentId = Number(refreshButton.dataset.paymentId);
      if (!paymentId) {
        return;
      }
      setStatus("Обновляю статус платежа...", "info");
      try {
        const response = await api(`/api/webapp/payments/${paymentId}/refresh`, {
          method: "POST",
        });
        state.latestPayment = response;
        const [me, subscription, access] = await Promise.all([
          api("/api/webapp/me"),
          api("/api/webapp/subscription"),
          api("/api/webapp/access"),
        ]);
        state.me = me;
        state.subscription = subscription;
        state.access = access;
        renderUserPanels();
        setStatus("Статус платежа обновлен.", "success");
      } catch (error) {
        setStatus(error.message || "Не удалось обновить статус платежа.", "error");
      }
    });
  }
}

function getAdminNavItems() {
  return [
    {
      id: "dashboard",
      label: "Обзор",
      hint: "Сводка и состояние сервиса",
      badge: null,
    },
    {
      id: "plans",
      label: "Тарифы",
      hint: "Цены, периоды и каталог",
      badge: String(state.adminPlans.length || 0),
    },
    {
      id: "content",
      label: "Инструкции",
      hint: "Тексты до и после покупки",
      badge: String(state.adminContent.length || 0),
    },
    {
      id: "yookassa",
      label: "YooKassa",
      hint: "Магазин, webhook и режим",
      badge: state.yookassaState?.mode ? translateYookassaModeBadge(state.yookassaState.mode) : null,
    },
    {
      id: "servers",
      label: "Серверы",
      hint: "Marzban / VPS и состояние",
      badge: String(state.adminServers.length || 0),
    },
  ];
}

function normalizeAdminPage(page) {
  const available = new Set(getAdminNavItems().map((item) => item.id));
  return available.has(page) ? page : "dashboard";
}

function renderAdminPageHeader(eyebrow, title, description, badgeText = "") {
  return `
    <section class="panel admin-page-header">
      <div class="section-title admin-page-heading">
        <div class="stack admin-page-copy">
          <span class="eyebrow">${escapeHtml(eyebrow)}</span>
          <h2>${escapeHtml(title)}</h2>
          <p>${escapeHtml(description)}</p>
        </div>
        ${badgeText ? `<span class="pill secondary">${escapeHtml(badgeText)}</span>` : ""}
      </div>
    </section>
  `;
}

function renderAdminDashboardPage() {
  const actor = getActorGreeting();
  const activePeriods = state.adminPlans.flatMap((plan) => plan.periods || []).filter((period) => period.is_active !== false).length;
  const healthyServers = state.adminServers.filter((server) => server.status === "healthy").length;
  const enabledServers = state.adminServers.filter((server) => server.is_enabled).length;
  const activeBlocks = state.adminContent.filter((block) => block.is_active !== false).length;
  const runtime = state.yookassaState || {};

  return `
    ${renderAdminPageHeader(
      "Админ",
      "Операционная панель",
      "Отдельные разделы админки работают поверх серверного API: интерфейс только показывает данные и отправляет изменения на сервер.",
      "Сессия Telegram",
    )}
    <div class="grid admin-stats-grid">
      <section class="panel stat">
        <span class="stat-label">Пользователи</span>
        <span class="stat-value">${state.dashboard?.users ?? "—"}</span>
      </section>
      <section class="panel stat">
        <span class="stat-label">Платежи</span>
        <span class="stat-value">${state.dashboard?.payments ?? "—"}</span>
      </section>
      <section class="panel stat">
        <span class="stat-label">Выручка</span>
        <span class="stat-value">${formatMoney(Number(state.dashboard?.revenue || 0), "RUB")}</span>
      </section>
      <section class="panel stat">
        <span class="stat-label">Серверы</span>
        <span class="stat-value">${state.dashboard?.servers ?? "—"}</span>
      </section>
    </div>
    <div class="grid two admin-dashboard-grid">
      <section class="panel admin-summary-card">
        <div class="section-title">
          <h3>Текущая сессия</h3>
          <span class="pill">Админ</span>
        </div>
        <div class="card-list admin-summary-list">
          <article class="settings-card admin-summary-item">
            <strong>${escapeHtml(getActorLabel(actor))}</strong>
            <div class="meta">
              Доступ админа уже подтвержден на сервере.<br />
              Роль: ${escapeHtml(translateRole(actor?.role || "admin"))}<br />
              ID Telegram: ${escapeHtml(actor?.telegram_id || "—")}
            </div>
          </article>
          <article class="settings-card admin-summary-item">
            <strong>Состояние панели</strong>
            <div class="meta">
              Разделы вынесены в отдельные страницы через меню.<br />
              Бизнес-логика по-прежнему остается на сервере.
            </div>
          </article>
        </div>
      </section>
      <section class="panel admin-summary-card">
        <div class="section-title">
          <h3>Каталог</h3>
          <span class="pill secondary">Контент</span>
        </div>
        <div class="card-list admin-summary-list">
          <article class="settings-card admin-summary-item">
            <strong>${state.adminPlans.length || 0} планов</strong>
            <div class="meta">
              Активных периодов: ${activePeriods}<br />
              Контент-блоков: ${state.adminContent.length || 0}<br />
              Активных инструкций: ${activeBlocks}
            </div>
          </article>
          <article class="settings-card admin-summary-item">
            <strong>Редактирование из панели</strong>
            <div class="meta">
              Тарифы и инструкции меняются без правки кода, только через серверное API.
            </div>
          </article>
        </div>
      </section>
      <section class="panel admin-summary-card">
        <div class="section-title">
          <h3>YooKassa</h3>
          <span class="pill ${runtime.configured ? "" : "warn"}">${runtime.configured ? "Настроено" : "Не настроено"}</span>
        </div>
        <div class="card-list admin-summary-list">
          <article class="settings-card admin-summary-item">
            <strong>${escapeHtml(translateYookassaMode(runtime.mode))}</strong>
            <div class="meta">
              Адрес webhook: ${escapeHtml(runtime.webhook_url || "—")}<br />
              Адрес возврата: ${escapeHtml(runtime.return_url || "—")}
            </div>
          </article>
        </div>
      </section>
      <section class="panel admin-summary-card">
        <div class="section-title">
          <h3>Серверный пул</h3>
          <span class="pill secondary">Marzban</span>
        </div>
        <div class="card-list admin-summary-list">
          <article class="settings-card admin-summary-item">
            <strong>${healthyServers}/${state.adminServers.length || 0} исправны</strong>
            <div class="meta">
              Включено серверов: ${enabledServers}<br />
              Новые пользователи идут только через серверный селектор.
            </div>
          </article>
        </div>
      </section>
    </div>
  `;
}

function renderAdminPlansPage() {
  return `
    ${renderAdminPageHeader(
      "Тарифы",
      "Тарифы и периоды",
      "Здесь редактируются цены и сроки, которые панель пользователя получает с сервера. Сам интерфейс тарифы не считает.",
      `${state.adminPlans.length || 0} планов`,
    )}
    <section class="panel admin-section-stack">
      <div class="card-list">
        ${state.adminPlans.map((plan) => `
          <article class="settings-card admin-plan-card">
            <div class="section-title">
              <div>
                <strong>${escapeHtml(plan.name)}</strong>
                <div class="meta">${escapeHtml(plan.description || "Описание пока не задано.")}</div>
              </div>
              <span class="pill ${plan.is_active ? "" : "warn"}">${plan.is_active ? "Активен" : "Выключен"}</span>
            </div>
            <div class="stack">
              ${(plan.periods || []).map((period) => `
                <div class="settings-card admin-nested-card">
                  <div class="section-title">
                    <strong>${escapeHtml(translatePeriodLabel(period.label, period.period_key, period.period_months))}</strong>
                    <span class="pill secondary">${escapeHtml(period.currency || "RUB")}</span>
                  </div>
                  <div class="grid two admin-form-grid">
                    <div class="field">
                      <label>Название периода</label>
                      <input type="text" data-period-input="label" value="${escapeHtml(period.label)}" />
                    </div>
                    <div class="field">
                      <label>Цена</label>
                      <input type="number" step="0.01" data-period-input="price_amount" value="${escapeHtml(period.price_amount)}" />
                    </div>
                    <div class="field">
                      <label>Дней</label>
                      <input type="number" data-period-input="duration_days" value="${escapeHtml(period.duration_days)}" />
                    </div>
                    <div class="field">
                      <label>Месяцев</label>
                      <input type="number" value="${escapeHtml(period.period_months)}" disabled />
                    </div>
                  </div>
                  <div class="actions">
                    <button class="button save-period" data-period-id="${period.id}">Сохранить тариф</button>
                  </div>
                </div>
              `).join("")}
            </div>
          </article>
        `).join("") || '<div class="empty">Тарифы пока не найдены.</div>'}
      </div>
    </section>
  `;
}

function renderAdminContentPage() {
  return `
    ${renderAdminPageHeader(
      "Контент",
      "Инструкции и тексты",
      "Редактируемые блоки для панели пользователя. Тексты до покупки и после покупки приходят в интерфейс только с сервера.",
      `${state.adminContent.length || 0} блоков`,
    )}
    <section class="panel admin-section-stack">
      <div class="card-list">
        ${state.adminContent.map((block) => `
          <article class="content-card admin-content-card">
            <div class="section-title">
              <div>
                <strong>${escapeHtml(block.title || block.key)}</strong>
                <div class="meta">Ключ: ${escapeHtml(block.key)}</div>
              </div>
              <span class="pill ${block.is_active ? "" : "warn"}">${block.is_active ? "Активен" : "Скрыт"}</span>
            </div>
            <div class="field">
              <label>Заголовок</label>
              <input type="text" data-content-input="title" value="${escapeHtml(block.title || "")}" />
            </div>
            <div class="field">
              <label>Текст</label>
              <textarea data-content-input="body">${escapeHtml(block.body)}</textarea>
            </div>
            <div class="actions">
              <button class="button secondary save-content" data-content-key="${block.key}">
                Сохранить блок
              </button>
            </div>
          </article>
        `).join("") || '<div class="empty">Контент пока не найден.</div>'}
      </div>
    </section>
  `;
}

function renderAdminYookassaPage() {
  const runtime = state.yookassaState || {};

  return `
    ${renderAdminPageHeader(
      "Платежи",
      "YooKassa",
      "Настройки магазина хранятся на сервере. Здесь мы только меняем конфиг и показываем текущее состояние.",
      translateYookassaModeBadge(runtime.mode),
    )}
    <div class="grid two admin-form-grid">
      <section class="panel admin-section-stack">
        <div class="section-title">
          <h3>Текущее состояние</h3>
          <span class="pill ${runtime.configured ? "" : "warn"}">${runtime.configured ? "Настроено" : "Не настроено"}</span>
        </div>
        <article class="settings-card">
          <strong>Публичное состояние</strong>
          <div class="meta">
            настроено: ${translateBooleanWord(runtime.configured)}<br />
            режим: ${escapeHtml(translateYookassaMode(runtime.mode))}<br />
            адрес webhook: ${escapeHtml(runtime.webhook_url || "—")}<br />
            адрес возврата: ${escapeHtml(runtime.return_url || "—")}
          </div>
        </article>
      </section>
      <section class="panel admin-section-stack">
        <div class="section-title">
          <h3>Настройки магазина</h3>
          <span class="pill secondary">Серверное API</span>
        </div>
        <article class="settings-card">
          <div class="field">
            <label>ID магазина</label>
            <input type="text" id="yookassa-shop-id" value="${escapeHtml(state.yookassa?.yookassa_shop_id?.value || "")}" />
          </div>
          <div class="field">
            <label>Секретный ключ</label>
            <input type="password" id="yookassa-secret-key" value="${escapeHtml(state.yookassa?.yookassa_secret_key?.value || "")}" />
          </div>
          <div class="field">
            <label>Режим</label>
            <select id="yookassa-mode">
              <option value="test" ${(state.yookassa?.yookassa_mode?.value || "test") === "test" ? "selected" : ""}>тест</option>
              <option value="live" ${(state.yookassa?.yookassa_mode?.value || "test") === "live" ? "selected" : ""}>боевой</option>
            </select>
          </div>
          <div class="actions">
            <button class="button" id="save-yookassa">Сохранить YooKassa</button>
          </div>
        </article>
      </section>
    </div>
  `;
}

function renderAdminServersPage() {
  const healthyServers = state.adminServers.filter((server) => server.status === "healthy").length;
  const enabledServers = state.adminServers.filter((server) => server.is_enabled).length;

  return `
    ${renderAdminPageHeader(
      "Серверы",
      "Marzban / VPS",
      "Панель пользователя не ходит к серверам напрямую. Админка только настраивает серверный пул, а сервер решает, куда выдавать доступ.",
      `${state.adminServers.length || 0} серверов`,
    )}
    <div class="grid admin-stats-grid">
      <section class="panel stat">
        <span class="stat-label">Всего серверов</span>
        <span class="stat-value">${state.adminServers.length || 0}</span>
      </section>
      <section class="panel stat">
        <span class="stat-label">Исправны</span>
        <span class="stat-value">${healthyServers}</span>
      </section>
      <section class="panel stat">
        <span class="stat-label">Включено</span>
        <span class="stat-value">${enabledServers}</span>
      </section>
    </div>
    <div class="grid two admin-form-grid">
      <section class="panel admin-section-stack">
        <div class="section-title">
          <h3>Добавить сервер</h3>
          <span class="pill secondary">Серверное API</span>
        </div>
        <article class="settings-card">
          <div class="field">
            <label>Название</label>
            <input type="text" data-create-server="name" placeholder="ru-msk-1" />
          </div>
          <div class="field">
            <label>Локация</label>
            <input type="text" data-create-server="location" placeholder="Moscow" />
          </div>
          <div class="field">
            <label>Адрес API</label>
            <input type="text" data-create-server="api_base_url" placeholder="https://marzban.example.com" />
          </div>
          <div class="field">
            <label>URL панели</label>
            <input type="text" data-create-server="dashboard_url" placeholder="https://panel.example.com" />
          </div>
          <div class="field">
            <label>Логин</label>
            <input type="text" data-create-server="username" placeholder="admin" />
          </div>
          <div class="field">
            <label>Пароль</label>
            <input type="password" data-create-server="password" placeholder="пароль" />
          </div>
          <div class="field">
            <label>Токен доступа</label>
            <input type="password" data-create-server="access_token" placeholder="необязательный токен" />
          </div>
          <div class="field">
            <label>Теги инбаундов</label>
            <input type="text" data-create-server="inbound_tags" placeholder="vless-ws, reality-443" />
          </div>
          <div class="grid two admin-form-grid">
            <div class="field">
              <label>Вес</label>
              <input type="number" data-create-server="weight" value="100" />
            </div>
            <div class="field">
              <label>Лимит пользователей</label>
              <input type="number" data-create-server="max_users" placeholder="необязательно" />
            </div>
            <div class="field">
              <label>Лимит трафика в байтах</label>
              <input type="number" data-create-server="max_traffic_bytes" placeholder="необязательно" />
            </div>
          </div>
          <div class="actions">
            <label class="checkbox-row">
              <input type="checkbox" data-create-server="is_enabled" checked />
              <span>Включен</span>
            </label>
            <label class="checkbox-row">
              <input type="checkbox" data-create-server="allow_new_users" checked />
              <span>Разрешить новых пользователей</span>
            </label>
          </div>
          <div class="actions">
            <button class="button" id="create-server">Добавить сервер</button>
          </div>
        </article>
      </section>
      <section class="panel admin-section-stack">
        <div class="section-title">
          <h3>Текущий пул</h3>
          <span class="pill">${healthyServers} исправны</span>
        </div>
        <article class="settings-card">
          <strong>Балансировка на сервере</strong>
          <div class="meta">
            Новые пользователи не выбирают сервер сами.<br />
            Админ управляет пулом серверов и их доступностью, а выбор остается на серверной стороне.
          </div>
        </article>
      </section>
    </div>
    <section class="panel admin-section-stack">
      <div class="section-title">
        <h3>Существующие серверы</h3>
        <span class="pill secondary">Отдельные карточки</span>
      </div>
      <div class="card-list">
        ${state.adminServers.map((server) => `
          <article class="settings-card admin-server-card" data-server-card="${server.id}">
            <div class="section-title">
              <div>
                <strong>${escapeHtml(server.name)}</strong>
                <div class="meta">${escapeHtml(server.location || "Локация не указана")}</div>
              </div>
              <span class="pill ${server.status === "healthy" ? "" : server.status === "degraded" ? "warn" : "danger"}">${escapeHtml(translateServerStatus(server.status || "unknown"))}</span>
            </div>
            <div class="meta">
              Пользователей: ${server.metrics?.active_users ?? 0}<br />
              Трафик за 24ч: ${formatBytes(Number(server.metrics?.traffic_24h_bytes || 0))}<br />
              Нагрузка: ${server.metrics?.load_score ?? "—"}<br />
              Последняя проверка: ${formatDate(server.last_checked_at)}<br />
              ${server.last_error ? `Последняя ошибка: ${escapeHtml(server.last_error)}` : ""}
            </div>
            <div class="grid two admin-form-grid">
              <div class="field">
                <label>Название</label>
                <input type="text" data-server-input="name" value="${escapeHtml(server.name || "")}" />
              </div>
              <div class="field">
                <label>Локация</label>
                <input type="text" data-server-input="location" value="${escapeHtml(server.location || "")}" />
              </div>
              <div class="field">
                <label>Адрес API</label>
                <input type="text" data-server-input="api_base_url" value="${escapeHtml(server.api_base_url || "")}" />
              </div>
              <div class="field">
                <label>URL панели</label>
                <input type="text" data-server-input="dashboard_url" value="${escapeHtml(server.dashboard_url || "")}" />
              </div>
              <div class="field">
                <label>Логин</label>
                <input type="text" data-server-input="username" value="${escapeHtml(server.username || "")}" />
              </div>
              <div class="field">
                <label>Пароль</label>
                <input type="password" data-server-input="password" value="${escapeHtml(server.password || "")}" />
              </div>
              <div class="field">
                <label>Токен доступа</label>
                <input type="password" data-server-input="access_token" value="${escapeHtml(server.access_token || "")}" />
              </div>
              <div class="field">
                <label>Теги инбаундов</label>
                <input type="text" data-server-input="inbound_tags" value="${escapeHtml(server.inbound_tags || "")}" />
              </div>
              <div class="field">
                <label>Вес</label>
                <input type="number" data-server-input="weight" value="${escapeHtml(server.weight)}" />
              </div>
              <div class="field">
                <label>Лимит пользователей</label>
                <input type="number" data-server-input="max_users" value="${escapeHtml(server.max_users ?? "")}" />
              </div>
              <div class="field">
                <label>Лимит трафика в байтах</label>
                <input type="number" data-server-input="max_traffic_bytes" value="${escapeHtml(server.max_traffic_bytes ?? "")}" />
              </div>
            </div>
            <div class="actions">
              <label class="checkbox-row">
                <input type="checkbox" data-server-input="is_enabled" ${server.is_enabled ? "checked" : ""} />
                <span>Включен</span>
              </label>
              <label class="checkbox-row">
                <input type="checkbox" data-server-input="allow_new_users" ${server.allow_new_users ? "checked" : ""} />
                <span>Разрешить новых пользователей</span>
              </label>
            </div>
            <div class="actions">
              <button class="button save-server" data-server-id="${server.id}">Сохранить сервер</button>
              <button class="button ghost check-server" data-server-id="${server.id}">Проверить</button>
            </div>
          </article>
        `).join("") || '<div class="empty">Серверы пока не добавлены.</div>'}
      </div>
    </section>
  `;
}

function renderAdminShell() {
  state.adminPage = normalizeAdminPage(state.adminPage);
  const actor = getActorGreeting();
  const navItems = getAdminNavItems();
  const currentPage = navItems.find((item) => item.id === state.adminPage) || navItems[0];

  $("#app").innerHTML = `
    <section class="admin-app">
      <header class="panel admin-topbar">
        <div class="admin-topbar-main">
          <button class="admin-menu-toggle" id="admin-menu-toggle" type="button" aria-label="Открыть меню">
            <span></span>
            <span></span>
            <span></span>
          </button>
          <div class="admin-brand">
            <div class="admin-brand-mark">M</div>
            <div class="admin-brand-copy">
              <strong>MiloshVPN Админ</strong>
              <span>${escapeHtml(currentPage.label)} · ${escapeHtml(currentPage.hint)}</span>
            </div>
          </div>
        </div>
        <div class="admin-actor-chip">
          <span class="pill">Админ</span>
          <div class="admin-actor-copy">
            <strong>${escapeHtml(getActorLabel(actor))}</strong>
            <span>Сессия Telegram</span>
          </div>
        </div>
      </header>
      <aside class="admin-sidebar ${state.adminMenuOpen ? "is-open" : ""}">
        <div class="admin-sidebar-head">
          <div class="admin-sidebar-copy">
            <strong>Разделы админки</strong>
            <span>Каждый экран работает поверх серверного API.</span>
          </div>
          <button class="admin-sidebar-close" id="admin-menu-close" type="button" aria-label="Закрыть меню">×</button>
        </div>
        <div class="admin-nav">
          ${navItems.map((item) => `
            <button
              class="admin-nav-button ${item.id === state.adminPage ? "is-active" : ""}"
              type="button"
              data-admin-page="${item.id}"
            >
              <span class="admin-nav-copy">
                <strong>${escapeHtml(item.label)}</strong>
                <span>${escapeHtml(item.hint)}</span>
              </span>
              ${item.badge ? `<span class="admin-nav-badge">${escapeHtml(item.badge)}</span>` : ""}
            </button>
          `).join("")}
        </div>
        <div class="admin-sidebar-foot">
          <strong>Логика на сервере</strong>
          <span>Тарифы, контент, YooKassa и серверы меняются через сервер, а не локально в интерфейсе.</span>
        </div>
      </aside>
      <button class="admin-overlay ${state.adminMenuOpen ? "is-open" : ""}" id="admin-menu-overlay" type="button" aria-label="Закрыть меню"></button>
      <section class="admin-workspace">
        <div id="admin-page" class="stack admin-page"></div>
      </section>
    </section>
  `;

  const rerenderAdmin = () => {
    renderAdminApp();
  };

  const menuToggle = $("#admin-menu-toggle");
  if (menuToggle) {
    menuToggle.addEventListener("click", () => {
      state.adminMenuOpen = !state.adminMenuOpen;
      rerenderAdmin();
    });
  }

  const menuClose = $("#admin-menu-close");
  if (menuClose) {
    menuClose.addEventListener("click", () => {
      state.adminMenuOpen = false;
      rerenderAdmin();
    });
  }

  const menuOverlay = $("#admin-menu-overlay");
  if (menuOverlay) {
    menuOverlay.addEventListener("click", () => {
      state.adminMenuOpen = false;
      rerenderAdmin();
    });
  }

  document.querySelectorAll("[data-admin-page]").forEach((button) => {
    button.addEventListener("click", () => {
      state.adminPage = button.dataset.adminPage || "dashboard";
      state.adminMenuOpen = false;
      rerenderAdmin();
    });
  });
}

function renderAdminApp() {
  renderAdminShell();
  renderAdminPanels();
}

function bindAdminPlanActions() {
  document.querySelectorAll(".save-period").forEach((button) => {
    button.addEventListener("click", async () => {
      const card = button.closest(".settings-card");
      const periodId = Number(button.dataset.periodId);
      if (!card || !periodId) {
        return;
      }
      const payload = {
        label: card.querySelector('[data-period-input="label"]').value,
        price_amount: Number(card.querySelector('[data-period-input="price_amount"]').value),
        duration_days: Number(card.querySelector('[data-period-input="duration_days"]').value),
      };
      setStatus("Сохраняю тариф...", "info");
      try {
        await api(`/api/admin/plan-periods/${periodId}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        const plans = await api("/api/admin/plans");
        state.adminPlans = plans.plans || [];
        renderAdminApp();
        setStatus("Тариф сохранен.", "success");
      } catch (error) {
        setStatus(error.message || "Не удалось сохранить тариф.", "error");
      }
    });
  });
}

function bindAdminContentActions() {
  document.querySelectorAll(".save-content").forEach((button) => {
    button.addEventListener("click", async () => {
      const card = button.closest(".content-card");
      const key = button.dataset.contentKey;
      if (!card || !key) {
        return;
      }
      const payload = {
        title: card.querySelector('[data-content-input="title"]').value,
        body: card.querySelector('[data-content-input="body"]').value,
      };
      setStatus("Сохраняю контент...", "info");
      try {
        await api(`/api/admin/content/${key}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        const content = await api("/api/admin/content");
        state.adminContent = content.content || [];
        renderAdminApp();
        setStatus("Контент сохранен.", "success");
      } catch (error) {
        setStatus(error.message || "Не удалось сохранить контент.", "error");
      }
    });
  });
}

function bindAdminYookassaActions() {
  const saveYookassaButton = $("#save-yookassa");
  if (!saveYookassaButton) {
    return;
  }

  saveYookassaButton.addEventListener("click", async () => {
    const payload = {
      yookassa_shop_id: $("#yookassa-shop-id").value,
      yookassa_secret_key: $("#yookassa-secret-key").value,
      yookassa_mode: $("#yookassa-mode").value,
    };
    setStatus("Сохраняю настройки YooKassa...", "info");
    try {
      const response = await api("/api/admin/yookassa", {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      state.yookassa = response.settings || {};
      state.yookassaState = response.public_state || null;
      renderAdminApp();
      setStatus("Настройки YooKassa сохранены.", "success");
    } catch (error) {
      setStatus(error.message || "Не удалось сохранить YooKassa.", "error");
    }
  });
}

function bindAdminServerActions() {
  const createServerButton = $("#create-server");
  if (createServerButton) {
    createServerButton.addEventListener("click", async () => {
      setStatus("Добавляю сервер Marzban...", "info");
      try {
        await api("/api/admin/servers", {
          method: "POST",
          body: JSON.stringify(readServerPayload(document, "create-server")),
        });
        const [dashboard, servers] = await Promise.all([
          api("/api/admin/dashboard"),
          api("/api/admin/servers"),
        ]);
        state.dashboard = dashboard;
        state.adminServers = servers.servers || [];
        renderAdminApp();
        setStatus("Сервер добавлен.", "success");
      } catch (error) {
        setStatus(error.message || "Не удалось добавить сервер.", "error");
      }
    });
  }

  document.querySelectorAll(".save-server").forEach((button) => {
    button.addEventListener("click", async () => {
      const serverId = Number(button.dataset.serverId);
      const card = button.closest('[data-server-card]');
      if (!serverId || !card) {
        return;
      }
      setStatus("Сохраняю сервер...", "info");
      try {
        await api(`/api/admin/servers/${serverId}`, {
          method: "PATCH",
          body: JSON.stringify(readServerPayload(card, "server-input")),
        });
        const servers = await api("/api/admin/servers");
        state.adminServers = servers.servers || [];
        renderAdminApp();
        setStatus("Сервер сохранен.", "success");
      } catch (error) {
        setStatus(error.message || "Не удалось сохранить сервер.", "error");
      }
    });
  });

  document.querySelectorAll(".check-server").forEach((button) => {
    button.addEventListener("click", async () => {
      const serverId = Number(button.dataset.serverId);
      if (!serverId) {
        return;
      }
      setStatus("Проверяю подключение к серверу...", "info");
      try {
        const response = await api(`/api/admin/servers/${serverId}/check`, {
          method: "POST",
        });
        const servers = await api("/api/admin/servers");
        state.adminServers = servers.servers || [];
        renderAdminApp();
        setStatus(response.check?.detail || "Проверка завершена.", "success");
      } catch (error) {
        setStatus(error.message || "Не удалось проверить сервер.", "error");
      }
    });
  });
}

function renderAdminPanels() {
  const page = $("#admin-page");
  if (!page) {
    return;
  }

  state.adminPage = normalizeAdminPage(state.adminPage);

  if (state.adminPage === "dashboard") {
    page.innerHTML = renderAdminDashboardPage();
    return;
  }

  if (state.adminPage === "plans") {
    page.innerHTML = renderAdminPlansPage();
    bindAdminPlanActions();
    return;
  }

  if (state.adminPage === "content") {
    page.innerHTML = renderAdminContentPage();
    bindAdminContentActions();
    return;
  }

  if (state.adminPage === "yookassa") {
    page.innerHTML = renderAdminYookassaPage();
    bindAdminYookassaActions();
    return;
  }

  page.innerHTML = renderAdminServersPage();
  bindAdminServerActions();
}

function readServerPayload(container, fieldPrefix) {
  const getValue = (name) => container.querySelector(`[data-${fieldPrefix}="${name}"]`);
  return {
    name: getValue("name")?.value ?? "",
    location: getValue("location")?.value ?? "",
    api_base_url: getValue("api_base_url")?.value ?? "",
    dashboard_url: getValue("dashboard_url")?.value ?? "",
    username: getValue("username")?.value ?? "",
    password: getValue("password")?.value ?? "",
    access_token: getValue("access_token")?.value ?? "",
    inbound_tags: getValue("inbound_tags")?.value ?? "",
    weight: Number(getValue("weight")?.value || 100),
    max_users: getValue("max_users")?.value || null,
    max_traffic_bytes: getValue("max_traffic_bytes")?.value || null,
    is_enabled: Boolean(getValue("is_enabled")?.checked),
    allow_new_users: Boolean(getValue("allow_new_users")?.checked),
  };
}

async function loadUserApp() {
  renderUserHero();
  const [plans, content] = await Promise.all([
    api("/api/webapp/plans"),
    api("/api/webapp/content"),
  ]);
  state.plans = plans.plans || [];
  state.content = content.content || [];

  if (state.sessionToken) {
    const [me, subscription, access, latestPayment] = await Promise.all([
      api("/api/webapp/me"),
      api("/api/webapp/subscription"),
      api("/api/webapp/access"),
      api("/api/webapp/payments/latest"),
    ]);
    state.me = me;
    state.subscription = subscription;
    state.access = access;
    state.latestPayment = latestPayment;
  }

  renderUserHero();
  renderUserPanels();
}

async function loadAdminApp() {
  const [dashboard, plans, content, yookassa, servers] = await Promise.all([
    api("/api/admin/dashboard"),
    api("/api/admin/plans"),
    api("/api/admin/content"),
    api("/api/admin/yookassa"),
    api("/api/admin/servers"),
  ]);
  state.dashboard = dashboard;
  state.adminPlans = plans.plans || [];
  state.adminContent = content.content || [];
  state.yookassa = yookassa.settings || {};
  state.yookassaState = yookassa.public_state || null;
  state.adminServers = servers.servers || [];
  renderAdminApp();
}

async function boot() {
  try {
    prepareTelegramWebApp();
    await bootstrapSession();
    if (!state.sessionToken) {
      renderAccessDenied();
      setStatus("Нет Telegram-сессии. Откройте панель через бота и кнопку «👤 Профиль».", "error");
      return;
    }
    if (state.appType === "admin") {
      await loadAdminApp();
      setStatus("Админ-панель готова к проверке.", "success");
    } else {
      await loadUserApp();
      setStatus("Панель пользователя готова к проверке.", "success");
    }
  } catch (error) {
    if (String(error?.message || "").includes("Web app session")) {
      localStorage.removeItem(getStorageKey());
      state.sessionToken = null;
      renderAccessDenied();
    }
    setStatus(error.message || "Не удалось загрузить панель.", "error");
  }
}

document.addEventListener("DOMContentLoaded", boot);
