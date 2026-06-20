import React from "react";
import ReactDOM from "react-dom/client";
// HashRouter (not BrowserRouter): GitHub Pages has no server to rewrite deep
// links, so path routing would 404 on refresh. Hash routing (/#/dashboard) is
// served entirely client-side and needs no Pages config.
import { HashRouter } from "react-router-dom";
import { AuthProvider } from "./auth";
import App from "./App";
import "./ds/styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <HashRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </HashRouter>
  </React.StrictMode>
);
