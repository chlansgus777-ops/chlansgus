import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router-dom";
import App from "./App";
import { Startup } from "./components/Startup";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Startup>
      <HashRouter>
        <App />
      </HashRouter>
    </Startup>
  </StrictMode>,
);
