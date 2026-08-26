// Cortex ComfyUI Bridge — envoi d'un workflow vers le pipeline (sans dépendance).
//
// Ajoute un bouton flottant "→ Pipeline" dans ComfyUI. Au clic, il convertit le
// workflow OUVERT via graphToPrompt (natif, aplatit les subgraphs) et le POST au
// bridge (/v1/workflows), qui l'auto-lie + le suit. Aucune dépendance Python.
//
// Installation : déposer ce fichier dans le dossier des extensions front de
// ComfyUI, p.ex.  ComfyUI/web/extensions/cortex/cortex_pipeline_sync.js
// (ou custom_nodes/<x>/web/). Recharger ComfyUI. URL du bridge configurable
// ci-dessous (BRIDGE_URL).

import { app } from "../../scripts/app.js";

const BRIDGE_URL = "http://127.0.0.1:8000";

app.registerExtension({
  name: "Cortex.PipelineSync",
  async setup() {
    const btn = document.createElement("button");
    btn.textContent = "→ Pipeline";
    Object.assign(btn.style, {
      position: "fixed", right: "12px", bottom: "12px", zIndex: 1000,
      padding: "8px 14px", borderRadius: "8px", border: "none", cursor: "pointer",
      fontWeight: "600", color: "#fff",
      background: "linear-gradient(135deg,#6ea8fe,#8b5cf6)",
    });
    btn.title = "Envoyer ce workflow au pipeline Cortex (auto-lié)";
    btn.onclick = async () => {
      const suggested = (app.graph && app.graph.extra && app.graph.extra.workflowName) || "workflow";
      const name = window.prompt("Nom du workflow dans le pipeline :", suggested);
      if (!name) return;
      btn.disabled = true; const label = btn.textContent; btn.textContent = "Envoi…";
      try {
        const { output } = await app.graphToPrompt();
        const res = await fetch(BRIDGE_URL + "/v1/workflows", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ name, workflow: output }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          const binds = Object.keys(data.bindings || {}).join(", ") || "aucun";
          alert(`Envoyé au pipeline ✓\n${data.name} (${data.kind})\nbindings auto : ${binds}`);
        } else {
          alert(`Échec (${res.status}) : ${(data.detail || data.title || "")}`);
        }
      } catch (e) {
        alert("Erreur : " + e);
      } finally {
        btn.disabled = false; btn.textContent = label;
      }
    };
    document.body.appendChild(btn);
  },
});
