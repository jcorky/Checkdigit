import "../styles/site.css";
import "../styles/app.css";
import { BULK_PANEL_HTML, mountBulk } from "../lib/bulk-view";
import { byId } from "../ui/dom";
import { enhanceNav } from "../ui/nav";

// The standalone bulk page mounts the same checker as the home "Bulk check"
// tab, so the two stay identical.

enhanceNav();
byId("bulk-root").innerHTML = BULK_PANEL_HTML;
mountBulk();
