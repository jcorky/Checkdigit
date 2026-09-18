/*
 * Progressive enhancement for the header "Tools" menu. The menu is a native
 * <details>, so it opens and closes with the keyboard on its own; this only
 * adds the conveniences a real menu needs: close on Escape or an outside
 * click, and keep aria-expanded in step with the open state.
 */
export function enhanceNav(): void {
  // Make the skip-link target focusable so activating it moves keyboard focus,
  // not just the scroll position.
  const main = document.getElementById("main");
  if (main && !main.hasAttribute("tabindex")) main.setAttribute("tabindex", "-1");

  const menus = Array.from(document.querySelectorAll<HTMLDetailsElement>("details.menu"));
  if (menus.length === 0) return;

  for (const menu of menus) {
    const summary = menu.querySelector("summary");
    const sync = (): void => summary?.setAttribute("aria-expanded", menu.open ? "true" : "false");
    sync();
    menu.addEventListener("toggle", sync);
  }

  document.addEventListener("click", (ev) => {
    const target = ev.target as Node;
    for (const menu of menus) {
      if (menu.open && !menu.contains(target)) menu.open = false;
    }
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    for (const menu of menus) {
      if (menu.open) {
        menu.open = false;
        menu.querySelector("summary")?.focus();
      }
    }
  });
}
