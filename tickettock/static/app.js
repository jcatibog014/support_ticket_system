// Extras only: every page works without this file.

// The Appearance choice on the Settings page. theme.js applies it on every page.
function setUpTheme() {
  const section = document.querySelector("[data-theme-settings]");
  if (!section) return;
  const root = document.documentElement;
  section.hidden = false;
  section.querySelectorAll('input[name="theme"]').forEach((choice) => {
    choice.checked = choice.value === (root.dataset.theme || "system");
    choice.addEventListener("change", () => {
      try {
        if (choice.value === "system") {
          delete root.dataset.theme;
          localStorage.removeItem("tickettock-theme");
        } else {
          root.dataset.theme = choice.value;
          localStorage.setItem("tickettock-theme", choice.value);
        }
      } catch (error) {
        // Storage can be blocked. The theme still changes for this page.
      }
    });
  });
}

// The ☰ button on phones.
function setUpMenu() {
  const button = document.querySelector("[data-menu-button]");
  const menu = document.getElementById(button && button.getAttribute("aria-controls"));
  if (!button || !menu) return;
  button.hidden = false;
  const setOpen = (open) => {
    button.setAttribute("aria-expanded", open);
    menu.classList.toggle("is-open", open);
  };
  button.addEventListener("click", () => setOpen(button.getAttribute("aria-expanded") !== "true"));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setOpen(false);
  });
}

// <details> menus (the account menu) close on an outside click or Escape.
function setUpPopovers() {
  const menus = () => document.querySelectorAll("details[data-popover][open]");
  document.addEventListener("click", (event) => {
    menus().forEach((menu) => {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    menus().forEach((menu) => {
      menu.open = false;
      menu.querySelector("summary").focus();
    });
  });
}

function setUpConfirm() {
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!confirm(form.dataset.confirm)) event.preventDefault();
    });
  });
  // One button of a form that needs asking first (Delete, beside Save).
  document.querySelectorAll("button[data-confirm-button]").forEach((button) => {
    button.addEventListener("click", (event) => {
      if (!confirm(button.dataset.confirmButton)) event.preventDefault();
    });
  });
}

// New Ticket: while the customer types the subject, show Help Articles that might answer it.
function setUpSuggestions() {
  const subject = document.querySelector("[data-suggest-url]");
  const box = document.querySelector("[data-suggestions]");
  if (!subject || !box) return;
  const list = box.querySelector("[data-suggestion-list]");
  let timer = null;
  let asked = "";
  const ask = async () => {
    const words = subject.value.trim();
    if (words === asked) return;
    asked = words;
    if (words.length < 4) {
      box.hidden = true;
      return;
    }
    try {
      const url = `${subject.dataset.suggestUrl}?q=${encodeURIComponent(words)}`;
      const answer = await (await fetch(url, { headers: { Accept: "application/json" } })).json();
      list.replaceChildren(...answer.articles.map((article) => {
        const item = document.createElement("li");
        const link = document.createElement("a");
        link.href = article.url;
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = article.title;  // text, never HTML
        item.append(link);
        return item;
      }));
      box.hidden = answer.articles.length === 0;
    } catch (error) {
      // No suggestions this time; the form still works.
    }
  };
  subject.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(ask, 400);
  });
}

// A select that saves itself (a staff member's role, a filter): no Save button needed.
function setUpAutoSubmit() {
  document.querySelectorAll("[data-auto-submit]").forEach((control) => {
    const button = control.form.querySelector("[data-auto-submit-button]");
    if (button) button.hidden = true;
    control.addEventListener("change", () => control.form.requestSubmit());
  });
}

// The Copy button beside an invite link.
function setUpCopy() {
  document.querySelectorAll("[data-copy]").forEach((button) => {
    const field = document.getElementById(button.dataset.copy);
    button.hidden = false;
    button.addEventListener("click", async () => {
      field.select();
      try {
        await navigator.clipboard.writeText(field.value);
      } catch (error) {
        document.execCommand("copy");
      }
      button.textContent = "Copied";
      setTimeout(() => (button.textContent = "Copy"), 1500);
    });
  });
}

// Ticket page: a saved reply goes straight into the reply box (without JavaScript, Use reloads the page).
function setUpSavedReplies() {
  const picker = document.querySelector("[data-saved-reply]");
  const box = document.querySelector("[data-reply-body]");
  if (!picker || !box) return;
  picker.addEventListener("change", () => {
    const option = picker.selectedOptions[0];
    if (!option || !option.dataset.text) return;
    const before = box.value.trimEnd();
    box.value = before ? [before, option.dataset.text].join("\n\n") : option.dataset.text;
    picker.value = "";
    box.focus();
  });
}

