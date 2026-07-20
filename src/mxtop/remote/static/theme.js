"use strict";

(function initializeThemeAndCleanUrl() {
  let theme = null;
  try { theme = localStorage.getItem("mxtop-theme"); } catch (_) {}
  if (theme !== "light" && theme !== "dark") {
    theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches
      ? "light"
      : "dark";
  }
  document.documentElement.dataset.theme = theme;

  // ?token= is only a one-time cookie bootstrap. Remove it immediately so
  // it is not left in the address bar, copied URLs, or subsequent history.
  const url = new URL(window.location.href);
  if (url.searchParams.has("token")) {
    url.searchParams.delete("token");
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }
})();
