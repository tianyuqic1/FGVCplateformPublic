import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import App from "./App.jsx";
import { queryClient } from "./query/queryClient.js";
import "./styles.css";
import "./design-system/tokens.css";
import "./design-system/workbench.css";
import "./design-system/table-polish.css";
import "./design-system/page-polish.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