// Ticket page: "Ben Torres is also looking at this ticket", checked every 20 seconds.
function setUpLooking() {
  const line = document.querySelector("[data-looking-url]");
  if (!line) return;
  const names = line.querySelector("[data-looking-names]");
  const check = async () => {
    if (document.hidden) return;
    try {
      const response = await fetch(line.dataset.lookingUrl, { headers: { Accept: "application/json" } });
      if (!response.ok) return;
      const answer = await response.json();
      line.hidden = answer.names.length === 0;
      names.textContent = answer.names.length
        ? `${answer.names.join(", ")} ${answer.names.length === 1 ? "is" : "are"} also looking at this ticket.`
        : "";
    } catch (error) {
      // Offline for a moment: try again next time.
    }
  };
  setInterval(check, 20000);
}

// Queue: the bulk bar shows only the box its action needs, and counts the ticked tickets.
function setUpBulk() {
  const bar = document.getElementById("bulk");
  if (!bar) return;
  const action = bar.querySelector('[name="action"]');
  const fields = { assign: bar.querySelector('[name="assignee_id"]'), priority: bar.querySelector('[name="priority"]') };
  const count = bar.querySelector("[data-bulk-count]");
  const picks = document.querySelectorAll("[data-bulk-pick]");
  const update = () => {
    Object.entries(fields).forEach(([key, field]) => (field.hidden = action.value !== key));
    const ticked = [...picks].filter((pick) => pick.checked).length;
    count.textContent = `${ticked} ticked:`;
    bar.hidden = ticked === 0; // the bar only shows once a ticket is ticked
  };
  action.addEventListener("change", update);
  picks.forEach((pick) => pick.addEventListener("change", update));
  update();
}

// The service worker (installable app, offline page) and the "Install the app" button in Settings.
// Browsers that can install it fire "beforeinstallprompt"; only then does the button show.
function setUpInstall() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Not a problem: the site works the same without it.
    });
  }
  const button = document.querySelector("[data-install]");
  if (!button) return;
  let offer = null;
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault(); // our own button instead of the browser's banner
    offer = event;
    button.hidden = false;
  });
  button.addEventListener("click", async () => {
    if (!offer) return;
    offer.prompt();
    await offer.userChoice;
    offer = null;
    button.hidden = true;
  });
  window.addEventListener("appinstalled", () => (button.hidden = true));
}

// Messages fade away after a few seconds (errors stay); every message can be closed.
function setUpToasts() {
  const leave = (toast) => {
    toast.classList.add("is-leaving");
    setTimeout(() => toast.remove(), 400);
  };
  document.querySelectorAll(".toast").forEach((toast) => {
    const close = toast.querySelector("[data-toast-close]");
    close.hidden = false;
    close.addEventListener("click", () => leave(toast));
    if (toast.hasAttribute("data-toast-fade")) setTimeout(() => leave(toast), 6000);
  });
}

// The demo note: closed once, it stays hidden until the browser tab is closed.
function setUpDemoBar() {
  const bar = document.querySelector("[data-demo-bar]");
  if (!bar) return;
  const close = bar.querySelector("[data-demo-close]");
  try {
    if (sessionStorage.getItem("tickettock-demo-note") === "closed") bar.classList.add("is-closed");
  } catch (error) {
    // Storage can be blocked: the note just stays.
  }
  close.hidden = false;
  close.addEventListener("click", () => {
    bar.classList.add("is-closed");
    try {
      sessionStorage.setItem("tickettock-demo-note", "closed");
    } catch (error) {
      // Fine: it is hidden on this page anyway.
    }
  });
}

// A form's Save button sleeps until something in the form changes (Categories).
function setUpWatchChanges() {
  document.querySelectorAll("form[data-watch-changes]").forEach((form) => {
    const button = form.querySelector('button[type="submit"]:not([name])');
    if (!button) return;
    button.disabled = true;
    const wake = () => (button.disabled = false);
    form.addEventListener("input", wake);
    form.addEventListener("change", wake);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setUpToasts();
  setUpDemoBar();
  setUpWatchChanges();
  setUpInstall();
  setUpSuggestions();
  setUpSavedReplies();
  setUpLooking();
  setUpBulk();
  setUpTheme();
  setUpMenu();
  setUpPopovers();
  setUpConfirm();
  setUpAutoSubmit();
  setUpCopy();
});
